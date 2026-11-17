"""Synthetic local extraction, privacy and confirmation boundaries; no production inputs."""
import copy
import json
import unittest
from unittest.mock import patch
import test_memory_v2 as fixture
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation,Cancelled

class LocalExtractor:
    config={'backend':'ollama','base_url':'http://127.0.0.1:11434'}
    def __init__(self):self.seen=[];self.reply=None;self.callback=None
    def chat(self,messages,**kw):
        self.seen.append(messages)
        paragraphs=json.loads(messages[-1]['content']);p=next(p for p in paragraphs if '完成志愿活动' in p['text'])
        result=self.reply or {'private_paragraphs':[],'cards':[{
            'paragraph':p['paragraph'],'title':'志愿活动','what':'柳叶完成志愿活动。','quote':'柳叶完成志愿活动。','private':False,'type':'event','who_names':['柳叶']}]}
        yield {'message':{'content':json.dumps(result,ensure_ascii=False)},'done':True}
        if self.callback:self.callback()

class MarkdownTests(unittest.TestCase):
    setUp=fixture.V2Tests.setUp;tearDown=fixture.V2Tests.tearDown;rows=fixture.V2Tests.rows
    def begin(self,token=None,scope=None,**kw):
        self.service.models=getattr(self,'local',None) or LocalExtractor();self.local=self.service.models
        self.target=scope or self.scope
        return self.service.dispatch('md/start',token or self.owner,{**self.target,'name':'活动.md','content':'柳叶完成志愿活动。\n\n联系电话13812345678，邮箱private@example.com。',**kw})
    def next(self,draft,token=None,cancel=None):
        return self.service.dispatch('md/next',token or self.owner,{**self.target,'extraction_token':draft['extraction_token']},cancel)
    def review(self,draft,token=None,**kw):
        return self.service.dispatch('md/review',token or self.owner,{**self.target,'extraction_token':draft['extraction_token'],**kw})
    def test_extract_and_redact_are_read_only_and_private_full_source_is_removed(self):
        d=self.begin();self.next(d);result=self.review(d)
        self.assertEqual(len(result['private_candidates']),0) # PII paragraph has no model card, still removed.
        self.assertEqual(result['private_count'],1)
        encoded=json.dumps(result['document'],ensure_ascii=False)
        self.assertNotIn('13812345678',encoded);self.assertNotIn('private@example.com',encoded)
        self.assertIn('13812345678',json.dumps(result['paragraphs']))
        self.assertEqual(self.rows(),[]);self.assertEqual(self.service.plans,{})
        e=result['document']['records'][0]['evidence'][0];source=result['document']['sources'][0]
        self.assertEqual(source['text'][e['start']:e['end']],e['text'])
    def test_model_can_mark_private_paragraph_without_a_private_card(self):
        d=self.begin(content='柳叶完成志愿活动。\n\n家中有一件不希望公开的事。')
        self.local.reply={'private_paragraphs':[1],'cards':[]};self.next(d)
        r=self.review(d);self.assertNotIn('不希望公开',r['document']['sources'][0]['text'])
    def test_human_can_add_private_paragraph_but_cannot_unmask_required(self):
        d=self.begin();self.next(d);r=self.review(d,private_paragraphs=[0])
        self.assertEqual(r['private_count'],2);self.assertEqual(r['document']['records'],[])
        self.assertNotIn('完成志愿活动',r['document']['sources'][0]['text'])
        r=self.review(d,private_paragraphs=[]);self.assertEqual(r['private_count'],1)
    def test_model_paragraph_reference_uses_server_original_not_model_copy(self):
        d=self.begin()
        self.local.reply={'private_paragraphs':[],'cards':[{'paragraph':0,'type':'quote','title':'活动','what':'柳叶完成志愿活动。','private':False}]}
        self.next(d);r=self.review(d)['document']['records'][0]
        self.assertEqual(r['evidence'][0]['text'],'柳叶完成志愿活动。')

    def test_whitespace_only_quote_alignment_keeps_exact_source_characters(self):
        from yiban.memory.md_extraction import original_quote
        original='2027年3月1日，柳叶收集12本图书。'
        self.assertEqual(original_quote('2027 年 3 月 1 日，柳叶收集 12 本图书。',original),original)
        with self.assertRaises(SharedError):original_quote('2027年3月1日，柳叶收集99本图书。',original)
        with self.assertRaises(SharedError):original_quote('2027 年3月1日，柳叶收集12本图书。',original+' '+original)

    def test_only_explicit_confirmation_saves_pending(self):
        d=self.begin();self.next(d);doc=self.review(d)['document']
        p=self.service.preview(self.owner,{**self.scope,'document':doc,'visibility':'all','edit_policy':'author'})
        self.assertEqual(self.rows(),[])
        with self.assertRaises(SharedError):self.service.confirm(self.owner,{**self.scope,'token':p['token']})
        self.service.confirm(self.owner,{**self.scope,'token':p['token'],'confirmed':True,'acknowledge_warnings':True})
        r=self.rows()[0];self.assertEqual(r['verification'],'pending');self.assertEqual(r['visibility'],'all');self.assertEqual(r['edit_policy'],'author')
    def test_reader_and_anonymous_fail_before_model_or_private_data(self):
        for actor in (self.reader,self.users['outsider']['token']):
            with self.assertRaises(SharedError):self.begin(token=actor)
        with self.assertRaises(SharedError):
            self.service.dispatch('md/start',None,{**self.scope,'name':'a.md','content':'秘密'})
    def test_production_model_router_is_cloned_with_bounded_extraction_budget(self):
        from pathlib import Path
        from yiban.backends.model_settings import ModelSettings
        from yiban.settings import DEFAULTS
        from yiban.memory.md_extraction import MarkdownExtraction
        self.service.models=ModelSettings(DEFAULTS['llm'],Path(self.tmp.name)/'model-settings.json')
        before=copy.deepcopy(self.service.models.config)
        client=MarkdownExtraction(self.service).local_model()
        self.assertEqual(client.config['max_tokens'],1600)
        self.assertEqual(self.service.models.config,before)

    def test_cloud_or_nonloopback_models_receive_no_raw_input(self):
        for config in ({'backend':'openai_compatible','base_url':'https://cloud.test'},
                       {'backend':'ollama','base_url':'http://remote.test:11434'}):
            self.local=LocalExtractor();self.local.config=config
            with self.assertRaises(SharedError):self.begin()
            self.assertEqual(self.local.seen,[])
    def test_no_links_or_path_fetch_and_filename_pii_is_redacted(self):
        d=self.begin(name='13812345678.md',content='柳叶完成志愿活动。\n\n![图](https://untrusted.test/a.png)\n\n[项目](https://untrusted.test/)')
        self.next(d);r=self.review(d);raw=json.dumps(r['document'],ensure_ascii=False)
        self.assertNotIn('https://untrusted.test',raw);self.assertNotIn('13812345678',raw)
    def test_paraphrased_quote_and_unknown_authority_fields_are_rejected(self):
        for bad in ({'paragraph':0,'title':'活动','what':'活动','quote':'改写的活动。','private':False},
                    {'paragraph':0,'title':'活动','what':'活动','quote':'柳叶完成志愿活动。','private':False,'owner':'another'}):
            d=self.begin();self.local.reply={'private_paragraphs':[],'cards':[bad]}
            with self.assertRaises(SharedError):self.next(d)
            with self.assertRaises(SharedError):self.review(d)
    def test_session_scope_and_expiry_bind_raw_draft(self):
        d=self.begin()
        with self.assertRaises(SharedError):self.review(d,token=self.editor)
        self.target={'scope_kind':'personal'}
        with self.assertRaises(SharedError):self.review(d)
        self.target=self.scope
        self.service.md_extraction.jobs[d['extraction_token']]['expires']=0
        with self.assertRaises(SharedError):self.next(d)
    def test_revoke_or_mutation_during_model_cannot_return_result(self):
        for change in ('revoke','version'):
            if change=='version':self.setUp()
            try:
                d=self.begin(token=self.editor)
                if change=='revoke':
                    self.local.callback=lambda:self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['editor']['account']['id']},True)
                else:
                    def mutate():
                        with self.store.connect() as db:db.execute('update shared_spaces set version=version+1 where id=?',(self.space,))
                    self.local.callback=mutate
                with self.assertRaises(SharedError):self.next(d,token=self.editor)
                self.assertEqual(self.rows(),[])
            finally:self.tearDown()
    def test_cancel_and_discard_do_not_publish_or_continue(self):
        d=self.begin();cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):self.next(d,cancel=cancel)
        self.service.dispatch('md/discard',self.owner,{**self.scope,'extraction_token':d['extraction_token']})
        with self.assertRaises(SharedError):self.next(d)
    def test_limits_and_review_numbers_are_strict(self):
        for kw in ({'name':'../a.md'},{'name':'image.png'},{'content':'x'*24001},{'content':''}):
            with self.assertRaises(SharedError):self.begin(**kw)
        d=self.begin();self.next(d)
        for values in ([True],[999],['0']):
            with self.assertRaises(SharedError):self.review(d,private_paragraphs=values)
    def test_fact_values_need_verbatim_numbers_and_schema(self):
        d=self.begin(content='柳叶完成志愿活动，柳叶收集12本图书。')
        base={'paragraph':0,'title':'图书','what':'柳叶收集12本图书。','quote':'柳叶收集12本图书。','private':False,'type':'fact',
              'fact':{'predicate':'quantity','subject':'柳叶','value_json':'12','unit':'本','metric':'收集'}}
        self.local.reply={'private_paragraphs':[],'cards':[base]};self.next(d);r=self.review(d)['document']['records'][0]
        self.assertEqual(r['fact']['value'],12);self.assertEqual(r['verification'],'pending')
        d=self.begin(content='柳叶完成志愿活动，柳叶收集12本图书。')
        base['fact']['value_json']='99';self.local.reply={'private_paragraphs':[],'cards':[base]};self.next(d)
        r=self.review(d);self.assertIsNone(r['document']['records'][0]['fact']);self.assertTrue(r['warnings'])

if __name__=='__main__':unittest.main()
