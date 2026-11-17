"""Reviewed packets retain real ranges but never bypass approval or membership."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.shared_import import SharedImport

class ReviewedMaterialsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryStore(Path(self.tmp.name)/'test.sqlite3');self.shared=SharedMemory(self.store)
        self.tokens={}
        for name in ['owner','reader','outsider']:
            self.shared.register({'name':name,'password':'review-test-password'})
            self.tokens[name]=self.shared.login({'name':name,'password':'review-test-password'})['token']
        self.space=self.shared.create_space(self.tokens['owner'],{'name':'独立审阅测试','kind':'team'})['id']
        self.shared.set_member(self.tokens['owner'],{'space_id':self.space,'name':'reader','role':'reader'})
        self.service=SharedImport(self.shared,SimpleNamespace())
        original='原始通知：星光活动在北门集合。'
        self.document={'schema':'yiban.reviewed-materials.v1','batch_name':'人工审阅第一批',
            'sources':[{'name':'通知.txt','text':original,'sha256':hashlib.sha256(original.encode()).hexdigest()}],
            'items':[{'review_key':'R001','content':'星光活动在北门集合。','event_date':'','status':'pending','source_note':'通知记录','source_index':0,
                      'ranges':[{'start':5,'end':len(original),'text':original[5:]}]}],
            'scope_hint':{'space_name':'其他空间','visibility':'all','edit_policy':'editors'}}

    def read(self,document=None,who='owner'):
        return self.service.dispatch('reviewed',self.tokens[who],{'space_id':self.space,'document':document or self.document})
    def count(self):
        with self.store.connect() as db:return db.execute('SELECT count(*) FROM shared_memories').fetchone()[0]
    def plan(self,draft):
        return self.service.preview(self.tokens['owner'],{'space_id':self.space,'batch_name':draft['batch_name'],'mode':'manual','keep_evidence':True,
            'items':[{**item,'visibility':'all','edit_policy':'author','viewers':[]} for item in draft['candidates']]})

    def test_read_preview_confirm_exact_evidence_and_permissions(self):
        draft=self.read();self.assertEqual(self.count(),0)
        self.assertEqual(draft['candidates'][0]['review_key'],'R001')
        self.assertNotIn('visibility',draft['candidates'][0]);self.assertNotIn('edit_policy',draft['candidates'][0])
        plan=self.plan(draft);self.assertEqual(self.count(),0)
        with self.assertRaises(SharedError):self.service.confirm(self.tokens['owner'],{'space_id':self.space,'token':plan['token']})
        result=self.service.confirm(self.tokens['owner'],{'space_id':self.space,'token':plan['token'],'confirmed':True})
        self.assertEqual(result['imported'],1)
        with self.store.connect() as db:identity=db.execute('SELECT id FROM shared_memories').fetchone()[0]
        source=self.shared.evidence.read(self.tokens['reader'],{'space_id':self.space,'id':identity})
        self.assertEqual(source['quotes'][0]['text'],self.document['items'][0]['ranges'][0]['text'])
        with self.assertRaises(SharedError):self.shared.evidence.read(self.tokens['reader'],{'space_id':self.space,'id':identity,'include_full':True})

    def test_reader_and_outsider_cannot_load(self):
        for who in ['reader','outsider']:
            with self.subTest(who=who),self.assertRaises(SharedError):self.read(who=who)
        self.assertEqual(self.count(),0)

    def test_bad_schema_hash_range_date_status_rejected(self):
        variants=[]
        d=copy.deepcopy(self.document);d['schema']='yiban.article-memory-review.v1';variants.append(d)
        d=copy.deepcopy(self.document);d['sources'][0]['sha256']='wrong';variants.append(d)
        d=copy.deepcopy(self.document);d['items'][0]['ranges'][0]['text']='编造引文';variants.append(d)
        d=copy.deepcopy(self.document);d['items'][0]['source_index']=True;variants.append(d)
        d=copy.deepcopy(self.document);d['items'][0]['event_date']='2026-04';variants.append(d)
        d=copy.deepcopy(self.document);d['items'][0]['status']='valid';variants.append(d)
        d=copy.deepcopy(self.document);d['items']*=41;variants.append(d)
        d=copy.deepcopy(self.document);d['items']*=2;variants.append(d)
        for d in variants:
            with self.subTest(document=d),self.assertRaises(SharedError):self.read(d)
        self.assertEqual(self.count(),0)

    def test_valid_state_retained_and_cross_session_reference_denied(self):
        d=copy.deepcopy(self.document);d['items'][0].update(status='valid',status_reason='测试角色已核对')
        draft=self.read(d);self.assertEqual(draft['candidates'][0]['status'],'valid')
        token=self.shared.login({'name':'owner','password':'review-test-password'})['token']
        item=draft['candidates'][0]
        with self.assertRaises(SharedError):self.shared.evidence.resolve(token,self.space,item['evidence_ref'],item['content'])
        self.assertEqual(self.count(),0)
