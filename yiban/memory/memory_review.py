"""Auditable, snapshot-bound previews for text, state and suspected conflicts."""
import secrets
import time
from yiban.memory.personal_state import state_fields, possible_conflicts, fact_key, conflict_pairs


class MemoryReviewMixin:
    @staticmethod
    def init_history(db):
        db.execute('''CREATE TABLE IF NOT EXISTS memory_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,profile TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            memory TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,operation TEXT NOT NULL,
            before_content TEXT,after_content TEXT NOT NULL,changed TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        columns={row['name'] for row in db.execute('PRAGMA table_info(memory_history)')}
        for side in ('before','after'):
            for key in ('status','status_reason','event_date'):
                column=side+'_'+key
                if column not in columns:db.execute('ALTER TABLE memory_history ADD COLUMN '+column+' TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS memory_history_lookup ON memory_history(profile,memory,id)')
        db.execute("INSERT INTO memory_history(profile,memory,operation,after_content) SELECT profile,id,'baseline',content FROM memories m WHERE NOT EXISTS(SELECT 1 FROM memory_history h WHERE h.memory=m.id)")
        db.execute('DROP TRIGGER IF EXISTS memory_created');db.execute('DROP TRIGGER IF EXISTS memory_edited')
        db.execute('''CREATE TRIGGER memory_created AFTER INSERT ON memories BEGIN
            INSERT INTO memory_history(profile,memory,operation,after_content,after_status,after_status_reason,after_event_date)
            VALUES(NEW.profile,NEW.id,'created',NEW.content,NEW.status,NEW.status_reason,NEW.event_date); END''')
        db.execute('''CREATE TRIGGER memory_edited AFTER UPDATE OF content,status,status_reason,event_date ON memories
            WHEN OLD.content!=NEW.content OR OLD.status!=NEW.status OR OLD.status_reason!=NEW.status_reason OR OLD.event_date!=NEW.event_date BEGIN
            INSERT INTO memory_history(profile,memory,operation,before_content,after_content,before_status,after_status,before_status_reason,after_status_reason,before_event_date,after_event_date)
            VALUES(NEW.profile,NEW.id,CASE WHEN OLD.content!=NEW.content THEN 'edited' ELSE 'state_changed' END,
            OLD.content,NEW.content,OLD.status,NEW.status,OLD.status_reason,NEW.status_reason,OLD.event_date,NEW.event_date); END''')

    def history(self,identity,memory_id):
        self.profile(identity)
        with self.connect() as db:
            db.execute('BEGIN')
            current=db.execute('SELECT id,content,status,status_reason,event_date FROM memories WHERE id=? AND profile=?',(memory_id,identity)).fetchone()
            if not current:raise ValueError('这条记忆不存在或不属于当前用户。')
            total=db.execute('SELECT COUNT(*) FROM memory_history WHERE profile=? AND memory=?',(identity,memory_id)).fetchone()[0]
            rows=[dict(row) for row in db.execute('SELECT * FROM memory_history WHERE profile=? AND memory=? ORDER BY id DESC LIMIT 100',(identity,memory_id))]
        return {'memory':dict(current),'entries':rows,'total':total}

    @staticmethod
    def _mark_history(db,memory_id,operation):
        db.execute('UPDATE memory_history SET operation=? WHERE id=(SELECT MAX(id) FROM memory_history WHERE memory=?)',(operation,memory_id))

    def _review_token(self,entry):
        now=time.monotonic();self._reviews={key:value for key,value in self._reviews.items() if value['expires']>now}
        if len(self._reviews)>=128:self._reviews.pop(next(iter(self._reviews)))
        token=secrets.token_urlsafe(32);self._reviews[token]={**entry,'expires':now+600};return token

    def request_save(self,identity,content,memory_id=None,expected_revision=None,*,source='manual',status=None,status_reason=None,event_date=None):
        self.profile(identity)
        if not isinstance(content,str) or not 1<=len(content.strip())<=1200:raise ValueError('记忆内容需为 1–1200 个字。')
        if source not in {'manual','conversation'}:raise ValueError('记忆来源不正确。')
        content=content.strip()
        with self._import_lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');profile,rows,fingerprint=self._import_snapshot(db,identity)
                original=next((row for row in rows if row['id']==memory_id),None) if memory_id else None
                if memory_id and original is None:raise ValueError('这条记忆已删除或不属于当前用户，请重新选择。')
                wanted=status if status is not None else original['status'] if original else 'pending'
                reason=status_reason if status_reason is not None else original['status_reason'] if original else ''
                date=event_date if event_date is not None else original['event_date'] if original else ''
                reset=bool(original and (content!=original['content'] or date!=original['event_date']))
                if not isinstance(wanted,str) or wanted not in ('pending','valid','outdated'):raise ValueError('请选择待核实、有效或过时状态。')
                fields=state_fields('pending' if reset else wanted,reason,date)
                if reset:fields['status_reason']=''
                duplicate=next((row for row in rows if row['content']==content),None)
                if duplicate:
                    if memory_id and duplicate['id']!=memory_id:raise ValueError('相同内容已经存在，请保留已有条目。')
                    if not original:
                        if any(fields[k]!=duplicate[k] for k in fields) and any(x is not None for x in (status,status_reason,event_date)):
                            raise ValueError('相同正文已有记录；如需调整状态或日期，请在列表编辑该条，已有状态未覆盖。')
                        return {'id':duplicate['id'],'changed':False}
                    if all(fields[k]==original[k] for k in fields):return {'id':duplicate['id'],'changed':False}
                conflicts=possible_conflicts(content,rows,exclude=memory_id) if fields['status']!='outdated' else []
                if conflicts:fields.update(status='pending',status_reason='')
                if not original and not conflicts and fields==state_fields():
                    return {'id':self._write_memory(db,identity,content,None,source,fields),'changed':True}
                allowed={row['id'] for row in conflicts}
                if original:allowed.add(original['id'])
                token=self._review_token({'kind':'save','identity':identity,'content':content,'source':source,'fields':fields,
                    'fingerprint':fingerprint,'allowed':allowed,'original':memory_id,'can_append':len(rows)<200 and not (original and content==original['content'])})
                return {'review':{'kind':'save','token':token,'target':profile,'content':content,**fields,'original':original,
                    'conflicts':conflicts,'state_reset':reset,'stale':bool(original and expected_revision!=original['revision']),
                    'can_append':len(rows)<200 and not (original and content==original['content']),'expires_in_seconds':600},'changed':False}

    def _checked_review(self,identity,token,confirmed,kind):
        if confirmed is not True:raise ValueError('请核对差异并勾选确认。')
        if not isinstance(token,str) or not token or len(token)>128:raise ValueError('确认预览无效，请重新预览。')
        plan=self._reviews.get(token)
        if plan is None or plan['expires']<=time.monotonic():
            self._reviews.pop(token,None);raise ValueError('预览已过期或已使用，请重新预览。')
        if identity!=plan['identity']:raise ValueError('当前用户已改变，请重新预览。')
        if plan['kind']!=kind:raise ValueError('预览类型不符，请使用对应确认入口。')
        return plan

    def confirm_save(self,identity,token,confirmed,resolution,replace_id=None):
        with self._import_lock:
            plan=self._checked_review(identity,token,confirmed,'save')
            if resolution not in {'append','replace'} or (resolution=='replace' and (not isinstance(replace_id,str) or replace_id not in plan['allowed'])):
                raise ValueError('请选择预览中允许的处理方式。')
            if resolution=='append' and not plan['can_append']:raise ValueError('本次仅允许更新选中条目，不能并存新增。')
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');_,rows,fingerprint=self._import_snapshot(db,identity)
                if fingerprint!=plan['fingerprint']:
                    self._reviews.pop(token,None);raise ValueError('记忆或档案已再次变化，请重新保存、查看最新差异后确认。')
                fields=dict(plan['fields'])
                # Keeping opposing current statements must not turn a new claim into verified truth.
                target=next((r for r in rows if r['id']==replace_id),None) if resolution=='replace' else None
                if target and target['content']!=plan['content']:fields.update(status='pending',status_reason='')
                memory_id=self._write_memory(db,identity,plan['content'],replace_id if resolution=='replace' else None,plan['source'],fields)
                self._mark_history(db,memory_id,'confirmed_state' if target and target['content']==plan['content'] else 'confirmed_replace' if resolution=='replace' else 'confirmed_append')
            self._reviews.pop(token,None)
            return {'id':memory_id,'changed':True}

    def cancel_review(self,identity,token):
        if not isinstance(token,str) or len(token)>128:raise ValueError('预览编号无效。')
        with self._import_lock:
            plan=self._reviews.get(token)
            if plan and plan['identity']!=identity:raise ValueError('当前用户已改变，请重新预览。')
            self._reviews.pop(token,None)
        return {'cancelled':True}

    def conflicts(self,identity):
        rows=self.memories(identity)
        return {**conflict_pairs(rows),'profile':{'id':identity,'name':self.profile(identity)['name']},'rule_scope':'有限明确事项规则；疑似不同不等于已证明矛盾，未覆盖全部语义。'}

    def request_conflict(self,identity,left_id,right_id,action,preferred_id=None,reason=''):
        if not isinstance(reason,str) or not reason.strip() or len(reason.strip())>500:raise ValueError('请填写不超过500字的核对说明。')
        if not isinstance(left_id,str) or not isinstance(right_id,str) or left_id==right_id:raise ValueError('请选择两条不同的疑似冲突记忆。')
        if action not in ('prefer','keep_both') or (action=='prefer' and preferred_id not in (left_id,right_id)):raise ValueError('请选择保留为有效的记录，或让两条待核实并存。')
        with self._import_lock:
            with self.connect() as db:
                db.execute('BEGIN');profile,rows,fingerprint=self._import_snapshot(db,identity)
                chosen=[r for r in rows if r['id'] in (left_id,right_id)]
                if len(chosen)!=2 or any(r['status']=='outdated' for r in chosen):raise ValueError('记录不属于当前档案、已删除或已过时，请刷新。')
                if not possible_conflicts(chosen[0]['content'],[chosen[1]]):raise ValueError('这两条不再属于规则检出的疑似冲突，请重新检查。')
                changes=[]
                for row in chosen:
                    state='pending' if action=='keep_both' else 'valid' if row['id']==preferred_id else 'outdated'
                    changes.append({'before':row,'after':{**row,**state_fields(state,reason,row['event_date'])}})
                token=self._review_token({'kind':'conflict','identity':identity,'fingerprint':fingerprint,'changes':changes,'action':action})
                return {'kind':'conflict','token':token,'target':profile,'changes':changes,'action':action,'expires_in_seconds':600}

    def confirm_conflict(self,identity,token,confirmed):
        with self._import_lock:
            plan=self._checked_review(identity,token,confirmed,'conflict')
            count=0
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');_,_,fingerprint=self._import_snapshot(db,identity)
                if fingerprint!=plan['fingerprint']:
                    self._reviews.pop(token,None);raise ValueError('记忆或档案已再次变化，请重新预览冲突处理。')
                for change in plan['changes']:
                    row=change['after'];fields={k:row[k] for k in ('status','status_reason','event_date')}
                    if any(row[k]!=change['before'][k] for k in fields):
                        self._write_memory(db,identity,row['content'],row['id'],row['source'],fields)
                        self._mark_history(db,row['id'],'conflict_'+plan['action']);count+=1
            self._reviews.pop(token,None)
        return {'changed':count>0,'updated':count,'action':plan['action']}
