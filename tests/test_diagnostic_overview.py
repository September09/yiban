"""Unified diagnostics use synthetic sources and temporary databases only."""
import io
import json
import threading
import unittest
import urllib.request
import uuid
from unittest.mock import patch
from pathlib import Path
import test_agent_audit as fixture
import test_shared_http as http_fixture
from yiban.conversation_diagnostics import TRACES, CURRENT, TraceStore, measured, model_events
from yiban.backends.models import ModelClient, ModelHTTPError
from yiban.memory.shared_memory import SharedError
from yiban.memory.agent_audit import AgentAudit
from yiban.turns import Cancellation, Cancelled


class OverviewTests(unittest.TestCase):
    setUp=fixture.AuditTests.setUp
    tearDown=fixture.AuditTests.tearDown
    token=fixture.AuditTests.token
    uid=fixture.AuditTests.uid
    member=fixture.AuditTests.member
    payload=fixture.AuditTests.payload
    publish=fixture.AuditTests.publish
    run_request=fixture.AuditTests.run_request

    def overview(self,who='owner'):
        return self.audit.overview(self.token(who),{'space_id':self.space})['records']

    def query(self,who='owner',read=True,cite=True):
        def callback():
            context=measured('memory',self.shared.question_context,self.token(who),self.space,'蓝鲸活动集合')
            sources=context['sources']
            if sources and read:self.shared.read_question_material(self.token(who),context['guard'],sources[0])
            return {'sources':sources,'answer_source_ids':[sources[0]['id']] if sources and cite else [],'source_usage':'model_reported'}
        return self.run_request(callback,who)

    def test_candidates_read_and_actual_citations_are_distinct_and_content_free_on_disk(self):
        first=self.publish(content='蓝鲸活动十点在北门集合',source_note='虚构通知',status='valid',status_reason='虚构资料核对')
        other=self.publish(content='蓝鲸活动乙负责接待')
        result=self.query();row=self.overview()[0]
        self.assertEqual(len(row['materials']['searched']),2)
        self.assertEqual(len(row['materials']['read']),1);self.assertEqual(len(row['materials']['cited']),1)
        self.assertEqual(row['materials']['read'][0]['id'],row['materials']['cited'][0]['id'])
        self.assertTrue(row['citation_checked']);self.assertTrue(row['search_performed'])
        with self.audit.connect() as db:dump='\n'.join(db.iterdump())
        for text in ('蓝鲸','虚构通知',self.token('owner'),'source_note','content','query'):
            self.assertNotIn(text,dump)
        metadata=self.audit.list(self.token('owner'),{'space_id':self.space})['records'][0]
        self.assertNotIn('material_refs',metadata);self.assertNotIn('cited_refs',metadata)

    def test_no_results_and_no_model_are_known_zero(self):
        self.query();row=self.overview()[0]
        self.assertEqual(row['materials'],{'searched':[],'read':[],'cited':[]})
        self.assertTrue(row['search_performed']);self.assertEqual(row['models'],[])
        self.assertEqual(row['usage']['model_calls'],0);self.assertTrue(row['citation_checked'])

    def test_unknown_citations_never_promote_candidates(self):
        item=self.publish(content='蓝鲸活动在北门集合')
        self.run_request(lambda:{'sources':[item],'answer_source_ids':['unknown'],'source_usage':'model_reported'})
        row=self.overview()[0];self.assertFalse(row['citation_checked']);self.assertEqual(row['materials']['cited'],[])
        self.assertEqual(len(row['materials']['searched']),1)
        self.assertFalse(row['search_performed'],'returned context alone does not claim a search tool ran')

    def test_source_updates_deletion_and_visibility_changes_redact_ids_and_body(self):
        item=self.publish(content='蓝鲸私密活动记录',visibility='all')
        self.query('reader')
        self.assertIn('蓝鲸私密',json.dumps(self.overview('reader'),ensure_ascii=False))
        data={**item,'space_id':self.space,'content':'已改为新的安排'}
        p=self.shared.preview(self.token('owner'),data);self.shared.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True})
        row=self.overview('reader')[0];text=json.dumps(row['materials'],ensure_ascii=False)
        self.assertNotIn(item['id'],text);self.assertNotIn('蓝鲸私密',text);self.assertIn('已更新',text)
        with self.store.connect() as db:record=self.shared._record(db,item['id'])
        p=self.shared.preview(self.token('owner'),{**record,'space_id':self.space,'visibility':'selected','viewers':[]})
        self.shared.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True})
        self.assertNotIn(item['id'],json.dumps(self.overview('reader')))
        self.shared.delete_memory(self.token('owner'),{'space_id':self.space,'id':item['id'],'version':record['version']+1,'confirmed':True})
        self.assertIn('不可访问',json.dumps(self.overview('reader'),ensure_ascii=False))

    def test_own_account_space_and_final_permission_guard(self):
        self.query('editor');self.assertEqual(self.overview('owner'),[])
        with self.assertRaises(SharedError):self.overview('outsider')
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('editor')},remove=True)
        with self.assertRaises(SharedError):self.overview('editor')
        self.query()
        with patch.object(self.shared,'validate_answer',side_effect=SharedError('changed',409)):
            with self.assertRaises(SharedError):self.overview()

    def test_live_candidates_and_failure_survive_agent_error_without_exception_text(self):
        self.publish(content='蓝鲸活动十点在北门集合');entered=threading.Event();release=threading.Event();results=[]
        def callback():
            self.shared.question_context(self.token('owner'),self.space,'蓝鲸活动集合');entered.set();release.wait(3)
            def fail():raise ModelHTTPError(429)
            return measured('model_wait',fail)
        def run():
            try:self.run_request(callback)
            except ModelHTTPError:results.append('expected')
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(2));row=self.overview()[0]
            self.assertEqual(row['status'],'running');self.assertEqual(len(row['materials']['searched']),1)
        finally:release.set();thread.join()
        row=self.overview()[0];self.assertEqual(row['failure'],{'code':'model_http','stage':'model_wait','http_status':429})
        self.assertEqual(len(row['materials']['searched']),1);self.assertEqual(row['materials']['cited'],[])
        self.assertEqual(results,['expected'])

    def test_model_identity_records_actual_service_without_url_credentials_or_prompt(self):
        client=ModelClient({'backend':'ollama','base_url':'http://127.0.0.1:11434/private?token=HIDDEN','model':'test-qwen','vision_model':'test-vision','max_tokens':10,'temperature':0.1})
        def callback():
            with patch.object(client,'stream_request',return_value=io.BytesIO(b'{"done":true,"prompt_eval_count":5,"eval_count":2}\n')):
                list(model_events(client.chat([{'role':'user','content':'PRIVATE QUESTION'}])))
            return {}
        self.run_request(callback);row=self.overview()[0]
        self.assertEqual(row['models'],[{'model':'test-qwen','backend':'ollama','route':'local_service'}])
        self.assertEqual(row['usage']['model_calls'],1)
        for private in ('HIDDEN','PRIVATE QUESTION','127.0.0.1','base_url'):self.assertNotIn(private,json.dumps(row))

    def test_public_trace_never_returns_private_source_identifiers(self):
        store=TraceStore();key=store.begin(str(uuid.uuid4()))
        store.materials(key,[{'id':'PRIVATE_SOURCE_ID','version':2,'content':'PRIVATE BODY'}])
        self.assertNotIn('PRIVATE',json.dumps(store.read(key)))
        self.assertEqual(store.read(key,private=True)['material_refs']['searched'],[{'id':'PRIVATE_SOURCE_ID','version':2}])
        store.failure(key,ConnectionRefusedError('PRIVATE RAW ERROR'),'model_wait')
        self.assertNotIn('PRIVATE',json.dumps(store.read(key)))
        self.assertEqual(store.read(key)['failure']['code'],'model_connection')

    def test_old_diagnostics_are_unknown_and_restart_retains_only_identifiers(self):
        self.publish(content='蓝鲸活动在北门');self.query()
        self.audit=AgentAudit(self.shared,self.audit.path)
        self.assertTrue(self.overview()[0]['materials']['searched'][0]['available'])
        with self.audit.connect() as db:db.execute("UPDATE events SET details='{}'")
        row=self.overview()[0];self.assertIsNone(row['materials']);self.assertNotIn('citation_checked',row)

    def test_timeout_cancel_and_protocol_failures_are_sanitized(self):
        from yiban.memory.read_only_agent import AgentProtocolError
        for error,code in [(TimeoutError('PRIVATE'),'timeout'),(Cancelled('PRIVATE'),'cancelled'),(AgentProtocolError('PRIVATE'),'tool_protocol')]:
            def fail():raise error
            with self.assertRaises(type(error)):self.run_request(lambda:measured('routing',fail))
            row=self.overview()[0];self.assertEqual(row['failure']['code'],code);self.assertNotIn('PRIVATE',json.dumps(row))


    def test_agent_deadline_is_reported_as_timeout_instead_of_user_cancellation(self):
        def callback():
            TRACES.failure(CURRENT.get(),Cancelled(),'model_wait')
            return {'agent':{'status':'timeout'},'sources':[],'answer_source_ids':[],'source_usage':'not_needed'}
        self.run_request(callback)
        row=self.overview()[0]
        self.assertEqual(row['status'],'timeout');self.assertEqual(row['reason'],'timeout')
        self.assertEqual(row['failure']['code'],'timeout')


class OverviewHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def test_program_reply_marks_rules_separately_from_memory_commands(self):
        def post(path,data):
            req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:return json.loads(response.read().splitlines()[0])
        clock=post('/api/chat',{'messages':[{'role':'user','content':'今天几号'}]})
        command=post('/api/chat',{'messages':[{'role':'user','content':'你记得什么'}]})
        self.assertEqual(clock['reply_mode'],'rule');self.assertEqual(command['reply_mode'],'memory_command')

    def test_overview_covers_plain_text_and_agent_failures_and_requires_login(self):
        owner,reader,space,_=self.setup_space('overview')
        self.assertEqual(self.post('/diagnostics/overview',{'space_id':space})[0],401)
        space=self.post('/spaces/create',{'name':'无资料的诊断空间','kind':'team'},owner)[1]['id']
        self.post('/members/set',{'space_id':space,'name':'readeroverview','role':'reader'},owner)
        self.assertEqual(self.post('/ask',{'space_id':space,'query':'本空间有什么记忆'},owner)[0],200)
        status,result=self.post('/diagnostics/overview',{'space_id':space},owner)
        self.assertEqual(status,200);self.assertEqual(len(result['records']),1)
        self.assertEqual(result['records'][0]['usage']['model_calls'],0)
        self.assertEqual(result['records'][0]['materials']['searched'],[])
        self.assertEqual(self.post('/diagnostics/overview',{'space_id':space},reader)[1]['records'],[])
        self.assertEqual(self.post('/agent/memory/confirm',{'space_id':space,'token':'missing','confirmed':True},owner)[0],409)
        row=self.post('/diagnostics/overview',{'space_id':space},owner)[1]['records'][0]
        self.assertEqual(row['status'],'rejected');self.assertEqual(row['reason'],'stale')
        self.assertEqual(self.post('/diagnostics/overview',[],owner)[0],400)


if __name__=='__main__':unittest.main()
