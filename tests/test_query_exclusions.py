"""Old records remain inspectable but explicitly excluded IDs cannot reach any query."""
import unittest
from types import SimpleNamespace
import test_shared_memory as fixture
from yiban.memory.shared_memory import SharedError
from yiban.memory.unified_memory import UnifiedMemory

class ExclusionTests(unittest.TestCase):
    setUp=fixture.SharedMemoryTests.setUp;tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token;uid=fixture.SharedMemoryTests.uid;member=fixture.SharedMemoryTests.member
    payload=fixture.SharedMemoryTests.payload;publish=fixture.SharedMemoryTests.publish
    def test_exclusion_filters_legacy_and_automatic_context_without_hiding_history(self):
        first=self.publish(content='图片旧资料记录了社团活动。')
        second=self.publish(content='其他历史资料记录了读书活动。')
        with self.store.connect() as db:db.execute('insert into shared_query_exclusions(memory,reason) values (?,?)',(first['id'],'原始图片批次停止查询'))
        self.assertEqual(len(self.shared.memories(self.token('owner'),self.space)['memories']),2)
        context=self.shared.question_context(self.token('owner'),self.space,'活动',include_outdated=True)
        self.assertNotIn(first['id'],[r['id'] for r in context['sources']])
        auto=UnifiedMemory(self.shared,SimpleNamespace(),None).snapshot(self.token('owner'),None,True)
        ids={r['record_id'] for r in auto['items']}
        self.assertNotIn(first['id'],ids);self.assertIn(second['id'],ids)
    def test_old_selected_source_cannot_be_read_after_exclusion(self):
        row=self.publish(content='图片旧资料记录了活动。')
        context=self.shared.question_context(self.token('owner'),self.space,'活动')
        source=context['sources'][0]
        with self.store.connect() as db:db.execute('insert into shared_query_exclusions(memory,reason) values (?,?)',(row['id'],'已改用MD'))
        with self.assertRaises(SharedError) as err:self.shared.read_question_material(self.token('owner'),context['guard'],source)
        self.assertEqual(err.exception.status,409)
