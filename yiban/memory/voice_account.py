"""Password-session-authorized voice access to the current memory service.

A voice match never logs into an account. Only server ASR receipts may drive
voice queries, under the same account ACLs and citation rules as text queries.
"""
import hashlib
import secrets
import threading
import time
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation


class VoiceAccount:
    def __init__(self, shared, memory):
        self.shared, self.memory, self.store = shared, memory, shared.store
        self.lock = threading.RLock()
        self.plans, self.receipts = {}, {}
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS voice_memory_bindings(
                    account TEXT PRIMARY KEY REFERENCES shared_accounts(id) ON DELETE CASCADE,
                    profile TEXT NOT NULL UNIQUE REFERENCES profiles(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, version INTEGER NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS voice_memory_grants(
                    token_hash TEXT PRIMARY KEY REFERENCES shared_sessions(token_hash) ON DELETE CASCADE,
                    account TEXT NOT NULL REFERENCES voice_memory_bindings(account) ON DELETE CASCADE,
                    binding_version INTEGER NOT NULL, authorized REAL NOT NULL);
            ''')

    @staticmethod
    def fields(data, allowed):
        if not isinstance(data,dict) or set(data)-set(allowed):
            raise SharedError('声纹记忆参数无效，请重新操作。')
        for key in ('profile_id','preview_token','receipt_id','conversation_id','turn_id','space_id','choice_id','space_choice'):
            if key in data and data[key] is not None and (not isinstance(data[key],str) or not 1<=len(data[key])<=128):
                raise SharedError('声纹记忆编号无效，请重新操作。')

    def fingerprint(self, db, profile):
        row=db.execute('SELECT embedding,model,enrolled FROM voiceprints WHERE profile=?',(profile,)).fetchone()
        if not row:raise SharedError('该档案没有可用声纹，请先由本人录入。',403)
        return hashlib.sha256(str(tuple(row)).encode()).hexdigest()

    def binding(self, db, actor):
        return db.execute('SELECT b.*,p.name AS profile_name FROM voice_memory_bindings b JOIN profiles p ON p.id=b.profile WHERE b.account=?',(actor['id'],)).fetchone()

    def grant(self, db, token):
        actor=self.shared._actor(db,token);binding=self.binding(db,actor)
        row=db.execute('SELECT * FROM voice_memory_grants WHERE token_hash=? AND account=?',(self.shared._token_hash(token),actor['id'])).fetchone()
        if not binding or not row or row['binding_version']!=binding['version']:
            raise SharedError('本次登录尚未授权声纹查询，请到“账号与空间 → 声纹与记忆账号”核对本人档案并确认授权。',403)
        if binding['fingerprint']!=self.fingerprint(db,binding['profile']):
            raise SharedError('声纹登记已变化，请重新核对并授权，当前没有读取记忆。',403)
        return actor,binding,row

    def status(self, token, data):
        self.fields(data,set())
        with self.store.connect() as db:
            actor=self.shared._actor(db,token);binding=self.binding(db,actor)
            profiles=[dict(r) for r in db.execute('''SELECT p.id,p.name FROM profiles p JOIN voiceprints v ON v.profile=p.id
                WHERE NOT EXISTS(SELECT 1 FROM voice_memory_bindings b WHERE b.profile=p.id AND b.account<>?) ORDER BY p.name''',(actor['id'],))]
            spaces=[dict(r) for r in db.execute('SELECT s.name,m.role FROM shared_members m JOIN shared_spaces s ON s.id=m.space WHERE m.account=? ORDER BY s.name',(actor['id'],))]
            reason='尚未授权本次登录。';authorized=False
            try:self.grant(db,token);authorized=True;reason='本次登录已授权。本人声纹匹配后自动查询个人及获准的团队记忆。'
            except SharedError as error:
                if error.status==401:raise
                reason=str(error)
        return {'account':dict(actor),'binding':{'profile_id':binding['profile'],'profile_name':binding['profile_name'],'version':binding['version']} if binding else None,
                'profiles':profiles,'spaces':spaces,'authorized':authorized,'message':reason}

    def preview(self, token, data):
        self.fields(data,{'profile_id'})
        with self.store.connect() as db:
            actor=self.shared._actor(db,token);profile=db.execute('SELECT id,name FROM profiles WHERE id=?',(data.get('profile_id'),)).fetchone()
            if not profile:raise SharedError('请选择本人的已登记声纹档案。')
            occupied=db.execute('SELECT 1 FROM voice_memory_bindings WHERE profile=? AND account<>?',(profile['id'],actor['id'])).fetchone()
            if occupied:raise SharedError('该声纹已绑定其他账号，请选择本人档案。',403)
            fingerprint=self.fingerprint(db,profile['id']);binding=self.binding(db,actor)
            spaces=[r['name'] for r in db.execute('SELECT s.name FROM shared_spaces s JOIN shared_members m ON m.space=s.id WHERE m.account=? ORDER BY s.name',(actor['id'],))]
        with self.lock:
            now=time.time();self.plans={k:v for k,v in self.plans.items() if v['expires']>now}
            if len(self.plans)>=128:raise SharedError('当前预览较多，请稍后重试。',429)
            identity=secrets.token_urlsafe(32)
            self.plans[identity]={'actor':actor['id'],'session':self.shared._token_hash(token),'profile':profile['id'],'fingerprint':fingerprint,
                                  'version':binding['version'] if binding else None,'expires':now+600}
        return {'preview_token':identity,'account_name':actor['name'],'profile_name':profile['name'],'spaces':spaces,
                'effect':'只允许本次已登录账号通过本人声纹读取自己的个人记忆和获准的团队记忆；不登录其他账号、不增加团队权限，也不保存原始录音。退出登录后授权失效。'}

    def confirm(self, token, data):
        self.fields(data,{'preview_token','confirmed','consent'})
        if data.get('confirmed') is not True or data.get('consent') is not True:
            raise SharedError('请核对本人账号、声纹档案和访问范围后明确同意。')
        with self.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=self.shared._actor(db,token);plan=self.plans.get(data.get('preview_token'))
            if not plan or plan['expires']<=time.time():raise SharedError('绑定预览已失效，请重新核对。',409)
            if plan['actor']!=actor['id'] or plan['session']!=self.shared._token_hash(token):raise SharedError('账号或登录会话与绑定预览不一致。',403)
            binding=self.binding(db,actor)
            if (binding['version'] if binding else None)!=plan['version'] or self.fingerprint(db,plan['profile'])!=plan['fingerprint']:
                raise SharedError('绑定或声纹已变化，请重新预览。',409)
            if db.execute('SELECT 1 FROM voice_memory_bindings WHERE profile=? AND account<>?',(plan['profile'],actor['id'])).fetchone():
                raise SharedError('该声纹已绑定其他账号，请重新核对。',409)
            changed=not binding or binding['profile']!=plan['profile'] or binding['fingerprint']!=plan['fingerprint']
            version=(binding['version']+1 if binding else 1) if changed else binding['version']
            if changed:
                db.execute('DELETE FROM voice_memory_grants WHERE account=?',(actor['id'],))
                db.execute('INSERT INTO voice_memory_bindings VALUES (?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET profile=excluded.profile,fingerprint=excluded.fingerprint,version=excluded.version,updated=excluded.updated',
                           (actor['id'],plan['profile'],plan['fingerprint'],version,time.time()))
            db.execute('INSERT INTO voice_memory_grants VALUES (?,?,?,?) ON CONFLICT(token_hash) DO UPDATE SET binding_version=excluded.binding_version,authorized=excluded.authorized',
                       (self.shared._token_hash(token),actor['id'],version,time.time()))
            self.plans.pop(data['preview_token'],None)
        return self.status(token,{})

    def revoke(self, token, data, unbind=False):
        self.fields(data,set())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');actor=self.shared._actor(db,token)
            if unbind:db.execute('DELETE FROM voice_memory_bindings WHERE account=?',(actor['id'],))
            else:db.execute('DELETE FROM voice_memory_grants WHERE token_hash=?',(self.shared._token_hash(token),))
        return self.status(token,{})

    def versions(self):
        with self.store.connect() as db:
            return {r['profile']:hashlib.sha256(str(tuple(r)[1:]).encode()).hexdigest()
                    for r in db.execute('SELECT profile,embedding,model,enrolled FROM voiceprints')}

    def receipt(self, text, speaker, versions=None):
        """Only called on server ASR output; callers cannot submit a matched name."""
        if not text:return None
        profile=speaker.get('id') if speaker.get('matched') is True else None
        fingerprint=None
        if profile:
            try:
                with self.store.connect() as db:fingerprint=self.fingerprint(db,profile)
                if versions is not None and versions.get(profile)!=fingerprint:profile=None
            except SharedError:profile=None
        with self.lock:
            now=time.time();self.receipts={k:v for k,v in self.receipts.items() if now-v['created']<120}
            while len(self.receipts)>=256:self.receipts.pop(next(iter(self.receipts)))
            identity=secrets.token_urlsafe(32);self.receipts[identity]={'text':text,'profile':profile,'fingerprint':fingerprint,'created':now}
            return identity

    def ask(self, token, data, cancel=None, context=''):
        self.fields(data,{'receipt_id','conversation_id','turn_id','scope_kind','space_id','choice_id','space_choice'})
        cancel=cancel or Cancellation();cancel.check()
        with self.lock,self.store.connect() as db:
            actor,binding,grant=self.grant(db,token)
            receipt=self.receipts.pop(data.get('receipt_id'),None)
            if not receipt or time.time()-receipt['created']>=120 or receipt['created']<grant['authorized']:
                raise SharedError('本次语音凭证已失效或已使用，请重新说话；当前没有读取记忆。',409)
            if not receipt['profile'] or receipt['profile']!=binding['profile'] or receipt['fingerprint']!=binding['fingerprint']:
                raise SharedError('本轮未确认是当前账号绑定的本人声音，没有读取个人或团队记忆。请本人重新说一句完整的话。',403)
            guard=(actor['id'],binding['version'],grant['authorized'])
        payload={k:v for k,v in data.items() if k!='receipt_id'};payload['query']=receipt['text']
        if self.store.identity_reply(binding['profile'],receipt['text']) is not None:
            result={'answer':binding['profile_name']+'，你好。很高兴和你聊天。','sources':[],
                    'answer_source_ids':[],'source_usage':'not_needed','memory_version':2,
                    'conversation':{'id':data.get('conversation_id'),'choices':[]}}
        else:result=self.memory.dispatch('ask',token,payload,cancel,context)
        cancel.check()
        with self.store.connect() as db:
            actor,current,current_grant=self.grant(db,token)
            if guard!=(actor['id'],current['version'],current_grant['authorized']):raise SharedError('本轮生成期间声纹授权发生变化，请重新提问。',409)
        return {**result,'voice_identity':{'account_name':actor['name'],'profile_name':binding['profile_name'],'matched':True}}

    def dispatch(self, path, token, data, cancel=None, context=''):
        if path=='status':return self.status(token,data)
        if path=='preview':return self.preview(token,data)
        if path=='confirm':return self.confirm(token,data)
        if path=='revoke':return self.revoke(token,data)
        if path=='unbind':return self.revoke(token,data,unbind=True)
        if path=='ask':return self.ask(token,data,cancel,context)
        raise SharedError('未知的声纹账号操作。',404)
