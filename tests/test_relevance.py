"""Behavior regressions for distractor filtering and source-scoped uncertainty."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yiban.memory.retrieval import MemoryRetriever, RetrievalUnavailable
from yiban.memory.answer_evidence import answer_question, resolve_answer
import test_shared_http as fixture


def memory(identity,content,status='valid'):
    return {'id':identity,'content':content,'status':status,'version':1}


class ScoreProvider:
    """Controlled semantic signals let tests isolate ranking from model drift."""
    def __init__(self,similarities):self.similarities=similarities
    def fingerprint(self,*args):return 'regression-model-v1'
    def embed(self,texts,*args):
        vectors=[]
        for text in texts:
            sim=1 if text.startswith('Instruct:') else self.similarities[text]
            vectors.append([sim,math.sqrt(1-sim*sim)]+[0]*14)
        return vectors


class RelevanceFilteringTests(unittest.TestCase):
    def setUp(self):
        self.items=[memory('science','星光科学体验上午十点在科技馆正门集合。'),
                    memory('books','周末共读在北岸书屋举行。'),
                    memory('birds','冬季观鸟暂定湿地集合，仍待确认。','pending')]
        self.provider=ScoreProvider({self.items[0]['content']:.84,self.items[1]['content']:.55,self.items[2]['content']:.46})
        self.r=MemoryRetriever({},provider=self.provider)
    def test_focused_question_excludes_weaker_topics(self):
        result=self.r.retrieve('星光科学体验在哪里集合？',self.items,namespace='test')
        self.assertEqual([x['id'] for x in result['hits']],['science'])
    def test_comparison_keeps_lower_scoring_requested_topic(self):
        result=self.r.retrieve('科学体验和共读的地点分别是什么？',self.items[:2],namespace='test')
        self.assertEqual({x['id'] for x in result['hits']},{'science','books'})
    def test_pending_status_does_not_make_relevant_record_less_visible(self):
        self.items[0]['status']='pending'
        result=self.r.retrieve('星光科学体验在哪里集合？',self.items,namespace='test')
        self.assertEqual(result['hits'][0]['id'],'science')
    def test_lexical_fallback_still_filters_weaker_records(self):
        items=[memory('target','星光科学体验十点在科技馆集合。'),memory('other','邻里科学讲座下周在剧院集合。')]
        with patch.object(self.provider,'fingerprint',side_effect=RetrievalUnavailable('offline')):
            result=self.r.retrieve('星光科学体验在哪里集合',items,namespace='test')
        self.assertTrue(result['fallback']);self.assertEqual([x['id'] for x in result['hits']],['target'])


class AnswerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sources=[memory('valid','科学活动十点集合。'),memory('pending','冬季观鸟暂定白鹭湿地。','pending'),memory('old','科学活动旧版安排九点集合。','outdated')]
    def test_current_schedule_is_not_a_wall_clock_question(self):
        self.assertEqual(answer_question('科学活动现在几点、在哪里集合？'),'请根据最新活动安排回答：科学活动几点、在哪里集合？')
        for query in ('现在几点？','北京时间现在几点，我们在哪里集合？','请查旧版，原来几点集合？'):
            self.assertEqual(answer_question(query),query)

    def test_unused_pending_candidate_does_not_qualify_valid_answer(self):
        result=resolve_answer(json.dumps({'answer':'十点集合。','references':[1]}),self.sources)
        self.assertEqual(result['answer'],'十点集合。');self.assertEqual(result['answer_source_ids'],['valid'])
    def test_used_pending_notice_identifies_only_its_record(self):
        result=resolve_answer(json.dumps({'answer':'科学活动十点集合；观鸟地点仍待确认。','references':[1,2]}),self.sources)
        self.assertIn('“冬季观鸟暂定白鹭湿地。”这条记录尚待核实',result['answer'])
        self.assertNotIn('“科学活动十点集合。”这条记录尚待核实',result['answer'])
        self.assertNotIn('相关内容尚不能确认',result['answer'])
    def test_outdated_reference_retains_warning(self):
        result=resolve_answer(json.dumps({'answer':'旧版九点集合，现行十点。','references':[3,1]}),self.sources)
        self.assertIn('已过时，仅供历史回顾',result['answer']);self.assertNotIn('观鸟',result['answer'])
    def test_missing_or_bad_reference_cannot_hide_pending_warning(self):
        for refs in (None,[999],[0],[1,1],['wrong'],[True]):
            result=resolve_answer(json.dumps({'answer':'待确认。','references':refs}),self.sources)
            self.assertEqual(result['source_usage'],'unavailable');self.assertIsNone(result['answer_source_ids'])
            self.assertIn('冬季观鸟',result['answer']);self.assertIn('尚待核实',result['answer'])
    def test_no_evidence_cannot_release_an_unsupported_claim(self):
        result=resolve_answer(json.dumps({'answer':'一个没有依据的数字123456。','references':[]}),self.sources)
        self.assertNotIn('123456',result['answer']);self.assertEqual(result['answer_source_ids'],[])
    def test_truncated_json_is_not_spoken(self):
        result=resolve_answer('{"answer":"未完成',self.sources)
        self.assertEqual(result['source_usage'],'unavailable');self.assertNotIn('{',result['answer'])

    def test_plain_text_fallback_keeps_scoped_warnings(self):
        result=resolve_answer('暂不确定。',self.sources)
        self.assertIn('冬季观鸟',result['answer']);self.assertIn('尚待核实',result['answer'])

    def test_blank_answer_never_returns_just_protocol(self):
        result=resolve_answer(json.dumps({'answer':'','references':[2]}),self.sources)
        self.assertEqual(result['source_usage'],'unavailable');self.assertNotIn('[依据:',result['answer'])
    def test_long_source_and_markers_remain_bounded(self):
        result=resolve_answer(json.dumps({'answer':'暂不确定。','references':[1]}),[memory('one','虚构记录内容'*200,'pending')])
        self.assertLess(len(result['answer']),150);self.assertNotIn('[依据',result['answer'])


class EvidenceHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.SharedHTTPTests.tearDownClass.__func__)
    post=fixture.SharedHTTPTests.post
    setup_space=fixture.SharedHTTPTests.setup_space

    def test_model_references_map_to_authorized_records(self):
        owner,reader,space,_=self.setup_space('source-citations')
        def chat(messages,**kwargs):
            self.assertIn('"reference": 1',messages[0]['content'])
            self.assertIn('references',messages[0]['content']);self.assertIsNotNone(kwargs.get('json_schema'))
            yield {'message':{'content':json.dumps({'answer':'公开资料的回答。','references':[1]})},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/ask',{'space_id':space,'query':'成员可见的内容'},reader)
        self.assertEqual(status,200);self.assertEqual(result['source_usage'],'model_reported')
        self.assertEqual(result['answer_source_ids'],[result['sources'][0]['id']])
        self.assertNotIn('[依据',result['answer']);self.assertIn('尚待核实',result['answer'])
