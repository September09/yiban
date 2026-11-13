"""Permission-scoped import provenance and reviewed, recoverable batch withdrawal."""
import copy
import hashlib
import json
import secrets
import threading
import time
import uuid
from yiban.memory.shared_memory import SharedError


class SharedBatches:
    def __init__(self, shared, clock=time.monotonic):
        self.shared, self.clock = shared, clock
        self.plans, self.lock = {}, threading.RLock()
        with shared.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shared_import_batches(
                    id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                    actor TEXT NOT NULL REFERENCES shared_accounts(id), name TEXT NOT NULL,
                    mode TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS shared_import_batches_space ON shared_import_batches(space,created);
                CREATE TABLE IF NOT EXISTS shared_import_batch_items(
                    batch TEXT NOT NULL REFERENCES shared_import_batches(id) ON DELETE CASCADE,
                    memory TEXT NOT NULL UNIQUE, initial_version INTEGER NOT NULL, source_name TEXT NOT NULL,
                    PRIMARY KEY(batch,memory));
                CREATE TABLE IF NOT EXISTS shared_import_undos(
                    receipt TEXT PRIMARY KEY, batch TEXT NOT NULL REFERENCES shared_import_batches(id) ON DELETE CASCADE,
                    actor TEXT NOT NULL REFERENCES shared_accounts(id), session TEXT NOT NULL,
                    reason TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def create(self, db, space, actor, name, mode):
        identity = uuid.uuid4().hex
        db.execute('INSERT INTO shared_import_batches(id,space,actor,name,mode) VALUES (?,?,?,?,?)',
                   (identity, space, actor, name, mode))
        return identity

    @staticmethod
    def attach(db, batch, memory, source_name, initial_version=1):
        db.execute('INSERT INTO shared_import_batch_items VALUES (?,?,?,?)',
                   (batch, memory, initial_version, source_name))

    def context(self, db, token, data):
        s = self.shared
        actor = s._actor(db, token)
        space = s._member(db, actor['id'], data.get('space_id'))
        return actor, space

    @staticmethod
    def manageable(actor, space, batch):
        return space['role'] == 'owner' or (space['role'] == 'editor' and batch['actor'] == actor['id'])

    def batch(self, db, space, identity):
        identity = self.shared._text(identity, '批次编号', 128)
        row = db.execute('SELECT b.*,a.name AS actor_name FROM shared_import_batches b JOIN shared_accounts a ON a.id=b.actor WHERE b.id=? AND b.space=?',
                         (identity, space['id'])).fetchone()
        if not row:raise SharedError('批次不存在或不可访问。', 403)
        return dict(row)

    def entries(self, db, actor, space, batch):
        s = self.shared; manager = self.manageable(actor, space, batch); result = []
        for link in db.execute('SELECT * FROM shared_import_batch_items WHERE batch=? ORDER BY rowid', (batch['id'],)).fetchall():
            identity = link['memory']; snapshot = None
            exists = db.execute('SELECT 1 FROM shared_memories WHERE id=? AND space=?', (identity, space['id'])).fetchone()
            if exists:
                item = s._record(db, identity)
                if not s._visible(actor, space, item):continue
                state = 'active'
            else:
                trash = db.execute('SELECT * FROM shared_trash WHERE memory=? AND space=?', (identity, space['id'])).fetchone()
                if not manager:continue
                if trash:
                    snapshot = json.loads(trash['snapshot']); item = snapshot['record']; state = 'trash'
                else:
                    result.append({'id':identity, 'state':'missing', 'eligible':False, 'reason':'记录已不在当前库或回收站，保留批次编号追溯。'})
                    continue
            if state == 'trash':reason = '已在回收站，本次不重复处理。'
            elif s.replacements.flags(db, identity)[0]:reason = '已关联新旧安排，请使用替代纠正流程，本批次保留。'
            elif item['version'] != link['initial_version']:reason = '导入后已经修改或恢复，本批次保留。'
            elif not manager or not s._configurable(actor, space, item):reason = '当前账号没有撤销这条记忆的权限。'
            else:reason = ''
            evidence = snapshot['evidence'] if snapshot else db.execute('SELECT * FROM shared_evidence WHERE memory=?', (identity,)).fetchone()
            result.append({**s.recovery.public_record(db, space, item), 'state':state, 'eligible':not reason,
                           'reason':reason, 'source_name':link['source_name'], 'has_evidence':bool(evidence),
                           'can_read_full':space['role']=='owner' or actor['id']==item['author']})
        return result

    def summary(self, actor, space, batch, entries):
        manager = self.manageable(actor, space, batch)
        return {'id':batch['id'], 'name':batch['name'] if manager else '共同资料导入批次',
                'actor_name':batch['actor_name'], 'created':batch['created'], 'mode':batch['mode'],
                'can_undo':manager, 'visible_count':len(entries),
                'active_count':sum(e['state']=='active' for e in entries),
                'trash_count':sum(e['state']=='trash' for e in entries),
                'eligible_count':sum(e['eligible'] for e in entries)}

    def list(self, token, data):
        s = self.shared
        with s.store.connect() as db:
            db.execute('BEGIN');actor, space = self.context(db, token, data)
            result = []
            for row in db.execute('SELECT id FROM shared_import_batches WHERE space=? ORDER BY created DESC,rowid DESC', (space['id'],)).fetchall():
                batch = self.batch(db, space, row['id']); entries = self.entries(db, actor, space, batch)
                if entries:result.append(self.summary(actor, space, batch, entries))
            legacy = db.execute('SELECT COUNT(*) FROM shared_memories m WHERE space=? AND NOT EXISTS(SELECT 1 FROM shared_import_batch_items i WHERE i.memory=m.id)', (space['id'],)).fetchone()[0] if space['role']=='owner' else None
        return {'batches':result, 'untracked_count':legacy}

    def detail(self, token, data):
        with self.shared.store.connect() as db:
            db.execute('BEGIN');actor, space = self.context(db, token, data)
            batch = self.batch(db, space, data.get('batch_id')); entries = self.entries(db, actor, space, batch)
            if not entries:raise SharedError('批次不存在或没有可见资料。', 403)
            events = []
            if self.manageable(actor, space, batch):
                events = [{'created':r['created'], 'actor_name':r['actor_name'], 'reason':r['reason'], 'withdrawn':json.loads(r['result'])['withdrawn']}
                          for r in db.execute('SELECT u.*,a.name AS actor_name FROM shared_import_undos u JOIN shared_accounts a ON a.id=u.actor WHERE batch=? ORDER BY u.created DESC,u.rowid DESC', (batch['id'],))]
            return {'batch':self.summary(actor, space, batch, entries), 'items':entries, 'events':events}

    def source(self, token, data):
        s = self.shared
        with s.store.connect() as db:
            db.execute('BEGIN');actor, space = self.context(db, token, data)
            batch = self.batch(db, space, data.get('batch_id'))
            entry = next((e for e in self.entries(db, actor, space, batch) if e['id']==data.get('id')), None)
            if not entry:raise SharedError('没有查看这条资料依据的权限。', 403)
            if data.get('expected_version') != entry.get('version') or type(data.get('expected_version')) is not int:
                raise SharedError('资料版本已变化，请重新查看批次。', 409)
            if data.get('include_full') is True and not entry['can_read_full']:
                raise SharedError('完整原文仅仍在空间内的作者和管理员可查看。', 403)
            if entry['state']=='trash':
                row = db.execute('SELECT snapshot FROM shared_trash WHERE memory=? AND space=?', (entry['id'], space['id'])).fetchone()
                evidence = json.loads(row['snapshot'])['evidence']
            else:
                evidence = db.execute('SELECT * FROM shared_evidence WHERE memory=?', (entry['id'],)).fetchone()
            if not evidence:return {'available':False, 'message':'导入时没有选择保留原文，无法从来源说明还原。'}
            result = {k:evidence[k] for k in ('source_name','source_hash','captured_content','created')}
            result.update(available=True, quotes=json.loads(evidence['quotes_json']), can_read_full=entry['can_read_full'],
                          content_changed=evidence['captured_content']!=entry['content'])
            if data.get('include_full') is True:result['source_text']=evidence['source_text']
            return result

    def build(self, db, actor, space, data):
        s = self.shared; batch = self.batch(db, space, data.get('batch_id'))
        if not self.manageable(actor, space, batch):raise SharedError('仅本批导入者（编辑成员）或管理员可撤销。', 403)
        reason = s._text(data.get('reason'), '撤销说明', 200)
        entries = self.entries(db, actor, space, batch); actions = []
        for entry in entries:
            if entry['eligible']:
                action = s.recovery.build(db, actor, space, {'operation':'trash', 'id':entry['id'], 'version':entry['version'], 'reason':reason})
                action['metadata'].update(batch_id=batch['id'], batch_name=batch['name'])
                actions.append(action)
        used = db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(snapshot AS BLOB))),0) FROM shared_trash WHERE space=?', (space['id'],)).fetchone()
        if used[0]+len(actions)>100 or used[1]+sum(len(s.recovery.encode(a['snapshot']).encode()) for a in actions)>24_000_000:
            raise SharedError('整批撤销会超过回收站100条或24 MB上限，未移除任何记忆。')
        signature = hashlib.sha256(self.encode({'entries':entries, 'snapshots':[a['snapshot'] for a in actions]}).encode()).hexdigest()
        return batch, entries, actions, signature, reason

    def preview(self, token, data):
        with self.shared.store.connect() as db:
            db.execute('BEGIN');actor, space = self.context(db, token, data)
            batch, entries, actions, signature, reason = self.build(db, actor, space, data)
        if not actions:raise SharedError('没有可撤销的未修改记录；请查看每条保留原因。', 409)
        key = secrets.token_urlsafe(32); now = self.clock()
        with self.lock:
            self.plans = {k:p for k,p in self.plans.items() if p['expires']>now and p['session']!=self.shared._token_hash(token)}
            while len(self.plans)>=16:self.plans.pop(next(iter(self.plans)))
            self.plans[key] = {'session':self.shared._token_hash(token), 'actor':actor['id'], 'space':space['id'],
                               'version':space['version'], 'expires':now+600, 'signature':signature,
                               'request':{'batch_id':batch['id'], 'reason':reason}, 'actions':copy.deepcopy(actions)}
        return {'token':key, 'space_id':space['id'], 'space_name':space['name'], 'actor_name':actor['name'],
                'batch':self.summary(actor, space, batch, entries), 'reason':reason,
                'withdraw':[e for e in entries if e['eligible']], 'retained':[e for e in entries if not e['eligible']], 'expires_in_seconds':600}

    def confirm(self, token, data):
        if data.get('confirmed') is not True or data.get('scope_confirmed') is not True:
            raise SharedError('请核对移入和保留清单、归属、权限，勾选两项确认。')
        key = data.get('token')
        if not isinstance(key, str) or not 20<=len(key)<=128:raise SharedError('缺少有效的批次撤销预览。')
        s = self.shared; digest = hashlib.sha256(key.encode()).hexdigest()
        with self.lock, s.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor, space = self.context(db, token, data)
            receipt = db.execute('SELECT * FROM shared_import_undos WHERE receipt=?', (digest,)).fetchone()
            if receipt:
                batch = self.batch(db, space, receipt['batch'])
                if receipt['actor']!=actor['id'] or receipt['session']!=s._token_hash(token) or not self.manageable(actor, space, batch):
                    raise SharedError('撤销记录不属于当前会话或权限已变化。', 403)
                return {**json.loads(receipt['result']), 'already_applied':True}
            plan = self.plans.get(key)
            if not plan or plan['expires']<=self.clock():raise SharedError('预览已过期或取消，请重新预览。', 409)
            if plan['session']!=s._token_hash(token) or plan['space']!=space['id'] or plan['actor']!=actor['id']:
                raise SharedError('预览不属于当前会话或空间。', 403)
            if space['version']!=plan['version']:raise SharedError('资料或成员权限已变化，请重新预览。', 409)
            batch, entries, actions, signature, reason = self.build(db, actor, space, plan['request'])
            if signature!=plan['signature']:raise SharedError('批次资料已变化，请重新预览。', 409)
            ids = []
            for action in copy.deepcopy(plan['actions']):
                s.recovery.apply(db, actor, space, action)
                ids.append(action['before'][0]['id'])
            result = {'applied':True, 'batch_id':batch['id'], 'withdrawn':len(ids), 'retained':len(entries)-len(ids), 'ids':ids}
            db.execute('INSERT INTO shared_import_undos(receipt,batch,actor,session,reason,result) VALUES (?,?,?,?,?,?)',
                       (digest, batch['id'], actor['id'], s._token_hash(token), reason, self.encode(result)))
        with self.lock:self.plans.pop(key, None)
        s.recovery.invalidate(space['id'], ids)
        return result

    def cancel(self, token, data):
        session = self.shared._token_hash(token)
        with self.shared.store.connect() as db:self.shared._actor(db, token)
        key = data.get('token')
        if isinstance(key, str):
            with self.lock:
                if key in self.plans and self.plans[key]['session']==session:self.plans.pop(key)
        return {'cancelled':True}

    def dispatch(self, action, token, data):
        if not isinstance(data, dict):raise SharedError('请求格式无效。')
        if action not in {'list','detail','source','preview','confirm','cancel'}:raise SharedError('未知批次操作。', 404)
        return getattr(self, action)(token, data)
