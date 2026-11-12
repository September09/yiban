"""Reviewed recovery of shared records. Deleted material is never searched or sent to a model."""
import copy
import hashlib
import json
import secrets
import threading
import time
import uuid

from yiban.memory.shared_memory import SharedError
from yiban.memory.memory_review import possible_conflicts


class SharedRecovery:
    OPERATIONS = {'trash', 'trash_restore', 'history_restore', 'undo_replacement'}
    RECORD_FIELDS = ('id', 'space', 'author', 'editor', 'content', 'visibility', 'edit_policy',
                     'event_date', 'source_note', 'version', 'created', 'updated', 'status', 'status_reason')

    def __init__(self, shared, clock=time.monotonic):
        self.shared, self.clock = shared, clock
        self.plans, self.lock = {}, threading.RLock()
        with shared.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS shared_trash(
                    id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                    memory TEXT NOT NULL UNIQUE, author TEXT NOT NULL REFERENCES shared_accounts(id),
                    deleted_by TEXT NOT NULL REFERENCES shared_accounts(id), snapshot TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS shared_trash_space ON shared_trash(space,created);
                CREATE TABLE IF NOT EXISTS shared_recovery_events(
                    id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                    actor TEXT NOT NULL REFERENCES shared_accounts(id), operation TEXT NOT NULL, reason TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL, metadata TEXT NOT NULL,
                    receipt_hash TEXT UNIQUE, session_hash TEXT, result_json TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS shared_recovery_space ON shared_recovery_events(space,created);
                CREATE TABLE IF NOT EXISTS shared_recovery_targets(
                    event TEXT NOT NULL REFERENCES shared_recovery_events(id) ON DELETE CASCADE,
                    memory TEXT NOT NULL, PRIMARY KEY(event,memory));
                CREATE INDEX IF NOT EXISTS shared_recovery_memory ON shared_recovery_targets(memory);
            ''')

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    @staticmethod
    def check_version(item, value):
        if type(value) is not int or value != item['version']:
            raise SharedError('记忆版本已变化，请刷新后重新预览。', 409)

    def eligible(self, actor, space, item):
        s = self.shared
        if item['space'] != space['id'] or not s._visible(actor, space, item) or not s._configurable(actor, space, item):
            raise SharedError('只有仍在空间中的作者（有编辑角色）或管理员可恢复或纠正。', 403)

    def trash_item(self, db, actor, space, identity):
        row = db.execute('SELECT * FROM shared_trash WHERE id=? AND space=?', (identity, space['id'])).fetchone()
        if not row:
            raise SharedError('回收站记录不存在或已恢复。', 409)
        snapshot = json.loads(row['snapshot'])
        self.eligible(actor, space, snapshot['record'])
        return dict(row), snapshot

    def snapshot(self, db, item):
        evidence = db.execute('SELECT * FROM shared_evidence WHERE memory=?', (item['id'],)).fetchone()
        return {'record': item, 'history': [dict(row) for row in db.execute(
            'SELECT * FROM shared_history WHERE memory=? ORDER BY id', (item['id'],))],
            'evidence': dict(evidence) if evidence else None}

    def undo_pair(self, db, actor, space, current):
        self.eligible(actor, space, current)
        edge = db.execute('SELECT * FROM shared_replacements WHERE new_id=?', (current['id'],)).fetchone()
        if not edge or self.shared.replacements.flags(db, current['id'])[1]:
            raise SharedError('只能纠正最近一次替代，请先处理替代链的当前安排。', 409)
        old = self.shared._record(db, edge['old_id'])
        self.eligible(actor, space, old)
        if old['status'] != 'outdated':
            raise SharedError('旧安排状态已变化，请重新核对。', 409)
        return old, current, dict(edge)

    def can_undo(self, db, actor, space, current):
        try:
            self.undo_pair(db, actor, space, current)
            return True
        except SharedError:
            return False

    def list(self, token, data):
        s = self.shared
        with s.store.connect() as db:
            db.execute('BEGIN'); actor = s._actor(db, token); space = s._member(db, actor['id'], data.get('space_id'))
            result = []
            for row in db.execute('SELECT t.*,a.name AS deleted_name FROM shared_trash t JOIN shared_accounts a ON a.id=t.deleted_by WHERE space=? ORDER BY t.created DESC,t.rowid DESC', (space['id'],)):
                item = json.loads(row['snapshot'])['record']
                try:self.eligible(actor, space, item)
                except SharedError:continue
                result.append({'trash_id': row['id'], 'id': item['id'], 'content': item['content'], 'status': item['status'],
                               'event_date': item['event_date'], 'created': row['created'], 'deleted_name': row['deleted_name']})
            return {'items': result, 'total': len(result)}

    def public_record(self, db, space, item):
        roster = {r['id']:r['name'] for r in db.execute('SELECT a.id,a.name FROM shared_members m JOIN shared_accounts a ON a.id=m.account WHERE m.space=?', (space['id'],))}
        fields = ('id', 'content', 'status', 'status_reason', 'event_date', 'source_note', 'version', 'visibility', 'edit_policy', 'author_name')
        result = {key:item.get(key) for key in fields}
        audience = set(roster) if item['visibility'] == 'all' else (set(item['viewers']) | {item['author'], space['owner']}) & set(roster)
        result['audience'] = sorted(roster[x] for x in audience)
        return result

    def build(self, db, actor, space, data):
        s = self.shared; operation = data.get('operation')
        if not isinstance(operation, str) or operation not in self.OPERATIONS:
            raise SharedError('请选择移入回收站、回收站恢复、历史版本恢复或替代纠正。')
        reason = s._text(data.get('reason'), '操作说明', 200)
        snapshot, metadata = None, {}
        if operation == 'trash_restore':
            row, snapshot = self.trash_item(db, actor, space, data.get('trash_id'))
            item = snapshot['record']; metadata['trash_id'] = row['id']
            if db.execute('SELECT 1 FROM shared_memories WHERE id=?', (item['id'],)).fetchone():
                raise SharedError('原记忆编号已存在，不能覆盖。', 409)
            before = [item]
            members = {r[0] for r in db.execute('SELECT account FROM shared_members WHERE space=?', (space['id'],))}
            removed = set(item['viewers']) - members
            metadata['removed_viewers'] = [r[0] for r in db.execute('SELECT name FROM shared_accounts WHERE id IN ('+','.join('?' for _ in removed)+')', sorted(removed))] if removed else []
            after = [copy.deepcopy(item)]
            after[0].update(viewers=sorted(set(item['viewers']) & members), status='pending', status_reason='从回收站恢复，需要重新核实。')
            after[0].update(s._payload(db, actor, space, after[0], item))
            if db.execute('SELECT COUNT(*) FROM shared_memories WHERE space=?', (space['id'],)).fetchone()[0] >= 200:
                raise SharedError('空间记忆已达200条，暂时无法恢复。')
        else:
            item = s._record(db, data.get('id')); self.eligible(actor, space, item)
            self.check_version(item, data.get('version')); before = [item]
            if operation == 'trash':
                if s.replacements.flags(db, item['id'])[0]:
                    raise SharedError('替代链中的记录需保留追溯，不能单独移入回收站。', 409)
                snapshot = self.snapshot(db, item); after = []
                size = len(self.encode(snapshot).encode())
                used = db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(snapshot AS BLOB))),0) FROM shared_trash WHERE space=?', (space['id'],)).fetchone()
                if used[0] >= 100 or used[1] + size > 24_000_000:
                    raise SharedError('回收站达到100条或24 MB上限，未删除任何内容。')
                metadata['trash_id'] = uuid.uuid4().hex
            elif operation == 'history_restore':
                if s.replacements.flags(db, item['id'])[1]:
                    raise SharedError('已被替代的旧记录只读，请先纠正最近一次替代。', 409)
                identity = data.get('history_id')
                if type(identity) is not int or identity < 1:raise SharedError('请选择有效的历史版本。')
                row = db.execute('SELECT after_json,changed FROM shared_history WHERE id=? AND memory=?', (identity, item['id'])).fetchone()
                if not row:raise SharedError('历史版本不存在或不属于这条记忆。', 403)
                historic = json.loads(row['after_json']); after = [copy.deepcopy(item)]
                if all(item.get(k, '') == historic.get(k, '') for k in ('content', 'event_date', 'source_note')):
                    raise SharedError('当前正文、日期和来源说明与所选历史相同，无需恢复。')
                after[0].update({k:historic.get(k, '') for k in ('content', 'event_date', 'source_note')})
                after[0].update(status='pending', status_reason='已恢复历史内容，需要重新核实。')
                after[0].update(s._payload(db, actor, space, after[0], item))
                metadata.update(history_id=identity, history_date=row['changed'])
            else:
                old, current, edge = self.undo_pair(db, actor, space, item)
                before = [old, current]; after = copy.deepcopy(before)
                after[0].update(status='pending', status_reason=('替代已纠正，旧安排恢复为待核实。'+reason)[:200])
                after[1].update(status='outdated', status_reason=('此替代已撤销，新记录保留追溯。'+reason)[:200])
                metadata['edge'] = edge
        for target in after:
            target.update(version=target['version']+1, editor=actor['id'], editor_name=actor['name'])
        conflicts = []
        if operation != 'trash':
            exclude = {x['id'] for x in before}
            active = [x for x in s._items(db, actor, space) if x['status'] != 'outdated' and x['id'] not in exclude]
            conflicts = possible_conflicts(after[0]['content'], active)
        return {'operation': operation, 'reason': reason, 'before': before, 'after': after,
                'metadata': metadata, 'snapshot': snapshot, 'conflicts': conflicts}

    def preview(self, token, data):
        s = self.shared
        with s.store.connect() as db:
            db.execute('BEGIN'); actor = s._actor(db, token); space = s._member(db, actor['id'], data.get('space_id'))
            plan = self.build(db, actor, space, data)
            before = [self.public_record(db, space, item) for item in plan['before']]
            after = [self.public_record(db, space, item) for item in plan['after']]
        now = self.clock(); key = secrets.token_urlsafe(32)
        plan.update(actor=actor['id'], space=space['id'], space_version=space['version'],
                    session=s._token_hash(token), expires=now+600, request={k:copy.deepcopy(data[k]) for k in ('space_id','operation','reason','id','version','trash_id','history_id') if k in data})
        with self.lock:
            self.plans = {k:p for k,p in self.plans.items() if p['expires'] > now and p['session'] != plan['session']}
            if len(self.plans) >= 4:self.plans.pop(next(iter(self.plans)))
            self.plans[key] = plan
        snapshot = plan['snapshot']
        source = snapshot['evidence'] if snapshot else None
        return {'token': key, 'operation': plan['operation'], 'reason': plan['reason'], 'space_id': space['id'],
                'space_name': space['name'], 'actor_name': actor['name'], 'before': before, 'after': after,
                'conflicts': plan['conflicts'], 'removed_viewers': plan['metadata'].get('removed_viewers', []),
                'history_count': len(snapshot['history']) if snapshot else 0,
                'source': {'name': source['source_name'], 'text': source['source_text']} if source else None,
                'expires_in_seconds': 600}

    def save_event(self, db, actor, space, plan, result, key=None):
        identity = uuid.uuid4().hex
        db.execute('INSERT INTO shared_recovery_events(id,space,actor,operation,reason,before_json,after_json,metadata,receipt_hash,session_hash,result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (identity, space['id'], actor['id'], plan['operation'], plan['reason'], self.encode(plan['before']),
                    self.encode(plan['after']), self.encode(plan['metadata']), hashlib.sha256(key.encode()).hexdigest() if key else None,
                    plan.get('session'), self.encode(result)))
        db.executemany('INSERT INTO shared_recovery_targets VALUES (?,?)', [(identity, item) for item in {m['id'] for m in plan['before']}])

    def apply(self, db, actor, space, plan, key=None):
        s = self.shared; operation = plan['operation']; before = plan['before']; after = plan['after']
        if operation == 'trash':
            db.execute('INSERT INTO shared_trash(id,space,memory,author,deleted_by,snapshot) VALUES (?,?,?,?,?,?)',
                       (plan['metadata']['trash_id'], space['id'], before[0]['id'], before[0]['author'], actor['id'], self.encode(plan['snapshot'])))
            db.execute('DELETE FROM shared_memories WHERE id=?', (before[0]['id'],))
        elif operation == 'trash_restore':
            item = after[0]; snapshot = plan['snapshot']; columns = self.RECORD_FIELDS
            db.execute('INSERT INTO shared_memories('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', [item[k] for k in columns])
            db.executemany('INSERT INTO shared_audience VALUES (?,?)', [(item['id'], x) for x in item['viewers']])
            for h in snapshot['history']:
                db.execute('INSERT INTO shared_history(memory,actor,before_json,after_json,changed) VALUES (?,?,?,?,?)', (h['memory'],h['actor'],h['before_json'],h['after_json'],h['changed']))
            if snapshot['evidence']:
                e=snapshot['evidence']; columns=('memory','source_name','source_text','source_hash','quotes_json','captured_content','created')
                db.execute('INSERT INTO shared_evidence VALUES (?,?,?,?,?,?,?)', [e[k] for k in columns])
            db.execute('DELETE FROM shared_trash WHERE id=?', (plan['metadata']['trash_id'],))
        else:
            if operation == 'undo_replacement':
                db.execute('DELETE FROM shared_replacements WHERE new_id=?', (before[-1]['id'],))
            for item in after:
                db.execute('UPDATE shared_memories SET content=?,event_date=?,source_note=?,status=?,status_reason=?,editor=?,version=?,updated=CURRENT_TIMESTAMP WHERE id=?',
                           (item['content'],item['event_date'],item['source_note'],item['status'],item['status_reason'],actor['id'],item['version'],item['id']))
        for index, item in enumerate(after):
            if operation == 'trash_restore':db.execute('UPDATE shared_memories SET updated=CURRENT_TIMESTAMP WHERE id=?', (item['id'],))
            actual = s._record(db, item['id'])
            actual['recovery'] = {'operation': operation, 'reason': plan['reason']}
            db.execute('INSERT INTO shared_history(memory,actor,before_json,after_json) VALUES (?,?,?,?)',
                       (item['id'],actor['id'],self.encode(before[index]),self.encode(actual)))
            after[index] = actual
        result = {'applied':True, 'operation':operation, 'ids':[item['id'] for item in before]}
        self.save_event(db, actor, space, plan, result, key)
        db.execute('UPDATE shared_spaces SET version=version+1 WHERE id=?', (space['id'],))
        return result

    def confirm(self, token, data):
        if data.get('confirmed') is not True or data.get('scope_confirmed') is not True:
            raise SharedError('请核对前后内容、归属、可见范围和状态变化，再明确确认。')
        key = data.get('token')
        if not isinstance(key,str) or not 20 <= len(key) <= 128:raise SharedError('缺少有效的恢复预览。')
        s=self.shared
        with self.lock, s.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=s._actor(db,token);space=s._member(db,actor['id'],data.get('space_id'))
            receipt=db.execute('SELECT * FROM shared_recovery_events WHERE receipt_hash=?', (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
            if receipt:
                if receipt['actor']!=actor['id'] or receipt['space']!=space['id'] or receipt['session_hash']!=s._token_hash(token):
                    raise SharedError('操作记录不属于当前登录会话或空间。',403)
                return {**json.loads(receipt['result_json']), 'already_applied':True}
            plan=self.plans.get(key)
            if not plan or plan['expires']<=self.clock():
                self.plans.pop(key,None);raise SharedError('预览已过期、取消或未找到，请重新预览。',409)
            if plan['actor']!=actor['id'] or plan['space']!=space['id'] or plan['session']!=s._token_hash(token):
                raise SharedError('预览不属于当前登录会话或空间。',403)
            if space['version']!=plan['space_version']:raise SharedError('资料或成员权限已变化，请刷新后重新预览。',409)
            if plan['conflicts'] and data.get('acknowledge_conflicts') is not True:raise SharedError('请额外核对可能冲突的资料。')
            # Recheck capacity, authorship and the full snapshot while holding the write transaction.
            current=self.build(db,actor,space,plan['request'])
            if any(current[k]!=plan[k] for k in ('before','after','snapshot')):raise SharedError('资料已变化，请重新预览。',409)
            result=self.apply(db,actor,space,copy.deepcopy(plan),key)
        with self.lock:self.plans.pop(key,None)
        self.invalidate(space['id'],result['ids'])
        return result

    def invalidate(self, space, ids):
        if self.shared.retriever:
            for identity in ids:
                try:self.shared.retriever.invalidate('shared:'+space,identity)
                except Exception:pass

    def delete(self, token, data):
        """Compatibility for the old confirmed delete route; it now always archives first."""
        if data.get('confirmed') is not True:raise SharedError('请确认将共同记忆移入回收站。')
        s=self.shared
        with s.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=s._actor(db,token);space=s._member(db,actor['id'],data.get('space_id'))
            plan=self.build(db,actor,space,{**data,'operation':'trash','reason':'用户确认移入回收站。'})
            self.apply(db,actor,space,plan)
        self.invalidate(space['id'],[data.get('id')])
        return {'deleted':True,'trashed':True}

    def events(self, token, data):
        s=self.shared
        with s.store.connect() as db:
            db.execute('BEGIN');actor=s._actor(db,token);space=s._member(db,actor['id'],data.get('space_id'))
            events=[]
            for row in db.execute('SELECT e.*,a.name AS actor_name FROM shared_recovery_events e JOIN shared_accounts a ON a.id=e.actor WHERE e.space=? ORDER BY e.created DESC,e.rowid DESC LIMIT 500',(space['id'],)):
                before=json.loads(row['before_json']);allowed=True
                for record in before:
                    current=db.execute('SELECT id FROM shared_memories WHERE id=? AND space=?',(record['id'],space['id'])).fetchone()
                    if current:item=s._record(db,current['id'])
                    else:
                        removed=db.execute('SELECT snapshot FROM shared_trash WHERE memory=? AND space=?',(record['id'],space['id'])).fetchone()
                        if not removed:allowed=False;break
                        item=json.loads(removed[0])['record']
                    try:self.eligible(actor,space,item)
                    except SharedError:allowed=False;break
                if not allowed:continue
                events.append({'operation':row['operation'],'reason':row['reason'],'actor_name':row['actor_name'],'created':row['created'],
                               'before':[self.public_record(db,space,x) for x in before],
                               'after':[self.public_record(db,space,x) for x in json.loads(row['after_json'])]})
                if len(events)>=100:break
            return {'events':events}

    def dispatch(self, action, token, data):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        if action not in {'list','events','preview','confirm','cancel'}:raise SharedError('未知恢复操作。',404)
        self.shared.me(token)
        if action=='cancel':
            key=data.get('token')
            if isinstance(key,str):
                with self.lock:
                    plan=self.plans.get(key)
                    if plan and plan['session']==self.shared._token_hash(token):self.plans.pop(key,None)
            return {'cancelled':True}
        return getattr(self,action)(token,data)
