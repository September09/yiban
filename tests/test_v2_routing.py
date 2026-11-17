"""Routing uses synthetic facts and still exercises final ACL/evidence guards."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch
import test_memory_v2 as fixture
from yiban.memory import v2_query, v2_tools, v2_routing
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation, Cancelled

class CoverageTests(unittest.TestCase):
    def row(self,content='社区讲座于2027年2月16日举行。',**kw):
        return dict(id='r',title='社区讲座安排',content=content,verification='confirmed',**kw)
    def covered(self,query='社区讲座是什么时候举行的？',rows=None,quotes=None,plan=None):
        rows=rows if rows is not None else [self.row()]
        quotes=quotes if quotes is not None else {r['id']:r['content'] for r in rows}
        return v2_routing.covered_record(plan or {},query,rows,quotes)
    def test_date_has_current_record_and_literal_evidence(self):self.assertTrue(self.covered())
    def test_missing_exact_evidence_cannot_shortcut(self):self.assertFalse(self.covered(quotes={}))
    def test_metadata_only_date_cannot_shortcut(self):
        self.assertFalse(self.covered(rows=[self.row(content='社区讲座已经举行。',event={'time':{'start':'2027-02-16'}})]))
    def test_conflicting_reviewed_rows_cannot_shortcut(self):
        other={**self.row(content='社区讲座于2027年2月17日举行。'),'id':'other'}
        self.assertFalse(self.covered(rows=[self.row(),other]))
    def test_competing_row_outside_payload_blocks_shortcut(self):
        row=self.row();other={**self.row(content='社区讲座于2027年2月17日举行。'),'id':'other'}
        self.assertFalse(v2_routing.covered_record({},'社区讲座是什么时候举行的？',[row],{'r':row['content']},[row,other]))
        other['verification']='pending'
        self.assertFalse(v2_routing.covered_record({},'社区讲座是什么时候举行的？',[row],{'r':row['content']},[row,other]))
    def test_unread_row_cannot_become_answer_evidence(self):
        row=self.row()
        self.assertFalse(v2_routing.covered_record({},'社区讲座是什么时候举行的？',[],{},[row]))
    def test_pending_and_disputed_cannot_shortcut(self):
        for status in ('pending','disputed'):
            row=self.row();row['verification']=status
            self.assertFalse(self.covered(rows=[row]))
    def test_question_specific_field_not_a_date_somewhere(self):
        self.assertFalse(self.covered('科普展览是什么时候举行的？'))
        self.assertFalse(self.covered('社区讲座是什么时候举行的，门票多少钱？'))
    def test_other_event_cannot_supply_a_missing_field(self):
        self.assertFalse(self.covered(plan={'focus':{'ids':['other']}}))
    def test_whole_same_record_multipart_is_supported(self):
        row=self.row('第二次读书会于2027年2月16日举行，面向2至5月生日的23位成员。');row['title']='第二次读书会'
        self.assertTrue(self.covered('第二次读书会是什么时候，给哪几个月生日的成员办的，有多少位？',rows=[row]))
    def test_one_clause_missing_stays_conservative(self):
        self.assertFalse(self.covered('社区讲座是什么时候举行的，谁主持？'))
    def test_course_list_needs_actual_list_field_in_evidence(self):
        row=self.row('第三学期课程包括应用统计、管理会计。');row['title']='第三学期两门课程'
        self.assertTrue(self.covered('第三学期的两门课分别是什么？',rows=[row]))
        self.assertFalse(self.covered('第三学期的两门课分别是什么？',rows=[row],quotes={'r':'第三学期已经开始。'}))
    def test_two_subjects_not_answered_from_one_name(self):
        row=self.row('飞羽队获得创意奖。');row['title']='飞羽队获奖'
        self.assertFalse(self.covered('飞羽队和山海队分别拿了什么奖？',rows=[row]))
    def test_explicit_comparison_still_uses_planner(self):
        row=self.row();quotes={'r':row['content']}
        for query,plan in [('社区讲座与上次相比有什么变化',{}),('第一次和第二次讲座各在哪天',{}),('社区讲座哪天举行',{'date_context':'2027-02-16'}),('社区讲座哪天举行',{'comparison_topics':[{},{}]})]:
            self.assertTrue(v2_tools.needs_tools(plan,query,[row],quotes))
    def test_simple_date_route_does_not_change_without_evidence(self):
        row=self.row();query='社区讲座是什么时候举行的？'
        self.assertFalse(v2_tools.needs_tools({},query,[row],{'r':row['content']}))
        self.assertTrue(v2_tools.needs_tools({},query,[row],{}))

class DirectRouteIntegrationTests(unittest.TestCase):
    setUp=fixture.V2Tests.setUp;tearDown=fixture.V2Tests.tearDown
    publish=fixture.V2Tests.publish;rows=fixture.V2Tests.rows
    def prepare(self):
        content='社区讲座于2027年2月16日举行。'
        self.doc={**self.doc,'entities':[],'events':[],
          'sources':[{'key':'note','kind':'original','name':'虚构讲座','text':content,'sha256':hashlib.sha256(content.encode()).hexdigest()}],
          'records':[{'key':'lecture','type':'quote','title':'社区讲座安排','summary':content,'participants':[],'verification':'confirmed','verification_reason':'人工核对测试资料',
            'evidence':[{'source_key':'note','start':0,'end':len(content),'text':content}]}]}
        self.publish();self.question={**self.scope,'query':'社区讲座是什么时候举行的？'}
    def ask(self,cancel=None):
        with patch.object(v2_tools.ReadSession,'run',side_effect=AssertionError('simple question planned')):
            return v2_query.answer(self.service,self.reader,self.question,cancel=cancel)
    def test_single_model_call_with_exact_evidence_and_no_write(self):
        self.prepare();before=self.rows();result=self.ask()
        self.assertEqual(len(self.model.seen),1);self.assertNotIn('agent',result)
        self.assertTrue(result['answer_source_ids']);self.assertIn('2027年2月16日',self.model.seen[0][0]['content'])
        self.assertEqual(self.rows(),before)
    def test_complete_evidence_omits_unrelated_candidates_not_original_text(self):
        self.prepare();other='社区讲座的停车安排在门外。'
        packet={**self.doc,'sources':[{'key':'parking','kind':'original','name':'虚构停车说明','text':other,'sha256':hashlib.sha256(other.encode()).hexdigest()}],
            'records':[{**self.doc['records'][0],'key':'parking','title':'社区讲座停车安排','summary':other,
                'evidence':[{'source_key':'parking','start':0,'end':len(other),'text':other}]}]}
        self.publish(packet);result=self.ask()
        self.assertEqual([r['key'] for r in result['sources']],['lecture'])
        self.assertEqual(result['query_route']['record_ids'],[result['sources'][0]['id']])
        self.assertIn('社区讲座于2027年2月16日举行。',self.model.seen[0][0]['content'])
        self.assertNotIn('停车安排',self.model.seen[0][0]['content'])
    def test_missing_references_still_returns_fixed_unknown(self):
        self.prepare()
        class NoCitation:
            def chat(self,*a,**kw):yield {'message':{'content':json.dumps({'answer':'这是随意编的日期','references':[]})},'done':True}
        self.service.models=NoCitation();result=self.ask()
        self.assertEqual(result['answer'],v2_query.v2_facts.UNKNOWN);self.assertEqual(result['answer_source_ids'],[])
    def test_revocation_during_direct_answer_blocks_final_return(self):
        self.prepare();self.model.callback=lambda:self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
        with self.assertRaises(SharedError) as error:self.ask()
        self.assertEqual(error.exception.status,403)
    def test_version_change_during_direct_answer_blocks_final_return(self):
        self.prepare()
        def change():
            row=self.rows()[0]
            plan=self.service.update_preview(self.owner,{**self.scope,'id':row['id'],'expected_version':row['version'],'changes':{'summary':'社区讲座改期，日期待定。'}})
            self.service.confirm(self.owner,{**self.scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
        self.model.callback=change
        with self.assertRaises(SharedError) as error:self.ask()
        self.assertEqual(error.exception.status,409)
    def test_cancel_during_direct_answer_blocks_final_return(self):
        self.prepare();cancel=Cancellation();self.model.callback=cancel.cancel
        with self.assertRaises(Cancelled):self.ask(cancel)

if __name__=='__main__':unittest.main()
