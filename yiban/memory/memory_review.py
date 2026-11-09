"""Auditable changes and explicit, snapshot-bound review of possible conflicts."""
import re
import secrets
import time


def fact_key(text):
    text = re.sub(r'\s+', '', text).rstrip('。.!！?？')
    style = re.fullmatch(r'我喜欢(简短|详细)(?:的)?回答', text)
    if style:
        return ('回答风格', style[1])
    preference = re.fullmatch(r'我(不喜欢|喜欢)([^，。；;！？!?]{1,80})', text)
    if preference:
        return ('喜好:' + preference[2], preference[1])
    field = re.fullmatch(r'(?:我的)?(称呼|居住城市|回答语言|回答风格)[：:](.{1,80})', text)
    if field:
        return (field[1], field[2])
    return None


def possible_conflicts(content, rows, exclude=None):
    fact = fact_key(content)
    if not fact:
        return []
    result = []
    for row in rows:
        other = fact_key(row['content'])
        if row['id'] != exclude and other and fact[0] == other[0] and fact[1] != other[1]:
            result.append({'id':row['id'], 'content':row['content'], 'reason':'同一事项存在不同或相反表述'})
    return result


class MemoryReviewMixin:
    @staticmethod
    def init_history(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                memory TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                operation TEXT NOT NULL,
                before_content TEXT,
                after_content TEXT NOT NULL,
                changed TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS memory_history_lookup ON memory_history(profile,memory,id);
            INSERT INTO memory_history(profile,memory,operation,after_content)
                SELECT profile,id,'baseline',content FROM memories m
                WHERE NOT EXISTS(SELECT 1 FROM memory_history h WHERE h.memory=m.id);
            CREATE TRIGGER IF NOT EXISTS memory_created AFTER INSERT ON memories
            BEGIN
                INSERT INTO memory_history(profile,memory,operation,after_content)
                    VALUES(NEW.profile,NEW.id,'created',NEW.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_edited AFTER UPDATE OF content ON memories
            WHEN OLD.content != NEW.content
            BEGIN
                INSERT INTO memory_history(profile,memory,operation,before_content,after_content)
                    VALUES(NEW.profile,NEW.id,'edited',OLD.content,NEW.content);
            END;
        """)

    def history(self, identity, memory_id):
        self.profile(identity)
        with self.connect() as db:
            db.execute('BEGIN')
            current = db.execute('SELECT id,content FROM memories WHERE id=? AND profile=?', (memory_id,identity)).fetchone()
            if not current:
                raise ValueError('这条记忆不存在或不属于当前用户。')
            total = db.execute('SELECT COUNT(*) FROM memory_history WHERE profile=? AND memory=?', (identity,memory_id)).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                'SELECT id,operation,before_content,after_content,changed FROM memory_history WHERE profile=? AND memory=? ORDER BY id DESC LIMIT 100',
                (identity,memory_id))]
        return {'memory':dict(current), 'entries':rows, 'total':total}

    @staticmethod
    def _mark_history(db, memory_id, operation):
        db.execute('UPDATE memory_history SET operation=? WHERE id=(SELECT MAX(id) FROM memory_history WHERE memory=?)', (operation,memory_id))

    def request_save(self, identity, content, memory_id=None, expected_revision=None, *, source='manual'):
        self.profile(identity)
        if not isinstance(content,str) or not 1 <= len(content.strip()) <= 1200:
            raise ValueError('记忆内容需为 1–1200 个字。')
        if source not in {'manual','conversation'}:
            raise ValueError('记忆来源不正确。')
        content = content.strip()
        with self._import_lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                profile, rows, fingerprint = self._import_snapshot(db,identity)
                original = next((row for row in rows if row['id']==memory_id),None) if memory_id else None
                if memory_id and original is None:
                    raise ValueError('这条记忆已删除或不属于当前用户，请重新选择。')
                duplicate = next((row for row in rows if row['content']==content),None)
                if duplicate:
                    if memory_id and duplicate['id'] != memory_id:
                        raise ValueError('相同内容已经存在，请保留已有条目。')
                    return {'id':duplicate['id'], 'changed':False}
                conflicts = possible_conflicts(content,rows,exclude=memory_id)
                if not original and not conflicts:
                    return {'id':self._write_memory(db,identity,content,None,source), 'changed':True}
                now = time.monotonic()
                self._reviews = {key:value for key,value in self._reviews.items() if value['expires']>now}
                if len(self._reviews)>=128:
                    self._reviews.pop(next(iter(self._reviews)))
                token = secrets.token_urlsafe(32)
                allowed = {row['id'] for row in conflicts}
                if original:
                    allowed.add(original['id'])
                self._reviews[token] = {'identity':identity,'content':content,'source':source,'fingerprint':fingerprint,
                                        'allowed':allowed,'expires':now+600,'original':memory_id}
                return {'review':{'token':token,'target':profile,'content':content,'original':original,
                    'conflicts':conflicts,'stale':bool(original and expected_revision!=original['revision']),
                    'can_append':len(rows)<200,'expires_in_seconds':600},'changed':False}

    def confirm_save(self, identity, token, confirmed, resolution, replace_id=None):
        if confirmed is not True:
            raise ValueError('请核对差异并勾选确认。')
        if not isinstance(token,str) or not token or len(token)>128:
            raise ValueError('确认预览无效，请重新保存以生成预览。')
        with self._import_lock:
            plan = self._reviews.get(token)
            if plan is None or plan['expires']<=time.monotonic():
                self._reviews.pop(token,None)
                raise ValueError('预览已过期或已使用，请重新保存以生成预览。')
            if identity!=plan['identity']:
                raise ValueError('当前用户已改变，请重新预览。')
            if resolution not in {'append','replace'} or (resolution=='replace' and (not isinstance(replace_id,str) or replace_id not in plan['allowed'])):
                raise ValueError('请选择预览中允许的处理方式。')
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _, _, fingerprint = self._import_snapshot(db,identity)
                if fingerprint!=plan['fingerprint']:
                    self._reviews.pop(token,None)
                    raise ValueError('记忆或档案已再次变化，请重新保存、查看最新差异后确认。')
                memory_id = self._write_memory(db,identity,plan['content'],replace_id if resolution=='replace' else None,plan['source'])
                self._mark_history(db,memory_id,'confirmed_replace' if resolution=='replace' else 'confirmed_append')
            self._reviews.pop(token,None)
            return {'id':memory_id,'changed':True}
