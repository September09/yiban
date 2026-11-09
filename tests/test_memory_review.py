"""History scope/deletion and explicit resolution of conflicts and concurrent edits."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore
from yiban.memory.memory_review import possible_conflicts


class MemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=MemoryStore(Path(self.temp.name)/'memory.sqlite3')
        self.a=self.store.create_profile('甲')['id'];self.b=self.store.create_profile('乙')['id']
        self.id=self.store.save(self.a,'我喜欢咖啡')
        self.store.enroll(self.a,[1,0,0],'voice')

    def tearDown(self):self.temp.cleanup()

    def plan(self,content='我不喜欢咖啡'):
        return self.store.request_save(self.a,content)['review']

    def test_history_before_after_and_no_duplicate_log(self):
        before=self.store.memories(self.a)[0]
        plan=self.store.request_save(self.a,'我喜欢茶',self.id,before['revision'])['review']
        self.assertFalse(plan['stale'])
        self.assertEqual(self.store.memories(self.a)[0]['content'],'我喜欢咖啡')
        self.store.confirm_save(self.a,plan['token'],True,'replace',self.id)
        history=self.store.history(self.a,self.id)
        self.assertEqual(history['total'],2)
        self.assertEqual(history['entries'][0]['before_content'],'我喜欢咖啡')
        self.assertEqual(history['entries'][0]['after_content'],'我喜欢茶')
        self.assertEqual(history['entries'][0]['operation'],'confirmed_replace')
        self.assertEqual(self.store.request_save(self.a,'我喜欢茶')['id'],self.id)
        self.assertEqual(self.store.history(self.a,self.id)['total'],2)
        self.assertNotIn('memory_history',str(self.store.export_memories(self.a)))
        self.assertNotIn('我喜欢咖啡',self.store.context(self.a,'我喜欢什么'))

    def test_replace_append_and_cancel_are_explicit(self):
        plan=self.plan()
        self.assertEqual(plan['conflicts'][0]['id'],self.id)
        self.assertEqual(len(self.store.memories(self.a)),1) # no confirmation: unchanged
        for confirmed in (False,None,1,'true'):
            with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],confirmed,'replace',self.id)
        with self.assertRaises(ValueError):self.store.confirm_save(self.b,plan['token'],True,'replace',self.id)
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'replace','unrelated')
        self.store.confirm_save(self.a,plan['token'],True,'append')
        self.assertEqual(len(self.store.memories(self.a)),2)
        self.assertEqual(self.store.voices('voice')[0]['id'],self.a)
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'append')

    def test_stale_editor_and_aba_conflict_require_reconfirmation(self):
        old=self.store.memories(self.a)[0]
        self.store.save(self.a,'其他窗口改动',self.id)
        stale=self.store.request_save(self.a,'新草稿',self.id,old['revision'])['review']
        self.assertTrue(stale['stale']);self.assertEqual(stale['original']['content'],'其他窗口改动')
        self.store.save(self.a,'再改一次',self.id)
        self.store.save(self.a,'其他窗口改动',self.id)
        with self.assertRaisesRegex(ValueError,'再次变化'):self.store.confirm_save(self.a,stale['token'],True,'replace',self.id)
        with self.assertRaisesRegex(ValueError,'变化'):self.store.delete_memory(self.a,self.id,old['revision'])

    def test_history_scope_and_forgetting_removes_history(self):
        self.store.save(self.a,'改后的内容',self.id)
        with self.assertRaises(ValueError):self.store.history(self.b,self.id)
        self.store.delete_memory(self.a,self.id)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_history').fetchone()[0],0)
        self.assertEqual(len(self.store.voices('voice')),1)
        other=self.store.save(self.b,'乙的内容');self.store.delete_profile(self.b)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_history WHERE memory=?',(other,)).fetchone()[0],0)

    def test_old_rows_get_one_honest_baseline(self):
        path=Path(self.temp.name)/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE);CREATE TABLE memories(id TEXT PRIMARY KEY,profile TEXT REFERENCES profiles(id) ON DELETE CASCADE,content TEXT,updated TEXT,created TEXT,source TEXT);INSERT INTO profiles VALUES('a','旧用户');INSERT INTO memories VALUES('m','a','历史内容','2020-01-01 00:00:00',NULL,'legacy');")
        store=MemoryStore(path);store=MemoryStore(path)
        history=store.history('a','m')
        self.assertEqual(history['total'],1)
        self.assertEqual(history['entries'][0]['operation'],'baseline')
        self.assertIsNone(history['entries'][0]['before_content'])
        self.assertEqual(store.memories('a')[0]['updated'],'2020-01-01 00:00:00')

    def test_history_and_write_roll_back_together(self):
        plan=self.plan()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON memory_history WHEN NEW.operation='edited' BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.confirm_save(self.a,plan['token'],True,'replace',self.id)
        self.assertEqual(self.store.memories(self.a)[0]['content'],'我喜欢咖啡')
        self.assertEqual(self.store.history(self.a,self.id)['total'],1)

    def test_expiry_and_profile_changes_invalidate_plan(self):
        with patch('yiban.memory.memory_review.time.monotonic',return_value=100):plan=self.plan()
        with patch('yiban.memory.memory_review.time.monotonic',return_value=701):
            with self.assertRaisesRegex(ValueError,'过期'):self.store.confirm_save(self.a,plan['token'],True,'append')
        plan=self.plan();self.store.set_salutation(self.a,'新称呼')
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'append')

    def test_rules_are_limited_and_do_not_confuse_different_objects(self):
        self.assertEqual(possible_conflicts('我不喜欢茶',self.store.memories(self.a)),[])
        self.assertEqual(possible_conflicts('也许以后不再爱咖啡',self.store.memories(self.a)),[])
        self.assertTrue(possible_conflicts('我喜欢详细的回答',[{'id':'x','content':'我喜欢简短回答'}]))
        self.assertTrue(possible_conflicts('居住城市：上海',[{'id':'x','content':'居住城市：北京'}]))
        self.assertEqual(self.store.request_save(self.b,'我不喜欢咖啡').get('review'),None)

    def test_import_conflicts_need_extra_consent_and_record_import_event(self):
        doc=self.store.export_memories(self.a)
        doc['memories'][0]['content']='我不喜欢咖啡'
        plan=self.store.preview_import(self.a,doc)
        self.assertEqual(plan['conflict_count'],1)
        with self.assertRaisesRegex(ValueError,'冲突'):self.store.confirm_import(self.a,plan['token'],True)
        result=self.store.confirm_import(self.a,plan['token'],True,True)
        self.assertEqual(result['imported'],1)
        new=next(m for m in self.store.memories(self.a) if m['id']!=self.id)
        self.assertEqual(self.store.history(self.a,new['id'])['entries'][0]['operation'],'imported')
        target=self.store.create_profile('空档案')['id']
        doc['memories'].append({**doc['memories'][0],'content':'我喜欢咖啡'})
        self.assertTrue(self.store.preview_import(target,doc)['requires_conflict_confirmation'])

    def test_full_capacity_allows_replace_but_not_append(self):
        for n in range(199):self.store.save(self.a,f'其他{n}')
        plan=self.plan();self.assertFalse(plan['can_append'])
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'append')
        self.store.confirm_save(self.a,plan['token'],True,'replace',self.id)
        self.assertEqual(len(self.store.memories(self.a)),200)


if __name__=='__main__':unittest.main()
