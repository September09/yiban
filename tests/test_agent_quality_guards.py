"""Regression cases discovered by the independent synthetic text evaluation."""
import json
import unittest
import test_read_only_agent as fixture
from yiban.memory.answer_evidence import resolve_answer, resolve_comparison
from yiban.memory.read_only_agent import decision_schema
from yiban.memory.shared_dialogue import catalog


class QualityGuardTests(unittest.TestCase):
    setUp=fixture.AgentTests.setUp
    tearDown=fixture.AgentTests.tearDown
    token=fixture.AgentTests.token
    uid=fixture.AgentTests.uid
    member=fixture.AgentTests.member
    payload=fixture.AgentTests.payload
    publish=fixture.AgentTests.publish
    ask=fixture.AgentTests.ask

    def test_pure_unknown_cannot_claim_unrelated_citation(self):
        sources=[{'id':'one','status':'pending','content':'活动在公园'}]
        result=resolve_answer(json.dumps({'answer':'资料未说明。','references':[1]}),sources)
        self.assertEqual(result['answer_source_ids'],[]);self.assertNotIn('公园',result['answer'])
        mixed=resolve_answer(json.dumps({'answer':'资料未说明预算，但活动暂定在公园。','references':[1]}),sources)
        self.assertEqual(mixed['answer_source_ids'],['one']);self.assertIn('尚待核实',mixed['answer'])

    def test_unquoted_events_require_date_selection_and_keep_supplements(self):
        a=self.publish(content='远山绘本会在2026年12月2日举行，地点为湖畔书屋。',event_date='2026-12-02')
        b=self.publish(content='远山绘本会在2026年12月9日举行，地点为山间书屋。',event_date='2026-12-09')
        c=self.publish(content='远山绘本会需要带图画纸。',event_date='2026-12-02')
        result=self.shared.question_context(self.token('reader'),self.space,'远山绘本会在哪里？',conversation=True)
        self.assertIn('clarification',result);self.assertIn('12-02',result['clarification']);self.assertIn('12-09',result['clarification'])
        topics=catalog([a,b,c]);first=next(t for t in topics.values() if t['date']=='2026-12-02')
        self.assertEqual(set(first['ids']),{a['id'],c['id']})

    def test_hidden_unquoted_event_never_appears_in_choices(self):
        self.publish(content='远山绘本会在2026年12月2日举行，地点为湖畔书屋。',event_date='2026-12-02')
        self.publish(content='远山绘本会在2026年12月9日举行，地点为秘密书屋。',event_date='2026-12-09',visibility='selected')
        result=self.shared.question_context(self.token('reader'),self.space,'远山绘本会在哪里？',conversation=True)
        self.assertNotIn('clarification',result);self.assertNotIn('秘密',json.dumps(result,ensure_ascii=False))

    def test_comparison_schema_requires_batch_read_before_finish(self):
        candidates=[{},{}]
        schema,actions,remaining=decision_schema({},candidates,{},None,True)
        self.assertNotIn('finish',actions);self.assertEqual(remaining,[1,2])
        read=next(v for v in schema['anyOf'] if v['properties']['action']['enum']==['read_material'])
        self.assertEqual(read['properties']['references']['minItems'],2)
        self.assertIn('finish',decision_schema({},candidates,{1:{},2:{}},None,True)[1])
        self.assertIn('finish',decision_schema({},candidates,{1:{}},None)[1])

    def test_comparison_rejects_early_finish_and_reads_both_authorized_records(self):
        a=self.publish(content='编织工作坊在青桐室举行。',status='valid',status_reason='测试')
        b=self.publish(content='编织工作坊在银杏厅举行。',status='valid',status_reason='测试')
        model=fixture.Scripted(fixture.step('search_memory','编织工作坊'),fixture.step('finish'),fixture.step('read_material',refs=[1,2]),fixture.step('finish'),{'assessment':'conflicting','references':[1,2]})
        result=self.ask(model,'编织工作坊两份安排一致吗？')
        self.assertEqual(result['agent']['status'],'done');self.assertEqual(set(result['answer_source_ids']),{a['id'],b['id']})
        self.assertEqual(result['agent']['steps'][1]['status'],'blocked')
        self.assertIn('青桐室',model.seen[-1][0]['content']);self.assertIn('银杏厅',model.seen[-1][0]['content'])

    def test_history_only_answer_uses_old_records_and_remaps_citations(self):
        old=self.publish(content='编织工作坊旧版是九点开始。',status='outdated',status_reason='测试旧版')
        current=self.publish(content='编织工作坊现行是十点开始。',status='valid',status_reason='测试现行')
        model=fixture.Scripted(fixture.step('search_memory','编织工作坊旧版'),fixture.step('read_material',refs=[1,2]),fixture.step('finish'),{'answer':'旧版九点开始，现已过时。','references':[1]})
        result=self.ask(model,'旧版记忆里编织工作坊几点开始？',include_outdated=True)
        self.assertEqual(result['answer_source_ids'],[old['id']]);self.assertEqual(result['sources'][1]['id'],current['id'])
        self.assertNotIn('现行是十点',model.seen[-1][0]['content']);self.assertIn('已过时',result['answer'])

    def test_history_comparison_still_supplies_both_versions(self):
        old=self.publish(content='编织工作坊旧版是九点开始。',status='outdated',status_reason='测试旧版')
        current=self.publish(content='编织工作坊现行是十点开始。',status='valid',status_reason='测试现行')
        model=fixture.Scripted(fixture.step('search_memory','编织工作坊旧版现行对比'),fixture.step('read_material',refs=[1,2]),fixture.step('finish'),{'assessment':'conflicting','references':[1,2]})
        result=self.ask(model,'编织工作坊旧版和现行安排对比有何变化？',include_outdated=True)
        self.assertEqual(set(result['answer_source_ids']),{old['id'],current['id']})
        self.assertIn('现行是十点',model.seen[-1][0]['content'])

    def test_historical_event_is_not_automatically_an_outdated_memory(self):
        item=self.publish(content='去年编织工作坊在森林教室举办。',status='valid',status_reason='已核对历史事实')
        model=fixture.Scripted(fixture.step('search_memory','去年编织工作坊'),fixture.step('read_material',refs=[1]),fixture.step('finish'),{'answer':'去年在森林教室举办。','references':[1]})
        result=self.ask(model,'历史回顾：去年编织工作坊在哪里举办？')
        self.assertEqual(result['answer_source_ids'],[item['id']]);self.assertIn('森林教室',result['answer'])

    def test_comparison_quotes_separate_sources_and_rejects_invented_prose(self):
        sources=[{'id':'a','status':'valid','content':'编织工作坊在青桐室。'}, {'id':'b','status':'pending','content':'编织工作坊在银杏厅。'}]
        result=resolve_comparison(json.dumps({'assessment':'conflicting','references':[1,2]}),sources)
        self.assertIn('资料1（有效',result['answer']);self.assertIn('资料2（待核实',result['answer'])
        self.assertNotIn('青桐室和银杏厅',result['answer']);self.assertIn('模型对照判断',result['answer'])
        self.assertEqual(result['answer_source_ids'],['a','b'])
        for raw in ({'assessment':'conflicting','references':[1]}, {'assessment':'conflicting','references':[1,999]},
                    {'assessment':'consistent','references':[True,2]}, {'assessment':'consistent','references':[1,1]},
                    {'assessment':'conflicting','references':[1,2],'answer':'编造安排'}):
            rejected=resolve_comparison(json.dumps(raw),sources)
            self.assertEqual(rejected['source_usage'],'unavailable');self.assertNotIn('编造',rejected['answer'])
        consistent=resolve_comparison(json.dumps({'assessment':'consistent','references':[1,2]}),sources)
        self.assertIn('基本一致',consistent['answer']);self.assertNotIn('存在不一致',consistent['answer'])

    def test_missing_old_version_does_not_substitute_current_fact(self):
        self.publish(content='编织工作坊现行是十点开始。',status='valid',status_reason='测试现行')
        model=fixture.Scripted(fixture.step('search_memory','编织工作坊旧版'),fixture.step('read_material',refs=[1]),fixture.step('finish'))
        result=self.ask(model,'旧版记忆里编织工作坊几点开始？',include_outdated=True)
        self.assertEqual(result['answer_source_ids'],[]);self.assertNotIn('十点',result['answer']);self.assertIn('旧版',result['answer'])

if __name__=='__main__':unittest.main()
