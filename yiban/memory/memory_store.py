"""Explicit, profile-scoped memories and opt-in voiceprints; no raw audio storage."""
import json
import hashlib
import secrets
import threading
import time
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from yiban.memory.memory_review import MemoryReviewMixin, possible_conflicts
from yiban.memory.personal_state import state_fields


class MemoryStore(MemoryReviewMixin):
    def __init__(self, path):
        self._import_lock = threading.Lock()
        self._imports = {}
        self._reviews = {}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, profile TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    content TEXT NOT NULL, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(profile,content));
                CREATE TABLE IF NOT EXISTS voiceprints(profile TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                    embedding TEXT NOT NULL, model TEXT NOT NULL, enrolled TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'salutation' not in {row['name'] for row in db.execute('PRAGMA table_info(profiles)')}:
                db.execute("ALTER TABLE profiles ADD COLUMN salutation TEXT NOT NULL DEFAULT ''")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(memories)')}
            # Old rows retain unknown creation time/source; never invent history.
            if 'created' not in columns:
                db.execute('ALTER TABLE memories ADD COLUMN created TEXT')
            if 'source' not in columns:
                db.execute("ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
            for column,definition in [('status',"TEXT NOT NULL DEFAULT 'pending'"),('status_reason',"TEXT NOT NULL DEFAULT ''"),('event_date',"TEXT NOT NULL DEFAULT ''")]:
                if column not in columns:db.execute('ALTER TABLE memories ADD COLUMN '+column+' '+definition)
            self.init_history(db)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def profiles(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT p.*, EXISTS(SELECT 1 FROM voiceprints v WHERE v.profile=p.id) AS enrolled FROM profiles p ORDER BY name')]

    def profile(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM profiles WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('用户档案不存在，请重新选择。')
        return dict(row)

    def create_profile(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError('请输入 1–40 个字的用户名称。')
        identity = uuid.uuid4().hex
        try:
            with self.connect() as db:
                if db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0] >= 30:
                    raise ValueError('最多保存 30 个用户档案。')
                db.execute('INSERT INTO profiles(id,name) VALUES (?,?)', (identity, name.strip()))
        except sqlite3.IntegrityError:
            raise ValueError('这个用户名称已经存在。') from None
        return self.profile(identity)

    def delete_profile(self, identity):
        with self.connect() as db:
            db.execute('DELETE FROM profiles WHERE id=?', (identity,))
        self.invalidate_personal(identity)

    def set_salutation(self, identity, salutation):
        self.profile(identity)
        if not isinstance(salutation, str) or len(salutation.strip()) > 40 or any(ord(c) < 32 for c in salutation):
            raise ValueError('称呼请使用不超过 40 个字的单行文字，例如王先生、李女士或小王。')
        with self.connect() as db:
            db.execute('UPDATE profiles SET salutation=? WHERE id=?', (salutation.strip(), identity))
        return self.profile(identity)

    def memories(self, identity):
        self.profile(identity)
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,content,created,updated,source,status,status_reason,event_date,(SELECT MAX(h.id) FROM memory_history h WHERE h.memory=memories.id) AS revision FROM memories WHERE profile=? ORDER BY updated DESC,rowid DESC', (identity,))]

    def export_memories(self, identity):
        # One read transaction; explicit allowlist excludes voiceprints and other users.
        with self.connect() as db:
            db.execute('BEGIN')
            profile = db.execute('SELECT id,name,salutation FROM profiles WHERE id=?', (identity,)).fetchone()
            if not profile:
                raise ValueError('用户档案不存在，请重新选择。')
            memories = [dict(row) for row in db.execute(
                'SELECT id,content,created,updated,source,status,status_reason,event_date FROM memories WHERE profile=? ORDER BY updated DESC,rowid DESC',
                (identity,))]
        return {'format': 'yiban-personal-memories', 'version': 1,
                'exported_at': datetime.now(timezone.utc).isoformat(), 'timestamp_timezone': 'UTC',
                'profile': dict(profile), 'memories': memories}

    @staticmethod
    def _import_document(document):
        if not isinstance(document, dict) or document.get('format') != 'yiban-personal-memories' or type(document.get('version')) is not int or document['version'] != 1:
            raise ValueError('请选择忆伴导出的 v1 文字记忆 JSON 文件。')
        if len(json.dumps(document, ensure_ascii=False).encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('导入文件不能超过 2 MB。')
        if document.get('timestamp_timezone') != 'UTC':
            raise ValueError('文件时间格式不正确，需要 UTC 时间。')
        profile = document.get('profile')
        if not isinstance(profile, dict) or not isinstance(profile.get('name'), str) or not 1 <= len(profile['name'].strip()) <= 40:
            raise ValueError('文件缺少有效的原档案名称。')
        rows = document.get('memories')
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError('文件需包含记忆列表，最多 200 条。')
        def timestamp(value, optional=False):
            if value is None and optional:
                return None
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
                raise ValueError('记忆时间格式不正确，请使用原始导出文件。')
            try:
                datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                raise ValueError('文件中存在无效的记忆时间。') from None
            return value
        cleaned = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('content'), str) or not 1 <= len(row['content'].strip()) <= 1200:
                raise ValueError('每条记忆需为 1–1200 个字，文件未导入。')
            source = row.get('source')
            if not isinstance(source, str) or source not in {'manual', 'conversation', 'legacy'}:
                raise ValueError('文件中存在未知的记忆来源。')
            created, updated = timestamp(row.get('created'), True), timestamp(row.get('updated'))
            if created and created > updated:
                raise ValueError('记忆首次保存时间不能晚于更新时间。')
            fields=state_fields(row.get('status','pending'),row.get('status_reason',''),row.get('event_date',''))
            cleaned.append({'content':row['content'].strip(), 'source':source, 'created':created, 'updated':updated,**fields})
        return profile['name'].strip(), cleaned

    @staticmethod
    def _import_snapshot(db, identity):
        if not isinstance(identity, str) or not identity:
            raise ValueError('请先选择要恢复到的用户档案。')
        profile = db.execute('SELECT id,name,salutation FROM profiles WHERE id=?', (identity,)).fetchone()
        if profile is None:
            raise ValueError('目标档案不存在，请重新选择。')
        rows = [dict(row) for row in db.execute(
            'SELECT id,content,created,updated,source,status,status_reason,event_date,(SELECT MAX(h.id) FROM memory_history h WHERE h.memory=memories.id) AS revision FROM memories WHERE profile=? ORDER BY id', (identity,))]
        fingerprint = hashlib.sha256(json.dumps([dict(profile), rows], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return dict(profile), rows, fingerprint

    def preview_import(self, identity, document):
        source_name, rows = self._import_document(document)
        with self.connect() as db:
            db.execute('BEGIN')
            profile, existing, fingerprint = self._import_snapshot(db, identity)
        known = {row['content'].strip() for row in existing}
        seen, items, additions = set(), [], []
        counts = {'new':0, 'existing':0, 'file_duplicate':0}
        conflict_rows = list(existing)
        conflict_count = 0
        for row in rows:
            content = row['content']
            status = 'file_duplicate' if content in seen else ('existing' if content in known else 'new')
            seen.add(content)
            counts[status] += 1
            conflicts = possible_conflicts(content,conflict_rows) if status=='new' and row['status']!='outdated' else []
            conflict_count += bool(conflicts)
            items.append({**row, 'memory_status':row['status'], 'status':status, 'conflicts':conflicts,'state_differs':status=='existing' and any(row[k]!=next(x for x in existing if x['content'].strip()==content)[k] for k in ('status','status_reason','event_date'))})
            if status == 'new':
                additions.append(row)
                conflict_rows.append({'id':'file:'+str(len(items)), **row})
        result = {'source_name':source_name, 'target':profile, 'total':len(rows), 'counts':counts,
                  'conflict_count':conflict_count, 'requires_conflict_confirmation':bool(conflict_count),
                  'current_count':len(existing), 'result_count':len(existing)+len(additions), 'items':items,
                  'can_import':bool(additions) and len(existing)+len(additions) <= 200, 'expires_in_seconds':600}
        if not result['can_import']:
            result['reason'] = '合并后超过每人 200 条，请先整理记忆，再重新预览。' if result['result_count'] > 200 else '没有需要新增的记忆。'
            return result
        with self._import_lock:
            now = time.monotonic()
            self._imports = {key:value for key,value in self._imports.items() if value['expires'] > now}
            if len(self._imports) >= 128:
                self._imports.pop(next(iter(self._imports)))
            token = secrets.token_urlsafe(32)
            self._imports[token] = {'identity':identity, 'fingerprint':fingerprint, 'rows':additions, 'expires':now+600, 'conflicts':bool(conflict_count)}
        result['token'] = token
        return result

    def confirm_import(self, identity, token, confirmed, acknowledge_conflicts=False):
        if confirmed is not True:
            raise ValueError('请先预览并明确确认恢复。')
        if not isinstance(token, str) or not token or len(token) > 128:
            raise ValueError('导入预览无效，请重新选择文件并预览。')
        with self._import_lock:
            plan = self._imports.get(token)
            if plan is None or plan['expires'] <= time.monotonic():
                self._imports.pop(token, None)
                raise ValueError('预览已过期或已使用，请重新预览。')
            if plan['identity'] != identity:
                raise ValueError('目标档案已改变，请重新预览。')
            if plan['conflicts'] and acknowledge_conflicts is not True:
                raise ValueError('导入中有可能冲突的内容，请核对并明确确认并存。')
            with self.connect() as db:
                # Lock the writer before checking the snapshot; all rows commit together.
                db.execute('BEGIN IMMEDIATE')
                _, existing, fingerprint = self._import_snapshot(db, identity)
                if fingerprint != plan['fingerprint']:
                    self._imports.pop(token, None)
                    raise ValueError('目标档案或记忆已变化，请重新预览后确认。')
                if len(existing) + len(plan['rows']) > 200:
                    raise ValueError('合并后超过每人 200 条，请先整理记忆。')
                for row in plan['rows']:
                    new_id = uuid.uuid4().hex
                    db.execute('INSERT INTO memories(id,profile,content,created,updated,source,status,status_reason,event_date) VALUES (?,?,?,?,?,?,?,?,?)',
                               (new_id, identity, row['content'], row['created'], row['updated'], row['source'],row['status'],row['status_reason'],row['event_date']))
                    self._mark_history(db,new_id,'imported')
            self._imports.pop(token, None)
            return {'imported':len(plan['rows']), 'total':len(existing)+len(plan['rows'])}

    def save(self, identity, content, memory_id=None, *, source='manual'):
        self.profile(identity)
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 1200:
            raise ValueError('记忆内容需为 1–1200 个字。')
        content = content.strip()
        if source not in {'manual', 'conversation'}:
            raise ValueError('记忆来源不正确。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._write_memory(db,identity,content,memory_id,source)

    def _write_memory(self,db,identity,content,memory_id,source,fields=None):
        if memory_id:
            original=db.execute('SELECT * FROM memories WHERE id=? AND profile=?',(memory_id,identity)).fetchone()
            if not original:raise ValueError('这条记忆不存在或不属于当前用户。')
            selected=fields or (state_fields(event_date=original['event_date']) if original['content']!=content else {k:original[k] for k in ('status','status_reason','event_date')})
            try:
                db.execute('UPDATE memories SET content=?,status=?,status_reason=?,event_date=?,updated=CURRENT_TIMESTAMP WHERE id=? AND profile=?',
                    (content,selected['status'],selected['status_reason'],selected['event_date'],memory_id,identity))
            except sqlite3.IntegrityError as error:
                if error.sqlite_errorcode==sqlite3.SQLITE_CONSTRAINT_UNIQUE:raise ValueError('相同内容已经存在。') from None
                raise
        else:
            existing=db.execute('SELECT id FROM memories WHERE profile=? AND content=?',(identity,content)).fetchone()
            if existing:return existing['id']
            if db.execute('SELECT COUNT(*) FROM memories WHERE profile=?',(identity,)).fetchone()[0]>=200:raise ValueError('每位用户最多保存200条记忆，请先整理。')
            selected=fields or state_fields();memory_id=uuid.uuid4().hex
            db.execute('INSERT INTO memories(id,profile,content,created,source,status,status_reason,event_date) VALUES (?,?,?,CURRENT_TIMESTAMP,?,?,?,?)',
                (memory_id,identity,content,source,selected['status'],selected['status_reason'],selected['event_date']))
        self.invalidate_personal(identity,memory_id)
        return memory_id

    def delete_memory(self, identity, memory_id, expected_revision=None):
        self.profile(identity)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if expected_revision is not None:
                actual = db.execute('SELECT MAX(id) FROM memory_history WHERE profile=? AND memory=?',(identity,memory_id)).fetchone()[0]
                if actual != expected_revision:
                    raise ValueError('这条记忆已变化，请刷新列表后再决定是否删除。')
            deleted=bool(db.execute('DELETE FROM memories WHERE id=? AND profile=?', (memory_id, identity)).rowcount)
        self.invalidate_personal(identity,memory_id)
        return deleted

    def command(self, identity, text):
        """Only explicit commands write data; ordinary/model text never writes."""
        text = text.strip()
        save = re.fullmatch(r'(?:请)?(?:帮我)?记住[：:,，\s]*(.+)', text, re.S)
        forget = re.fullmatch(r'(?:请)?(?:帮我)?忘(?:掉|记)[：:,，\s]*(.+)', text, re.S)
        recall = text.rstrip('。！？?！') in {'你记得什么', '查看记忆', '我的记忆'}
        if not (save or forget or recall):
            return None
        if not identity:
            return '请先在「个人记忆」中创建或选择你的档案，再保存或查看长期记忆。'
        if save:
            result = self.request_save(identity,save[1],source='conversation')
            if result.get('review'):
                return {'reply':'发现可能冲突的记忆，尚未保存。请在「个人记忆」核对差异，选择并存或替换后确认。','review':result['review']}
            return '已保存到你的长期记忆（待核实；相同内容沿用已有状态）：' + save[1]
        items = self.memories(identity)
        if recall:
            return '你保存的记忆：\n' + '\n'.join('【'+{'pending':'待核实','valid':'有效（档案标记）','outdated':'过时'}[item['status']]+'】'+item['content'] for item in items[:20]) if items else '你还没有保存长期记忆。'
        query = forget[1].strip().rstrip('。')
        matches = [item for item in items if item['content'].rstrip('。') == query]
        if len(matches) != 1:
            return '没有找到完全匹配的记忆，请在「个人记忆」列表里选择要删除的那一条。'
        self.delete_memory(identity,matches[0]['id'],matches[0]['revision'])
        return '这条长期记忆已删除。'

    def identity_reply(self, identity, text):
        text = re.sub(r'[\s，。！？、,:：.!?]', '', text)
        identity_questions = {
            '我是谁', '你知道我是谁吗', '你知道我是谁', '你还记得我是谁吗',
            '你记得我是谁吗', '你认识我吗', '你认得我吗', '你认出我了吗',
            '我叫什么', '我叫什么名字', '你知道我叫什么名字吗',
            '你记得我的名字吗', '当前用户是谁', '现在是谁在说话',
        }
        voice_identity_question = re.fullmatch(
            r'(?:小伴)?(?:现在你|你现在|现在|你)?(?:可以|能不能|能|可不可以)?'
            r'(?:通过|根据|凭|听)(?:这样的|我的|这段|这句|刚才的)?(?:声音|声纹|语音)(?:来)?'
            r'(?:猜猜我是谁|猜出我是谁|猜我是谁|判断我是谁|认出我|识别我|知道我是谁)(?:了)?(?:吗|么|呢)?', text,
        )
        if text not in identity_questions and not voice_identity_question:
            return None
        if not identity:
            return '这轮还没有确认你的用户档案，目前按访客处理。请查看页面的声音识别诊断；测试声纹需要通过麦克风说话，文字发送不会重新识别人。'
        # A profile may be selected manually; do not claim biometric verification.
        return f'当前对话的用户档案是「{self.profile(identity)["name"]}」。'

    def context(self, identity, query):
        from yiban.memory.personal_retrieval import PersonalMemory
        return PersonalMemory(self, getattr(self, 'personal_retriever', None)).search(identity, query)['context']

    def invalidate_personal(self, identity, memory_id=None):
        retriever=getattr(self, 'personal_retriever', None)
        if retriever:retriever.invalidate('personal:'+identity, memory_id)

    def enroll(self, identity, embedding, model):
        self.profile(identity)
        with self.connect() as db:
            db.execute('INSERT INTO voiceprints(profile,embedding,model) VALUES (?,?,?) ON CONFLICT(profile) DO UPDATE SET embedding=excluded.embedding,model=excluded.model,enrolled=CURRENT_TIMESTAMP',
                       (identity, json.dumps(embedding), model))

    def forget_voice(self, identity):
        self.profile(identity)
        with self.connect() as db:
            db.execute('DELETE FROM voiceprints WHERE profile=?', (identity,))

    def voices(self, model):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT p.id,p.name,v.embedding FROM voiceprints v JOIN profiles p ON p.id=v.profile WHERE model=?', (model,))]
