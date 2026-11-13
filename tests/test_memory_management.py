"""Memory metadata migration and profile-only exports, using disposable databases."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore


class MemoryManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'

    def tearDown(self):
        self.temp.cleanup()

    def test_old_database_migration_preserves_rows_and_unknown_history(self):
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,salutation TEXT NOT NULL DEFAULT '');
                CREATE TABLE memories(id TEXT PRIMARY KEY,profile TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(profile,content));
                CREATE TABLE voiceprints(profile TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                    embedding TEXT NOT NULL,model TEXT NOT NULL,enrolled TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO profiles VALUES ('a','测试用户','测试称呼');
                INSERT INTO memories VALUES ('old','a','旧记忆','2026-01-01 08:00:00');
                INSERT INTO voiceprints VALUES ('a','[1,0,0]','unchanged-model','2026-01-02 08:00:00');
            """)
            voice = db.execute('SELECT * FROM voiceprints').fetchall()
        store = MemoryStore(self.path)
        # Reopening must also be safe and idempotent.
        store = MemoryStore(self.path)
        item = store.memories('a')[0]
        self.assertGreater(item.pop('revision'),0)
        self.assertEqual(item, {'id':'old','content':'旧记忆','updated':'2026-01-01 08:00:00','created':None,'source':'legacy','status':'pending','status_reason':'','event_date':''})
        self.assertEqual(store.profile('a')['salutation'], '测试称呼')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT * FROM voiceprints').fetchall(), voice)
        store.save('a', '修正后的旧记忆', 'old')
        self.assertIsNone(store.memories('a')[0]['created'])
        self.assertEqual(store.memories('a')[0]['source'], 'legacy')

    def test_creation_provenance_survives_edit_and_duplicate_save(self):
        store = MemoryStore(self.path)
        a = store.create_profile('甲')['id']
        store.command(a, '记住：喜欢乌龙茶')
        first = store.memories(a)[0]
        self.assertEqual(first['source'], 'conversation')
        self.assertIsNotNone(first['created'])
        self.assertEqual(store.save(a, '喜欢乌龙茶'), first['id'])
        self.assertEqual(store.memories(a)[0], first)
        store.save(a, '改为喜欢绿茶', first['id'])
        changed = store.memories(a)[0]
        self.assertEqual(changed['source'], first['source'])
        self.assertEqual(changed['created'], first['created'])
        self.assertEqual(changed['content'], '改为喜欢绿茶')
        store.save(a, '手动新增')
        self.assertEqual(store.memories(a)[0]['source'], 'manual')

    def test_export_only_current_profile_text_and_no_mutation(self):
        store = MemoryStore(self.path)
        a, b = (store.create_profile(name)['id'] for name in ('甲', '乙'))
        store.save(a, '甲的文字记忆')
        store.save(b, '乙的私人内容')
        store.enroll(a, [1,0,0], 'private-model-hash')
        before = self.path.read_bytes()
        export = store.export_memories(a)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(export['format'], 'yiban-personal-memories')
        self.assertEqual(export['version'], 1)
        self.assertEqual(export['timestamp_timezone'], 'UTC')
        self.assertEqual(set(export['profile']), {'id','name','salutation'})
        self.assertEqual([m['content'] for m in export['memories']], ['甲的文字记忆'])
        text = json.dumps(export, ensure_ascii=False)
        for secret in ('乙的私人内容','embedding','private-model-hash','voiceprints'):
            self.assertNotIn(secret, text)
        for identity in ('', None, 'missing'):
            with self.assertRaises(ValueError):
                store.export_memories(identity)
        empty = store.create_profile('空档案')['id']
        self.assertEqual(store.export_memories(empty)['memories'], [])


if __name__ == '__main__':
    unittest.main()
