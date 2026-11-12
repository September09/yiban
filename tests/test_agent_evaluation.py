"""The evaluator must reject leaked/uncited answers and keep scores separate from human review."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from tools.evaluate_agent import assess,load_dataset,render_html,safe_local_config
from yiban.settings import ROOT,load_settings


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data,_=load_dataset(ROOT/'tests/fixtures/agent/text-v1.json')
        self.alias={r['key']:r['key'] for r in self.data['records']}
    def case(self,name):return next(c for c in self.data['cases'] if c['id']==name)
    def result(self,answer,refs):return {'answer':answer,'sources':[{'id':x} for x in refs],'answer_source_ids':refs,'agent':{'status':'done'}}
    def score(self,name,result,**kw):return assess(self.data,self.case(name),result,kw.get('status',200),kw.get('changed',False),self.alias)

    def test_valid_answer_still_requires_human_review(self):
        result=self.score('place-paraphrase',self.result('在青藤室。',['book-current']))
        self.assertTrue(result['rule_passed']);self.assertIn('待人工核对',result['human_review'])
        # Keyword scoring deliberately cannot prove sentence entailment.
        self.assertTrue(self.score('place-paraphrase',self.result('不是青藤室。',['book-current']))['rule_passed'])

    def test_uncited_facts_unknown_refs_and_private_sources_fail(self):
        self.assertFalse(self.score('place-paraphrase',self.result('在青藤室。',[]))['rule_passed'])
        result=self.score('reader-private',self.result('无法确认，但D42有钥匙。',['key-private']))
        self.assertFalse(result['checks']['authorized_sources']);self.assertFalse(result['checks']['forbidden_claims_absent'])
        self.assertFalse(self.score('place-paraphrase',self.result('青藤室',['invented']))['checks']['authorized_citations'])

    def test_candidate_and_actual_citation_are_not_confused(self):
        data=self.result('青藤室',['book-current']);data['sources'].append({'id':'book-materials'})
        self.assertTrue(self.score('place-paraphrase',data)['rule_passed'])
        data['answer_source_ids'].append('book-materials')
        self.assertFalse(self.score('place-paraphrase',data)['checks']['expected_citations'])

    def test_denied_requests_no_answer_and_writes_fail(self):
        self.assertTrue(self.score('outsider',{},status=403)['rule_passed'])
        self.assertFalse(self.score('outsider',self.result('青藤室',['book-current']),status=403)['rule_passed'])
        self.assertFalse(self.score('place-paraphrase',self.result('青藤室',['book-current']),changed=True)['checks']['no_memory_write'])

    def test_pending_notice_alone_cannot_pass_missing_answer(self):
        data=self.result('资料提示：“河畔公园”尚待核实。\n地点尚未确定。',['night-pending'])
        self.assertFalse(self.score('pending-location',data)['checks']['required_facts'])
        data['answer']+='提议地点为河畔公园。';self.assertTrue(self.score('pending-location',data)['rule_passed'])

    def test_fixtures_are_valid_and_duplicates_are_rejected(self):
        _,digest=load_dataset(ROOT/'tests/fixtures/agent/text-v1.json');self.assertEqual(len(digest),64)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.json';bad=copy.deepcopy(self.data);bad['cases'].append(bad['cases'][0]);path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):load_dataset(path)

    def test_cloud_or_remote_models_do_not_run_implicitly(self):
        config=load_settings(ROOT/'config/mac.json');config['llm']['backend']='ollama';config['llm']['base_url']='http://127.0.0.1:11434';config['retrieval']['base_url']='http://localhost:11434'
        self.assertIs(safe_local_config(config),config)
        for section in ('llm','retrieval'):
            bad=copy.deepcopy(config);bad[section]['base_url']='https://example.com'
            with self.assertRaises(ValueError):safe_local_config(bad)
        config['llm']['backend']='openai_compatible'
        with self.assertRaises(ValueError):safe_local_config(config)

    def test_report_escapes_model_text_instead_of_rendering_markup(self):
        row=self.score('place-paraphrase',self.result('<script>alert(1)</script>青藤室',['book-current']))
        row.update(id='example',category='test',query='<img src=x>',expected_refs=['book-current'],elapsed_ms=1)
        page=render_html({'model':'qwen','dataset':'synthetic','label':'first','dataset_sha256':'digest','summary':{'passed':1,'total':1},'cases':[row]})
        self.assertNotIn('<script>',page);self.assertNotIn('<img src=x>',page);self.assertIn('&lt;script&gt;',page)

if __name__=='__main__':unittest.main()
