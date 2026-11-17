"""Read-only tool boundaries with synthetic data; no audio or production accounts."""
import json
import unittest
from unittest.mock import patch
import test_memory_v2 as fixture
from yiban.memory import v2_query,v2_tools
from yiban.memory.read_only_agent import AgentProtocolError
from yiban.turns import Cancellation,Cancelled


class ToolProtocolTests(unittest.TestCase):
    def test_strict_names_arguments_limits_and_no_scope_override(self):
        invalid=[
          {'calls':[{'tool':'delete','query':'x'}]},
          {'calls':[{'tool':'search_memory','query':'x','space_id':'other'}]},
          {'calls':[{'tool':'search_memory','query':'x','account_id':'owner'}]},
          {'calls':[{'tool':'search_memory','query':1}]},
          {'calls':[{'tool':'search_memory','query':'x'*1201}]},
          {'calls':[{'tool':'search_memory','query':'x'}]*2},
          {'calls':[{'tool':'query_facts','query':str(i)} for i in range(4)]},
          {'calls':[],'answer':'invented'},
        ]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(AgentProtocolError):
                v2_tools.parse_calls(json.dumps(value))
        calls=[{'tool':name,'query':'虚构活动'} for name in v2_tools.QUERY_TOOLS]
        self.assertEqual(v2_tools.parse_calls(json.dumps({'calls':calls})),calls)

    def test_multi_target_detection_is_not_a_business_fact(self):
        for q in ('第一届和第二届的学习委员分别是谁','初赛人数和决赛地点','第一次和第二次庆生的日期','顺便查口号'):
            self.assertTrue(v2_tools.broad_request(q),q)
        self.assertFalse(v2_tools.broad_request('第二届班长是谁'))


