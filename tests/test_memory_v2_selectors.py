"""Semantic selector regressions with invented people, events and quantities."""
import copy
import unittest
import test_memory_v2 as fixture
from yiban.memory.v2_facts import UNKNOWN


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.V2Tests();self.fx.setUp()
        self.doc=self.fx.doc
        self.doc['events'].append({'key':'game-event','kind':'competition','title':'第一届棋友交流赛','state':'held','time':{'precision':'unknown'}})
        self.doc['records'].append({**copy.deepcopy(self.doc['records'][0]),'key':'chess','title':'第一届棋友交流赛','summary':'棋友交流比赛。','event_key':'game-event'})
        executive=copy.deepcopy(next(r for r in self.doc['records'] if r['key']=='role-b'))
        executive.update(key='executive',title='第二届执行班长任职')
        executive['fact']['subject_key']='a';executive['fact']['value']='执行班长';self.doc['records'].append(executive)
    def tearDown(self):self.fx.tearDown()
    def publish(self):self.fx.publish()
    def ask(self,q,prior=None):return self.fx.ask(q,conversation_id=prior.get('conversation',{}).get('id') if prior else None)
    def book_components(self):
        base=next(r for r in self.doc['records'] if r['key']=='delivered')
        self.doc['records']=[r for r in self.doc['records'] if not r.get('fact') or r['fact']['predicate']!='quantity']
        for key,metric,value in [('new','筛选后精选新书数量',131),('used','筛选后二手书数量',217)]:
            r=copy.deepcopy(base);r.update(key=key,title=metric,summary=metric);r['fact'].update(metric=metric,value=value);self.doc['records'].append(r)
    def test_ordinals_do_not_anchor_unrelated_competition(self):
        self.publish();r=self.ask('第一届班长是谁？');self.assertIn('角色甲',r['answer']);self.assertNotIn('棋友',r['answer']);self.assertEqual(self.fx.model.seen,[])
    def test_both_roles_are_answered_and_substring_is_not_a_second_role(self):
        self.publish();r=self.ask('第二届班长和执行班长分别是谁？');self.assertEqual(len(r['answer_source_ids']),2)
        r=self.ask('第二届执行班长是谁？');self.assertEqual(len(r['answer_source_ids']),1);self.assertIn('角色甲',r['answer'])
    def test_term_followup_inherits_role_but_not_old_ordinal(self):
        self.publish();a=self.ask('第一届班长是谁？');b=self.ask('那第二届呢？',a)
        self.assertIn('角色乙',b['answer']);self.assertNotIn('角色甲',b['answer']);self.assertEqual(len(b['answer_source_ids']),1)
    def test_unknown_term_is_fixed_refusal_even_if_model_would_invent_answer(self):
        self.publish();r=self.ask('第三届班长是谁？');self.assertEqual(r['answer'],UNKNOWN);self.assertEqual(r['answer_source_ids'],[]);self.assertEqual(self.fx.model.seen,[])
    def test_term_ten_does_not_substring_match_first_term(self):
        self.publish();self.assertEqual(self.ask('第十一届班长是谁？')['answer'],UNKNOWN)
    def test_safe_sum_uses_stored_disjoint_components(self):
        self.book_components();self.publish();r=self.ask('131本新书加217本二手书，一共多少本？')
        self.assertIn('131+217=348本',r['answer']);self.assertIn('不代表签收',r['answer']);self.assertEqual(r['calculation']['kind'],'quantity_sum');self.assertEqual(len(r['answer_source_ids']),2)
    def test_subtype_followup_changes_target_without_previous_new_book_filter(self):
        self.book_components();self.publish();a=self.ask('捐书新书多少本？');b=self.ask('那二手书多少本？',a)
        self.assertIn('217本',b['answer']);self.assertNotIn('131本',b['answer'])
    def test_unrecorded_receipt_and_delivery_are_not_screened_counts(self):
        self.book_components();self.publish()
        for q,word in [('捐书最终签收多少本？','签收'),('捐书是不是准确送达500多本？','送达')]:
            r=self.ask(q);self.assertEqual(r['answer'],UNKNOWN);self.assertIn('没有记载'+word,r['calculation']['detail']);self.assertTrue(r['answer_source_ids']);self.assertIn('暂时无法确认',r['answer']);self.assertNotIn('500',r['answer'])
    def test_conflicting_components_do_not_sum(self):
        self.book_components();r=copy.deepcopy(self.doc['records'][-1]);r['key']='used-conflict';r['fact']['value']=218;self.doc['records'].append(r)
        self.publish();a=self.ask('捐书新书和二手书合计多少本？');self.assertEqual(a['calculation']['kind'],'insufficient');self.assertNotIn('=',''.join(a['answer']))
    def test_duplicate_reports_are_not_extra_summands(self):
        self.book_components();r=copy.deepcopy(self.doc['records'][-1]);r['key']='used-copy';self.doc['records'].append(r)
        self.publish();a=self.ask('捐书新书和二手书合计多少本？');self.assertIn('=348本',a['answer']);self.assertEqual(len(a['answer_source_ids']),2)
    def test_total_and_different_stage_are_not_summands(self):
        self.book_components();r=copy.deepcopy(self.doc['records'][-1]);r.update(key='total',title='筛选后合计');r['fact'].update(value=348,metric='筛选后合计');self.doc['records'].append(r)
        r=copy.deepcopy(r);r.update(key='collected',title='征集后新书数量');r['fact'].update(value=900,metric='征集后新书数量');self.doc['records'].append(r)
        self.publish();a=self.ask('捐书筛选后新书和二手书合计多少本？');self.assertIn('=348本',a['answer']);self.assertNotIn('900',a['answer'])
    def test_parallel_games_and_opponent_change_are_not_narrowed_to_first_event(self):
        self.publish();a=self.ask('篮球对阵乙队和丙队，比分和胜负分别是什么？');self.assertEqual(len(a['answer_source_ids']),2)
        a=self.ask('对阵乙队比分是什么？');b=self.ask('那对阵丙队那场呢？',a);self.assertEqual(b['answer'],UNKNOWN);self.assertTrue(b['answer_source_ids']);self.assertNotIn('38比49',b['answer'])
    def test_unknown_opponent_never_returns_all_games(self):
        self.publish();self.assertEqual(self.ask('篮球对99Z比分是多少？')['answer'],UNKNOWN)
    def test_individual_score_cannot_use_team_score_even_when_team_name_is_in_query(self):
        self.publish();a=self.ask('甲班对乙队，角色甲个人得了多少分？');self.assertEqual(a['answer'],UNKNOWN);self.assertIn('个人得分',a['calculation']['detail']);self.assertTrue(a['answer_source_ids']);self.assertIn('暂时无法确认',a['answer']);self.assertNotIn('38比49',a['answer'])
    def test_specific_day_filters_games_within_same_month(self):
        for e in self.doc['events']:
            if e['key']=='game-one':e['time']={'start':'2026-11-07','precision':'day','basis':'explicit'}
            if e['key']=='game-two':e['time']={'start':'2026-11-08','precision':'day','basis':'explicit'}
        self.publish();a=self.ask('11月8日篮球比分是什么？');self.assertEqual(len(a['answer_source_ids']),1);self.assertNotIn('38比49',a['answer'])
    def test_receipt_paraphrase_bypasses_model_without_using_screened_total(self):
        self.book_components();self.publish()
        first=self.ask('捐书新书和二手书合计多少本？')
        for question in ('所以签收的也是348本吗？','也就是说签收了348本？'):
            r=self.ask(question,first);first=r
            self.assertEqual(r['answer'],UNKNOWN);self.assertTrue(r['answer_source_ids'])
        self.assertEqual(self.fx.model.seen,[])

    def test_score_paraphrase_unknown_opponent_and_bare_followup(self):
        self.publish()
        first=self.ask('篮球对阵乙队比分是多少？')
        for question in ('那场是不是也打成38比49？','那对99Z打成38比49吗？'):
            r=self.ask(question,first);first=r
            if '99Z' in question:self.assertEqual(r['answer'],UNKNOWN)
            else:self.assertIn('38比49',r['answer'])
        self.assertEqual(self.fx.model.seen,[])

    def test_future_arrangements_cannot_be_answered_from_past_dinner(self):
        self.publish();a=self.ask('下次班级聚餐餐厅的地址和预算是多少？');self.assertEqual(a['answer'],UNKNOWN);self.assertEqual(a['answer_source_ids'],[])
    def test_model_no_evidence_and_malformed_references_share_fixed_wording(self):
        self.publish()
        for raw in ['{"answer":"资料未说明。","references":[]}', '{"answer":"任意猜测的地点","references":[99]}']:
            self.fx.model.chat=lambda *a,body=raw,**k:iter([{'message':{'content':body},'done':True}])
            result=self.ask('第一次聚餐经历');self.assertEqual(result['answer'],UNKNOWN);self.assertEqual(result['answer_source_ids'],[])

if __name__=='__main__':unittest.main()
