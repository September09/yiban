"""Synthetic, isolated tests of retrieval quality, cache identity and access boundaries."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from yiban.memory.retrieval import MemoryRetriever, RetrievalUnavailable, VectorCache, validate_config, unit_vector
from yiban.turns import Cancellation, Cancelled
from yiban.conversation_diagnostics import TraceStore
import test_shared_memory as fixture
import test_shared_http as http_fixture


class Provider:
    def __init__(self):self.calls=[];self.digest='model-v1'
    def fingerprint(self,cancel,deadline):cancel.check();return self.digest
    def embed(self,texts,cancel,deadline):
        cancel.check();self.calls.append(list(texts))
        return [[1.0]+[0.0]*15 for _ in texts]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cache=Path(self.temp.name)/'vectors.sqlite3'
        self.provider=Provider();self.r=MemoryRetriever({},self.cache,self.provider)
        self.items=[{'id':'a','version':1,'content':'春季植树由李老师带队，集合地点为青松公园。','status':'valid'}]
    def tearDown(self):self.temp.cleanup()
    def get(self,**kwargs):return self.r.retrieve('一起栽树谁带队',self.items,namespace='shared:one',**kwargs)

    def test_empty_candidates_do_not_call_model(self):
        result=self.r.retrieve('你好',[],namespace='shared:one')
        self.assertEqual(result['decision'],'no_match');self.assertEqual(self.provider.calls,[])

    def test_lexical_rejects_unrelated_and_missing_attributes(self):
        self.r=MemoryRetriever({'strategy':'lexical'})
        for query in ('宠物猫吃什么药','春季植树预算多少钱','春季植树负责人的电话'):
            self.assertEqual(self.r.retrieve(query,self.items,namespace='s')['hits'],[])
        self.assertEqual(self.r.retrieve('春季植树集合地点',self.items,namespace='s')['hits'][0]['id'],'a')

    def test_cache_identity_includes_namespace_version_content_and_model(self):
        self.get();self.assertEqual(self.get()['cache_hits'],1)
        self.items[0]['version']=2;self.assertEqual(self.get()['cache_hits'],0)
        self.items[0]['content']='植树改到河边公园';self.assertEqual(self.get()['cache_hits'],0)
        self.provider.digest='model-v2';self.assertEqual(self.get()['cache_hits'],0)
        self.assertEqual(self.r.retrieve('植树',self.items,namespace='other')['cache_hits'],0)
        self.r.invalidate('shared:one','a');self.assertEqual(self.get()['cache_hits'],0)
        self.r.invalidate('shared:one');self.assertEqual(self.get()['cache_hits'],0)

    def test_cache_does_not_store_plaintext(self):
        self.get()
        self.assertNotIn(self.items[0]['content'].encode(),self.cache.read_bytes())
        self.assertNotIn('一起栽树谁带队'.encode(),self.cache.read_bytes())

    def test_unavailable_service_falls_back_without_error_text(self):
        self.provider.embed=Mock(side_effect=RetrievalUnavailable('secret upstream data'))
        result=self.get();self.assertTrue(result['fallback']);self.assertEqual(result['strategy'],'lexical')
        self.assertNotIn('secret',json.dumps(result))

    def test_corrupt_index_falls_back(self):
        self.cache.write_bytes(b'not sqlite');result=self.get()
        self.assertTrue(result['fallback']);self.assertEqual(result['reason_code'],'embedding_unavailable')

    def test_vector_validation_rejects_nan_zero_and_dimensions(self):
        for vector in ([0]*16,[float('nan')]*16,[1]*3,[True]*16):
            with self.assertRaises(RetrievalUnavailable):unit_vector(vector)

    def test_cancel_before_and_during_embedding_never_falls_back(self):
        cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):self.get(cancel=cancel)
        cancel=Cancellation()
        def embed(*args):cancel.cancel();cancel.check()
        self.provider.embed=embed
        with self.assertRaises(Cancelled):self.get(cancel=cancel)
        self.assertFalse(self.r._lock.locked())

    def test_cancel_while_waiting_for_retriever(self):
        cancel=Cancellation();errors=[];self.r._lock.acquire()
        def run():
            try:self.get(cancel=cancel)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run);worker.start();cancel.cancel();worker.join(1);self.r._lock.release()
        self.assertFalse(worker.is_alive());self.assertIsInstance(errors[0],Cancelled)

    def test_inputs_and_remote_endpoint_are_rejected(self):
        for url in ('https://example.com','http://127.0.0.1@evil.test','http://127.0.0.1/path'):
            with self.assertRaises(ValueError):validate_config({'base_url':url})
        for top_k in (0,9,True):
            with self.assertRaises(ValueError):self.get(top_k=top_k)
        with self.assertRaises(ValueError):self.r.retrieve('x',self.items*2,namespace='s')

    def test_diagnostics_are_metadata_only(self):
        trace=TraceStore();identity=trace.begin(str(uuid.uuid4()))
        trace.retrieval(identity,dict(self.get(),content='secret',query='secret',token='secret'))
        encoded=json.dumps(trace.read(identity));self.assertNotIn('secret',encoded);self.assertNotIn('hits',trace.read(identity)['retrieval'])
        self.assertEqual(trace.read(identity)['retrieval']['strategy'],'hybrid')


class RetrievalAccessTests(unittest.TestCase):
    setUp=fixture.SharedMemoryTests.setUp
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish

    def attach(self):
        provider=Provider();self.shared.retriever=MemoryRetriever({},Path(self.temp.name)/'vectors.sqlite3',provider)
        return provider

    def test_acl_public_only_status_and_space_filter_before_embedding(self):
        self.publish(content='公开当前记录',status='valid',status_reason='人工核对原始记录')
        self.publish(content='秘密指定记录',visibility='selected')
        self.publish(content='旧版不应默认加载',status='outdated',status_reason='已变更')
        self.attach();r=self.shared.retriever
        with patch.object(r,'retrieve',wraps=r.retrieve) as spy:
            result=self.shared.question_context(self.token('owner'),self.space,'当前记录',public_only=True)
            self.assertEqual([x['content'] for x in spy.call_args.args[1]],['公开当前记录'])
            self.assertEqual(len(result['sources']),1)
        self.assertEqual(len(r.provider.calls[0]),1)
        with self.assertRaises(fixture.SharedError):self.shared.question_context(self.token('outsider'),self.space,'当前记录')

    def test_retriever_cannot_inject_ids_or_stale_versions(self):
        item=self.publish();self.attach()
        for hit in ({'id':'foreign','version':1},{'id':item['id'],'version':999}):
            with patch.object(self.shared.retriever,'retrieve',return_value={'hits':[hit]}):
                with self.assertRaises(fixture.SharedError) as error:self.shared.question_context(self.token('reader'),self.space,'家庭')
                self.assertEqual(error.exception.status,409)

    def test_revocation_during_retrieval_blocks_prompt(self):
        self.publish();self.attach()
        original=self.shared.retriever.retrieve
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs)
            self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
            return result
        with patch.object(self.shared.retriever,'retrieve',side_effect=revoke):
            with self.assertRaises(fixture.SharedError) as error:self.shared.question_context(self.token('reader'),self.space,'家庭')
            self.assertEqual(error.exception.status,403)

    def test_delete_invalidates_vector_and_never_retrieves_deleted_memory(self):
        item=self.publish();self.attach();self.shared.question_context(self.token('reader'),self.space,'家庭')
        self.shared.delete_memory(self.token('owner'),{'space_id':self.space,'id':item['id'],'version':item['version'],'confirmed':True})
        result=self.shared.question_context(self.token('reader'),self.space,'家庭');self.assertEqual(result['sources'],[])
        with self.shared.retriever.cache.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM vectors').fetchone()[0],0)


class RetrievalHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def test_no_match_skips_llm_and_does_not_mutate_memories(self):
        owner,reader,space,_=self.setup_space('retrieval')
        before=self.app.SHARED.memories(owner,space)
        with patch.object(self.app.SHARED,'retriever',MemoryRetriever({'strategy':'lexical'})),patch.object(self.app.SERVICES.models,'chat') as chat:
            status,result=self.post('/ask',{'space_id':space,'query':'火星上有没有鲸鱼'},reader)
            self.assertEqual(status,200);self.assertEqual(result['sources'],[]);chat.assert_not_called()
            self.assertEqual(result['retrieval']['hit_count'],0)
        self.assertEqual(self.app.SHARED.memories(owner,space),before)