# Reuse fixture setup/helpers only, not all inherited test methods.
class V2ToolTests(unittest.TestCase):
    setUp=fixture.V2Tests.setUp;tearDown=fixture.V2Tests.tearDown
    publish=fixture.V2Tests.publish;rows=fixture.V2Tests.rows;check_error=fixture.V2Tests.check_error

    def session(self,token=None,items=None,cancel=None):
        token=token or self.reader
        scopes,allowed=v2_query.snapshot(self.service,token,self.scope)
        return v2_tools.ReadSession(self.service,token,self.scope,scopes,
            allowed if items is None else items,cancel or Cancellation(),{})

    def test_tool_search_and_evidence_are_read_only(self):
        self.publish();before=self.rows()
        session=self.session()
        rows=session.execute('search_memory',{'query':'第二届班长'})
        self.assertTrue(rows)
        session.execute('read_evidence',{'ids':[r['id'] for r in rows]})
        self.assertTrue(all(session.quotes[r['id']] for r in rows))
        self.assertEqual(before,self.rows())
        self.assertTrue(all(t['status']=='done' for t in session.trace))

    def test_no_arbitrary_record_reads_or_extra_tool_arguments(self):
        self.publish();s=self.session();identity=self.rows()[0]['id']
        for name,args in [('read_evidence',{'ids':[identity]}),('save',{'query':'x'}),
                          ('search_memory',{'query':'x','scope_kind':'personal'})]:
            with self.assertRaises(AgentProtocolError):s.execute(name,args)

    def test_tool_cannot_widen_original_allowed_record_set(self):
        self.publish();allowed=self.rows()[:1]
        s=self.session(items=allowed)
        found=s.execute('search_memory',{'query':'聚餐 班长 比分 捐书'})
        self.assertTrue({r['id'] for r in found}<={r['id'] for r in allowed})

    def test_selected_visibility_and_nonmember(self):
        self.publish(visibility='selected')
        self.assertEqual(self.session().execute('search_memory',{'query':'班长'}),[])
        with self.assertRaises(SharedError):self.session(self.users['outsider']['token'])

    def test_permission_change_before_search_or_read_blocks(self):
        self.publish();s=self.session()
        found=s.execute('search_memory',{'query':'第二届班长'})
        self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
        for name,args in [('search_memory',{'query':'班长'}),('read_evidence',{'ids':[r['id'] for r in found]})]:
            with self.assertRaises(SharedError):s.execute(name,args)

    def test_current_record_version_is_rechecked(self):
        self.publish();s=self.session()
        with self.store.connect() as db:db.execute('UPDATE memory_v2_records SET version=version+1')
        with self.assertRaises(SharedError):s.execute('search_memory',{'query':'班长'})

    def test_cancelled_before_tools_does_not_read(self):
        self.publish();cancel=Cancellation();s=self.session(cancel=cancel);cancel.cancel()
        with self.assertRaises(Cancelled):s.execute('search_memory',{'query':'班长'})
        self.assertEqual(s.trace,[])

    def test_simple_fact_does_not_use_planner(self):
        self.publish()
        with patch.object(v2_tools.ReadSession,'run',side_effect=AssertionError('planner called')):
            r=v2_query.answer(self.service,self.reader,{**self.scope,'query':'第二届班长是谁'})
        self.assertIn('角色乙',r['answer']);self.assertNotIn('agent',r)

    def test_multiple_terms_use_program_fact_tool(self):
        self.publish()
        class Model:
            calls=0
            def chat(inner,messages,**kw):
                inner.calls+=1
                if 'calls' in kw['json_schema']['properties']:
                    body={'calls':[{'tool':'query_facts','query':'第一届班长是谁'},{'tool':'query_facts','query':'第二届班长是谁'}]}
                else:
                    payload=json.loads(messages[0]['content'].rsplit('\n',1)[-1])
                    body={'answer':'第一届角色甲，第二届角色乙。','references':[p['reference'] for p in payload if p.get('fact',{}).get('predicate')=='holds_role']}
                yield {'message':{'content':json.dumps(body,ensure_ascii=False)},'done':True}
        self.service.models=Model()
        r=v2_query.answer(self.service,self.reader,{**self.scope,'query':'第一届和第二届班长分别是谁'})
        self.assertEqual(r['agent']['status'],'done')
        self.assertEqual([s['tool'] for s in r['agent']['steps']],['query_facts','query_facts','read_evidence'])
        self.assertIn('角色甲',r['answer']);self.assertIn('角色乙',r['answer'])
        self.assertEqual(self.service.models.calls,1)
        self.assertNotIn('\\n',r['answer'])
        self.assertEqual(self.service.plans,{})

    def test_comparison_reads_all_selected_candidates(self):
        self.publish();s=self.session();candidates=s.fresh()[:6]
        with patch.object(v2_tools,'collect_json',return_value='{"calls":[{"tool":"compare_events","query":"比较虚构活动"}]}'),patch.object(s,'search',return_value=candidates):
            rows,quotes,agent=s.run('比较虚构活动',[])
        self.assertEqual(len(rows),6)
        self.assertEqual(set(quotes),{r['id'] for r in candidates})
        self.assertEqual(agent['steps'][-1]['count'],6)

    def test_invalid_planner_never_dispatches_action(self):
        self.publish();s=self.session()
        with patch.object(v2_tools,'collect_json',return_value='{"calls":[{"tool":"delete","query":"all"}]}'),patch.object(s,'execute') as execute:
            with self.assertRaises(AgentProtocolError):s.run('复杂问题',[])
            execute.assert_not_called()

    def test_revocation_after_planner_prevents_tools(self):
        self.publish();s=self.session()
        def revoke(*a,**kw):
            self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
            return '{"calls":[{"tool":"search_memory","query":"班长"}]}'
        with patch.object(v2_tools,'collect_json',side_effect=revoke),patch.object(s,'execute') as execute:
            with self.assertRaises(SharedError):s.run('复杂问题',[])
            execute.assert_not_called()

    def test_no_event_answer_clears_stale_focus_without_breaking_finish(self):
        from types import SimpleNamespace
        from yiban.memory import v2_dialogue
        class Dialogue:
            def finish(inner,plan):
                self.assertIsNone(plan['focus'])
                return {'id':None}
        plan={'focus':{'ids':['old']},'topics':{}}
        current={'id':'new','title':'一般制度'}
        self.assertEqual(v2_dialogue.finish(SimpleNamespace(dialogue=Dialogue()),plan,[current],['new']),{'id':None})

    def test_only_pure_absence_is_canonicalized(self):
        from yiban.memory import v2_grounding
        self.assertTrue(v2_grounding.unsupported_answer('资料未记载个人缴费金额和账户号码。'))
        self.assertFalse(v2_grounding.unsupported_answer('资料未记载费用，但活动已有明确日期。'))
        self.assertFalse(v2_grounding.unsupported_answer('年度回顾列出四家机构，但不能据此计算活动次数。'))

    def test_revocation_after_evidence_prevents_final_return(self):
        self.publish();s=self.session()
        s.execute('search_memory',{'query':'班长'});old=self.service.evidence
        def evidence(*a,**kw):
            r=old(*a,**kw)
            self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
            return r
        with patch.object(self.service,'evidence',side_effect=evidence),self.assertRaises(SharedError):
            s.execute('read_evidence',{'ids':list(s.known)[:1]})


from yiban.memory.shared_memory import SharedError
if __name__=='__main__':unittest.main()
