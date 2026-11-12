"""Explicit, own-account answer feedback with permission-checked historical snapshots."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from yiban.memory.shared_memory import SharedError

CATEGORIES = {'correct', 'wrong', 'missing', 'citation'}
STATES = {'open', 'working', 'resolved'}
SOURCE_FIELDS = {'id', 'version', 'reference', 'content', 'status', 'status_reason',
                 'event_date', 'updated', 'author_name', 'source_note', 'has_evidence', 'evidence_source_name'}


class AnswerFeedback:
    def __init__(self, shared, path, clock=time.time):
        self.shared = shared
        self.path = Path(path)
        self.clock = clock
        self.pending = {}
        self.lock = threading.Lock()
        self.ready = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(descriptor)
            with self.connect() as db:
                db.executescript('''CREATE TABLE IF NOT EXISTS feedback(
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, space TEXT NOT NULL,
                    category TEXT NOT NULL, note TEXT NOT NULL, state TEXT NOT NULL,
                    resolution TEXT NOT NULL, version INTEGER NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, snapshot TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS feedback_owner ON feedback(actor,space,created DESC);''')
            self.path.chmod(0o600)
            self.ready = True
        except (OSError, SharedError):
            pass

    @contextmanager
    def connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=2)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error:
            raise SharedError('反馈记录暂不可用，请稍后重试。', 503) from None

    @staticmethod
    def text(value, label, maximum):
        if not isinstance(value, str) or len(value) > maximum:
            raise SharedError(label + '格式不正确或太长。')
        return value.strip()

    def offer(self, token, data, result, guard):
        """Keep a bounded, session-bound receipt in RAM until explicit submission."""
        if (not self.ready or not isinstance(result.get('answer'), str)
                or not result['answer'].strip() or result.get('memory_preview') or result.get('memory_selection')
                or (result.get('agent') or {}).get('status', 'done') != 'done'):
            return result
        sources = [{k: v for k, v in source.items() if k in SOURCE_FIELDS}
                   for source in result.get('sources', [])[:8]]
        if len(result['answer']) > 16000:
            return result
        self.shared.validate_answer(token, guard)
        receipt = secrets.token_urlsafe(32)
        snapshot = {'question': data['query'], 'answer': result['answer'], 'sources': sources,
                    'answer_source_ids': result.get('answer_source_ids', []),
                    'source_usage': result.get('source_usage', 'unavailable'),
                    'topic': (result.get('conversation') or {}).get('topic', ''),
                    'include_outdated': data.get('include_outdated') is True}
        now = self.clock()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v['expires'] > now}
            while len(self.pending) >= 128:
                self.pending.pop(next(iter(self.pending)))
            self.pending[receipt] = {'id': uuid.uuid4().hex, 'session': self.shared._token_hash(token),
                                     'guard': guard, 'expires': now + 600, 'snapshot': snapshot}
        return {**result, 'feedback_receipt': receipt}

    def prune(self, db):
        db.execute('DELETE FROM feedback WHERE created<?', (self.clock() - 90 * 86400,))
        db.execute('DELETE FROM feedback WHERE id IN '
                   '(SELECT id FROM feedback ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET 500)')

    def submit(self, token, data):
        if data.get('consent') is not True:
            raise SharedError('请先同意在本机保存本轮问答、资料快照和反馈。')
        category = data.get('category')
        if not isinstance(category, str) or category not in CATEGORIES:
            raise SharedError('请选择反馈类型。')
        note = self.text(data.get('note', ''), '反馈说明', 2000)
        receipt = data.get('receipt')
        if not isinstance(receipt, str):
            raise SharedError('请重新提问后提交反馈。', 409)
        with self.lock:
            pending = self.pending.get(receipt)
            if not pending or pending['expires'] <= self.clock():
                raise SharedError('反馈入口已过期，请重新提问。', 409)
            if pending['session'] != self.shared._token_hash(token) or pending['guard']['space'] != data.get('space_id'):
                raise SharedError('反馈入口不属于当前会话或空间。', 403)
            self.shared.validate_answer(token, pending['guard'])
            # Keep the main DB read transaction open through the feedback write.
            with self.shared.store.connect() as access:
                access.execute('BEGIN')
                actor = self.shared._actor(access, token)
                space = self.shared._member(access, actor['id'], data.get('space_id'))
                if space['version'] != pending['guard']['version']:
                    raise SharedError('资料已变化，请重新提问再反馈。', 409)
                now = self.clock()
                with self.connect() as db:
                    db.execute('INSERT OR IGNORE INTO feedback VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (pending['id'], actor['id'], space['id'], category, note, 'open', '', 1,
                                now, now, json.dumps(pending['snapshot'], ensure_ascii=False)))
                    saved = db.execute('SELECT category,note FROM feedback WHERE id=?', (pending['id'],)).fetchone()
                    if saved['category'] != category or saved['note'] != note:
                        raise SharedError('这轮反馈已经保存。请在回答反馈中查看原记录并更新处理说明。', 409)
                    self.prune(db)
        return {'id': pending['id'], 'saved': True}

    def readable(self, db, actor, space, snapshot):
        changed = False
        for source in snapshot['sources']:
            try:
                item = self.shared._record(db, source['id'])
            except SharedError:
                return False, True
            if item['space'] != space['id'] or not self.shared._visible(actor, space, item):
                return False, True
            if item['version'] != source['version']:
                changed = True
                if space['role'] != 'owner' and item['author'] != actor['id']:
                    return False, True
        return True, changed

    def dispatch(self, action, token, data):
        if not isinstance(data, dict):
            raise SharedError('请求格式不正确。')
        if not self.ready:
            raise SharedError('反馈记录暂不可用。', 503)
        if action == 'submit':
            return self.submit(token, data)
        if action not in {'list', 'detail', 'update'}:
            raise SharedError('未知反馈操作。', 404)
        with self.shared.store.connect() as access:
            access.execute('BEGIN')
            actor = self.shared._actor(access, token)
            space = self.shared._member(access, actor['id'], data.get('space_id'))
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.prune(db)
                if action == 'list':
                    rows = db.execute('SELECT id,category,state,version,created,updated FROM feedback '
                                      'WHERE actor=? AND space=? ORDER BY created DESC,rowid DESC LIMIT 50',
                                      (actor['id'], space['id'])).fetchall()
                    return {'records': [dict(row) for row in rows], 'retention_days': 90}
                row = db.execute('SELECT * FROM feedback WHERE id=? AND actor=? AND space=?',
                                 (data.get('id'), actor['id'], space['id'])).fetchone()
                if row is None:
                    raise SharedError('未找到当前账号的反馈。', 404)
                if action == 'update':
                    state = data.get('state')
                    resolution = self.text(data.get('resolution', ''), '处理说明', 2000)
                    if not isinstance(state, str) or state not in STATES or (state == 'resolved' and not resolution):
                        raise SharedError('请选择处理进度，标记已复核时请填写说明。')
                    if type(data.get('version')) is not int or row['version'] != data['version']:
                        raise SharedError('反馈已在其他页面更新，请刷新后重试。', 409)
                    db.execute('UPDATE feedback SET state=?,resolution=?,version=version+1,updated=? WHERE id=?',
                               (state, resolution, self.clock(), row['id']))
                    return {'updated': True}
                snapshot = json.loads(row['snapshot'])
                allowed, changed = self.readable(access, actor, space, snapshot)
                result = {k: row[k] for k in ('id', 'category', 'state', 'version', 'created', 'updated')}
                result.update(content_available=allowed, sources_changed=changed)
                if allowed:
                    result.update(snapshot=snapshot, note=row['note'], resolution=row['resolution'])
                return result
