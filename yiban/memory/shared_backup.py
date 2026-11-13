"""Portable shared-space archives, restored atomically into a new reviewed space."""
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import secrets
import threading
import time
import uuid

from yiban.memory.shared_memory import SharedError

FORMAT = 'yiban-shared-space'
MAX_BYTES = 24_000_000
FIELDS = ('id', 'author', 'editor', 'content', 'visibility', 'edit_policy', 'event_date',
          'source_note', 'status', 'status_reason', 'version', 'created', 'updated', 'viewers')


class SharedBackup:
    def __init__(self, shared, clock=time.time):
        self.shared = shared
        self.clock = clock
        self.plans = {}
        self.lock = threading.Lock()
        with shared.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS shared_restores(
                id TEXT PRIMARY KEY, actor TEXT NOT NULL REFERENCES shared_accounts(id),
                space TEXT NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                signature TEXT NOT NULL, plan_hash TEXT NOT NULL UNIQUE, session_hash TEXT NOT NULL,
                source_name TEXT NOT NULL, account_mapping TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(actor,signature))''')

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @classmethod
    def digest(cls, value):
        return hashlib.sha256(cls.encode(value).encode()).hexdigest()

    @staticmethod
    def text(value, label, maximum=1200, empty=False):
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
            raise SharedError(label + '格式无效或超出长度上限。')
        return value

    @classmethod
    def timestamp(cls, value):
        value = cls.text(value, '备份时间', 40)
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise SharedError('备份时间格式无效。') from None
        return value

    @classmethod
    def record(cls, value, accounts, identity=None):
        if not isinstance(value, dict):
            raise SharedError('记忆记录格式无效。')
        record = {key: value.get(key) for key in FIELDS}
        record['id'] = cls.text(record['id'], '记忆编号', 128)
        if identity and record['id'] != identity:
            raise SharedError('修改历史指向了其他记忆。')
        if any(not isinstance(record[k], str) or record[k] not in accounts for k in ('author', 'editor')):
            raise SharedError('记忆作者或修改者不在账号映射中。')
        record['content'] = cls.text(record['content'], '记忆正文')
        if record['visibility'] not in ('all', 'selected') or record['edit_policy'] not in ('author', 'editors'):
            raise SharedError('备份可见范围或修改权限无效。')
        viewers = record['viewers']
        if not isinstance(viewers, list) or len(viewers) > 100 or any(not isinstance(x, str) or x not in accounts for x in viewers):
            raise SharedError('备份可见账号无效。')
        record['viewers'] = sorted(set(viewers)) if record['visibility'] == 'selected' else []
        record['event_date'] = cls.text(record['event_date'] or '', '事件日期', 10, True)
        if record['event_date']:
            try:
                if date.fromisoformat(record['event_date']).isoformat() != record['event_date']:
                    raise ValueError()
            except ValueError:
                raise SharedError('备份事件日期无效。') from None
        record['source_note'] = cls.text(record['source_note'] or '', '来源说明', 200, True)
        record['status'] = record['status'] or 'pending'
        if record['status'] not in ('pending', 'valid', 'outdated'):
            raise SharedError('备份记忆状态无效。')
        record['status_reason'] = cls.text(record['status_reason'] or '', '状态说明', 200, True)
        if type(record['version']) is not int or not 1 <= record['version'] <= 1_000_000_000:
            raise SharedError('备份记忆版本无效。')
        record['created'] = cls.timestamp(record['created'])
        record['updated'] = cls.timestamp(record['updated'])
        recovery = value.get('recovery')
        if recovery is not None:
            if not isinstance(recovery, dict) or recovery.get('operation') not in ('trash_restore','history_restore','undo_replacement'):
                raise SharedError('历史恢复操作说明无效。')
            record['recovery'] = {'operation': recovery['operation'], 'reason': cls.text(recovery.get('reason'), '恢复说明', 200)}
        return record

    @staticmethod
    def rows(document, key, maximum):
        values = document.get(key)
        if not isinstance(values, list) or len(values) > maximum or any(not isinstance(x, dict) for x in values):
            raise SharedError('备份中的' + key + '格式无效或超出数量上限。')
        return values

    def validate(self, document):
        if not isinstance(document, dict) or document.get('format') != FORMAT or type(document.get('version')) is not int or document['version'] != 1:
            raise SharedError('请选择忆伴共同记忆 v1 备份文件。')
        if len(self.encode(document).encode()) > MAX_BYTES:
            raise SharedError('共同记忆备份最多24 MB，未进行截断。')
        space = document.get('space')
        if not isinstance(space, dict) or space.get('kind') not in ('family', 'team'):
            raise SharedError('备份空间信息无效。')
        space = {key: space.get(key) for key in ('id', 'name', 'kind', 'owner', 'created')}
        space['id'] = self.text(space['id'], '空间编号', 128)
        space['name'] = self.text(space['name'], '空间名称', 60)
        space['created'] = self.timestamp(space['created'])
        accounts = {}
        for account in self.rows(document, 'accounts', 100):
            identity = self.text(account.get('id'), '账号编号', 128)
            if identity in accounts:
                raise SharedError('备份账号编号重复。')
            accounts[identity] = {'id': identity, 'name': self.shared._name(account.get('name'))}
        if not isinstance(space['owner'], str) or space['owner'] not in accounts:
            raise SharedError('备份缺少原空间管理员。')
        members = {}
        for member in self.rows(document, 'members', 100):
            identity, role = member.get('account'), member.get('role')
            if not isinstance(identity, str) or identity not in accounts or identity in members or role not in ('owner', 'reader', 'editor'):
                raise SharedError('备份成员角色无效或重复。')
            if (role == 'owner') != (identity == space['owner']):
                raise SharedError('备份管理员与空间归属不一致。')
            members[identity] = role
        if members.get(space['owner']) != 'owner':
            raise SharedError('备份缺少管理员成员关系。')
        memories = {}
        for value in self.rows(document, 'memories', 200):
            record = self.record(value, accounts)
            if record['id'] in memories:
                raise SharedError('备份记忆编号重复。')
            if not set(record['viewers']) <= set(members):
                raise SharedError('当前记忆指定了非空间成员。')
            memories[record['id']] = record
        history = []
        for row in self.rows(document, 'history', 10000):
            identity, actor = row.get('memory'), row.get('actor')
            if not isinstance(identity, str) or not isinstance(actor, str) or identity not in memories or actor not in accounts:
                raise SharedError('历史记录的记忆或操作账号不存在。')
            before = self.record(row['before'], accounts, identity) if row.get('before') is not None else None
            after = self.record(row.get('after'), accounts, identity)
            if after['version'] > memories[identity]['version']:
                raise SharedError('历史版本超出当前记忆版本。')
            history.append({'memory': identity, 'actor': actor, 'before': before, 'after': after, 'changed': self.timestamp(row.get('changed'))})
        sources = {}
        for source in self.rows(document, 'sources', 200):
            key = self.text(source.get('key'), '原文编号', 128)
            text = self.text(source.get('text'), '原文', 30000)
            digest = hashlib.sha256(text.encode()).hexdigest()
            if key in sources or digest != source.get('hash'):
                raise SharedError('原文重复或内容校验失败。')
            sources[key] = {'key': key, 'name': self.text(source.get('name'), '原文名称', 160), 'text': text, 'hash': digest}
        evidence = {}
        for item in self.rows(document, 'evidence', 200):
            identity = item.get('memory')
            if not isinstance(identity, str) or not isinstance(item.get('source'), str) or identity not in memories or identity in evidence or item.get('source') not in sources:
                raise SharedError('原文依据指向无效的记忆或资料。')
            quotes = item.get('quotes')
            if not isinstance(quotes, list) or not 1 <= len(quotes) <= 100 or any(not isinstance(q, dict) for q in quotes):
                raise SharedError('原文摘录范围无效。')
            source = sources[item['source']]
            located = [self.shared.evidence.locate(source['text'], q.get('start'), q.get('end')) for q in quotes]
            evidence[identity] = {'memory': identity, 'source': source['key'], 'quotes': located,
                                  'captured_content': self.text(item.get('captured_content'), '采集时记忆正文'),
                                  'created': self.timestamp(item.get('created'))}
        replacements = []
        successors, predecessors = {}, set()
        for edge in self.rows(document, 'replacements', 200):
            old, new = edge.get('old_id'), edge.get('new_id')
            if any(not isinstance(x, str) for x in (old, new, edge.get('actor'))) or old not in memories or new not in memories or old == new or old in successors or new in predecessors or edge.get('actor') not in accounts:
                raise SharedError('新旧安排关联不完整或重复。')
            if memories[old]['status'] != 'outdated':
                raise SharedError('被替代记录必须标为过时。')
            successors[old] = new; predecessors.add(new)
            replacements.append({'old_id': old, 'new_id': new, 'actor': edge['actor'], 'created': self.timestamp(edge.get('created'))})
        for identity in successors:
            seen = set(); current = identity
            while current in successors:
                if current in seen:
                    raise SharedError('新旧安排关联形成循环。')
                seen.add(current); current = successors[current]
        batches, batch_ids, assigned = [], set(), set()
        for batch in self.rows({'batches':document.get('batches', [])}, 'batches', 200):
            identity = self.text(batch.get('id'), '批次编号', 128)
            actor = batch.get('actor'); mode = batch.get('mode')
            if identity in batch_ids or not isinstance(actor,str) or actor not in accounts or mode not in ('paragraphs','ai','manual'):
                raise SharedError('导入批次编号、账号或整理方式无效。')
            batch_ids.add(identity); items = []
            for item in self.rows(batch, 'items', 40):
                memory = item.get('memory'); version = item.get('initial_version')
                if not isinstance(memory,str) or memory not in memories or memory in assigned or memories[memory]['author']!=actor:
                    raise SharedError('批次记忆关联或原导入账号无效。')
                if type(version) is not int or not 1<=version<=memories[memory]['version']:
                    raise SharedError('批次初始版本无效。')
                assigned.add(memory)
                items.append({'memory':memory,'initial_version':version,'source_name':self.text(item.get('source_name'),'来源名称',160,True)})
            if not items:raise SharedError('备份批次不能没有在库记忆。')
            batches.append({'id':identity,'actor':actor,'name':self.text(batch.get('name'),'批次名称',160),
                            'mode':mode,'created':self.timestamp(batch.get('created')),'items':items})
        return {'format': FORMAT, 'version': 1, 'exported_at': self.timestamp(document.get('exported_at')),
                'space': space, 'accounts': list(accounts.values()),
                'members': [{'account': key, 'role': role} for key, role in members.items()],
                'memories': list(memories.values()), 'history': history,
                'sources': list(sources.values()), 'evidence': list(evidence.values()), 'replacements': replacements, 'batches':batches}

    def export(self, token, data):
        if data.get('confirmed') is not True:
            raise SharedError('请先确认备份包含完整原文、私密记忆及历史。')
        s = self.shared
        with s.store.connect() as db:
            db.execute('BEGIN')
            actor = s._actor(db, token); space = s._member(db, actor['id'], data.get('space_id'), owner=True)
            memories = [s._record(db, row[0]) for row in db.execute('SELECT id FROM shared_memories WHERE space=? ORDER BY id', (space['id'],)).fetchall()]
            history = [{'memory': row['memory'], 'actor': row['actor'], 'before': json.loads(row['before_json']) if row['before_json'] else None,
                        'after': json.loads(row['after_json']), 'changed': row['changed']}
                       for row in db.execute('SELECT h.* FROM shared_history h JOIN shared_memories m ON h.memory=m.id WHERE m.space=? ORDER BY h.id LIMIT 10001', (space['id'],))]
            members = [dict(row) for row in db.execute('SELECT account,role FROM shared_members WHERE space=? ORDER BY account', (space['id'],))]
            sources, evidence = {}, []
            for row in db.execute('SELECT e.* FROM shared_evidence e JOIN shared_memories m ON e.memory=m.id WHERE m.space=?', (space['id'],)):
                key = hashlib.sha256((row['source_name']+'\0'+row['source_text']).encode()).hexdigest()
                sources[key] = {'key': key, 'name': row['source_name'], 'text': row['source_text'], 'hash': row['source_hash']}
                evidence.append({'memory': row['memory'], 'source': key, 'quotes': json.loads(row['quotes_json']),
                                 'captured_content': row['captured_content'], 'created': row['created']})
            replacements = [dict(row) for row in db.execute('SELECT r.* FROM shared_replacements r JOIN shared_memories m ON r.old_id=m.id WHERE m.space=? ORDER BY r.created,r.old_id', (space['id'],))]
            needed = {space['owner']} | {x['account'] for x in members}
            for record in memories + [item for h in history for item in (h['before'], h['after']) if item]:
                needed.update((record['author'], record['editor'])); needed.update(record.get('viewers', []))
            batches = []
            for row in db.execute('SELECT * FROM shared_import_batches WHERE space=? ORDER BY created,id', (space['id'],)).fetchall():
                items = [dict(r) for r in db.execute('SELECT i.memory,i.initial_version,i.source_name FROM shared_import_batch_items i JOIN shared_memories m ON m.id=i.memory WHERE i.batch=? ORDER BY i.rowid', (row['id'],))]
                if items:batches.append({**dict(row),'items':items})
            needed.update(b['actor'] for b in batches)
            needed.update(h['actor'] for h in history); needed.update(e['actor'] for e in replacements)
            accounts = [dict(row) for row in db.execute('SELECT id,name FROM shared_accounts ORDER BY id') if row['id'] in needed]
            document = {'format': FORMAT, 'version': 1, 'exported_at': datetime.now(timezone.utc).isoformat(),
                        'space': dict(space), 'accounts': accounts, 'members': members,
                        'memories': memories, 'history': history, 'sources': list(sources.values()),
                        'evidence': evidence, 'replacements': replacements, 'batches':batches}
        return {'document': self.validate(document)}

    @staticmethod
    def counts(document):
        return {key: len(document[key]) for key in ('memories', 'history', 'sources', 'evidence', 'replacements', 'members', 'batches')}

    def inspect(self, token, data):
        actor = self.shared.me(token)['account']
        document = self.validate(data.get('document'))
        roles = {row['account']: row['role'] for row in document['members']}
        return {'name': document['space']['name'], 'kind': document['space']['kind'], 'counts': self.counts(document),
                'accounts': [{'id': row['id'], 'name': row['name'], 'role': roles.get(row['id'], 'historical'),
                              'owner': row['id'] == document['space']['owner'],
                              'target_name': actor['name'] if row['id'] == document['space']['owner'] else row['name']}
                             for row in document['accounts']]}

    def resolve_accounts(self, db, actor, document, names):
        if not isinstance(names, dict) or set(names) != {row['id'] for row in document['accounts']}:
            raise SharedError('请完整核对每个备份账号对应的本机账号。')
        mapping, seen = {}, set()
        for source in document['accounts']:
            name = self.shared._name(names[source['id']])
            row = db.execute('SELECT id,name FROM shared_accounts WHERE canonical=?', (name.casefold(),)).fetchone()
            if row is None:
                raise SharedError('本机没有账号「'+name+'」。请先注册该账号，或指定其他现有账号。')
            if row['id'] in seen:
                raise SharedError('不同备份账号不能合并为同一个本机账号。')
            if source['id'] == document['space']['owner'] and row['id'] != actor['id']:
                raise SharedError('原管理员必须对应当前执行恢复的账号。')
            seen.add(row['id']); mapping[source['id']] = dict(row)
        return mapping

    def preview(self, token, data):
        document = self.validate(data.get('document'))
        name = self.shared._text(data.get('name'), '新空间名称', 60)
        with self.shared.store.connect() as db:
            db.execute('BEGIN'); actor = self.shared._actor(db, token)
            mapping = self.resolve_accounts(db, actor, document, data.get('mapping'))
            canonical = {key: value for key, value in document.items() if key != 'exported_at' and (key != 'batches' or value)}
            signature = self.digest({'document': canonical, 'mapping': mapping, 'name': name})
            duplicate = db.execute('SELECT r.space,s.name FROM shared_restores r JOIN shared_spaces s ON r.space=s.id WHERE r.actor=? AND r.signature=? AND s.owner=?',
                                   (actor['id'], signature, actor['id'])).fetchone()
            if duplicate:
                return {'duplicate': dict(duplicate), 'counts': self.counts(document)}
            if db.execute('SELECT COUNT(*) FROM shared_spaces WHERE owner=?', (actor['id'],)).fetchone()[0] >= 20:
                raise SharedError('每个账号最多创建20个空间。')
        key = secrets.token_urlsafe(32); now = self.clock()
        with self.lock:
            self.plans = {k: p for k, p in self.plans.items() if p['expires'] > now and p['session'] != self.shared._token_hash(token)}
            while len(self.plans) >= 4:self.plans.pop(next(iter(self.plans)))
            self.plans[key] = {'actor': actor['id'], 'session': self.shared._token_hash(token), 'expires': now+600,
                               'document': document, 'mapping': mapping, 'name': name, 'signature': signature}
        roles = {m['account']: m['role'] for m in document['members']}
        return {'token': key, 'name': name, 'kind': document['space']['kind'], 'counts': self.counts(document),
                'mapping': [{'source': a['name'], 'target': mapping[a['id']]['name'], 'role': roles.get(a['id'], 'historical')} for a in document['accounts']],
                'memories': [{'content': m['content'], 'status': m['status'], 'status_reason': m['status_reason'], 'event_date': m['event_date'],
                              'author': mapping[m['author']]['name'], 'edit_policy': m['edit_policy'], 'visibility': m['visibility'],
                              'viewers': [mapping[x]['name'] for x in sorted((set(m['viewers']) | {document['space']['owner'], m['author']}) & set(roles))]}
                             for m in document['memories']], 'expires_in_seconds': 600}

    def remap_record(self, record, accounts, memories, space):
        result = copy.deepcopy(record)
        result.update(id=memories[record['id']], space=space,
                      author=accounts[record['author']]['id'], editor=accounts[record['editor']]['id'],
                      author_name=accounts[record['author']]['name'], editor_name=accounts[record['editor']]['name'],
                      viewers=[accounts[x]['id'] for x in record['viewers']])
        return result

    def confirm(self, token, data):
        if data.get('confirmed') is not True or data.get('permissions_confirmed') is not True:
            raise SharedError('请确认新空间归属、账号对应、成员角色及全部资料可见范围。')
        key = data.get('token')
        if not isinstance(key, str) or len(key) > 128:
            raise SharedError('缺少有效的恢复预览。')
        plan_hash = hashlib.sha256(key.encode()).hexdigest()
        s = self.shared
        with self.lock, s.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); actor = s._actor(db, token)
            old = db.execute('SELECT r.*,s.owner FROM shared_restores r JOIN shared_spaces s ON r.space=s.id WHERE plan_hash=?', (plan_hash,)).fetchone()
            if old:
                if old['actor'] != actor['id'] or old['session_hash'] != s._token_hash(token) or old['owner'] != actor['id']:
                    raise SharedError('恢复结果不属于当前会话。', 403)
                return {'space_id': old['space'], 'restored': True, 'already_restored': True}
            plan = self.plans.get(key)
            if not plan or plan['expires'] <= self.clock():
                raise SharedError('恢复预览已过期，请重新预览。', 409)
            if plan['actor'] != actor['id'] or plan['session'] != s._token_hash(token):
                raise SharedError('恢复预览不属于当前登录会话。', 403)
            names = {source: target['name'] for source, target in plan['mapping'].items()}
            accounts = self.resolve_accounts(db, actor, plan['document'], names)
            if accounts != plan['mapping']:
                raise SharedError('账号对应关系已变化，请重新预览。', 409)
            duplicate = db.execute('SELECT r.space FROM shared_restores r JOIN shared_spaces s ON r.space=s.id WHERE r.actor=? AND r.signature=? AND s.owner=?',
                                   (actor['id'], plan['signature'], actor['id'])).fetchone()
            if duplicate:
                return {'space_id': duplicate['space'], 'restored': True, 'already_restored': True}
            if db.execute('SELECT COUNT(*) FROM shared_spaces WHERE owner=?', (actor['id'],)).fetchone()[0] >= 20:
                raise SharedError('每个账号最多创建20个空间。')
            document = plan['document']; space = uuid.uuid4().hex
            db.execute('INSERT INTO shared_spaces(id,name,kind,owner) VALUES (?,?,?,?)', (space, plan['name'], document['space']['kind'], actor['id']))
            db.executemany('INSERT INTO shared_members VALUES (?,?,?)', [(space, accounts[m['account']]['id'], m['role']) for m in document['members']])
            memory_ids = {m['id']: uuid.uuid4().hex for m in document['memories']}
            for record in document['memories']:
                item = self.remap_record(record, accounts, memory_ids, space)
                columns = ('id', 'space') + FIELDS[1:-1]
                db.execute('INSERT INTO shared_memories('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', [item[k] for k in columns])
                db.executemany('INSERT INTO shared_audience VALUES (?,?)', [(item['id'], viewer) for viewer in item['viewers']])
            for entry in document['history']:
                before = self.remap_record(entry['before'], accounts, memory_ids, space) if entry['before'] else None
                after = self.remap_record(entry['after'], accounts, memory_ids, space)
                db.execute('INSERT INTO shared_history(memory,actor,before_json,after_json,changed) VALUES (?,?,?,?,?)',
                           (memory_ids[entry['memory']], accounts[entry['actor']]['id'], self.encode(before) if before else None, self.encode(after), entry['changed']))
            sources = {source['key']: source for source in document['sources']}
            for item in document['evidence']:
                source = sources[item['source']]
                db.execute('INSERT INTO shared_evidence VALUES (?,?,?,?,?,?,?)',
                           (memory_ids[item['memory']], source['name'], source['text'], source['hash'], self.encode(item['quotes']), item['captured_content'], item['created']))
            db.executemany('INSERT INTO shared_replacements VALUES (?,?,?,?)',
                           [(memory_ids[e['old_id']], memory_ids[e['new_id']], accounts[e['actor']]['id'], e['created']) for e in document['replacements']])
            for batch in document['batches']:
                batch_id = s.batches.create(db,space,accounts[batch['actor']]['id'],batch['name'],batch['mode'])
                db.execute('UPDATE shared_import_batches SET created=? WHERE id=?',(batch['created'],batch_id))
                for item in batch['items']:
                    s.batches.attach(db,batch_id,memory_ids[item['memory']],item['source_name'],item['initial_version'])
            db.execute('INSERT INTO shared_restores(id,actor,space,signature,plan_hash,session_hash,source_name,account_mapping) VALUES (?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, actor['id'], space, plan['signature'], plan_hash, plan['session'], document['space']['name'], self.encode(plan['mapping'])))
            self.plans.pop(key, None)
        return {'space_id': space, 'restored': True, 'counts': self.counts(document)}

    def dispatch(self, action, token, data):
        if not isinstance(data, dict):raise SharedError('请求格式不正确。')
        if action not in {'export', 'inspect', 'preview', 'confirm', 'cancel'}:raise SharedError('未知备份操作。', 404)
        self.shared.me(token)
        if action == 'cancel':
            key = data.get('token')
            if isinstance(key, str):
                with self.lock:
                    plan = self.plans.get(key)
                    if plan and plan['session'] == self.shared._token_hash(token):self.plans.pop(key)
            return {'cancelled': True}
        return getattr(self, action)(token, data)
