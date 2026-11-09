"""Provenance and status tests use synthetic text and temporary databases."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_shared_memory as fixture
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.shared_import import SharedImport
from yiban.memory.memory_store import MemoryStore
from yiban.backends.models import ModelClient

class EvidenceTests(unittest.TestCase):
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish
    def setUp(self):
        fixture.SharedMemoryTests.setUp(self)
        self.importer=SharedImport(self.shared,ModelClient({'model':'test','base_url':'http://127.0.0.1:11434'}))
    def reject(self,expected,fn,*args,**kwargs):
        with self.assertRaises(SharedError) as error:fn(*args,**kwargs)
        self.assertEqual(error.exception.status,expected)
    def stage(self):
        return self.importer.organize(self.token('editor'),{'space_id':self.space,'sources':[{'name':'activity.pdf','text':'【第 2 页】\n活动在公园\U0001f600。\n\n私人内容不要公开。'}]})['candidates']
    def preview(self,candidates,keep=True,**fields):
        return self.importer.preview(self.token('editor'),{'space_id':self.space,'keep_evidence':keep,'items':[
            self.payload(content=c['content'],evidence_ref=c['evidence_ref'],**fields) for c in candidates]})
    def confirm(self,plan):
        return self.importer.confirm(self.token('editor'),{'space_id':self.space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
    def newest(self):
        return self.shared.memories(self.token('editor'),self.space)['memories'][0]
    def read(self,who,item,full=False):
        return self.shared.evidence.read(self.token(who),{'space_id':self.space,'id':item['id'],'include_full':full})
    def edit(self,item,**changes):
        data=self.payload(**{k:item[k] for k in ('id','version','content','visibility','viewers','edit_policy','event_date','source_note','status','status_reason')})
        data.update(changes);p=self.shared.preview(self.token('editor'),data)
        self.shared.confirm(self.token('editor'),{'space_id':self.space,'token':p['token'],'confirmed':True,'acknowledge_conflicts':True})
        return self.newest()

    def test_confirmed_snapshot_and_private_full_source(self):
        p=self.preview(self.stage()[:1])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_evidence').fetchone()[0],0)
        p['items'][0]['evidence']['source_text']='tamper'
        self.confirm(p);item=self.newest();e=self.read('reader',item)
        self.assertTrue(e['available']);self.assertFalse(e['can_read_full'])
        self.assertNotIn('私人内容',json.dumps(e,ensure_ascii=False));self.assertNotIn('source_text',e)
        self.reject(403,self.read,'reader',item,True)
        full=self.read('editor',item,True)
        self.assertIn('私人内容',full['source_text'])
        for q in full['quotes']:self.assertEqual(full['source_text'][q['start']:q['end']],q['text'])
        self.assertIn('\U0001f600',full['quotes'][0]['text'])
        self.assertEqual(self.read('owner',item,True)['source_hash'],full['source_hash'])
        self.reject(403,self.read,'outsider',item)

    def test_opt_out_invalid_and_expired_source(self):
        p=self.preview(self.stage()[:1],keep=False);self.confirm(p)
        self.assertFalse(self.newest()['has_evidence']);self.assertFalse(self.read('editor',self.newest())['available'])
        c=self.stage()[1:];c[0]['evidence_ref']['candidate']=999
        self.reject(400,self.preview,c)
        c=self.stage()[1:];ref=c[0]['evidence_ref']
        self.shared.evidence.drafts[ref['draft']]['expires']=time.monotonic()-1
        self.reject(409,self.preview,c)

    def test_cross_session_space_and_discard(self):
        c=self.stage()[:1];ref=c[0]['evidence_ref']
        self.reject(403,self.shared.evidence.resolve,self.token('owner'),self.space,ref,'test')
        other=self.shared.create_space(self.token('editor'),{'name':'other','kind':'team'})['id']
        self.reject(403,self.shared.evidence.resolve,self.token('editor'),other,ref,'test')
        self.importer.discard(self.token('editor'),{'space_id':self.space,'draft':ref['draft']})
        self.reject(409,self.preview,c)

    def test_current_acl_and_delete_cascade(self):
        p=self.preview(self.stage()[:1],visibility='selected');self.confirm(p);item=self.newest()
        self.reject(403,self.read,'reader',item)
        item=self.edit(item,visibility='all')
        self.assertTrue(self.read('reader',item)['available'])
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},remove=True)
        self.reject(403,self.read,'reader',item)
        self.shared.delete_memory(self.token('editor'),{'space_id':self.space,'id':item['id'],'version':item['version'],'confirmed':True})
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_evidence').fetchone()[0],0)

    def test_status_lifecycle_history_and_edited_content(self):
        p=self.preview(self.stage()[:1]);self.confirm(p);item=self.newest()
        self.assertEqual(item['status'],'pending')
        self.reject(400,self.edit,item,status='valid',status_reason='')
        item=self.edit(item,status='valid',status_reason='已与负责人核对')
        self.assertEqual(item['status'],'valid')
        item=self.edit(item,status='outdated',status_reason='活动安排已更改')
        self.assertEqual(item['status'],'outdated')
        item=self.edit(item,content='活动改到博物馆。',status='valid',status_reason='不能沿用旧状态')
        self.assertEqual(item['status'],'pending')
        e=self.read('editor',item)
        self.assertTrue(e['content_changed']);self.assertIn('公园',e['captured_content'])
        history=self.shared.history(self.token('editor'),{'space_id':self.space,'id':item['id']})
        self.assertEqual([r['after']['status'] for r in reversed(history['entries'])],['pending','valid','outdated','pending'])

    def test_status_permission_and_stale_preview(self):
        item=self.publish('editor',content='活动信息')
        data=self.payload(**{k:item[k] for k in ('id','version','content','visibility','viewers','edit_policy')},status='valid',status_reason='核实')
        self.reject(403,self.shared.preview,self.token('reader'),data)
        p=self.shared.preview(self.token('editor'),data)
        self.edit(item,status='outdated',status_reason='安排变更')
        self.reject(409,self.shared.confirm,self.token('editor'),{'space_id':self.space,'token':p['token'],'confirmed':True})

    def test_query_states_and_no_full_text_sent_to_model(self):
        self.publish('editor',content='有效活动在公园',status='valid',status_reason='核实')
        self.publish('editor',content='待核实活动在学校')
        old=self.publish('editor',content='过时活动在商场',status='outdated',status_reason='改期')
        p=self.preview(self.stage()[:1]);self.confirm(p)
        context=self.shared.question_context(self.token('reader'),self.space,'活动')
        self.assertEqual(context['sources'][0]['status'],'valid')
        self.assertNotIn('过时活动在商场',context['context']);self.assertNotIn('私人内容',context['context'])
        self.assertIn('尚待核实',context['context'])
        for c in [self.shared.question_context(self.token('reader'),self.space,'活动',include_outdated=True),
                  self.shared.question_context(self.token('reader'),self.space,'查看过时活动',public_only=True)]:
            self.assertIn(old['id'],[x['id'] for x in c['sources']])

    def test_evidence_failure_rolls_back_batch(self):
        p=self.preview(self.stage()[:1])
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_evidence BEFORE INSERT ON shared_evidence BEGIN SELECT RAISE(ABORT,'test'); END;")
        with self.assertRaises(sqlite3.IntegrityError):self.confirm(p)
        with self.store.connect() as db:
            for table in ('shared_memories','shared_evidence','shared_history'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_legacy_migration_is_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store=MemoryStore(Path(directory)/'old.sqlite3')
            with store.connect() as db:
                db.execute("CREATE TABLE shared_memories(id TEXT PRIMARY KEY,space TEXT,author TEXT,editor TEXT,content TEXT,visibility TEXT,edit_policy TEXT,event_date TEXT DEFAULT '',source_note TEXT DEFAULT '',version INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP)")
                db.execute("INSERT INTO shared_memories(id,content) VALUES ('old','旧记录')")
            SharedMemory(store);SharedMemory(store)
            with store.connect() as db:
                self.assertEqual(tuple(db.execute("SELECT content,status,status_reason FROM shared_memories WHERE id='old'").fetchone()),('旧记录','pending',''))

    def test_ai_unicode_offsets_and_pdf_page(self):
        def events(*args,**kwargs):
            yield {'message':{'content':'[{"content":"活动在公园","source_ids":[2]}]'},'done':True}
        with patch('yiban.memory.shared_import.ModelClient.chat',events):
            c=self.importer.organize(self.token('editor'),{'space_id':self.space,'mode':'ai','sources':[{'name':'scan.pdf','text':'【第 2 页】\n\U0001f600活动在公园。\n私密信息。'}]})['candidates']
        p=self.preview(c);self.confirm(p)
        q=self.read('reader',self.newest())['quotes'][0]
        self.assertEqual(q['page'],2);self.assertEqual(q['line_start'],2);self.assertEqual(q['text'],'\U0001f600活动在公园。')
