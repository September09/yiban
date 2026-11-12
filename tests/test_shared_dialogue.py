"""Dialogue security and ambiguity checks use disposable databases and fake answers."""
import json
import unittest
from unittest.mock import patch
import test_shared_memory as fixture
import test_shared_http as http_fixture
from yiban.memory.shared_memory import SharedError
from yiban.memory.shared_dialogue import catalog


class SharedDialogueTests(unittest.TestCase):
    setUp=fixture.SharedMemoryTests.setUp
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish

    def seed(self,duplicate=False):
        self.a=self.publish(content='2026年11月21日举行“星光科学活动”，十点在科技馆集合，由林老师带队。')
        self.b=self.publish(content='参加2026年11月21日“星光科学活动”，带水杯和笔记本，不安排大巴。')
        self.c=self.publish(content='2026年11月28日举行“周末读书会”，陶老师主持，带一本书。')
        if duplicate:self.d=self.publish(content='2026年12月5日举行“星光科学活动”，下午两点在少年宫集合，赵老师带队。')

    def ask(self,query,identity=None,token=None,**kwargs):
        return self.shared.question_context(token or self.token('owner'),self.space,query,conversation=True,conversation_id=identity,**kwargs)

    def finish(self,result):return self.shared.dialogue.finish(result['dialogue_plan'])['id']

    def test_followup_reloads_all_records_for_selected_event(self):
        self.seed();first=self.ask('星光科学活动在哪里集合？');identity=self.finish(first)
        next_turn=self.ask('那需要带什么？',identity)
        self.assertIn('星光科学活动',next_turn['query'])
        self.assertEqual({x['id'] for x in next_turn['sources']},{self.a['id'],self.b['id']})
        identity=self.finish(next_turn)
        third=self.ask('谁带队？',identity);self.assertNotIn('读书会',third['context'])

    def test_same_title_different_dates_requires_selection_before_answer(self):
        self.seed(True);result=self.ask('星光科学活动在哪里集合？')
        self.assertTrue(result['clarification']);self.assertEqual(result['sources'],[])
        state=self.shared.dialogue.finish(result['dialogue_plan'])
        self.assertEqual(len(state['choices']),2)
        answer=self.ask('选择',state['id'],choice_id=state['choices'][1]['id'])
        self.assertIn('在哪里集合',answer['query']);self.assertIn('2026-12-05',answer['query'])
        self.assertEqual([x['id'] for x in answer['sources']],[self.d['id']])

    def test_ordinal_and_date_selection_preserve_original_question(self):
        self.seed(True)
        for selection in ('第二个','12月5日那次'):
            initial=self.ask('星光科学活动谁带队？');result=self.ask(selection,self.finish(initial))
            self.assertNotIn('clarification',result);self.assertIn('谁带队',result['query']);self.assertIn('2026-12-05',result['query'])

    def test_missing_topic_and_unresolved_choice_do_not_guess(self):
        self.seed();result=self.ask('谁带队？');self.assertIn('clarification',result)
        again=self.ask('就是那个呀',self.finish(result));self.assertIn('clarification',again);self.assertEqual(again['sources'],[])

    def test_explicit_new_topic_replaces_previous(self):
        self.seed();result=self.ask('星光科学活动在哪里？');result=self.ask('读书会带什么？',self.finish(result))
        self.assertEqual([x['id'] for x in result['sources']],[self.c['id']])
        result=self.ask('谁主持？',self.finish(result));self.assertNotIn('星光',result['context'])

    def test_comparison_does_not_silently_choose_one_for_next_turn(self):
        self.seed();result=self.ask('科学活动和读书会分别在哪里？')
        self.assertNotIn('clarification',result);self.assertIsNone(self.shared.dialogue.finish(result['dialogue_plan'])['id'])

    def test_invisible_event_never_appears_in_choices(self):
        self.seed();self.publish(content='2026年12月1日举行“秘密内部活动”，口令是虚构测试。',visibility='selected')
        result=self.ask('需要带什么？',token=self.token('reader'))
        self.assertNotIn('秘密',json.dumps(result,ensure_ascii=False))

    def test_tokens_bound_to_login_not_just_account(self):
        self.seed();identity=self.finish(self.ask('星光科学活动在哪里？'))
        second=self.shared.login({'name':'owner','password':'test-password-2026'})['token']
        with self.assertRaises(SharedError) as error:self.ask('谁带队？',identity,token=second)
        self.assertEqual(error.exception.status,409)
        # An invalid foreign attempt cannot consume the owner's topic.
        self.assertNotIn('clarification',self.ask('谁带队？',identity))

    def test_space_scope_and_version_changes_invalidate(self):
        self.seed();identity=self.finish(self.ask('星光科学活动在哪里？'))
        other=self.shared.create_space(self.token('owner'),{'name':'另一团队','kind':'team'})['id']
        with self.assertRaises(SharedError):self.shared.question_context(self.token('owner'),other,'谁带队？',conversation=True,conversation_id=identity)
        with self.assertRaises(SharedError):self.ask('谁带队？',identity,public_only=True)
        self.publish(content='新增资料导致空间版本变化')
        with self.assertRaises(SharedError):self.ask('谁带队？',identity)

    def test_logout_revocation_and_reset_prevent_reuse(self):
        self.seed();identity=self.finish(self.ask('星光科学活动在哪里？'))
        self.shared.reset_question(self.token('owner'),{'conversation_id':identity})
        with self.assertRaises(SharedError):self.ask('谁带队？',identity)
        identity=self.finish(self.ask('星光科学活动在哪里？',token=self.token('reader')))
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
        with self.assertRaises(SharedError) as error:self.ask('谁带队？',identity,token=self.token('reader'))
        self.assertEqual(error.exception.status,403)
        identity=self.finish(self.ask('星光科学活动在哪里？'))
        self.shared.logout(self.token('owner'));self.assertNotIn(identity,self.shared.dialogue._entries)

    def test_expiry_and_replay_fail_closed(self):
        self.seed();identity=self.finish(self.ask('星光科学活动在哪里？'))
        self.ask('谁带队？',identity)
        with self.assertRaises(SharedError):self.ask('谁带队？',identity)
        identity=self.finish(self.ask('星光科学活动在哪里？'))
        self.shared.dialogue._entries[identity]['expires']=0
        with self.assertRaises(SharedError):self.ask('谁带队？',identity)

    def test_history_setting_and_forged_choice_are_rejected(self):
        self.seed();identity=self.finish(self.ask('星光科学活动在哪里？'))
        with self.assertRaises(SharedError):self.ask('谁带队？',identity,include_outdated=True)
        result=self.ask('需要带什么？');identity=self.finish(result)
        with self.assertRaises(SharedError):self.ask('第一个',identity,choice_id='forged')

    def test_empty_catalog_does_not_trap_later_questions_in_pending_selection(self):
        result=self.ask('谁带队？')
        self.assertIn('clarification',result)
        state=self.shared.dialogue.finish(result['dialogue_plan'])
        self.assertIsNone(state['id']);self.assertFalse(state['needs_clarification'])
        result=self.ask('我们团队什么时候成立？')
        self.assertNotIn('clarification',result)

    def test_date_less_supplement_joins_only_unambiguous_title(self):
        items=[{'id':'a','content':'2026年11月21日“星光科学活动”十点集合','status':'valid'},
               {'id':'b','content':'“星光科学活动”需要带水杯','status':'pending'}]
        self.assertEqual(len(catalog(items)),1)
        items.append({'id':'c','content':'2026年12月5日“星光科学活动”两点集合','status':'valid'})
        self.assertEqual(len(catalog(items)),3)


class SharedDialogueHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def seed(self,suffix):
        owner,reader,space,_=self.setup_space(suffix)
        for content in ('2026年11月21日“星光科学活动”十点集合，由林老师带队。','2026年12月5日“星光科学活动”两点集合，由赵老师带队。'):
            plan=self.post('/memories/preview',{'space_id':space,'content':content,'visibility':'all','edit_policy':'author'},owner)[1]
            self.assertEqual(self.post('/memories/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True},owner)[0],200)
        return owner,reader,space

    def test_clarification_then_selection_calls_model_only_after_choice(self):
        _,reader,space=self.seed('dialogue')
        with patch.object(self.app.SERVICES.models,'chat') as model:
            status,result=self.post('/ask',{'space_id':space,'query':'星光科学活动谁带队？','conversation_mode':True},reader)
            self.assertEqual(status,200);model.assert_not_called();self.assertTrue(result['conversation']['needs_clarification'])
        seen=[]
        def chat(messages,**kwargs):
            seen.extend(messages);yield {'message':{'content':json.dumps({'answer':'赵老师带队。','references':[1]})},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,answer=self.post('/ask',{'space_id':space,'query':'第二个','conversation_mode':True,'conversation_id':result['conversation']['id']},reader)
        self.assertEqual(status,200);self.assertIn('12-05',answer['conversation']['topic'])
        self.assertIn('谁带队',seen[-1]['content']);self.assertNotIn('林老师',seen[0]['content'])
        self.assertEqual(self.post('/ask/reset',{'conversation_id':answer['conversation']['id']},reader)[0],200)

    def test_change_during_generation_cannot_publish_topic(self):
        owner,reader,space=self.seed('dialogue-race')
        size=len(self.app.SHARED.dialogue._entries)
        def chat(messages,**kwargs):
            with self.app.STORE.connect() as db:db.execute('UPDATE shared_spaces SET version=version+1 WHERE id=?',(space,))
            yield {'message':{'content':json.dumps({'answer':'不应送达','references':[1]})},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/ask',{'space_id':space,'query':'11月21日科学活动谁带队？','conversation_mode':True},reader)
        self.assertEqual(status,409);self.assertNotIn('不应送达',json.dumps(result));self.assertEqual(len(self.app.SHARED.dialogue._entries),size)

    def test_topic_survives_unstructured_model_reply_without_reusing_answer(self):
        _,reader,space=self.seed('dialogue-plain')
        seen=[]
        def chat(messages,**kwargs):
            seen.append(messages);yield {'message':{'content':'普通文本回答，不作为下轮事实依据。'},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,first=self.post('/ask',{'space_id':space,'query':'11月21日科学活动在哪里集合？','conversation_mode':True},reader)
            self.assertEqual(status,200);self.assertTrue(first['conversation']['id'])
            status,second=self.post('/ask',{'space_id':space,'query':'谁带队？','conversation_mode':True,'conversation_id':first['conversation']['id']},reader)
        self.assertEqual(status,200);self.assertIn('11-21',second['conversation']['topic'])
        self.assertNotIn('普通文本回答',json.dumps(seen[-1],ensure_ascii=False))
