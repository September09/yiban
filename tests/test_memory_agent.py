"""Confirmed memory tools: previews never write; explicit confirmation uses frozen plans."""
import json
import time
import unittest
from unittest.mock import patch
import test_read_only_agent as fixture
import test_shared_http as http_fixture
from yiban.memory.memory_agent import MemoryAgent, parse_proposal
from yiban.memory.read_only_agent import AgentProtocolError
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation, Cancelled


def proposal(action='save_memory_preview',**values):
    return dict(action=action,query=values.get('query',''),content=values.get('content','周六在公园集合'),visibility=values.get('visibility','keep'),viewer_names=values.get('viewer_names',[]),event_date=values.get('event_date',''))


class MemoryAgentTests(unittest.TestCase):
    setUp=fixture.AgentTests.setUp
    tearDown=fixture.AgentTests.tearDown
    token=fixture.AgentTests.token
    uid=fixture.AgentTests.uid
    member=fixture.AgentTests.member
    payload=fixture.AgentTests.payload
    publish=fixture.AgentTests.publish
    edit=fixture.AgentTests.edit
    newest=fixture.AgentTests.newest
    def agent(self,*responses):return MemoryAgent(self.shared,fixture.Scripted(*responses))
    def ask(self,agent,who='owner',**data):
        return agent.answer(self.token(who),{'space_id':self.space,'query':'请记住周六在公园集合',**data},Cancellation())
    def confirm(self,agent,result,who='owner',**data):
        return agent.confirm(self.token(who),{'space_id':self.space,'token':result['memory_preview']['token'],'confirmed':True,**data})
    def dump(self):
        with self.store.connect() as db:return '\n'.join(db.iterdump())
    def error(self,status,fn,*args,**kwargs):
        with self.assertRaises(SharedError) as caught:fn(*args,**kwargs)
        self.assertEqual(caught.exception.status,status)

    def test_save_is_preview_only_until_explicit_confirm_and_replay_fails(self):
        agent=self.agent(proposal());before=self.dump();result=self.ask(agent)
        self.assertEqual(self.dump(),before);self.assertEqual(result['agent']['status'],'needs_confirmation')
        plan=result['memory_preview'];self.assertEqual(plan['after']['visibility'],'selected');self.assertEqual(plan['after']['status'],'pending')
        self.assertEqual(plan['audience'],['owner']);self.assertNotIn(plan['token'],self.shared._plans)
        self.error(400,self.confirm,agent,result,confirmed=False)
        saved=self.confirm(agent,result,content='篡改内容',visibility='all')
        item=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(item['id'],saved['id']);self.assertEqual(item['content'],'周六在公园集合');self.assertEqual(item['visibility'],'selected')
        self.error(409,self.confirm,agent,result)
        self.assertEqual(self.shared.history(self.token('owner'),{'space_id':self.space,'id':saved['id']})['total'],1)

    def test_selected_named_member_and_all_scope_are_visible_in_preview(self):
        agent=self.agent(proposal(visibility='selected',viewer_names=['reader']))
        result=self.ask(agent,query='请记住周六在公园集合，仅reader可见');self.assertEqual(result['memory_preview']['audience'],['owner','reader'])
        self.confirm(agent,result);self.assertEqual(len(self.shared.memories(self.token('reader'),self.space)['memories']),1)
        self.assertEqual(self.shared.memories(self.token('editor'),self.space)['memories'],[])
        result=self.ask(self.agent(proposal(visibility='all')),query='请记住周六在公园集合，全体成员可见')
        self.assertEqual(result['memory_preview']['after']['visibility'],'all')
        self.assertEqual(set(result['memory_preview']['audience']),{'owner','reader','editor'})

    def test_unknown_member_and_reader_cannot_prepare_write(self):
        before=self.dump()
        self.error(400,self.ask,self.agent(proposal(visibility='selected',viewer_names=['unknown'])),query='请记住集合安排，仅unknown可见')
        self.error(403,self.ask,self.agent(proposal()),who='reader')
        self.assertEqual(self.dump(),before)

    def test_cancel_removes_outer_and_inner_plans(self):
        agent=self.agent(proposal());result=self.ask(agent);before=self.dump();key=result['memory_preview']['token']
        inner=agent.pending[key]['inner'];agent.cancel(self.token('owner'),{'space_id':self.space,'token':key})
        self.assertNotIn(inner,self.shared._plans);self.assertEqual(self.dump(),before)
        self.error(409,self.confirm,agent,result)

    def test_confirm_bound_to_exact_login_and_space(self):
        agent=self.agent(proposal());result=self.ask(agent);key=result['memory_preview']['token']
        second=self.shared.login({'name':'owner','password':'test-password-2026'})['token']
        self.error(403,agent.confirm,second,{'space_id':self.space,'token':key,'confirmed':True})
        self.error(403,self.confirm,agent,result,who='editor')
        self.error(403,self.confirm,agent,result,space_id='foreign')
        self.assertIn(key,agent.pending);self.confirm(agent,result)

    def test_expiry_and_permission_change_invalidate_confirmation(self):
        agent=self.agent(proposal(),proposal());result=self.ask(agent);agent.pending[result['memory_preview']['token']]['expires']=0
        self.error(409,self.confirm,agent,result)
        result=self.ask(agent,who='editor');self.member('editor','reader')
        self.error(409,self.confirm,agent,result,who='editor')
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_modify_preserves_scope_and_requires_confirmation_then_history(self):
        item=self.publish(content='活动周六十点在公园集合，由林老师带队。',visibility='selected',viewers=[self.uid('reader')],event_date='2026-11-21',status='valid',status_reason='已核对')
        agent=self.agent(proposal('update_memory_preview',query='活动公园'),{'content':'活动周六十一点在公园集合，由林老师带队。','event_date':'2026-11-21'})
        before=self.dump();result=self.ask(agent);plan=result['memory_preview']
        self.assertEqual(self.dump(),before);self.assertEqual(plan['before']['content'],item['content'])
        self.assertEqual(plan['after']['viewers'],[self.uid('reader')]);self.assertEqual(plan['after']['status'],'pending')
        self.assertIn('林老师',plan['after']['content']);self.confirm(agent,result)
        updated=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(updated['version'],2);self.assertEqual(updated['visibility'],'selected')
        history=self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})
        self.assertEqual(history['total'],2);self.assertEqual(history['entries'][0]['before']['content'],item['content'])

    def test_multiple_candidates_need_explicit_target_before_preview(self):
        self.publish(content='活动甲在公园');self.publish(content='活动乙在学校')
        agent=self.agent(proposal('update_memory_preview',query='活动'),{'content':'活动乙改在科技馆','event_date':''})
        before=self.dump();result=self.ask(agent);selection=result['memory_selection']
        self.assertEqual(result['agent']['status'],'needs_selection');self.assertNotIn('memory_preview',result)
        self.error(409,agent.confirm,self.token('owner'),{'space_id':self.space,'token':selection['token'],'confirmed':True})
        result=self.ask(agent,selection_token=selection['token'],memory_choice=1)
        self.assertEqual(result['memory_preview']['before']['content'],selection['choices'][0]['content'])
        self.assertEqual(self.dump(),before)

    def test_selection_foreign_number_and_replay_are_rejected(self):
        self.publish(content='活动甲');self.publish(content='活动乙')
        agent=self.agent(proposal('update_memory_preview',query='活动'),{'content':'活动新地点','event_date':''})
        result=self.ask(agent);key=result['memory_selection']['token']
        self.error(400,self.ask,agent,selection_token=key,memory_choice=True)
        self.error(400,self.ask,agent,selection_token=key,memory_choice=99)
        self.ask(agent,selection_token=key,memory_choice=1)
        self.error(409,self.ask,agent,selection_token=key,memory_choice=1)

    def test_non_author_editor_may_edit_body_but_cannot_expand_scope(self):
        self.publish(content='活动安排',edit_policy='editors',visibility='selected',viewers=[self.uid('editor')])
        agent=self.agent(proposal('update_memory_preview',query='活动',visibility='all'),{'content':'活动新安排','event_date':''})
        self.error(403,self.ask,agent,who='editor',query='把活动改为新安排，全体成员可见')
        agent=self.agent(proposal('update_memory_preview',query='活动'),{'content':'活动新安排','event_date':''})
        result=self.ask(agent,who='editor');self.confirm(agent,result,who='editor')

    def test_uneditable_and_hidden_records_never_become_edit_previews(self):
        self.publish(content='公开但只有作者能改的活动')
        agent=self.agent(proposal('update_memory_preview',query='活动'))
        self.error(403,self.ask,agent,who='editor')
        self.publish(content='内部口令',visibility='selected')
        agent=self.agent(proposal('update_memory_preview',query='内部口令'))
        self.error(403,self.ask,agent,who='editor')
        self.assertNotIn('内部口令',json.dumps(agent.models.seen,ensure_ascii=False))

    def test_conflicts_require_separate_acknowledgment(self):
        self.publish(content='我喜欢咖啡')
        agent=self.agent(proposal(content='我不喜欢咖啡'));result=self.ask(agent)
        self.assertTrue(result['memory_preview']['conflicts'])
        self.error(400,self.confirm,agent,result)
        self.confirm(agent,result,acknowledge_conflicts=True)

    def test_model_cannot_request_confirm_or_inject_ids(self):
        for value in [proposal('confirm'),dict(proposal(),id='other'),dict(proposal(),confirmed=True)]:
            with self.assertRaises(AgentProtocolError):parse_proposal(json.dumps(value))

    def test_cancel_during_preparation_discards_pending_preview(self):
        agent=self.agent(proposal());cancel=Cancellation();original=self.shared.preview;before=self.dump()
        def preview(*args,**kwargs):
            result=original(*args,**kwargs);cancel.cancel();return result
        with patch.object(self.shared,'preview',side_effect=preview),self.assertRaises(Cancelled):
            agent.answer(self.token('owner'),{'space_id':self.space,'query':'记住活动安排'},cancel)
        self.assertEqual(agent.pending,{});self.assertEqual(self.shared._plans,{});self.assertEqual(self.dump(),before)

    def test_concurrent_edit_rejects_stale_preview(self):
        item=self.publish('editor',content='活动十点集合')
        agent=self.agent(proposal('update_memory_preview',query='活动'),{'content':'活动十一点集合','event_date':''})
        result=self.ask(agent);self.edit(item,content='活动十二点集合')
        self.error(409,self.confirm,agent,result)

    def test_model_cannot_expand_scope_without_explicit_user_request(self):
        agent=self.agent(proposal(visibility='all'))
        result=self.ask(agent)
        self.assertEqual(result['memory_preview']['after']['visibility'],'selected')
        self.assertEqual(result['memory_preview']['audience'],['owner'])

    def test_explicit_scope_and_date_survive_model_omissions(self):
        agent=self.agent(proposal(content='2026年12月6日手工课',visibility='keep'))
        result=agent.answer(self.token('owner'),{'space_id':self.space,'query':'请记住：2026年12月6日手工课，仅reader可见。'},Cancellation())
        after=result['memory_preview']['after']
        self.assertEqual(after['event_date'],'2026-12-06');self.assertEqual(after['viewers'],[self.uid('reader')])
        from yiban.memory.memory_agent import explicit_fields
        self.error(400,explicit_fields,proposal(),'仅reader和unknown可见',['reader'])
        self.error(400,explicit_fields,proposal(),'仅reader可见，同时全体成员可见',['reader'])

    def test_ordinary_questions_cannot_trigger_write_proposals(self):
        agent=self.agent(fixture.step('get_current_time'),fixture.step('finish'))
        result=agent.answer(self.token('owner'),{'space_id':self.space,'query':'今天几号？'},Cancellation())
        self.assertIn('当前电脑时间',result['answer']);self.assertFalse(agent.pending)
        from yiban.memory.memory_agent import has_write_intent,proposal_schema
        for query in ['活动需要带什么？','不要保存，只查一下活动地点','别修改记忆']:
            self.assertFalse(has_write_intent(query))
        schema=proposal_schema(['owner','reader'])
        for variant in schema['anyOf']:
            fields=variant['properties']
            if fields['visibility']['enum']!=['selected']:self.assertEqual(fields['viewer_names']['maxItems'],0)

    def test_read_requests_reuse_read_only_tools(self):
        agent=self.agent(proposal('read_only',content=''),fixture.step('get_current_time'),fixture.step('finish'))
        result=self.ask(agent)
        self.assertEqual(result['agent']['status'],'done');self.assertIn('当前电脑时间',result['answer']);self.assertFalse(agent.pending)


class MemoryAgentHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space
    def test_preview_cancel_confirm_routes_and_auth(self):
        owner,reader,space,_=self.setup_space('write-agent')
        request={'space_id':space,'query':'记住周末公园集合'}
        self.assertEqual(self.post('/agent/memory/ask',request)[0],401)
        self.assertEqual(self.post('/agent/memory/ask',request,owner,origin='https://example.com')[0],403)
        with patch.object(self.app.SERVICES.models,'chat',side_effect=fixture.Scripted(proposal(),proposal()).chat):
            status,result=self.post('/agent/memory/ask',request,owner)
            self.assertEqual(status,200);data={'space_id':space,'token':result['memory_preview']['token'],'confirmed':True}
            self.assertEqual(self.post('/agent/memory/cancel',data,owner)[0],200)
            self.assertEqual(self.post('/agent/memory/confirm',data,owner)[0],409)
            _,result=self.post('/agent/memory/ask',request,owner);data['token']=result['memory_preview']['token']
            self.assertEqual(self.post('/agent/memory/confirm',data,reader)[0],403)
            self.assertEqual(self.post('/agent/memory/confirm',data,owner)[0],200)
            self.assertEqual(self.post('/agent/memory/confirm',data,owner)[0],409)
