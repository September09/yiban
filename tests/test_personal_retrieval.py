"""Only this profile's current records may influence semantic personal-memory answers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yiban.memory.memory_store import MemoryStore
from yiban.memory.personal_retrieval import PersonalMemory,PersonalMemoryChanged
from yiban.memory.retrieval import MemoryRetriever,RetrievalUnavailable
from yiban.turns import Cancellation,Cancelled
import test_personal_http as http_fixture


class SemanticProvider:
    def __init__(self):self.seen=[]
    def fingerprint(self,*args):return 'synthetic-personal-v1'
    def embed(self,texts,cancel,deadline):
        self.seen+=texts;cancel.check();vectors=[]
        for text in texts:
            slot=0 if any(x in text for x in ('拿铁','饮品','咖啡')) else 1 if any(x in text for x in ('慢跑','运动','解压')) else 2
            vector=[0.]*16;vector[slot]=1.;vectors.append(vector)
        return vectors


class Model:
    def __init__(self,answer=None,callback=None):self.seen=[];self.answer=answer or {'answer':'你通常喜欢低糖燕麦拿铁。','references':[1]};self.callback=callback
    def chat(self,messages,**kwargs):
        self.seen.append(messages)
        if self.callback:self.callback()
        yield {'message':{'content':json.dumps(self.answer,ensure_ascii=False)},'done':True}


class PersonalRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=MemoryStore(Path(self.temp.name)/'test.sqlite3')
        self.a=self.store.create_profile('甲')['id'];self.b=self.store.create_profile('乙')['id']
        self.ma=self.store.save(self.a,'我喜欢低糖燕麦拿铁。');self.mb=self.store.save(self.b,'乙的私密咖啡偏好是全糖热可可。')
        self.provider=SemanticProvider();self.retriever=MemoryRetriever({},Path(self.temp.name)/'vectors.sqlite3',self.provider)
        self.model=Model();self.service=PersonalMemory(self.store,self.retriever,self.model);self.store.personal_retriever=self.retriever
    def tearDown(self):self.temp.cleanup()

    def test_semantic_paraphrase_is_profile_scoped_before_embedding_and_model(self):
        result=self.service.answer(self.a,'我平时的饮品口味是什么？')
        self.assertEqual(result['answer_source_ids'],[self.ma]);self.assertEqual(result['sources'][0]['revision'],1)
        sent=json.dumps(self.model.seen,ensure_ascii=False)+' '.join(self.provider.seen)
        self.assertNotIn('全糖热可可',sent);self.assertEqual(result['profile']['id'],self.a)
        self.assertEqual(result['sources'][0]['status'],'pending')

    def test_guest_and_missing_personal_fact_do_not_call_answer_model(self):
        result=self.service.answer(None,'我的饮品习惯')
        self.assertEqual(result['sources'],[]);self.assertEqual(self.provider.seen,[]);self.assertEqual(self.model.seen,[])
        result=self.service.answer(self.a,'我用什么运动来解压？')
        self.assertEqual(result['sources'],[]);self.assertIn('没有找到',result['answer']);self.assertEqual(self.model.seen,[])

    def test_unavailable_embeddings_fall_back_with_threshold_and_explicit_metadata(self):
        with patch.object(self.provider,'embed',side_effect=RetrievalUnavailable('secret upstream')):
            match=self.service.search(self.a,'我喜欢低糖燕麦拿铁吗？')
            missing=self.service.search(self.a,'我的护照编号是什么？')
        self.assertTrue(match['retrieval']['fallback']);self.assertEqual(match['sources'][0]['id'],self.ma)
        self.assertEqual(missing['sources'],[]);self.assertNotIn('secret',json.dumps(match))

    def test_candidate_budget_versions_and_no_plaintext_cache(self):
        self.service.search(self.a,'饮品');self.assertEqual(self.service.search(self.a,'饮品')['retrieval']['cache_hits'],1)
        self.store.save(self.a,'我喜欢无糖燕麦拿铁。',self.ma)
        changed=self.service.search(self.a,'饮品');self.assertEqual(changed['retrieval']['cache_hits'],0)
        self.assertIn('无糖',changed['sources'][0]['content'])
        self.assertNotIn('燕麦拿铁'.encode(),self.retriever.cache.path.read_bytes())
        self.store.delete_memory(self.a,self.ma)
        with self.retriever.cache.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE namespace=?',('personal:'+self.a,)).fetchone()[0],0)

    def test_profile_delete_clears_only_its_namespace(self):
        self.service.search(self.a,'饮品');self.service.search(self.b,'饮品')
        self.store.delete_profile(self.a)
        with self.retriever.cache.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE namespace=?',('personal:'+self.a,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE namespace=?',('personal:'+self.b,)).fetchone()[0],1)

    def test_change_during_embedding_rejects_stale_results_and_late_cache(self):
        original=self.provider.embed;changed=[]
        def embed(*args):
            result=original(*args)
            if not changed:changed.append(True);self.store.delete_memory(self.a,self.ma)
            return result
        with patch.object(self.provider,'embed',side_effect=embed):
            with self.assertRaises(PersonalMemoryChanged):self.service.search(self.a,'饮品')
        with self.retriever.cache.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE namespace=?',('personal:'+self.a,)).fetchone()[0],0)

    def test_changed_or_deleted_profile_during_answer_never_returns_old_text(self):
        self.model.callback=lambda:self.store.save(self.a,'改成美式咖啡',self.ma)
        with self.assertRaises(PersonalMemoryChanged):self.service.answer(self.a,'饮品')
        self.model.callback=lambda:self.store.delete_profile(self.a)
        with self.assertRaises(PersonalMemoryChanged):self.service.answer(self.a,'饮品')

    def test_bad_and_empty_references_cannot_be_presented_as_cited_answer(self):
        for answer in ({'answer':'凭空回答','references':[99]},{'answer':'凭空回答','references':[]},{'answer':'凭空回答'}):
            self.model.answer=answer;result=self.service.answer(self.a,'饮品')
            self.assertNotIn('凭空回答',result['answer']);self.assertEqual(result['answer_source_ids'],[])

    def test_read_only_and_cancelled_query_does_not_save_or_use_history_as_candidates(self):
        self.store.save(self.a,'咖啡改成无糖拿铁',self.ma)
        with self.store.connect() as db:before='\n'.join(db.iterdump())
        result=self.service.search(self.a,'饮品');self.assertNotIn('低糖',result['context'])
        with self.store.connect() as db:self.assertEqual(before,'\n'.join(db.iterdump()))
        cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):self.service.answer(self.a,'饮品',cancel)


class PersonalRetrievalHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.PersonalHTTPTests.tearDownClass.__func__)
    post=http_fixture.PersonalHTTPTests.post

    def test_ask_search_routes_and_read_only_profile_scope(self):
        a=self.post('/api/profiles/create',{'name':'语义甲'})[0]['id'];b=self.post('/api/profiles/create',{'name':'语义乙'})[0]['id']
        saved=self.post('/api/memory/save',{'profile_id':a,'content':'我喜欢低糖燕麦拿铁。'})[0]
        provider=SemanticProvider();retriever=MemoryRetriever({},None,provider)
        with patch.object(self.app.PERSONAL_MEMORY,'retriever',retriever),patch.object(self.app.SERVICES.models,'chat',side_effect=Model().chat):
            result=self.post('/api/memory/ask',{'profile_id':a,'query':'饮品习惯'})[0]
            self.assertEqual(result['answer_source_ids'],[saved['id']])
            self.assertEqual(self.post('/api/memory/search',{'profile_id':b,'query':'饮品习惯'})[0]['sources'],[])
            self.assertEqual(self.post('/api/memory/ask',{'query':'饮品习惯'})[0]['sources'],[])
        self.assertEqual(len(self.app.STORE.memories(a)),1)

    def test_ordinary_chat_returns_candidates_and_blocks_generation_race(self):
        a=self.post('/api/profiles/create',{'name':'聊天检索甲'})[0]['id']
        item=self.post('/api/memory/save',{'profile_id':a,'content':'我喜欢低糖燕麦拿铁。'})[0]
        retriever=MemoryRetriever({},None,SemanticProvider())
        with patch.object(self.app.PERSONAL_MEMORY,'retriever',retriever),patch.object(self.app.SERVICES.models,'chat',side_effect=Model().chat):
            result=self.post('/api/chat',{'profile_id':a,'messages':[{'role':'user','content':'饮品习惯'}]})
        self.assertEqual(result[0]['personal_memory']['sources'][0]['id'],item['id'])
        model=Model(callback=lambda:self.app.STORE.save(a,'后来改喝美式咖啡',item['id']))
        with patch.object(self.app.PERSONAL_MEMORY,'retriever',retriever),patch.object(self.app.SERVICES.models,'chat',side_effect=model.chat):
            import urllib.error
            with self.assertRaises(urllib.error.HTTPError):self.post('/api/chat',{'profile_id':a,'messages':[{'role':'user','content':'饮品习惯'}]})


if __name__=='__main__':unittest.main()
