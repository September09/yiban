"""Read-only tool orchestration with isolated data, scripted model decisions and HTTP."""
import json
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import test_answer_sources as fixture
import test_shared_http as http_fixture
from yiban.memory.read_only_agent import ReadOnlyAgent, parse_step, AgentProtocolError
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation, Cancelled


def step(action,query='',refs=None):
    return {'action':action,'query':query,'references':refs or []}


class Scripted:
    def __init__(self,*responses):self.responses=iter(responses);self.seen=[]
    def chat(self,messages,**kwargs):
        self.seen.append(json.loads(json.dumps(messages)))
        item=next(self.responses)
        if callable(item):item=item()
        yield {'message':{'content':json.dumps(item,ensure_ascii=False)},'done':True}


class AgentTests(unittest.TestCase):
    setUp=fixture.AnswerSourceTests.setUp
    tearDown=fixture.AnswerSourceTests.tearDown
    token=fixture.AnswerSourceTests.token
    uid=fixture.AnswerSourceTests.uid
    member=fixture.AnswerSourceTests.member
    payload=fixture.AnswerSourceTests.payload
    publish=fixture.AnswerSourceTests.publish
    stage=fixture.AnswerSourceTests.stage
    preview=fixture.AnswerSourceTests.preview
    confirm=fixture.AnswerSourceTests.confirm
    newest=fixture.AnswerSourceTests.newest
    edit=fixture.AnswerSourceTests.edit
    archived=fixture.AnswerSourceTests.archived

    def ask(self,model,query='活动在哪里？',**kwargs):
        agent=ReadOnlyAgent(self.shared,model,clock=lambda:datetime(2026,11,10,16,30,tzinfo=timezone.utc))
        return agent.answer(self.token('reader'),dict(space_id=self.space,query=query,**kwargs),Cancellation())

    def test_protocol_only_allows_typed_whitelisted_actions(self):
        invalid=[step('delete_memory'),step('read_material',refs=[True]),step('read_material',refs=[1,1]),
                 step('search_memory'),step('get_current_time','url'),step('read_material',refs=[0]),
                 dict(step('finish'),space_id='another'),step({}),step(['finish']),{'action':'finish'}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(AgentProtocolError):parse_step(json.dumps(value))
        self.assertEqual(parse_step(json.dumps(step('get_current_time'))),step('get_current_time'))

    def test_search_read_answer_includes_excerpt_not_private_original_and_is_read_only(self):
        item=self.archived();self.publish(content='机密活动口令',visibility='selected')
        with self.store.connect() as db:before='\n'.join(db.iterdump())
        model=Scripted(step('search_memory','活动在哪里？'),step('read_material',refs=[1]),step('finish'),{'answer':'活动在公园，尚待核实。','references':[1]})
        result=self.ask(model)
        self.assertEqual(result['answer_source_ids'],[item['id']]);self.assertIn('尚待核实',result['answer'])
        self.assertEqual([x['name'] for x in result['agent']['steps']],['search_memory','read_material'])
        sent=json.dumps(model.seen,ensure_ascii=False)
        self.assertNotIn('机密活动口令',sent);self.assertNotIn('私人内容',sent)
        self.assertIn('archived_excerpt',sent);self.assertNotIn('source_text',sent)
        self.assertNotIn('archived_excerpt',json.dumps(model.seen[:-1]))
        with self.store.connect() as db:self.assertEqual(before,'\n'.join(db.iterdump()))

    def test_time_uses_host_clock_in_shanghai_without_reading_memories(self):
        self.publish(content='私人记录',visibility='selected')
        model=Scripted(step('get_current_time'),step('finish'))
        with patch.object(self.shared,'question_context',side_effect=AssertionError('should not search')):
            result=self.ask(model,'今天几号？')
        self.assertEqual(result['clock']['iso'],'2026-11-11T00:30:00+08:00')
        self.assertIn('星期三',result['answer']);self.assertEqual(result['sources'],[])

    def test_read_subset_is_remapped_and_unread_candidates_cannot_be_cited(self):
        self.publish(content='活动甲在公园');self.publish(content='活动乙在科技馆')
        source=self.shared.question_context(self.token('reader'),self.space,'活动')['sources'][1]
        model=Scripted(step('search_memory','活动'),step('read_material',refs=[2]),step('finish'),{'answer':source['content'],'references':[1]})
        result=self.ask(model,'活动')
        self.assertEqual(result['answer_source_ids'],[source['id']]);self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['sources'][0]['reference'],1)
        self.assertNotIn(result['sources'][1]['content'],model.seen[-1][0]['content'])

    def test_mixed_time_memory_and_candidates_are_returned(self):
        self.archived()
        model=Scripted(step('get_current_time'),step('search_memory','活动'),step('read_material',refs=[1]),step('finish'),{'answer':'活动在公园，今天是11月11日。','references':[1]})
        result=self.ask(model,'活动在哪里，今天几号？')
        self.assertIn('2026-11-11',result['clock']['iso'])
        self.assertEqual(len(result['agent']['steps']),3);self.assertIn('clock',model.seen[-1][0]['content'])

    def test_unknown_tool_and_forged_reference_are_blocked(self):
        for responses in [(step('delete_memory'),step('delete_memory')),
                          (step('read_material',refs=[1]),step('read_material',refs=[1]))]:
            with self.subTest(responses=responses):
                result=self.ask(Scripted(*responses))
                self.assertEqual(result['agent']['status'],'failed');self.assertEqual(result['sources'],[])
                self.assertTrue(all(x['status']=='blocked' for x in result['agent']['steps']))

    def test_repeated_calls_stop_with_a_bounded_number_of_decisions(self):
        model=Scripted(*([step('get_current_time')]*3))
        result=self.ask(model)
        self.assertEqual(result['agent']['status'],'failed');self.assertEqual(len(model.seen),3)
        self.assertEqual(sum(x['status']=='done' for x in result['agent']['steps']),1)

    def test_changed_permissions_during_model_call_reject_answer(self):
        def revoke():
            self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
            return step('get_current_time')
        with self.assertRaises(SharedError) as error:self.ask(Scripted(revoke))
        self.assertEqual(error.exception.status,403)

    def test_change_after_read_before_answer_rejects_old_sources(self):
        item=self.archived()
        def change():
            self.edit(item,content='活动改在另一地点')
            return {'answer':'旧资料','references':[1]}
        with self.assertRaises(SharedError) as error:
            self.ask(Scripted(step('search_memory','活动'),step('read_material',refs=[1]),step('finish'),change))
        self.assertEqual(error.exception.status,409)

    def test_cancel_before_start_does_not_call_model(self):
        cancel=Cancellation();cancel.cancel();model=Scripted()
        with self.assertRaises(Cancelled):ReadOnlyAgent(self.shared,model).answer(self.token('reader'),{'space_id':self.space,'query':'今天几号'},cancel)
        self.assertEqual(model.seen,[])

    def test_deadline_cancels_stream_and_returns_timeout(self):
        def slow(messages,cancel,**kwargs):
            cancel.event.wait(1);cancel.check()
            yield {}
        agent=ReadOnlyAgent(self.shared,SimpleNamespace(chat=slow));agent.MAX_SECONDS=.02
        start=time.monotonic();result=agent.answer(self.token('reader'),{'space_id':self.space,'query':'今天几号'},Cancellation())
        self.assertLess(time.monotonic()-start,1);self.assertEqual(result['agent']['status'],'timeout')

    def test_ambiguous_activity_requires_choice_then_reads_selected_record(self):
        self.publish(content='2026年11月21日举行“星光科学活动”，十点在科技馆集合。')
        later=self.publish(content='2026年12月5日举行“星光科学活动”，下午两点在少年宫集合。')
        initial=self.ask(Scripted(step('search_memory','星光科学活动在哪里集合？')),query='星光科学活动在哪里集合？',conversation_mode=True)
        self.assertEqual(initial['agent']['status'],'needs_clarification')
        self.assertEqual(len(initial['conversation']['choices']),2)
        model=Scripted(step('search_memory','选择活动'),step('read_material',refs=[1]),step('finish'),{'answer':'下午两点在少年宫集合。','references':[1]})
        result=self.ask(model,query='选择活动',conversation_mode=True,conversation_id=initial['conversation']['id'],choice_id=initial['conversation']['choices'][1]['id'])
        self.assertEqual(result['answer_source_ids'],[later['id']])
        self.assertIn('在哪里集合',model.seen[-1][-1]['content'])

    def test_dynamic_schema_excludes_repeated_tools_and_unavailable_references(self):
        from yiban.memory.read_only_agent import decision_schema
        schema,actions,remaining=decision_schema({},[{},{}],{1:{}},{'iso':'now'})
        self.assertEqual(actions,['finish','read_material']);self.assertEqual(remaining,[2])
        self.assertEqual(schema['anyOf'][1]['properties']['references']['items']['enum'],[2])

    def test_empty_search_finishes_without_invented_answer(self):
        result=self.ask(Scripted(step('search_memory','活动'),step('finish')))
        self.assertIn('没有找到',result['answer']);self.assertEqual(result['answer_source_ids'],[])

    def test_model_cannot_claim_it_wrote_a_record(self):
        self.archived()
        result=self.ask(Scripted(step('search_memory','活动'),step('read_material',refs=[1]),step('finish'),{'answer':'已经删除了活动记忆','references':[1]}))
        self.assertIn('没有保存、修改或删除',result['answer']);self.assertEqual(result['answer_source_ids'],[])


class AgentHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def test_agent_endpoint_requires_auth_and_uses_current_space(self):
        owner,reader,space,_=self.setup_space('agent')
        data={'space_id':space,'query':'成员可见内容'}
        self.assertEqual(self.post('/agent/ask',data)[0],401)
        self.assertEqual(self.post('/agent/ask',data,reader,origin='https://example.com')[0],403)
        model=Scripted(step('search_memory','成员可见内容'),step('read_material',refs=[1]),step('finish'),{'answer':'成员可见的公开内容','references':[1]})
        with patch.object(self.app.SERVICES.models,'chat',side_effect=model.chat):
            status,result=self.post('/agent/ask',data,reader)
        self.assertEqual(status,200);self.assertEqual(result['agent']['status'],'done')
        self.assertNotIn('秘密',json.dumps(model.seen,ensure_ascii=False))
