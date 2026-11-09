"""Authenticated spoken memory workflow; ASR receipts and review state stay in RAM."""
import hashlib
import re
import secrets
import threading
import time

from yiban.memory.shared_memory import SharedError


def clean(text):
    text = re.sub(r'^\s*(?:你好[，, ]*)?(?:小[伴半办]|忆伴)[，,：: ]*', '', text).strip()
    return text.strip(' \t\r\n，。！？!?')


def compact(text):
    return re.sub(r'[\s，。！？、：,:;.!?；“”"「」]', '', text)


class SharedVoice:
    def __init__(self, shared):
        self.shared = shared
        self.lock = threading.RLock()
        self.sessions = {}
        self.receipts = {}

    def receipt(self, text, speaker):
        """Called only with server ASR/voiceprint output, never a client identity."""
        if not text:
            return None
        now = time.monotonic()
        with self.lock:
            self.receipts = {k:v for k,v in self.receipts.items() if now-v['time'] < 120}
            while len(self.receipts) >= 256:
                self.receipts.pop(next(iter(self.receipts)))
            identity = secrets.token_urlsafe(32)
            self.receipts[identity] = {'text':text, 'time':now,
                'profile':speaker.get('id') if speaker.get('matched') else None}
            return identity

    def _voiceprint(self, db, profile):
        row = db.execute('SELECT embedding,model,enrolled FROM voiceprints WHERE profile=?', (profile,)).fetchone()
        if not row:
            raise SharedError('请先选择已登记声纹的个人档案，再开启共同记忆语音。', 403)
        return hashlib.sha256(str(tuple(row)).encode()).hexdigest()

    def start(self, token, data):
        with self.shared.store.connect() as db:
            db.execute('BEGIN')
            actor = self.shared._actor(db, token)
            space = self.shared._member(db, actor['id'], data.get('space_id'))
            profile = data.get('profile_id')
            if not isinstance(profile,str):
                raise SharedError('请先选择本人已登记声纹的档案。')
            fingerprint = self._voiceprint(db, profile)
        if data.get('private_listening',False) not in (True,False):
            raise SharedError('请选择公开播放或私密收听。')
        identity = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.sessions = {k:v for k,v in self.sessions.items() if v['expires']>now and v['token']!=self.shared._token_hash(token)}
            if len(self.sessions)>=64:
                raise SharedError('语音会话较多，请稍后重试。')
            self.sessions[identity] = {'token':self.shared._token_hash(token), 'actor':actor['id'],
                'profile':profile, 'fingerprint':fingerprint, 'space':space['id'], 'pending':None,
                'created':now, 'public_only':not data.get('private_listening',False), 'expires':now+1800, 'generation':0}
        mode = '公开播放，只查询全体成员可见的记忆' if not data.get('private_listening') else '私密收听，可以查询当前账号可见的指定成员记忆'
        return {'session':identity, 'expires_in_seconds':1800,
            'reply':f"已进入{'家庭' if space['kind']=='family' else '团队'}「{space['name']}」共同记忆。当前账号是{actor['name']}。{mode}。你可以提问，或者说，记住，加上要保存的内容。"}

    def _session(self, token, identity):
        if not isinstance(identity,str):
            raise SharedError('请在后台开启共同记忆语音。',403)
        session = self.sessions.get(identity)
        if not session or session['expires']<=time.monotonic() or session['token']!=self.shared._token_hash(token):
            raise SharedError('共同记忆语音已锁定或过期，请重新开启。',403)
        try:
            with self.shared.store.connect() as db:
                db.execute('BEGIN')
                actor = self.shared._actor(db,token)
                space = self.shared._member(db,actor['id'],session['space'])
                if self._voiceprint(db,session['profile'])!=session['fingerprint']:
                    raise SharedError('声纹登记发生变化，请重新开启共同记忆语音。',403)
        except SharedError:
            self.sessions.pop(identity,None)
            raise
        return session, actor, space

    def cancel(self, token, data, stop=False):
        with self.lock:
            session,_,_ = self._session(token,data.get('session'))
            session['pending']=None
            session['generation']+=1
            if stop:
                self.sessions.pop(data['session'],None)
        return {'cancelled':True}

    def played(self, token, data):
        with self.lock:
            session,_,space=self._session(token,data.get('session'))
            plan=session['pending']
            if not plan or data.get('review_id')!=plan['review_id'] or plan['expires']<=time.monotonic():
                raise SharedError('语音确认已取消或过期，请重新提出保存或修改。',409)
            if plan['space_version']!=space['version']:
                session['pending']=None
                raise SharedError('空间内容或权限已变化，请重新提出保存或修改。',409)
            if plan['armed'] is None:
                plan['armed']=time.monotonic()
                plan['expires']=plan['armed']+90
            return {'listening_for_confirmation':True,'expires_in_seconds':90}

    def validate_query(self, token, data, generation):
        with self.lock:
            session,_,_=self._session(token,data.get('session'))
            if session['generation']!=generation:
                raise SharedError('本轮共同记忆问答已取消。',409)

    def _preview(self, token, session, space, payload):
        # Reuse database ACL/version/transaction rules, never grant permissions via speech.
        session['pending']=None
        plan=self.shared.preview(token,payload,public_only=session['public_only'])
        review_id=secrets.token_urlsafe(24)
        summary=f"准备{'修改' if payload.get('id') else '保存'}到{'家庭' if space['kind']=='family' else '团队'}「{space['name']}」。"
        summary+=f"内容是：{plan['after']['content']}。"
        summary+='记忆状态：'+{'pending':'待核实','valid':'有效','outdated':'过时'}[plan['after']['status']]+'。'
        if plan['after']['status']=='pending':summary+='保存不代表事实已核实，可稍后在后台核实。'
        if plan['after']['visibility']=='all':
            summary+='可见范围是全体成员，包括以后加入的成员。'
        else:
            summary+='可见成员是'+ '、'.join(plan['audience'])+'。仍在空间的作者和管理员始终可见。'
        summary+=('仅作者和管理员可以修改。' if plan['after']['edit_policy']=='author' else '可见的编辑成员也可以修改正文。')
        phrase='确认修改' if payload.get('id') else '确认保存'
        if plan['conflicts']:
            phrase+='并保留冲突'
            summary+='这条内容与已有可见记忆可能冲突，不会自动覆盖冲突记录。'
        spoken='我'+phrase.replace('确认保存','确认保存这条共同记忆').replace('确认修改','确认修改这条共同记忆')
        summary+=f'听完后，请完整说“{spoken}”，或说“取消”。'
        session['pending']={'review_id':review_id,'token':plan['token'],'payload':payload,
            'phrase':phrase,'armed':None,'expires':time.monotonic()+600,
            'space_version':space['version']}
        return {'reply':summary,'review_id':review_id,'needs_confirmation':True,'saved':False}

    def _visibility(self, token, space, text):
        if compact(text) in ('所有成员可见','全体成员可见','所有人可见','让所有成员可见'):
            return {'visibility':'all','viewers':[]}
        match=re.fullmatch(r'(?:仅|只)(?:让|给)?(.+?)(?:可见|能看|可以看)',text)
        if not match:
            return None
        names=[x.strip(' ，、和与') for x in re.split(r'[、,，]|和|与',match[1])]
        roster=self.shared.members(token,space['id'])['members']
        lookup={m['name']:m['id'] for m in roster}
        if not names or any(n not in lookup for n in names):
            raise SharedError('没有准确匹配到成员账号，请说“仅让账号名称可见”，或在后台设置。')
        return {'visibility':'selected','viewers':[lookup[n] for n in names]}

    def process(self, token, data, cancel):
        with self.lock:
            session,actor,space=self._session(token,data.get('session'))
            receipt_id=data.get('utterance_id')
            receipt=self.receipts.pop(receipt_id,None) if isinstance(receipt_id,str) else None
            if not receipt or time.monotonic()-receipt['time']>=120 or receipt['time']<session['created']:
                session['pending']=None
                raise SharedError('本次语音凭证无效或已使用，请重新说话。',409)
            cancel.check()
            text=clean(receipt['text'])
            phrase=compact(text)
            phrase=re.sub(r'^我(?=确认)', '', phrase).replace('这条共同记忆','')
            if phrase in ('取消','取消保存','取消修改','不用记了','不要保存','算了'):
                session['pending']=None
                return {'reply':'已取消待确认操作，没有保存或修改记忆。','saved':False}
            if phrase in ('退出共同记忆','退出共同记忆语音','结束共同记忆'):
                self.sessions.pop(data['session'],None)
                return {'reply':'已退出共同记忆语音。','stopped':True}
            if receipt['profile']!=session['profile']:
                self.sessions.pop(data['session'],None)
                raise SharedError('未确认是当前账号绑定的说话人，已锁定共同记忆语音，请重新登录并开启。',403)
            pending=session['pending']
            if phrase.startswith('确认') or phrase in ('是','是的','对','好的','可以'):
                if not pending:
                    return {'reply':'目前没有待确认的共同记忆。请先完整说出要保存或修改的内容。','saved':False}
                if pending['expires']<=time.monotonic():
                    session['pending']=None
                    return {'reply':'确认已过期，没有写入，请重新说出要保存或修改的内容。','saved':False}
                if pending['armed'] is None or receipt['time']<=pending['armed']:
                    return {'reply':'请先完整听完归属和可见范围的播报，再进行确认。当前尚未写入。','saved':False}
                if phrase!=pending['phrase']:
                    return {'reply':f"为避免误保存，请完整说“我{pending['phrase'].replace('确认保存','确认保存这条共同记忆').replace('确认修改','确认修改这条共同记忆')}”，或说“取消”。",'saved':False,'follow_up':True}
                session['pending']=None
                cancel.check()
                result=self.shared.confirm(token,{'space_id':session['space'],'token':pending['token'],
                    'confirmed':True,'acknowledge_conflicts':pending['phrase'].endswith('并保留冲突')})
                return {'reply':f"已{'修改' if pending['payload'].get('id') else '保存'}到「{space['name']}」共同记忆。",'saved':True,'id':result['id']}
            # A spoken scope change creates a new review and requires another full playback.
            session['pending']=None
            visibility=self._visibility(token,space,text)
            if visibility:
                if not pending:
                    return {'reply':'请先说出要保存或修改的内容，再指定可见成员。','saved':False}
                if pending['expires']<=time.monotonic():
                    session['pending']=None
                    return {'reply':'上一条草稿已过期，请重新说出完整内容。','saved':False}
                payload=dict(pending['payload'],**visibility)
                return self._preview(token,session,space,payload)
            session['pending']=None
            if re.search(r'删除|移除成员|添加成员|修改权限|转让|切换(?:到)?(?:家庭|团队)',text):
                return {'reply':'成员、删除、修改权限和切换空间请在后台管理中操作。当前没有写入。','saved':False}
            # Explicit other-space requests cannot accidentally fall into the active space.
            if re.search(r'(?:保存到|记到|存到|修改|查询|看看)(?:家庭|团队)',text):
                if ('团队' in text and space['kind']=='family') or ('家庭' in text and space['kind']=='team'):
                    return {'reply':'你提到的空间类型与当前空间不同，请先在后台切换空间。','saved':False}
            save=re.fullmatch(r'(?:(?:请|帮我|请帮我)\s*)?(?:记住|记一下|记录一下|保存共同记忆|保存到(?:家庭|团队)共同记忆|记到(?:家庭|团队)共同记忆)[：:，,\s]*(.+)',text)
            edit=re.fullmatch(r'(?:(?:请|帮我|请帮我)\s*)?(?:把|将)(.+?)(?:修改为|修改成|改成|改为)[：:，,\s]*(.+)',text)
            if not edit:
                edit=re.fullmatch(r'(?:(?:请|帮我|请帮我)\s*)?修改(?:共同记忆)?[：:，,\s]*(.+?)(?:为|成)[：:，,\s]*(.+)',text)
            if (save or edit) and space['role']=='reader':
                return {'reply':'当前账号是只读成员，可以查询可见的共同记忆，不能保存或修改。','saved':False}
            if save:
                payload={'space_id':space['id'],'content':save[1].strip(),'visibility':'all',
                         'viewers':[],'edit_policy':'author','source_note':'语音确认','event_date':''}
                # Optional explicit scope at the end of a save instruction.
                parts=re.split(r'[，,；;]',payload['content'])
                if len(parts)>1:
                    scope=self._visibility(token,space,parts[-1].strip())
                    if scope:payload.update(scope);payload['content']='，'.join(parts[:-1]).strip()
                return self._preview(token,session,space,payload)
            if edit:
                target=compact(edit[1])
                items=self.shared.memories(token,space['id'])['memories']
                items=[item for item in items if not session['public_only'] or item['visibility']=='all']
                matches=[item for item in items if target and target in compact(item['content'])]
                if len(matches)!=1:
                    return {'reply':'没有找到唯一匹配的可见记忆。请多说几个原文关键词，例如，把家庭周末活动改成新的完整安排。当前没有修改。','saved':False}
                item=matches[0]
                payload={key:item[key] for key in ('id','version','visibility','viewers','edit_policy','source_note','event_date')}
                payload.update(space_id=space['id'],content=edit[2].strip())
                return self._preview(token,session,space,payload)
            # Never send a malformed write intent to a model that might claim a write succeeded.
            if re.search(r'记住|保存|改成|改为|修改|记到|存到',text):
                return {'reply':'请说“记住，加上完整内容”，或“把原记忆中的关键词改成新的完整内容”。当前没有写入。','saved':False}
            return {'query':text,'space_id':space['id'],'public_only':session['public_only'],'generation':session['generation']}
