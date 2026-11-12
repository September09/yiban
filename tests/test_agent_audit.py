"""Content-free diagnostic persistence, authorization and business isolation."""
import json
from pathlib import Path
import sqlite3
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memory_agent as memory_fixture
import test_shared_http as http_fixture
from yiban.memory.agent_audit import AgentAudit, clean_trace
from yiban.memory.shared_memory import SharedError
from yiban.conversation_diagnostics import CURRENT, TRACES, measured, model_events
from yiban.turns import Cancellation, Cancelled


class AuditTests(unittest.TestCase):
    tearDown=memory_fixture.MemoryAgentTests.tearDown
    token=memory_fixture.MemoryAgentTests.token
    uid=memory_fixture.MemoryAgentTests.uid
    member=memory_fixture.MemoryAgentTests.member
    payload=memory_fixture.MemoryAgentTests.payload
    publish=memory_fixture.MemoryAgentTests.publish
    agent=memory_fixture.MemoryAgentTests.agent

    def setUp(self):
        memory_fixture.MemoryAgentTests.setUp(self)
        self.audit=AgentAudit(self.shared,Path(self.temp.name)/'audit.sqlite3')
    def run_request(self,callback,who='owner',action='ask',**data):
        return self.audit.run(self.token(who),{'space_id':self.space,**data},action,callback)
    def rows(self,who='owner',space=None):return self.audit.list(self.token(who),{'space_id':space or self.space})['records']
    def preview(self,agent):
        return self.run_request(lambda:agent.answer(self.token('owner'),{'space_id':self.space,'query':'请记住周六在公园集合'},Cancellation()))

    def test_preview_confirm_correlate_and_do_not_store_private_data(self):
        agent=self.agent(memory_fixture.proposal());result=self.preview(agent);key=result['memory_preview']['token']
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])
        self.assertTrue(result['diagnostic']['available'])
        data={'space_id':self.space,'token':key,'confirmed':True}
        saved=self.run_request(lambda:measured('commit',agent.confirm,self.token('owner'),data),action='confirm',token=key)
        self.assertTrue(saved['saved']);rows=self.rows();self.assertEqual([r['status'] for r in rows],['saved','needs_confirmation'])
        self.assertEqual(rows[0]['operation'],rows[1]['operation']);self.assertEqual(rows[0]['steps'][0]['stage'],'commit')
        with self.audit.connect() as db:dump='\n'.join(db.iterdump())
        for private in (key,self.token('owner'),'周六','test-password-2026','content','query'):
            self.assertNotIn(private,dump)
        restarted=AgentAudit(self.shared,self.audit.path)
        self.assertEqual(restarted.list(self.token('owner'),{'space_id':self.space})['records'],rows)

    def test_cancel_and_replay_keep_correlation_and_reason_without_error_text(self):
        agent=self.agent(memory_fixture.proposal());key=self.preview(agent)['memory_preview']['token']
        data={'space_id':self.space,'token':key}
        self.run_request(lambda:agent.cancel(self.token('owner'),data),action='cancel',token=key)
        with self.assertRaises(SharedError) as caught:
            self.run_request(lambda:agent.confirm(self.token('owner'),dict(data,confirmed=True)),action='confirm',token=key)
        self.assertTrue(caught.exception.agent_diagnostic['available'])
        rows=self.rows();self.assertEqual(len({r['operation'] for r in rows}),1)
        self.assertEqual([r['status'] for r in rows],['rejected','cancelled','needs_confirmation'])
        self.assertEqual(rows[0]['reason'],'stale');self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_selection_token_links_to_preview_and_commit(self):
        # Exercise both link names without depending on semantic retrieval rankings.
        a=self.run_request(lambda:{'agent':{'status':'needs_selection'},'memory_selection':{'token':'selection-private'}})
        b=self.run_request(lambda:{'agent':{'status':'needs_confirmation'},'memory_preview':{'token':'preview-private'}},action='select',selection_token='selection-private')
        self.run_request(lambda:{'saved':True},action='confirm',token='preview-private')
        self.assertEqual(len({r['operation'] for r in self.rows()}),1)

    def test_own_records_scope_and_revoked_membership(self):
        self.run_request(lambda:{'answer':'private'},who='editor')
        self.assertEqual(len(self.rows('editor')),1);self.assertEqual(self.rows('owner'),[])
        second=self.shared.create_space(self.token('owner'),{'name':'别的空间','kind':'team'})['id']
        self.assertEqual(self.rows(space=second),[])
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('editor')},remove=True)
        with self.assertRaises(SharedError):self.rows('editor')

    def test_unauthorized_never_runs_callback_and_checks_access_again_after_read(self):
        callback=lambda:self.fail('unauthorized callback ran')
        with self.assertRaises(SharedError):self.audit.run(None,{'space_id':self.space},'ask',callback)
        with self.assertRaises(SharedError):self.run_request(callback,who='outsider')
        self.assertEqual(self.rows(),[])
        self.run_request(lambda:{})
        with patch.object(self.shared,'validate_answer',side_effect=SharedError('权限变化',403)):
            with self.assertRaises(SharedError):self.rows()

    def test_audit_write_failure_does_not_undo_or_misreport_actual_save(self):
        agent=self.agent(memory_fixture.proposal());key=self.preview(agent)['memory_preview']['token']
        with patch.object(self.audit,'finish',side_effect=ValueError('private audit failure')):
            result=self.run_request(lambda:agent.confirm(self.token('owner'),{'space_id':self.space,'token':key,'confirmed':True}),action='confirm',token=key)
        self.assertTrue(result['saved']);self.assertFalse(result['diagnostic']['available'])
        self.assertEqual(len(self.shared.memories(self.token('owner'),self.space)['memories']),1)
        with patch.object(self.audit,'begin',side_effect=sqlite3.OperationalError('locked')):
            self.assertFalse(self.run_request(lambda:{'answer':'still works'})['diagnostic']['available'])
        self.assertIsNone(CURRENT.get())

    def test_live_phase_and_interrupted_restart(self):
        entered=threading.Event();release=threading.Event();results=[]
        def callback():
            def block():entered.set();release.wait(3)
            measured('model_wait',block);return {}
        thread=threading.Thread(target=lambda:results.append(self.run_request(callback)));thread.start()
        try:
            self.assertTrue(entered.wait(2));row=self.rows()[0]
            self.assertEqual(row['status'],'running');self.assertEqual(row['steps'][0]['status'],'running')
        finally:release.set();thread.join()
        guard=self.shared.question_guard(self.token('owner'),self.space);self.audit.begin(guard,'confirm',{})
        restarted=AgentAudit(self.shared,self.audit.path)
        row=restarted.list(self.token('owner'),{'space_id':self.space})['records'][0]
        self.assertEqual(row['status'],'interrupted');self.assertEqual(row['reason'],'service_restarted');self.assertIsNone(row['elapsed_ms'])

    def test_retention_cap_expiry_and_missing_row(self):
        now=[10000000.0];self.audit=AgentAudit(self.shared,self.audit.path,limit=3,clock=lambda:now[0])
        for _ in range(5):now[0]+=1;self.run_request(lambda:{})
        self.assertEqual(len(self.rows()),3)
        guard=self.shared.question_guard(self.token('owner'),self.space);event=self.audit.begin(guard,'ask',{})
        now[0]+=15*86400;self.assertEqual(self.rows(),[])
        self.assertFalse(self.audit.finish(event,guard,{},None,{},0))

    def test_cancelled_error_has_no_message_and_unsupported_fields_are_removed(self):
        def fail():raise Cancelled('private speech')
        with self.assertRaises(Cancelled):self.run_request(lambda:measured('routing',fail))
        self.assertEqual(self.rows()[0]['status'],'cancelled');self.assertNotIn('private',json.dumps(self.rows()))
        trace=clean_trace({'steps':[{'stage':'routing','status':'done','elapsed_ms':5,'secret':'private'},{'stage':'secret','status':'done'}],
                          'usage':{'model_calls':2,'reported_calls':1,'input_tokens':8,'output_tokens':True,'secret':'private'}})
        self.assertNotIn('private',json.dumps(trace));self.assertFalse(trace['usage']['complete']);self.assertIsNone(trace['usage']['output_tokens'])

    def test_usage_aggregates_actual_returns_only_and_restores_outer_trace(self):
        outer=TRACES.begin(str(uuid.uuid4()));context=CURRENT.set(outer)
        def stream(events):yield from events
        def callback():
            list(model_events(stream([{'message':{'content':'private'}},{'done':True,'prompt_eval_count':10,'eval_count':4}])))
            list(model_events(stream([{'done':True}])))
            return {}
        try:
            self.run_request(callback);self.assertEqual(CURRENT.get(),outer)
            usage=self.rows()[0]['usage'];self.assertEqual(usage,{'model_calls':2,'reported_calls':1,'input_tokens':10,'output_tokens':4,'complete':False})
            self.assertEqual(TRACES.read(outer)['steps'],[])
        finally:CURRENT.reset(context)
        def complete():
            for n in range(2):list(model_events(stream([{'done':True,'prompt_eval_count':10,'eval_count':4}]*2)))
            return {}
        self.run_request(complete);usage=self.rows()[0]['usage'];self.assertTrue(usage['complete']);self.assertEqual(usage['output_tokens'],8)
        self.run_request(lambda:(list(model_events(stream([{'done':True,'prompt_eval_count':True,'eval_count':-1}]))),{})[1])
        self.assertIsNone(self.rows()[0]['usage']['input_tokens'])


class AuditHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space
    def test_record_route_auth_and_failed_confirm_metadata(self):
        owner,reader,space,_=self.setup_space('audit')
        self.assertEqual(self.post('/agent/records',{'space_id':space})[0],401)
        status,result=self.post('/agent/memory/confirm',{'space_id':space,'confirmed':True,'token':'missing'},owner)
        self.assertEqual(status,409);self.assertTrue(result['diagnostic']['available'])
        status,result=self.post('/agent/records',{'space_id':space},owner)
        self.assertEqual(status,200);self.assertEqual(result['records'][0]['reason'],'stale')
        self.assertEqual(self.post('/agent/records',{'space_id':space},reader)[1]['records'],[])

if __name__=='__main__':unittest.main()
