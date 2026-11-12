"""Atomic, confirmed supersession. A new record preserves the old record and its evidence."""
import copy
import hashlib
import json
import secrets
import threading
import time
import uuid
from yiban.memory.shared_memory import SharedError
from yiban.memory.memory_review import possible_conflicts


class SharedReplacement:
    def __init__(self,shared):
        self.shared=shared;self.plans={};self.lock=threading.RLock()
        with shared.store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS shared_replacements(
                old_id TEXT PRIMARY KEY REFERENCES shared_memories(id) ON DELETE CASCADE,
                new_id TEXT NOT NULL UNIQUE REFERENCES shared_memories(id) ON DELETE CASCADE,
                actor TEXT NOT NULL REFERENCES shared_accounts(id),
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')

    @staticmethod
    def flags(db,identity):
        row=db.execute('SELECT old_id,new_id FROM shared_replacements WHERE old_id=? OR new_id=?',(identity,identity)).fetchall()
        return bool(row),any(r['old_id']==identity for r in row)

    def eligible(self,db,actor,space,old):
        if old['space']!=space['id'] or not self.shared._visible(actor,space,old) or not self.shared._configurable(actor,space,old):
            raise SharedError('只有原作者（有编辑角色）或管理员可以替代这条安排。',403)
        if old['status']=='outdated' or self.flags(db,old['id'])[1]:raise SharedError('旧安排已经过时或被替代，请选择当前安排。',409)

    def preview(self,token,data):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        s=self.shared
        with s.store.connect() as db:
            db.execute('BEGIN');actor=s._actor(db,token);space=s._member(db,actor['id'],data.get('space_id'));old=s._record(db,data.get('id'))
            self.eligible(db,actor,space,old)
            if type(data.get('version')) is not int or data['version']!=old['version']:raise SharedError('原安排已变化，请刷新后重新预览。',409)
            if db.execute('SELECT COUNT(*) FROM shared_memories WHERE space=?',(space['id'],)).fetchone()[0]>=200:raise SharedError('空间记忆已达上限，无法保留旧记录并新增安排。')
            reason=s._text(data.get('reason'),'替代原因与核实说明',200)
            payload=s._payload(db,actor,space,dict(content=data.get('content'),event_date=data.get('event_date',old['event_date']),
                visibility=old['visibility'],viewers=old['viewers'],edit_policy=old['edit_policy'],source_note='确认替代旧安排；原记录保留用于追溯',status='valid',status_reason=reason),None)
            if (payload['content'],payload['event_date'])==(old['content'],old['event_date']):raise SharedError('新旧内容和日期相同，无需替代。')
            roster={r['id']:r['name'] for r in db.execute('SELECT a.id,a.name FROM shared_members m JOIN shared_accounts a ON a.id=m.account WHERE m.space=?',(space['id'],))}
            audience=sorted(roster.values()) if old['visibility']=='all' else sorted(roster[x] for x in set(old['viewers'])|{space['owner'],old['author']} if x in roster)
            conflicts=possible_conflicts(payload['content'],[x for x in s._items(db,actor,space) if x['status']!='outdated'],exclude=old['id'])
        now=time.monotonic();identity=secrets.token_urlsafe(32)
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>now}
            if len(self.plans)>=128:self.plans.pop(next(iter(self.plans)))
            self.plans[identity]={'session':hashlib.sha256(token.encode()).hexdigest(),'actor':actor['id'],'space':space['id'],'space_version':space['version'],'old_id':old['id'],'version':old['version'],'payload':copy.deepcopy(payload),'expires':now+600,'conflicts':bool(conflicts)}
        return {'token':identity,'operation':'replace','space_id':space['id'],'space_name':space['name'],'kind':space['kind'],'actor_name':actor['name'],
                'author_name':old['author_name'],'before':old,'after':payload,'audience':audience,'conflicts':conflicts,'expires_in_seconds':600}

    def discard(self,identity):
        with self.lock:self.plans.pop(identity,None)

    def confirm(self,token,data):
        s=self.shared
        if not isinstance(data,dict) or data.get('confirmed') is not True or data.get('replacement_confirmed') is not True:
            raise SharedError('请核对新旧内容，并确认新安排有效、旧安排过时且保留追溯。')
        key=data.get('token')
        if not isinstance(key,str) or len(key)>128:raise SharedError('缺少有效的替代预览。')
        with self.lock:
            plan=self.plans.get(key)
            if not plan or plan['expires']<=time.monotonic():
                self.plans.pop(key,None);raise SharedError('替代预览已过期、取消或使用，请重新预览。',409)
            if plan['session']!=hashlib.sha256((token or '').encode()).hexdigest() or plan['space']!=data.get('space_id'):raise SharedError('登录会话或空间与替代预览不一致。',403)
            if plan['conflicts'] and data.get('acknowledge_conflicts') is not True:raise SharedError('请先核对其他可能冲突的记忆。')
            with s.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');actor=s._actor(db,token);space=s._member(db,actor['id'],plan['space']);old=s._record(db,plan['old_id'])
                self.eligible(db,actor,space,old)
                if space['version']!=plan['space_version'] or old['version']!=plan['version']:
                    self.plans.pop(key,None);raise SharedError('资料或成员权限已变化，请重新生成替代预览。',409)
                if db.execute('SELECT COUNT(*) FROM shared_memories WHERE space=?',(space['id'],)).fetchone()[0]>=200:raise SharedError('空间记忆已达上限。')
                p=plan['payload'];identity=uuid.uuid4().hex
                db.execute('INSERT INTO shared_memories(id,space,author,editor,content,visibility,edit_policy,event_date,source_note,status,status_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,space['id'],old['author'],actor['id'],p['content'],p['visibility'],p['edit_policy'],p['event_date'],p['source_note'],'valid',p['status_reason']))
                db.executemany('INSERT INTO shared_audience VALUES (?,?)',[(identity,x) for x in p['viewers']])
                db.execute("UPDATE shared_memories SET status='outdated',status_reason=?,editor=?,version=version+1,updated=CURRENT_TIMESTAMP WHERE id=?",('已确认由新安排替代，旧记录保留追溯。',actor['id'],old['id']))
                db.execute('INSERT INTO shared_replacements(old_id,new_id,actor) VALUES (?,?,?)',(old['id'],identity,actor['id']))
                for record,before in ((old['id'],old),(identity,None)):
                    after=s._record(db,record)
                    db.execute('INSERT INTO shared_history(memory,actor,before_json,after_json) VALUES (?,?,?,?)',(record,actor['id'],json.dumps(before,ensure_ascii=False) if before else None,json.dumps(after,ensure_ascii=False)))
                db.execute('UPDATE shared_spaces SET version=version+1 WHERE id=?',(space['id'],))
            self.plans.pop(key,None)
        # Versioned cache keys already prevent stale hits; maintenance failure cannot undo the committed transition.
        if s.retriever:
            for record in (old['id'],identity):
                try:s.retriever.invalidate('shared:'+space['id'],record)
                except Exception:pass
        return {'saved':True,'id':identity,'replaced_id':old['id'],'operation':'replace'}

    def lineage(self,token,data):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        s=self.shared
        with s.store.connect() as db:
            db.execute('BEGIN');actor=s._actor(db,token);space=s._member(db,actor['id'],data.get('space_id'));item=s._record(db,data.get('id'))
            if item['space']!=space['id'] or not s._visible(actor,space,item):raise SharedError('记录不存在或不可访问。',403)
            if type(data.get('version')) is not int or data['version']!=item['version']:raise SharedError('记录已变化，请刷新后查看追溯。',409)
            nodes=[item];seen={item['id']};limited=False
            for direction in ('before','after'):
                current=item
                for _ in range(20):
                    column,other=('new_id','old_id') if direction=='before' else ('old_id','new_id')
                    edge=db.execute('SELECT '+other+' FROM shared_replacements WHERE '+column+'=?',(current['id'],)).fetchone()
                    if not edge:break
                    if edge[0] in seen:limited=True;break
                    adjacent=s._record(db,edge[0])
                    if adjacent['space']!=space['id'] or not s._visible(actor,space,adjacent):limited=True;break
                    seen.add(adjacent['id'])
                    if direction=='before':nodes.insert(0,adjacent)
                    else:nodes.append(adjacent)
                    current=adjacent
                else:limited=True
            fields=('id','content','status','status_reason','event_date','source_note','created','updated','version','has_evidence')
            return {'records':[{k:n[k] for k in fields} for n in nodes],'limited':limited,'selected_id':item['id']}
