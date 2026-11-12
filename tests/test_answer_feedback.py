"""Explicit feedback receipts, durable replay and current authorization boundaries."""
import json
import unittest
from unittest.mock import patch

import test_read_only_agent as fixture
import test_shared_http as http_fixture
from yiban.memory.answer_feedback import AnswerFeedback
from yiban.memory.shared_memory import SharedError


class AnswerFeedbackTests(unittest.TestCase):
    setUp = fixture.AgentTests.setUp
    tearDown = fixture.AgentTests.tearDown
    token = fixture.AgentTests.token
    uid = fixture.AgentTests.uid
    member = fixture.AgentTests.member
    payload = fixture.AgentTests.payload
    publish = fixture.AgentTests.publish
    newest = fixture.AgentTests.newest

    def edit(self, item, **changes):
        data=self.payload(**{key:item[key] for key in ('id','version','content','visibility','viewers','edit_policy','event_date','source_note','status','status_reason')})
        data.update(changes)
        plan=self.shared.preview(self.token('owner'),data)
        self.shared.confirm(self.token('owner'),{'space_id':self.space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})

    def service(self):
        return AnswerFeedback(self.shared, self.store.path.parent / 'feedback-test.sqlite3')

    def offer(self, service, who='owner'):
        token = self.token(who)
        guard = self.shared.question_guard(token, self.space)
        sources = self.shared.question_context(token, self.space, '读书会在哪里集合')['sources']
        result = {'answer': '在青松图书馆。', 'sources': sources,
                  'answer_source_ids': [source['id'] for source in sources], 'source_usage': 'model_reported'}
        return service.offer(token, {'space_id': self.space, 'query': '读书会在哪里集合'}, result, guard)

    def submit(self, service, offered, who='owner', **extra):
        return service.dispatch('submit', self.token(who), {'space_id': self.space,
            'receipt': offered['feedback_receipt'], 'category': 'wrong', 'note': '地点需要核实', 'consent': True, **extra})

    def detail(self, service, identity, who='owner'):
        return service.dispatch('detail', self.token(who), {'space_id': self.space, 'id': identity})

    def error(self, status, fn, *args, **kwargs):
        with self.assertRaises(SharedError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def test_explicit_save_authoritative_snapshot_and_idempotent_retry(self):
        self.publish(content='读书会在青松图书馆集合。', visibility='all')
        service = self.service()
        offered = self.offer(service)
        self.assertEqual(service.dispatch('list', self.token('owner'), {'space_id': self.space})['records'], [])
        self.error(400, self.submit, service, offered, consent=False)
        first = self.submit(service, offered, answer='伪造回答', sources=[])
        self.assertEqual(self.submit(service, offered)['id'], first['id'])
        self.error(409, self.submit, service, offered, note='改成其他反馈')
        detail = self.detail(service, first['id'])
        self.assertEqual(detail['snapshot']['answer'], '在青松图书馆。')
        self.assertNotIn('伪造回答', json.dumps(detail, ensure_ascii=False))
        self.assertEqual(len(service.dispatch('list', self.token('owner'), {'space_id': self.space})['records']), 1)
        reopened = self.service()
        self.assertEqual(self.detail(reopened, first['id'])['note'], '地点需要核实')

    def test_receipt_session_space_expiry_and_stale_guards(self):
        service = self.service(); offered = self.offer(service)
        self.error(403, self.submit, service, offered, who='reader')
        self.error(403, self.submit, service, offered, space_id='other')
        second = self.shared.login({'name': 'owner', 'password': 'test-password-2026'})['token']
        self.error(403, service.dispatch, 'submit', second, {'space_id': self.space,
            'receipt': offered['feedback_receipt'], 'category': 'correct', 'consent': True})
        service.pending[offered['feedback_receipt']]['expires'] = 0
        self.error(409, self.submit, service, offered)
        offered = self.offer(service)
        self.publish(content='空间新增资料。')
        self.error(409, self.submit, service, offered)

    def test_own_account_only_and_removed_members_cannot_read(self):
        service = self.service(); identity = self.submit(service, self.offer(service, 'reader'), 'reader')['id']
        self.error(404, self.detail, service, identity, 'owner')
        self.assertEqual(service.dispatch('list', self.token('owner'), {'space_id': self.space})['records'], [])
        self.shared.set_member(self.token('owner'), {'space_id': self.space, 'account_id': self.uid('reader')}, remove=True)
        self.error(403, self.detail, service, identity, 'reader')

    def test_changed_reader_sources_are_redacted_but_author_can_review_history(self):
        item = self.publish(content='读书会在青松图书馆集合。', visibility='all')
        service = self.service()
        reader_id = self.submit(service, self.offer(service, 'reader'), 'reader')['id']
        owner_id = self.submit(service, self.offer(service))['id']
        self.edit(item, content='读书会在白杨书屋集合。')
        hidden = self.detail(service, reader_id, 'reader')
        self.assertFalse(hidden['content_available'])
        self.assertNotIn('青松图书馆', json.dumps(hidden, ensure_ascii=False))
        self.assertNotIn('note', hidden)
        visible = self.detail(service, owner_id)
        self.assertTrue(visible['sources_changed'])
        self.assertIn('青松图书馆', json.dumps(visible, ensure_ascii=False))

    def test_revoked_source_visibility_and_deleted_sources_hide_snapshots(self):
        item = self.publish(content='读书会在青松图书馆集合。', visibility='all')
        service = self.service(); identity = self.submit(service, self.offer(service, 'reader'), 'reader')['id']
        self.edit(item, visibility='selected', viewers=[])
        self.assertFalse(self.detail(service, identity, 'reader')['content_available'])
        owner_id = self.submit(service, self.offer(service))['id']
        item = self.shared.memories(self.token('owner'), self.space)['memories'][0]
        self.shared.delete_memory(self.token('owner'), {'space_id': self.space, 'id': item['id'], 'version': item['version'], 'confirmed': True})
        self.assertFalse(self.detail(service, owner_id)['content_available'])

    def test_no_receipt_for_write_previews_or_failed_answers_and_private_file(self):
        service = self.service()
        guard = self.shared.question_guard(self.token('owner'), self.space)
        data = {'space_id': self.space, 'query': '保存测试'}
        for result in ({'answer': '预览', 'memory_preview': {'token': 'private'}},
                       {'answer': '未完成', 'agent': {'status': 'failed'}}, {'answer': ''}):
            self.assertNotIn('feedback_receipt', service.offer(self.token('owner'), data, result, guard))
        self.assertEqual(service.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(service.pending, {})

    def test_progress_is_version_checked_and_requires_resolution(self):
        service = self.service(); identity = self.submit(service, self.offer(service))['id']
        update = {'space_id': self.space, 'id': identity, 'version': 1, 'state': 'resolved', 'resolution': ''}
        self.error(400, service.dispatch, 'update', self.token('owner'), update)
        update['resolution'] = '已核对来源，重新查询正确。'
        service.dispatch('update', self.token('owner'), update)
        self.error(409, service.dispatch, 'update', self.token('owner'), update)
        self.assertEqual(self.detail(service, identity)['version'], 2)
        self.assertEqual(self.detail(service, identity)['state'], 'resolved')
        service.clock = lambda: 10**12
        self.assertEqual(service.dispatch('list', self.token('owner'), {'space_id': self.space})['records'], [])


class FeedbackHTTPTests(unittest.TestCase):
    setUpClass = classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post = http_fixture.SharedHTTPTests.post
    setup_space = http_fixture.SharedHTTPTests.setup_space

    def test_http_answer_receipt_save_and_detail(self):
        owner, reader, space, _ = self.setup_space('feedback')
        self.assertEqual(self.post('/feedback/list', {'space_id': space})[0], 401)
        self.assertEqual(self.post('/feedback/list', [], owner)[0], 400)
        with patch.object(self.app.SERVICES.models, 'chat', return_value=(event for event in [
            {'message': {'content': '{"answer":"公开内容","references":[1]}'}, 'done': True}])):
            status, result = self.post('/ask', {'space_id': space, 'query': '成员可见的公开内容'}, owner)
        self.assertEqual(status, 200, result)
        self.assertIn('feedback_receipt', result)
        status, saved = self.post('/feedback/submit', {'space_id': space, 'receipt': result['feedback_receipt'],
            'category': 'correct', 'consent': True}, owner)
        self.assertEqual(status, 200)
        self.assertEqual(self.post('/feedback/detail', {'space_id': space, 'id': saved['id']}, reader)[0], 404)
        detail = self.post('/feedback/detail', {'space_id': space, 'id': saved['id']}, owner)[1]
        self.assertEqual(detail['snapshot']['question'], '成员可见的公开内容')


if __name__ == '__main__':
    unittest.main()
