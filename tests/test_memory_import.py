"""Import is previewed, bound to a profile/snapshot, and committed atomically."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore


class MemoryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name)/'memory.sqlite3')
        self.a = self.store.create_profile('甲')['id']
        self.b = self.store.create_profile('乙')['id']
        self.store.save(self.a, '原有内容')
        self.store.enroll(self.a, [1,0,0], 'voice-model')
        self.store.set_salutation(self.a, '原称呼')

    def tearDown(self):
        self.temp.cleanup()

    def document(self, *contents):
        result = self.store.export_memories(self.a)
        result['profile'].update(id=self.b, name='文件原档案', salutation='不应导入的称呼')
        result['memories'] = [dict(id='same-id', content=c, created='2025-01-01 00:00:00',
            updated='2025-01-02 00:00:00', source='conversation') for c in contents]
        result['voiceprints'] = [{'embedding':[0,1,0]}]
        return result

    def test_preview_no_write_counts_and_merge_preserves_other_data(self):
        before = self.store.path.read_bytes()
        plan = self.store.preview_import(self.a, self.document(' 原有内容 ', '新内容', '新内容', '另一条'))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(plan['counts'], {'new':2, 'existing':1, 'file_duplicate':1})
        self.assertEqual(plan['result_count'], 3)
        result = self.store.confirm_import(self.a, plan['token'], True)
        self.assertEqual(result, {'imported':2, 'total':3})
        self.assertEqual(self.store.profile(self.a)['salutation'], '原称呼')
        self.assertEqual(self.store.voices('voice-model')[0]['embedding'], '[1, 0, 0]')
        self.assertEqual(self.store.memories(self.b), [])
        new = next(m for m in self.store.memories(self.a) if m['content']=='新内容')
        self.assertEqual(new['created'], '2025-01-01 00:00:00')
        self.assertEqual(new['source'], 'conversation')
        self.assertNotEqual(new['id'], 'same-id')
        again = self.store.preview_import(self.a, self.document('原有内容', '新内容', '另一条'))
        self.assertFalse(again['can_import'])
        self.assertNotIn('token', again)

    def test_confirmation_target_and_single_use_required(self):
        plan = self.store.preview_import(self.a, self.document('新增'))
        for consent in (False, None, 1, 'true'):
            with self.assertRaises(ValueError):self.store.confirm_import(self.a, plan['token'], consent)
        with self.assertRaises(ValueError):self.store.confirm_import(self.b, plan['token'], True)
        with self.assertRaises(ValueError):self.store.confirm_import(self.a, 'made-up', True)
        self.assertEqual(len(self.store.memories(self.a)), 1)
        self.store.confirm_import(self.a, plan['token'], True)
        with self.assertRaises(ValueError):self.store.confirm_import(self.a, plan['token'], True)

    def test_expired_and_restart_preview_cannot_write(self):
        with patch('yiban.memory.memory_store.time.monotonic', return_value=10):
            plan = self.store.preview_import(self.a, self.document('新增'))
        with patch('yiban.memory.memory_store.time.monotonic', return_value=611):
            with self.assertRaisesRegex(ValueError, '过期'):self.store.confirm_import(self.a, plan['token'], True)
        plan = self.store.preview_import(self.a, self.document('新增'))
        reopened = MemoryStore(self.store.path)
        with self.assertRaises(ValueError):reopened.confirm_import(self.a, plan['token'], True)
        self.assertEqual(len(self.store.memories(self.a)), 1)

    def test_changed_database_or_profile_requires_new_preview(self):
        for change in ('edit','add','name'):
            plan = self.store.preview_import(self.a, self.document('待导入'))
            if change=='edit':self.store.save(self.a, '已修正', self.store.memories(self.a)[0]['id'])
            elif change=='add':self.store.save(self.a, '其他窗口新增')
            else:self.store.set_salutation(self.a, '新称呼')
            with self.assertRaisesRegex(ValueError, '变化'):self.store.confirm_import(self.a, plan['token'], True)
            self.assertNotIn('待导入', [m['content'] for m in self.store.memories(self.a)])

    def test_invalid_document_fails_without_partial_write(self):
        good = self.document('合法内容', '第二条')
        invalid = [None, [], {}, {**good,'version':True}, {**good,'version':2},
                   {**good,'timestamp_timezone':'local'}, {**good,'profile':{}},
                   {**good,'memories':[None]}, {**good,'memories':good['memories']*101},
                   {**good,'extra':'a'*(2*1024*1024)}]
        for field,value in [('content',' '),('content','a'*1201),('content',3),('source',[]),
                            ('source','unknown'),('updated','2025-02-30 00:00:00'),
                            ('updated','2025-01-01T00:00:00Z'),('created','2026-01-01 00:00:00')]:
            bad=copy.deepcopy(good);bad['memories'][1][field]=value;invalid.append(bad)
        before=self.store.path.read_bytes()
        for doc in invalid:
            with self.subTest(document_type=type(doc).__name__):
                with self.assertRaises(ValueError):self.store.preview_import(self.a, doc)
        self.assertEqual(self.store.path.read_bytes(),before)
        for target in ('',None,[], 'missing'):
            with self.assertRaises(ValueError):self.store.preview_import(target,good)

    def test_limit_blocks_whole_batch_and_empty_is_noop(self):
        for n in range(198):self.store.save(self.a, f'已有{n}')
        plan=self.store.preview_import(self.a,self.document('新1','新2'))
        self.assertFalse(plan['can_import']);self.assertEqual(plan['result_count'],201)
        self.assertNotIn('token',plan)
        plan=self.store.preview_import(self.a,self.document('新1','原有内容'))
        self.store.confirm_import(self.a,plan['token'],True)
        self.assertEqual(len(self.store.memories(self.a)),200)
        self.assertFalse(self.store.preview_import(self.a,self.document())['can_import'])

    def test_failure_rolls_back_all_rows(self):
        plan=self.store.preview_import(self.a,self.document('first','second'))
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON memories WHEN NEW.content='second' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.confirm_import(self.a,plan['token'],True)
        self.assertEqual([m['content'] for m in self.store.memories(self.a)],['原有内容'])
        with self.store.connect() as db:db.execute('DROP TRIGGER reject_second')
        self.assertEqual(self.store.confirm_import(self.a,plan['token'],True)['imported'],2)

    def test_concurrent_confirmation_only_imports_once(self):
        plan=self.store.preview_import(self.a,self.document('新增'))
        def confirm():
            try:return self.store.confirm_import(self.a,plan['token'],True)['imported']
            except ValueError:return 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _:confirm(),range(2))),1)
        self.assertEqual(len(self.store.memories(self.a)),2)

    def test_legacy_metadata_and_similar_text_are_not_invented_or_merged(self):
        doc=self.document('喜欢茶','喜欢 茶','不喜欢茶')
        doc['memories'][0].update(created=None, source='legacy')
        plan=self.store.preview_import(self.a,doc)
        self.assertEqual(plan['counts']['new'],3)
        self.store.confirm_import(self.a,plan['token'],True)
        item=next(m for m in self.store.memories(self.a) if m['content']=='喜欢茶')
        self.assertIsNone(item['created']);self.assertEqual(item['source'],'legacy')


if __name__=='__main__':unittest.main()
