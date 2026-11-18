"""Generic regression data: no class answers, dates or names from the evaluation."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_memory_v2 as fixture
from test_v2_reasoning_guards import row,event
from yiban.memory import v2_dialogue,v2_facts,v2_relevance,v2_tools,v2_grounding
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation,Cancelled

class ContextRepairTests(unittest.TestCase):
    def setup_dialogue(self):
        return SimpleNamespace(dialogue=SharedDialogue(SharedError)),[{'id':'team:test','version':1,'member_version':1}]
    def test_episode_followups_and_habits_have_different_scopes(self):
        service,scopes=self.setup_dialogue()
        training=event('stair','首次爬楼训练','training')
        race=event('race','秋季比赛','competition')
        rows=[row('first','首次爬楼','在教学楼训练。',training),
              row('habit','日常训练','经常在湖边训练。',race),
              row('partner','训练搭子','与柳叶一起练习。',race)]
        p,_,_,_=v2_dialogue.begin(service,'token',{'query':'首次爬楼在哪里？'},scopes,rows)
        previous=v2_dialogue.finish(service,p,rows,['first'])
        for q in ['平时我在哪里训练？','我的训练搭子是谁？','比赛后有什么感悟？']:
            p,found,_,clarify=v2_dialogue.begin(service,'token',{'query':q,'conversation_id':previous['id']},scopes,rows)
            self.assertIsNone(clarify);self.assertEqual(len(found),3,q)
            previous=v2_dialogue.finish(service,p,[],[])
    def test_combined_object_followup_and_judge_followup_reuse_current_event(self):
        service,scopes=self.setup_dialogue()
        a=event('a','秋季摄影赛','photography');b=event('b','春季案例赛','competition')
        rows=[row('a','摄影纪念品','柳叶制作并赠给松石。',a),row('b','案例评委','陈老师担任评委。',b)]
        p,_,_,_=v2_dialogue.begin(service,'token',{'query':'秋季摄影赛有什么纪念品？'},scopes,rows)
        previous=v2_dialogue.finish(service,p,rows,['a'])
        for q in ['是谁做的，送给了谁？','评委是哪两位？']:
            p,found,_,clarify=v2_dialogue.begin(service,'token',{'query':q,'conversation_id':previous['id']},scopes,rows)
            self.assertIsNone(clarify);self.assertEqual([x['id'] for x in found],['a'])
            previous=v2_dialogue.finish(service,p,found,['a'])
    def test_this_gift_refers_to_last_cited_object(self):
        service,scopes=self.setup_dialogue()
        activity=event('trip','秋季出游','hiking')
        rows=[row('cup','纪念杯','青石赠送纪念杯。',activity),
              row('badge','纪念徽章','柳叶制作徽章。',activity)]
        p,_,_,_=v2_dialogue.begin(service,'token',{'query':'秋季出游的纪念杯是什么？'},scopes,rows)
        previous=v2_dialogue.finish(service,p,rows,['cup'])
        p,members,_,clarify=v2_dialogue.begin(service,'token',{'query':'这份礼物有什么纪念意义？','conversation_id':previous['id']},scopes,rows)
        self.assertIsNone(clarify);self.assertTrue(p['object_followup'])
        self.assertEqual([r['id'] for r in v2_dialogue.expand_sources(p,p['query'],members,rows)],['cup'])
    def test_attribute_question_keeps_content_evidence_despite_title_overlap(self):
        rows=[row('route','高山森林徒步','路线约八公里，爬升三百米。'),
              row('gift','高山徒步纪念品','赠送徽章。')]
        plan={'focus':{'title':'高山森林徒步','primary_ids':[]}}
        found=v2_dialogue.expand_sources(plan,'高山徒步多长、爬升多高？',rows,rows)
        self.assertEqual({x['id'] for x in found},{'route','gift'})
        self.assertNotIn('facet_ids',plan)
        self.assertIsNone(v2_dialogue.extractive_overview({'facet_ids':['gift']},'柳叶拿了什么奖，属于哪个赛道？',[{**rows[1],'reference':1}]))
    def test_music_and_photo_cross_activity_is_broad(self):
        self.assertTrue(v2_tools.broad_request('歌唱比赛集体奖和摄影一等奖分别是什么？'))
    def test_committee_actions_are_not_office_queries(self):
        for q in ('班委如何讨论包装设计并修改定稿？','班长如何安排这次排练？'):
            self.assertIsNone(v2_facts.request(q,[]))
        self.assertEqual(v2_facts.request('第一届班委成员名单是谁？',[])['kind'],'roles')
        self.assertEqual(v2_facts.request('第二届班长是谁？',[])['roles'],['班长'])
    def test_untimed_field_overrides_unrelated_event_focus(self):
        service,scopes=self.setup_dialogue();brand=event('brand','班级品牌文创建设','project')
        rows=[row('brand','文创制作','制作纪念品。',brand),row('rules','团队公约','守信、合作。'),row('goal','共同目标','共创与共进。')]
        plan,members,_,_=v2_dialogue.begin(service,'token',{'query':'品牌文创制作怎么样？'},scopes,rows)
        prior=v2_dialogue.finish(service,plan,members,['brand'])
        plan,members,_,clarify=v2_dialogue.begin(service,'token',{'query':'那两条公约呢？','conversation_id':prior['id']},scopes,rows)
        self.assertIsNone(clarify);self.assertIsNone(plan['focus']);self.assertEqual([r['id'] for r in members],['rules'])
    def test_explicit_field_can_be_attached_to_a_meeting(self):
        service,scopes=self.setup_dialogue()
        meeting=event('meeting','章程讨论班会','meeting')
        project=event('project','品牌文创建设','project')
        rows=[row('goal','共同目标','共创与共进。',meeting),row('brand','品牌建设','制作文创。',project)]
        plan,members,_,clarify=v2_dialogue.begin(service,'token',{'query':'建设中的完整目标是什么？'},scopes,rows)
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in members],['goal'])
        self.assertEqual(plan['facet_ids'],['goal'])
    def test_month_uses_metadata_to_disambiguate(self):
        service,scopes=self.setup_dialogue()
        rows=[row('spring','春季班会','开班会。',event('spring','春季班会','meeting','2027-04-10')),
              row('winter','年度班会','总结班会。',event('winter','年度班会','meeting','2027-12-09'))]
        _,members,_,clarify=v2_dialogue.begin(service,'token',{'query':'十二月班会是哪天？'},scopes,rows)
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in members],['winter'])
    def test_class_codes_do_not_override_charity_intent(self):
        service,scopes=self.setup_dialogue()
        rows=[row('game','31C对31D篮球赛','双方打篮球。',event('game','31C对31D篮球赛','basketball')),
              row('books','公益捐书协作','31C和31D捐书。',event('books','公益捐书','charity'))]
        _,members,_,clarify=v2_dialogue.begin(service,'token',{'query':'31C和31D的捐书还有哪些人帮忙？'},scopes,rows)
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in members],['books'])
    def test_abstract_numbered_label_is_not_a_quantity_request(self):
        r=row('goal','共同目标','共创与共进。')
        self.assertIsNone(v2_dialogue.missing_quantity('“两个共”的完整目标是什么？',[r],{'goal':'共创与共进。'}))
        r=row('gift','纪念徽章','柳叶制作徽章。')
        self.assertIsNotNone(v2_dialogue.missing_quantity('送出了两个徽章吗？',[r],{'gift':r['content']}))
    def test_cross_activity_conjunction_preserves_multiple_targets(self):
        self.assertTrue(v2_tools.broad_request('摄影赛一等奖和案例赛创意奖分别归谁？'))
        self.assertFalse(v2_tools.broad_request('摄影赛两个赛道分别归谁？'))
    def test_placeholder_plan_is_rejected_even_when_not_duplicated(self):
        with self.assertRaises(v2_tools.AgentProtocolError):
            v2_tools.parse_calls('{"calls":[{"tool":"search_memory","query":"活动名称和所问字段"}]}')
    def test_no_new_focus_from_an_uncited_answer(self):
        service,scopes=self.setup_dialogue()
        r=row('brand','品牌文创','文创。',event('brand','品牌文创','project'))
        plan,_,_,_=v2_dialogue.begin(service,'token',{'query':'品牌文创多少钱？'},scopes,[r])
        v2_dialogue.finish(service,plan,[r],[])
        self.assertIsNone(plan['focus'])

class FallbackBoundaryTests(unittest.TestCase):
    setUp=fixture.V2Tests.setUp;tearDown=fixture.V2Tests.tearDown;publish=fixture.V2Tests.publish;rows=fixture.V2Tests.rows
    def session(self,cancel=None):
        self.publish()
        from yiban.memory.v2_query import snapshot
        scopes,rows=snapshot(self.service,self.reader,self.scope)
        return v2_tools.ReadSession(self.service,self.reader,self.scope,scopes,rows,cancel or Cancellation(),
            {'query':'摄影赛一等奖和案例赛创意奖，分别给了谁？'})
    def test_fallback_queries_both_literal_targets_and_reads_evidence(self):
        s=self.session();before=self.rows()
        with patch.object(s,'execute',wraps=s.execute) as execute:
            _,_,agent=s.fallback('ignored background',[])
        searches=[call.args[1]['query'] for call in execute.call_args_list if call.args[0]=='search_memory']
        self.assertEqual(len(searches),2);self.assertIn('摄影赛',searches[0]);self.assertIn('案例赛',searches[1])
        self.assertEqual(execute.call_args_list[-1].args[0],'read_evidence');self.assertEqual(agent['status'],'fallback')
        self.assertEqual(self.rows(),before)
    def test_revocation_before_fallback_cannot_read(self):
        s=self.session()
        self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
        with self.assertRaises(SharedError):s.fallback('query',[])
        self.assertEqual(s.trace,[])
    def test_cancel_before_fallback_cannot_read(self):
        cancel=Cancellation();s=self.session(cancel);cancel.cancel()
        with self.assertRaises(Cancelled):s.fallback('query',[])

    def test_query_service_recovers_invalid_plan_with_actual_read_tools(self):
        self.publish();before=self.rows()
        def invalid_plan(session,*args):
            return v2_tools.parse_calls('{"calls":[{"tool":"search_memory","query":"活动名称和所问字段"}]}')
        with patch.object(v2_tools.ReadSession,'run',invalid_plan):
            result=self.service.dispatch('ask',self.reader,{**self.scope,
                'query':'篮球赛和捐书公益，分别有什么资料？'})
        self.assertEqual(result['agent']['status'],'fallback')
        self.assertEqual([s['tool'] for s in result['agent']['steps']].count('search_memory'),2)
        self.assertEqual(result['agent']['steps'][-1]['tool'],'read_evidence')
        self.assertTrue(result['sources'])
        self.assertEqual(self.rows(),before)
        self.assertEqual(len(self.service.plans),0)

class LiteralReferenceTests(unittest.TestCase):
    def rows(self):
        return [{'id':'a','content':'按专长分工。','entities':{}},
                {'id':'b','content':'器材处理流程。','entities':{}}]
    def test_adds_only_already_read_literal_proof_without_changing_text(self):
        rows=self.rows();answer='按专长分工，并进行了器材的逐项消毒。';quotes={'a':'按专长分工。','b':'器材都经过逐项消毒再包装。'}
        self.assertEqual(v2_grounding.complete_literal_references(answer,rows,quotes,['a']),['a','b'])
    def test_unread_or_unlisted_quote_cannot_authorize_reference(self):
        rows=self.rows()
        self.assertEqual(v2_grounding.complete_literal_references('器材的逐项消毒。',rows,{},['a']),['a'])
        self.assertEqual(v2_grounding.complete_literal_references('器材的逐项消毒。',rows[:1],{'hidden':'逐项消毒'},['a']),['a'])
    def test_entity_and_number_mismatch_do_not_gain_a_citation(self):
        rows=self.rows();rows[0]['entities']={'p':'柳叶'};rows[1]['entities']={'p':'林海'}
        quote={'b':'林海给44件器材逐项消毒。'}
        self.assertEqual(v2_grounding.complete_literal_references('柳叶进行了逐项消毒。',rows,quote,['a']),['a'])
        self.assertEqual(v2_grounding.complete_literal_references('55件器材的逐项消毒。',rows,quote,['a']),['a'])
    def test_never_repairs_an_uncited_factual_answer(self):
        self.assertEqual(v2_grounding.complete_literal_references('逐项消毒。',self.rows(),{'b':'逐项消毒。'},[]),[])

if __name__=='__main__':unittest.main()
