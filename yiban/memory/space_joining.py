"""Account-bound join requests. A request code never grants membership or memory access."""
import hashlib
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from yiban.memory.shared_memory import SharedError


class SpaceJoining:
    CODE_SECONDS = 7 * 24 * 60 * 60
    PLAN_SECONDS = 10 * 60

    def __init__(self, shared):
        self.shared, self.store = shared, shared.store
        self.lock = threading.RLock()
        self.plans = {}
        self.attempts = defaultdict(deque)
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS shared_join_codes(
                    space TEXT PRIMARY KEY REFERENCES shared_spaces(id) ON DELETE CASCADE,
                    digest TEXT NOT NULL UNIQUE, expires REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_join_requests(
                    id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                    account TEXT NOT NULL REFERENCES shared_accounts(id),
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','cancelled')),
                    created REAL NOT NULL, updated REAL NOT NULL, decided_by TEXT REFERENCES shared_accounts(id),
                    UNIQUE(space,account));
                CREATE INDEX IF NOT EXISTS join_inbox ON shared_join_requests(space,status,created);
            ''')

    @staticmethod
    def fields(data, allowed):
        if not isinstance(data, dict) or set(data)-set(allowed):
            raise SharedError('加入申请参数无效，请重新操作。')
        for key in ('space_id','request_id','preview_token'):
            if key in data and (not isinstance(data[key],str) or not 1<=len(data[key])<=128):
                raise SharedError('加入申请编号无效，请重新操作。')

    def plan(self, value):
        with self.lock:
            now = time.time()
            self.plans = {k:v for k,v in self.plans.items() if v['expires'] > now}
            if len(self.plans) >= 1000:
                raise SharedError('当前预览较多，请稍后重试。', 429)
            token = secrets.token_urlsafe(32)
            self.plans[token] = {**value, 'expires':now+self.PLAN_SECONDS}
            return token

    def actor(self, db, token):
        return self.shared._actor(db, token)

    def code_create(self, token, data):
        self.fields(data, {'space_id'})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db, token)
            self.shared._member(db, actor['id'], data.get('space_id'), owner=True)
            code = secrets.token_urlsafe(18)
            expires = time.time()+self.CODE_SECONDS
            db.execute('INSERT INTO shared_join_codes VALUES (?,?,?,?) ON CONFLICT(space) DO UPDATE SET digest=excluded.digest,expires=excluded.expires,created=excluded.created',
                       (data['space_id'],hashlib.sha256(code.encode()).hexdigest(),expires,time.time()))
        return {'code':code,'expires':expires,'message':'申请码只用于提交申请，须管理员批准后才能查看获准的团队资料。'}

    def code_disable(self, token, data):
        self.fields(data, {'space_id'})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db, token)
            self.shared._member(db, actor['id'], data.get('space_id'), owner=True)
            db.execute('DELETE FROM shared_join_codes WHERE space=?',(data['space_id'],))
        return {'disabled':True}

    def lookup(self, token, data):
        self.fields(data, {'code'})
        with self.store.connect() as db:
            actor = self.actor(db, token)
            code = data.get('code')
            if not isinstance(code,str) or not 16 <= len(code.strip()) <= 100:
                raise SharedError('申请码无效或已失效，请向管理员索取新的申请码。')
            with self.lock:
                now=time.time();recent=self.attempts[actor['id']]
                while recent and recent[0] < now-60:recent.popleft()
                if len(recent)>=20:raise SharedError('核对申请码过于频繁，请稍后重试。',429)
                recent.append(now)
            digest=hashlib.sha256(code.strip().encode()).hexdigest()
            row=db.execute('SELECT s.id,s.name,s.kind,c.expires FROM shared_join_codes c JOIN shared_spaces s ON s.id=c.space WHERE c.digest=? AND c.expires>?',(digest,time.time())).fetchone()
            if not row:raise SharedError('申请码无效或已失效，请向管理员索取新的申请码。')
            member=db.execute('SELECT role FROM shared_members WHERE space=? AND account=?',(row['id'],actor['id'])).fetchone()
        result={'space_id':row['id'],'space_name':row['name'],'kind':row['kind'],'already_member':bool(member),'role':member['role'] if member else None}
        if not member:result['preview_token']=self.plan({'kind':'request','actor':actor['id'],'space':row['id'],'digest':digest})
        return result

    def request(self, token, data):
        self.fields(data, {'preview_token','confirmed'})
        if data.get('confirmed') is not True:raise SharedError('请先核对账号与空间，再确认提交申请。')
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=self.actor(db,token)
            plan=self.plans.get(data.get('preview_token'))
            if not plan or plan['expires']<=time.time() or plan['kind']!='request':raise SharedError('申请预览已失效，请重新核对申请码。',409)
            if plan['actor']!=actor['id']:raise SharedError('账号与申请预览不一致。',403)
            code=db.execute('SELECT 1 FROM shared_join_codes WHERE space=? AND digest=? AND expires>?',(plan['space'],plan['digest'],time.time())).fetchone()
            if not code:raise SharedError('申请码已失效，请重新核对。',409)
            member=db.execute('SELECT role FROM shared_members WHERE space=? AND account=?',(plan['space'],actor['id'])).fetchone()
            if member:
                self.plans.pop(data['preview_token'],None)
                return {'status':'member','role':member['role']}
            row=db.execute('SELECT id,status FROM shared_join_requests WHERE space=? AND account=?',(plan['space'],actor['id'])).fetchone()
            identity=row['id'] if row else uuid.uuid4().hex
            if not row:
                db.execute('INSERT INTO shared_join_requests VALUES (?,?,?,?,?,?,NULL)',(identity,plan['space'],actor['id'],'pending',time.time(),time.time()))
            elif row['status']!='pending':
                db.execute("UPDATE shared_join_requests SET status='pending',updated=?,decided_by=NULL WHERE id=?",(time.time(),identity))
            self.plans.pop(data['preview_token'],None)
        return {'request_id':identity,'status':'pending','message':'申请已提交。批准前不能查看团队资料，个人记忆仍可正常使用。'}

    def mine(self, token, data):
        self.fields(data, set())
        with self.store.connect() as db:
            actor=self.actor(db,token)
            rows=db.execute('SELECT r.id,r.status,r.updated,s.name AS space_name,m.role FROM shared_join_requests r JOIN shared_spaces s ON s.id=r.space LEFT JOIN shared_members m ON m.space=r.space AND m.account=r.account WHERE r.account=? ORDER BY r.updated DESC LIMIT 100',(actor['id'],)).fetchall()
        return {'requests':[dict(r) for r in rows]}

    def cancel(self, token, data):
        self.fields(data, {'request_id'})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=self.actor(db,token)
            row=db.execute('SELECT * FROM shared_join_requests WHERE id=? AND account=?',(data.get('request_id'),actor['id'])).fetchone()
            if not row:raise SharedError('申请不存在。',404)
            if row['status']!='pending':raise SharedError('申请已处理，请刷新列表。',409)
            db.execute("UPDATE shared_join_requests SET status='cancelled',updated=? WHERE id=?",(time.time(),row['id']))
        return {'cancelled':True}

    def inbox(self, token, data):
        self.fields(data, {'space_id'})
        with self.store.connect() as db:
            actor=self.actor(db,token);space=self.shared._member(db,actor['id'],data.get('space_id'),owner=True)
            rows=db.execute("SELECT r.id,r.updated,a.name AS account_name FROM shared_join_requests r JOIN shared_accounts a ON a.id=r.account WHERE r.space=? AND r.status='pending' ORDER BY r.created LIMIT 100",(space['id'],)).fetchall()
            code=db.execute('SELECT expires FROM shared_join_codes WHERE space=? AND expires>?',(space['id'],time.time())).fetchone()
        return {'requests':[dict(r) for r in rows],'code_active':bool(code),'code_expires':code['expires'] if code else None}

    def review(self, token, data):
        self.fields(data, {'space_id','request_id','decision'})
        if data.get('decision') not in ('approve','reject'):raise SharedError('请选择通过或拒绝。')
        with self.store.connect() as db:
            actor=self.actor(db,token);space=self.shared._member(db,actor['id'],data.get('space_id'),owner=True)
            row=db.execute("SELECT r.*,a.name AS account_name FROM shared_join_requests r JOIN shared_accounts a ON a.id=r.account WHERE r.id=? AND r.space=? AND r.status='pending'",(data.get('request_id'),space['id'])).fetchone()
            if not row:raise SharedError('待处理申请不存在，请刷新。',404)
            member=db.execute('SELECT role FROM shared_members WHERE space=? AND account=?',(space['id'],row['account'])).fetchone()
        plan={'kind':'review','actor':actor['id'],'space':space['id'],'space_version':space['version'],'request':row['id'],'updated':row['updated'],'account':row['account'],'decision':data['decision']}
        return {'preview_token':self.plan(plan),'space_name':space['name'],'account_name':row['account_name'],'decision':data['decision'],'role':member['role'] if member else 'reader',
                'effect':'通过后可查看团队全体成员可见及明确授权给本人的记忆；不能修改团队记忆，也不能读取任何其他账号的个人记忆。' if not member else '对方已是成员，本次处理不降低或扩大现有角色权限。'}

    def confirm(self, token, data):
        self.fields(data, {'preview_token','confirmed'})
        if data.get('confirmed') is not True:raise SharedError('请核对申请人、空间和权限后确认。')
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=self.actor(db,token);plan=self.plans.get(data.get('preview_token'))
            if not plan or plan['kind']!='review' or plan['expires']<=time.time():raise SharedError('批准预览已失效，请重新核对。',409)
            if actor['id']!=plan['actor']:raise SharedError('账号与批准预览不一致。',403)
            space=self.shared._member(db,actor['id'],plan['space'],owner=True)
            row=db.execute('SELECT * FROM shared_join_requests WHERE id=? AND space=?',(plan['request'],space['id'])).fetchone()
            if space['version']!=plan['space_version'] or not row or row['status']!='pending' or row['updated']!=plan['updated']:
                raise SharedError('申请或成员权限已变化，请刷新后重新预览。',409)
            if plan['decision']=='approve':
                member=db.execute('SELECT role FROM shared_members WHERE space=? AND account=?',(space['id'],plan['account'])).fetchone()
                if not member:
                    if db.execute('SELECT COUNT(*) FROM shared_members WHERE space=?',(space['id'],)).fetchone()[0]>=100:raise SharedError('空间最多100位成员。')
                    db.execute("INSERT INTO shared_members VALUES (?,?,'reader')",(space['id'],plan['account']))
                    db.execute('UPDATE shared_spaces SET version=version+1 WHERE id=?',(space['id'],))
                status='approved'
            else:status='rejected'
            db.execute('UPDATE shared_join_requests SET status=?,updated=?,decided_by=? WHERE id=?',(status,time.time(),actor['id'],row['id']))
            self.plans.pop(data['preview_token'],None)
        return {'status':status,'request_id':row['id']}

    def dispatch(self, action, token, data):
        routes={'code/create':self.code_create,'code/disable':self.code_disable,'lookup':self.lookup,'request':self.request,'mine':self.mine,'cancel':self.cancel,'inbox':self.inbox,'review/preview':self.review,'review/confirm':self.confirm}
        if action not in routes:raise SharedError('未知的加入申请操作。',404)
        return routes[action](token,data)
