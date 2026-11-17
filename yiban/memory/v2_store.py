"""Structured memory v2: shared authentication, one schema, review-first writes."""
import copy
import json
import secrets
import threading
import time
import uuid

from yiban.memory.shared_memory import SharedError
from yiban.memory.v2_schema import validate, digest, fields, text, strings


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class StructuredMemory:
    def __init__(self, shared, models):
        self.shared, self.store, self.models = shared, shared.store, models
        self.lock, self.plans = threading.Lock(), {}
        from yiban.memory.shared_dialogue import SharedDialogue
        self.dialogue = SharedDialogue(SharedError)
        with self.store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS memory_v2_meta(version INTEGER NOT NULL);
              INSERT INTO memory_v2_meta SELECT 2 WHERE NOT EXISTS(SELECT 1 FROM memory_v2_meta);
              CREATE TABLE IF NOT EXISTS memory_v2_scopes(id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS memory_v2_entities(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES memory_v2_scopes(id), external_key TEXT NOT NULL, kind TEXT NOT NULL,
                name TEXT NOT NULL, aliases_json TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(scope,external_key));
              CREATE TABLE IF NOT EXISTS memory_v2_events(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES memory_v2_scopes(id), external_key TEXT NOT NULL, kind TEXT NOT NULL,
                title TEXT NOT NULL, state TEXT NOT NULL, time_json TEXT NOT NULL, location TEXT NOT NULL,
                payload_json TEXT NOT NULL, UNIQUE(scope,external_key));
              CREATE TABLE IF NOT EXISTS memory_v2_sources(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES memory_v2_scopes(id), external_key TEXT NOT NULL, author TEXT NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL, text TEXT NOT NULL,
                payload_json TEXT NOT NULL, UNIQUE(scope,external_key));
              CREATE TABLE IF NOT EXISTS memory_v2_records(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES memory_v2_scopes(id), external_key TEXT NOT NULL, author TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, detail TEXT NOT NULL,
                event_id TEXT REFERENCES memory_v2_events(id), verification TEXT NOT NULL,
                verification_reason TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'active',
                visibility TEXT NOT NULL, edit_policy TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(scope,external_key));
              CREATE TABLE IF NOT EXISTS memory_v2_audience(
                record TEXT NOT NULL REFERENCES memory_v2_records(id) ON DELETE CASCADE,
                account TEXT NOT NULL, PRIMARY KEY(record,account));
              CREATE TABLE IF NOT EXISTS memory_v2_participants(
                record TEXT NOT NULL REFERENCES memory_v2_records(id) ON DELETE CASCADE,
                entity TEXT NOT NULL REFERENCES memory_v2_entities(id), PRIMARY KEY(record,entity));
              CREATE TABLE IF NOT EXISTS memory_v2_facts(
                record TEXT PRIMARY KEY REFERENCES memory_v2_records(id) ON DELETE CASCADE,
                subject TEXT NOT NULL REFERENCES memory_v2_entities(id), object TEXT REFERENCES memory_v2_entities(id),
                predicate TEXT NOT NULL, value_json TEXT NOT NULL, unit TEXT NOT NULL, metric TEXT NOT NULL,
                term TEXT NOT NULL, effective_json TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS memory_v2_evidence(
                record TEXT NOT NULL REFERENCES memory_v2_records(id) ON DELETE CASCADE,
                source TEXT NOT NULL REFERENCES memory_v2_sources(id), start INTEGER NOT NULL, end INTEGER NOT NULL,
                quote TEXT NOT NULL, PRIMARY KEY(record,source,start,end));
              CREATE TABLE IF NOT EXISTS memory_v2_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT NOT NULL REFERENCES memory_v2_records(id),
                actor TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
              CREATE INDEX IF NOT EXISTS memory_v2_scope_records ON memory_v2_records(scope,lifecycle);
              CREATE INDEX IF NOT EXISTS memory_v2_event_records ON memory_v2_records(event_id);
              CREATE INDEX IF NOT EXISTS memory_v2_fact_predicate ON memory_v2_facts(predicate,term);
            ''')
            if [r[0] for r in db.execute('SELECT version FROM memory_v2_meta')] != [2]:
                raise RuntimeError('新版记忆数据库版本不受支持，请先备份并升级。')

    def scope(self, db, token, data, write=False):
        actor = self.shared._actor(db, token)
        kind = data.get('scope_kind', 'team')
        if kind == 'personal':
            result = {'id': 'personal:'+actor['id'], 'name': '个人 · '+actor['name'], 'kind': kind,
                      'owner': actor['id'], 'role': 'owner', 'member_version': 0, 'members': {actor['id']}}
        elif kind == 'team':
            space = self.shared._member(db, actor['id'], data.get('space_id'))
            result = {'id': 'team:'+space['id'], 'space_id': space['id'], 'name': ('家庭 · ' if space['kind']=='family' else '团队 · ')+space['name'],
                      'kind': kind, 'owner': space['owner'], 'role': space['role'], 'member_version': space['version'],
                      'members': {r[0] for r in db.execute('SELECT account FROM shared_members WHERE space=?', (space['id'],))}}
        else:
            raise SharedError('记忆归属只能为个人或团队。')
        if write and result['role'] == 'reader':
            raise SharedError('只读成员不能发布或修改记忆。', 403)
        row = db.execute('SELECT version FROM memory_v2_scopes WHERE id=?', (result['id'],)).fetchone()
        result['version'] = row[0] if row else 0
        return actor, result

    @staticmethod
    def can_read(db, actor, scope, row):
        return scope['kind']=='personal' or row['author']==actor['id'] or scope['role']=='owner' or row['visibility']=='all' or bool(
            db.execute('SELECT 1 FROM memory_v2_audience WHERE record=? AND account=?', (row['id'],actor['id'])).fetchone())

    @staticmethod
    def can_edit(actor, scope, row):
        return scope['role']=='owner' or (scope['role']=='editor' and (row['author']==actor['id'] or row['edit_policy']=='editors'))

    @staticmethod
    def check_guard(before, after):
        if (before['id'], before['version'], before['member_version']) != (after['id'], after['version'], after['member_version']):
            raise SharedError('记忆内容或空间权限已变化，请重新预览或提问。', 409)

    def options(self, db, actor, scope, data):
        visibility = data.get('visibility', 'selected') if scope['kind']=='team' else 'selected'
        policy = data.get('edit_policy', 'author')
        viewers = strings(data.get('viewers', []), '可见成员编号', 100, 100)
        if visibility not in {'selected', 'all'} or policy not in {'author', 'editors'}:
            raise SharedError('查看或修改权限无效。')
        if set(viewers)-scope['members'] or (scope['kind']=='personal' and set(viewers)-{actor['id']}):
            raise SharedError('可见成员不属于当前范围。')
        return {'visibility': visibility, 'edit_policy': 'author' if scope['kind']=='personal' else policy,
                'viewers': sorted(set(viewers)) if visibility=='selected' else []}

    def save_plan(self, token, actor, scope, payload):
        identity = secrets.token_urlsafe(32)
        with self.lock:
            self.plans = {k:v for k,v in self.plans.items() if v['expires']>time.monotonic()}
            if len(self.plans)>=64:
                raise SharedError('待审阅操作过多，请稍后再试。', 429)
            self.plans[identity] = {'session': self.shared._token_hash(token), 'actor': actor['id'], 'scope': scope,
                                    'expires': time.monotonic()+600, **copy.deepcopy(payload)}
        return identity

    def preview(self, token, data):
        document = validate(data.get('document'))
        selection = data.get('selected_keys')
        if selection is not None:
            selection = set(strings(selection, '选中候选编号', 200, 100))
            if selection-set(r['key'] for r in document['records']):
                raise SharedError('选中的候选不存在。')
            document['records'] = [r for r in document['records'] if r['key'] in selection]
        if not document['records']:
            raise SharedError('请至少选择一条候选。')
        warnings, skipped = [], []
        with self.store.connect() as db:
            db.execute('BEGIN')
            actor, scope = self.scope(db, token, data, True)
            options = self.options(db, actor, scope, data)
            if scope['kind']=='team' and any(r['sensitivity']=='personal' for r in document['records']):
                raise SharedError('选中项含私人内容，请先存入个人记忆；团队发布请另行核对脱敏后的候选。')
            for table, name in [('entities','entities'), ('events','events'), ('sources','sources')]:
                # Only dependencies of selected records are persisted and compared.
                used = self.dependencies(document, name)
                document[name] = [x for x in document[name] if x['key'] in used]
                for item in document[name]:
                    row = db.execute('SELECT payload_json FROM memory_v2_'+table+' WHERE scope=? AND external_key=?', (scope['id'],item['key'])).fetchone()
                    if row and json.loads(row['payload_json']) != item:
                        raise SharedError('相同编号的'+{'entities':'人物／组织','events':'事件','sources':'来源'}[name]+'已有不同内容，请核对或使用新的版本编号。', 409)
            fresh = []
            for item in document['records']:
                row = db.execute('SELECT * FROM memory_v2_records WHERE scope=? AND external_key=?', (scope['id'],item['key'])).fetchone()
                if row:
                    if not self.can_read(db, actor, scope, row):
                        raise SharedError('候选编号不可用，请为新资料生成新的编号。', 409)
                    if row['content_hash']!=digest(item):
                        raise SharedError('同一记忆编号的内容已变化，请从记忆库预览修改，不会覆盖原记录。', 409)
                    skipped.append(item['key']); continue
                fresh.append(item)
                if any(s['kind']=='candidate' for s in document['sources'] if s['key'] in {e['source_key'] for e in item['evidence']}):
                    warnings.append(item['title']+'：依据是候选文件，尚未核对原始文章。')
                event = next((e for e in document['events'] if e['key']==item['event_key']), None)
                if event and event['time']['basis']=='inferred':
                    warnings.append(item['title']+'：事件时间含推定，请核对年份及依据。')
            document['records'] = fresh
            if not fresh:
                return {'duplicate_only': True, 'skipped': skipped, 'items': [], 'warnings': [], 'scope_name': scope['name']}
            # Similar titles/times are hints, never an automatic event merge.
            for event in document['events']:
                same = [r for r in db.execute('SELECT r.* FROM memory_v2_records r JOIN memory_v2_events e ON r.event_id=e.id WHERE e.scope=? AND e.title=? AND e.external_key<>?', (scope['id'],event['title'],event['key'])) if self.can_read(db,actor,scope,r)]
                if same:
                    warnings.append(event['title']+'：当前范围已有同名事件，发布前请核对是否应沿用原事件编号。')
        public = {'scope_name': scope['name'], 'scope_kind': scope['kind'], 'items': document['records'],
                  'events': document['events'], 'entities': document['entities'], 'options': options,
                  'skipped': skipped, 'warnings': warnings, 'expires_in_seconds': 600}
        public['token'] = self.save_plan(token, actor, scope, {'action':'import', 'document':document, 'options':options, 'warnings':warnings})
        return public

    @staticmethod
    def dependencies(document, name):
        if name == 'sources':
            return {e['source_key'] for r in document['records'] for e in r['evidence']}
        if name == 'entities':
            return {p for r in document['records'] for p in r['participants']} | {
                p for r in document['records'] if r['fact'] for p in (r['fact']['subject_key'],r['fact']['object_key']) if p}
        used = {r['event_key'] for r in document['records'] if r['event_key']}
        events = {e['key']:e for e in document['events']}
        for identity in list(used):
            while events[identity]['parent_key']:
                identity = events[identity]['parent_key']; used.add(identity)
        return used

    def confirm(self, token, data):
        if data.get('confirmed') is not True:
            raise SharedError('请先核对归属、内容及权限，再明确确认。')
        with self.lock:
            plan = self.plans.get(data.get('token'))
            if not plan or plan['expires']<=time.monotonic():
                raise SharedError('预览已失效，请重新预览。', 409)
            if plan['session']!=self.shared._token_hash(token):
                raise SharedError('预览不属于当前登录账号。', 403)
            if plan.get('warnings') and data.get('acknowledge_warnings') is not True:
                raise SharedError('请额外确认预览中列出的待核对问题。')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                actor, scope = self.scope(db, token, data, True)
                if actor['id']!=plan['actor']:
                    raise SharedError('账号与预览不一致。', 403)
                self.check_guard(plan['scope'], scope)
                db.execute('INSERT OR IGNORE INTO memory_v2_scopes(id,version) VALUES (?,0)',(scope['id'],))
                if plan['action']=='event_update':
                    result=self.apply_event_update(db,actor,scope,plan)
                elif plan['action']=='update':
                    result = self.apply_update(db, actor, scope, plan)
                else:
                    result = self.apply_import(db, actor, scope, plan)
                db.execute('INSERT INTO memory_v2_scopes(id,version) VALUES (?,1) ON CONFLICT(id) DO UPDATE SET version=version+1', (scope['id'],))
            del self.plans[data['token']]
        return result

    def apply_import(self, db, actor, scope, plan):
        doc, options = plan['document'], plan['options']
        mappings = {}
        for name in ('entities','events','sources'):
            mappings[name] = {}
            for item in doc[name]:
                row = db.execute('SELECT id,payload_json FROM memory_v2_'+name+' WHERE scope=? AND external_key=?', (scope['id'],item['key'])).fetchone()
                if row:
                    if json.loads(row['payload_json'])!=item:
                        raise SharedError('资料依赖已变化，请重新核对。', 409)
                    mappings[name][item['key']] = row['id']; continue
                identity = uuid.uuid4().hex; mappings[name][item['key']] = identity
                base = (identity,scope['id'],item['key'])
                if name == 'entities':
                    db.execute('INSERT INTO memory_v2_entities VALUES (?,?,?,?,?,?,?)', base+(item['kind'],item['name'],encoded(item['aliases']),encoded(item)))
                elif name == 'events':
                    db.execute('INSERT INTO memory_v2_events VALUES (?,?,?,?,?,?,?,?,?)', base+(item['kind'],item['title'],item['state'],encoded(item['time']),item['location'],encoded(item)))
                else:
                    db.execute('INSERT INTO memory_v2_sources VALUES (?,?,?,?,?,?,?,?,?)', base+(actor['id'],item['name'],item['kind'],item['sha256'],item['text'],encoded(item)))
        for item in doc['records']:
            identity = uuid.uuid4().hex
            db.execute('INSERT INTO memory_v2_records(id,scope,external_key,author,kind,title,summary,detail,event_id,verification,verification_reason,visibility,edit_policy,payload_json,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity,scope['id'],item['key'],actor['id'],item['type'],item['title'],item['summary'],item['detail'],
                        mappings['events'].get(item['event_key']),item['verification'],item['verification_reason'],options['visibility'],options['edit_policy'],encoded(item),digest(item)))
            db.executemany('INSERT INTO memory_v2_audience VALUES (?,?)', [(identity,x) for x in options['viewers']])
            db.executemany('INSERT INTO memory_v2_participants VALUES (?,?)', [(identity,mappings['entities'][x]) for x in item['participants']])
            f = item['fact']
            if f:
                db.execute('INSERT INTO memory_v2_facts VALUES (?,?,?,?,?,?,?,?,?)', (identity,mappings['entities'][f['subject_key']],mappings['entities'].get(f['object_key']),f['predicate'],encoded(f['value']),f['unit'],f['metric'],f['term'],encoded(f['effective'])))
            for e in item['evidence']:
                db.execute('INSERT INTO memory_v2_evidence VALUES (?,?,?,?,?)', (identity,mappings['sources'][e['source_key']],e['start'],e['end'],e['text']))
            self.history(db, identity, actor['id'], None, {'record':item,'options':options,'lifecycle':'active','version':1})
        return {'saved':True,'imported':len(doc['records'])}

    @staticmethod
    def history(db, identity, actor, before, after):
        db.execute('INSERT INTO memory_v2_history(record,actor,before_json,after_json) VALUES (?,?,?,?)',
                   (identity,actor,encoded(before) if before is not None else None,encoded(after)))

    def projection(self, db, actor, scope, row, excerpts=False):
        payload = json.loads(row['payload_json'])
        event = db.execute('SELECT payload_json FROM memory_v2_events WHERE id=?', (row['event_id'],)).fetchone() if row['event_id'] else None
        entities = list(db.execute('SELECT e.* FROM memory_v2_entities e JOIN memory_v2_participants p ON p.entity=e.id WHERE p.record=?',(row['id'],)))
        if payload['fact']:
            f = db.execute('SELECT e.external_key,e.name,e.kind FROM memory_v2_entities e JOIN memory_v2_facts f ON (e.id=f.subject OR e.id=f.object) WHERE f.record=?',(row['id'],))
            entities.extend(f)
        names = {x['external_key']:x['name'] for x in entities}
        kinds = {x['external_key']:x['kind'] for x in entities}
        result = {**payload,'id':row['id'],'version':row['version'],'lifecycle':row['lifecycle'],'scope_name':scope['name'],
                  'scope_kind':scope['kind'],'space_id':scope.get('space_id'),'visibility':row['visibility'],'edit_policy':row['edit_policy'],
                  'can_edit':self.can_edit(actor,scope,row),'entities':names,'entity_kinds':kinds,'event':json.loads(event[0]) if event else None,
                  'content':row['summary']+('\n'+row['detail'] if row['detail'] else ''),
                  'status':{'pending':'pending','confirmed':'valid','disputed':'pending'}[row['verification']],
                  'event_date':json.loads(event[0])['time']['start'] or '' if event else '', 'has_evidence':True,
                  'source_note':'新版结构化记忆', 'origin':'personal' if scope['kind']=='personal' else 'shared',
                  'scope_key':scope['id']}
        # Exact excerpts only, never source bodies or unrelated entity names.
        if excerpts:
            result['evidence'] = [dict(x) for x in db.execute('SELECT e.source AS source_id,e.start,e.end,e.quote AS text,s.name AS source_name,s.kind AS source_kind FROM memory_v2_evidence e JOIN memory_v2_sources s ON s.id=e.source WHERE e.record=?',(row['id'],))]
        else:
            result.pop('evidence',None)
        return result

    def listing(self, token, data):
        with self.store.connect() as db:
            db.execute('BEGIN'); actor,scope = self.scope(db,token,data)
            items=[]
            for row in db.execute('SELECT * FROM memory_v2_records WHERE scope=? ORDER BY created,id',(scope['id'],)):
                if not self.can_read(db,actor,scope,row):continue
                if row['lifecycle']=='archived' and data.get('include_archived') is not True:continue
                items.append(self.projection(db,actor,scope,row))
            return {'scope_name':scope['name'],'version':scope['version'],'records':items}

    def evidence(self, token, data):
        with self.store.connect() as db:
            db.execute('BEGIN'); actor,scope=self.scope(db,token,data)
            row=db.execute('SELECT * FROM memory_v2_records WHERE scope=? AND id=?',(scope['id'],data.get('id'))).fetchone()
            if not row or not self.can_read(db,actor,scope,row):raise SharedError('未找到可查看的记忆。',404)
            if data.get('expected_version') is not None and data['expected_version']!=row['version']:raise SharedError('记忆已变化，请重新查询。',409)
            quotes=self.projection(db,actor,scope,row,True)['evidence']
            return {'available':True,'quotes':[{**q,'location':q['source_name']+' · 原文字符 '+str(q['start'])+'–'+str(q['end'])} for q in quotes]}

    def update_preview(self, token, data):
        changes=fields(data.get('changes'),{'title','summary','detail','verification','verification_reason','lifecycle','tags','emotions','fact'},'修改内容')
        with self.store.connect() as db:
            db.execute('BEGIN');actor,scope=self.scope(db,token,data,True)
            row=db.execute('SELECT * FROM memory_v2_records WHERE scope=? AND id=?',(scope['id'],data.get('id'))).fetchone()
            if not row or not self.can_read(db,actor,scope,row):raise SharedError('未找到可修改的记忆。',404)
            if not self.can_edit(actor,scope,row):raise SharedError('只有作者和获授权的编辑成员／管理员可修改。',403)
            if type(data.get('expected_version')) is not int or row['version']!=data['expected_version']:raise SharedError('版本已变化，请重新加载。',409)
            item=json.loads(row['payload_json']); before=copy.deepcopy(item)
            for k,limit in [('title',160),('summary',1200),('detail',4000),('verification_reason',500)]:
                if k in changes:item[k]=text(changes[k],k,limit,k in {'detail','verification_reason'})
            for k in ('tags','emotions'):
                if k in changes:item[k]=strings(changes[k],k)
            if 'fact' in changes:
                if item['type']!='fact':raise SharedError('只有事实记忆可以修改结构化事实。')
                from yiban.memory.v2_schema import fact
                keys=set(item['participants'])|{item['fact']['subject_key'],item['fact']['object_key']};keys.discard(None)
                item['fact']=fact(changes['fact'],keys)
            if 'verification' in changes:item['verification']=changes['verification']
            if item['verification'] not in {'pending','confirmed','disputed'}:raise SharedError('核实状态无效。')
            if any(item[k]!=before[k] for k in ('title','summary','detail','fact')):
                item['verification']='pending';item['verification_reason']='正文已修改，请重新核实与原文是否一致。'
            if item['verification']!='pending' and not item['verification_reason']:raise SharedError('请填写核实说明。')
            lifecycle=changes.get('lifecycle',row['lifecycle'])
            if lifecycle not in {'active','archived'}:raise SharedError('生命周期无效。')
            plan={'action':'update','id':row['id'],'expected_version':row['version'],'before':before,'after':item,'lifecycle':lifecycle,'warnings':[]}
        return {'token':self.save_plan(token,actor,scope,plan),'scope_name':scope['name'],'before':before,'after':item,'lifecycle':lifecycle,'warnings':[]}

    def apply_update(self, db, actor, scope, plan):
        row=db.execute('SELECT * FROM memory_v2_records WHERE id=? AND scope=?',(plan['id'],scope['id'])).fetchone()
        if not row or row['version']!=plan['expected_version'] or not self.can_edit(actor,scope,row):raise SharedError('权限或版本已变化，请重新核对。',409)
        item=plan['after']
        db.execute('UPDATE memory_v2_records SET title=?,summary=?,detail=?,verification=?,verification_reason=?,payload_json=?,content_hash=?,lifecycle=?,version=version+1,updated=CURRENT_TIMESTAMP WHERE id=?',
                   (item['title'],item['summary'],item['detail'],item['verification'],item['verification_reason'],encoded(item),digest(item),plan['lifecycle'],row['id']))
        if item['fact']:
            f=item['fact'];ids={x['external_key']:x['id'] for x in db.execute('SELECT id,external_key FROM memory_v2_entities WHERE scope=?',(scope['id'],))}
            db.execute('UPDATE memory_v2_facts SET subject=?,object=?,predicate=?,value_json=?,unit=?,metric=?,term=?,effective_json=? WHERE record=?',(ids[f['subject_key']],ids.get(f['object_key']),f['predicate'],encoded(f['value']),f['unit'],f['metric'],f['term'],encoded(f['effective']),row['id']))
        self.history(db,row['id'],actor['id'],{'record':plan['before'],'lifecycle':row['lifecycle'],'version':row['version']},
                     {'record':item,'lifecycle':plan['lifecycle'],'version':row['version']+1})
        return {'saved':True,'id':row['id'],'version':row['version']+1}

    def event_preview(self, token, data):
        from yiban.memory.v2_schema import temporal
        changes=fields(data.get('changes'),{'title','kind','time','state','location'},'事件修改')
        with self.store.connect() as db:
            db.execute('BEGIN');actor,scope=self.scope(db,token,data,True)
            row=db.execute('SELECT * FROM memory_v2_records WHERE id=? AND scope=?',(data.get('id'),scope['id'])).fetchone()
            if not row or not self.can_read(db,actor,scope,row):raise SharedError('未找到可修改的事件记忆。',404)
            if not row['event_id']:raise SharedError('这条记忆没有关联事件。')
            if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:raise SharedError('记忆已变化，请刷新。',409)
            affected=list(db.execute('SELECT * FROM memory_v2_records WHERE event_id=? AND scope=?',(row['event_id'],scope['id'])))
            if any(not self.can_edit(actor,scope,r) for r in affected):raise SharedError('该事件关联其他作者的记忆，请由有权修改全部关联记录的管理员核对。',403)
            before=json.loads(db.execute('SELECT payload_json FROM memory_v2_events WHERE id=?',(row['event_id'],)).fetchone()[0]);after=copy.deepcopy(before)
            for name,limit in [('title',160),('kind',80),('location',200)]:
                if name in changes:after[name]=text(changes[name],name,limit,name=='location')
            if 'time' in changes:after['time']=temporal(changes['time'])
            if 'state' in changes:
                if not isinstance(changes['state'],str) or changes['state'] not in {'held','planned','cancelled','unknown'}:raise SharedError('事件状态无效。')
                after['state']=changes['state']
            items=[{'id':r['id'],'version':r['version'],'title':r['title']} for r in affected]
            plan={'action':'event_update','event_id':row['event_id'],'before':before,'after':after,'items':items,'warnings':['事件变更会影响以上全部关联记忆；这些记录将回到待核实，原文与历史继续保留。']}
        return {'token':self.save_plan(token,actor,scope,plan),'scope_name':scope['name'],'before':before,'after':after,'items':items,'warnings':plan['warnings']}

    def apply_event_update(self, db, actor, scope, plan):
        event=db.execute('SELECT payload_json FROM memory_v2_events WHERE id=? AND scope=?',(plan['event_id'],scope['id'])).fetchone()
        if not event or json.loads(event[0])!=plan['before']:raise SharedError('事件已变化，请重新预览。',409)
        after=plan['after']
        db.execute('UPDATE memory_v2_events SET title=?,kind=?,time_json=?,state=?,location=?,payload_json=? WHERE id=?',
                   (after['title'],after['kind'],encoded(after['time']),after['state'],after['location'],encoded(after),plan['event_id']))
        for item in plan['items']:
            row=db.execute('SELECT * FROM memory_v2_records WHERE id=? AND scope=?',(item['id'],scope['id'])).fetchone()
            if not row or row['version']!=item['version'] or not self.can_edit(actor,scope,row):raise SharedError('关联记忆版本或权限已变化，请重新预览。',409)
            before=json.loads(row['payload_json']);record=copy.deepcopy(before);record['verification']='pending';record['verification_reason']='关联事件已修订，需要重新核对。'
            db.execute('UPDATE memory_v2_records SET verification=?,verification_reason=?,payload_json=?,content_hash=?,version=version+1,updated=CURRENT_TIMESTAMP WHERE id=?',
                       ('pending',record['verification_reason'],encoded(record),digest(record),row['id']))
            self.history(db,row['id'],actor['id'],{'record':before,'event':plan['before'],'version':row['version']},
                         {'record':record,'event':after,'version':row['version']+1})
        return {'saved':True,'updated_records':len(plan['items'])}

    def read_trace(self, token, data, path):
        with self.store.connect() as db:
            db.execute('BEGIN');actor,scope=self.scope(db,token,data)
            row=db.execute('SELECT * FROM memory_v2_records WHERE scope=? AND id=?',(scope['id'],data.get('id'))).fetchone()
            if not row or not self.can_read(db,actor,scope,row):raise SharedError('未找到可查看的记忆。',404)
            if path=='history':
                return {'history':[dict(r) for r in db.execute('SELECT id,actor,before_json,after_json,created FROM memory_v2_history WHERE record=? ORDER BY id DESC LIMIT 50',(row['id'],))]}
            if actor['id']!=row['author'] and scope['role']!='owner':raise SharedError('全文仅作者和当前范围管理员可查看。',403)
            return {'sources':[{'name':r['name'],'text':r['text'],'kind':r['kind'],'sha256':r['sha256']} for r in db.execute('SELECT s.* FROM memory_v2_sources s JOIN memory_v2_evidence e ON e.source=s.id WHERE e.record=? GROUP BY s.id',(row['id'],))]}

    def manual_candidate(self, token, data):
        """A literal user note, not automatic event/fact extraction or a write."""
        import hashlib
        data=fields(data,{'scope_kind','space_id','title','content'},'文字记忆')
        with self.store.connect() as db:
            self.scope(db,token,data,write=True)
        content=text(data.get('content'),'记忆内容',1200).strip()
        title=text(data.get('title',''),'标题',160,True).strip() or content.splitlines()[0][:32]
        sha=hashlib.sha256(content.encode()).hexdigest()
        source_key='manual:'+sha[:24]
        document={'schema':'yiban.memory-candidates.v2','batch_name':'手动文字记忆',
            'entities':[],'events':[],
            'sources':[{'key':source_key,'kind':'manual','name':'手动录入 · '+title[:120],
                        'text':content,'sha256':sha}],
            'records':[{'key':'note:'+sha[:24],'type':'quote','title':title,'summary':content,
                'detail':'','participants':[],'event_key':None,'fact':None,
                'verification':'pending','verification_reason':'手动整理的文字记忆，尚待本人核对。',
                'sensitivity':'personal' if data.get('scope_kind')=='personal' else 'normal',
                'evidence':[{'source_key':source_key,'start':0,'end':len(content),'text':content}]}]}
        return {'document':validate(document),'notice':'已生成待核验候选，尚未入库。'}

    def dispatch(self, path, token, data, cancel=None, context=''):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        # Every route, including conversion and discarded previews, requires a session.
        self.shared.me(token)
        for name in ('token','id','space_id'):
            if name in data and data[name] is not None and (not isinstance(data[name],str) or len(data[name])>200):raise SharedError('操作编号无效。')
        if path.startswith('md/'):
            from yiban.memory.md_extraction import MarkdownExtraction
            with self.lock:
                if not hasattr(self,'md_extraction'):self.md_extraction=MarkdownExtraction(self)
            return self.md_extraction.dispatch(path[3:],token,data,cancel)
        if path=='draft':return self.manual_candidate(token,data)
        if path=='convert':
            from yiban.memory.v2_convert import convert
            return convert(data.get('document'),data.get('original_text'))
        if path=='validate':return {'document':validate(data.get('document')),'notice':'候选尚未入库。'}
        if path=='preview':return self.preview(token,data)
        if path=='confirm':return self.confirm(token,data)
        if path=='list':return self.listing(token,data)
        if path=='evidence':return self.evidence(token,data)
        if path in {'history','source'}:return self.read_trace(token,data,path)
        if path=='update/preview':return self.update_preview(token,data)
        if path=='event/preview':return self.event_preview(token,data)
        if path=='reset':
            self.dialogue.clear(token,data.get('conversation_id'))
            return {'reset':True}
        if path=='ask':
            from yiban.memory.v2_query import answer
            return answer(self,token,data,cancel,context)
        if path=='discard':
            with self.lock:
                plan=self.plans.get(data.get('token'))
                if plan and plan['session']==self.shared._token_hash(token):self.plans.pop(data['token'],None)
            return {'discarded':True}
        raise SharedError('未知的新版记忆操作。',404)
