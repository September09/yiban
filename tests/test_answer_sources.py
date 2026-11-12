"""Answer source metadata and on-demand evidence stay within current authorization."""
import json
import unittest
import test_shared_evidence as fixture
from yiban.memory.shared_memory import SharedError


class AnswerSourceTests(unittest.TestCase):
    setUp=fixture.EvidenceTests.setUp
    tearDown=fixture.EvidenceTests.tearDown
    token=fixture.EvidenceTests.token
    uid=fixture.EvidenceTests.uid
    member=fixture.EvidenceTests.member
    payload=fixture.EvidenceTests.payload
    publish=fixture.EvidenceTests.publish
    stage=fixture.EvidenceTests.stage
    preview=fixture.EvidenceTests.preview
    confirm=fixture.EvidenceTests.confirm
    newest=fixture.EvidenceTests.newest
    edit=fixture.EvidenceTests.edit

    def archived(self):
        plan=self.preview(self.stage()[:1],event_date='2026-11-21',source_note='测试活动纪要')
        self.confirm(plan)
        return self.newest()

    def test_source_metadata_is_complete_without_sending_full_source_to_model(self):
        item=self.archived()
        context=self.shared.question_context(self.token('reader'),self.space,'活动在哪里？')
        source=context['sources'][0]
        self.assertEqual(source['id'],item['id'])
        self.assertEqual(source['evidence_source_name'],'activity.pdf')
        self.assertEqual(source['event_date'],'2026-11-21')
        self.assertEqual(source['source_note'],'测试活动纪要')
        self.assertEqual(source['version'],item['version'])
        self.assertTrue(source['updated']);self.assertTrue(source['has_evidence'])
        self.assertNotIn('source_text',json.dumps(context))
        self.assertNotIn('私人内容',json.dumps(context,ensure_ascii=False))
        self.assertNotIn('evidence_source_name',context['context'])

    def test_missing_evidence_does_not_invent_original_or_event_date(self):
        self.publish('editor',content='活动记录写了11月21日，但日期字段未填写',source_note='群里说的')
        source=self.shared.question_context(self.token('reader'),self.space,'活动')['sources'][0]
        self.assertFalse(source['has_evidence']);self.assertEqual(source['evidence_source_name'],'')
        self.assertEqual(source['event_date'],'');self.assertEqual(source['source_note'],'群里说的')

    def test_stale_answer_source_requires_requery(self):
        item=self.archived()
        request={'space_id':self.space,'id':item['id'],'expected_version':item['version']}
        self.assertTrue(self.shared.evidence.read(self.token('reader'),request)['available'])
        item=self.edit(item,status='valid',status_reason='已核对')
        with self.assertRaises(SharedError) as error:self.shared.evidence.read(self.token('reader'),request)
        self.assertEqual(error.exception.status,409)
        request['expected_version']=item['version']
        self.assertEqual(self.shared.evidence.read(self.token('reader'),request)['memory_status'],'valid')
        request['expected_version']=True
        with self.assertRaises(SharedError):self.shared.evidence.read(self.token('reader'),request)

    def test_revoked_access_hides_metadata_and_evidence(self):
        item=self.archived()
        self.edit(item,visibility='selected',viewers=[])
        context=self.shared.question_context(self.token('reader'),self.space,'活动')
        self.assertEqual(context['sources'],[]);self.assertNotIn('activity.pdf',json.dumps(context))
        with self.assertRaises(SharedError) as error:
            self.shared.evidence.read(self.token('reader'),{'space_id':self.space,'id':item['id'],'expected_version':item['version']})
        self.assertEqual(error.exception.status,403)

    def test_full_source_remains_author_or_admin_only(self):
        item=self.archived();request={'space_id':self.space,'id':item['id'],'expected_version':item['version'],'include_full':True}
        with self.assertRaises(SharedError) as error:self.shared.evidence.read(self.token('reader'),request)
        self.assertEqual(error.exception.status,403)
        self.assertIn('私人内容',self.shared.evidence.read(self.token('editor'),request)['source_text'])


class AnswerSourcePageTests(unittest.TestCase):
    import test_personal_http as http_fixture
    setUpClass=classmethod(http_fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.PersonalHTTPTests.tearDownClass.__func__)

    def test_every_script_referenced_by_the_page_is_served(self):
        import re
        import urllib.request
        base=f'http://127.0.0.1:{self.http.server_port}'
        with urllib.request.urlopen(base) as response:page=response.read().decode()
        scripts=re.findall(r'<script src="([^"]+)"',page)
        self.assertIn('/shared-answer-sources.js',scripts)
        for path in scripts:
            with self.subTest(script=path),urllib.request.urlopen(base+path) as response:
                self.assertEqual(response.status,200)
                self.assertIn('javascript',response.headers['Content-Type'])
                self.assertTrue(response.read())
