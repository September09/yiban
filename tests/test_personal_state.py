"""Personal state and finite suspected-conflict rules; all data is synthetic and isolated."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from yiban.memory.memory_store import MemoryStore
from yiban.memory.personal_retrieval import PersonalMemory, PersonalMemoryChanged
from yiban.memory.personal_state import state_fields, conflict_pairs, possible_conflicts
from yiban.turns import Cancellation
import test_personal_http as fixture
import test_personal_retrieval as retrieval_fixture
from yiban.memory.retrieval import MemoryRetriever


class PersonalStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryStore(Path(self.tmp.name)/'memory.sqlite3')
        self.a=self.store.create_profile('状态甲')['id'];self.b=self.store.create_profile('状态乙')['id']
        self.store.enroll(self.a,[1,0,0],'synthetic-voice-model')
    def row(self,id):return next(x for x in self.store.memories(self.a) if x['id']==id)
    def state(self,id,**changes):
        row=self.row(id);plan=self.store.request_save(self.a,row['content'],id,row['revision'],**changes)['review']
        return self.store.confirm_save(self.a,plan['token'],True,'replace',id)
    def pair(self):
        left=self.store.save(self.a,'居住城市：南京');right=self.store.save(self.a,'我住在上海')
        return left,right
    def plan(self,left,right,**changes):
        return self.store.request_conflict(self.a,left,right,changes.get('action','prefer'),changes.get('preferred_id',left),changes.get('reason','已与本人核对当前情况'))

    def test_state_change_previews_then_audits_and_preserves_voiceprint(self):
        id=self.store.save(self.a,'我喜欢咖啡');before=self.store.path.read_bytes();row=self.row(id)
        plan=self.store.request_save(self.a,row['content'],id,row['revision'],status='valid',status_reason='本人已核对')['review']
        self.assertEqual(self.store.path.read_bytes(),before);self.assertFalse(plan['can_append'])
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],False,'replace',id)
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'append')
        self.store.confirm_save(self.a,plan['token'],True,'replace',id)
        self.assertEqual(self.row(id)['status'],'valid')
        history=self.store.history(self.a,id)['entries'][0]
        self.assertEqual((history['before_status'],history['after_status']),('pending','valid'))
        self.assertEqual(history['after_status_reason'],'本人已核对')
        self.assertEqual(len(self.store.voices('synthetic-voice-model')),1)

    def test_content_and_date_change_reset_verified_status_in_preview_and_commit(self):
        id=self.store.save(self.a,'居住城市：南京');self.state(id,status='valid',status_reason='已核对')
        row=self.row(id);plan=self.store.request_save(self.a,'居住城市：上海',id,row['revision'],status='valid',status_reason='')['review']
        self.assertTrue(plan['state_reset']);self.assertEqual(plan['status'],'pending')
        self.store.confirm_save(self.a,plan['token'],True,'replace',id)
        self.assertEqual(self.row(id)['status'],'pending')
        self.state(id,status='valid',status_reason='已重新核对')
        row=self.row(id);plan=self.store.request_save(self.a,row['content'],id,row['revision'],event_date='2026-11-13')['review']
        self.assertEqual(plan['status'],'pending');self.assertEqual(plan['status_reason'],'')
        self.store.confirm_save(self.a,plan['token'],True,'replace',id)
        self.assertEqual(self.row(id)['event_date'],'2026-11-13')

    def test_invalid_state_reason_date_and_cross_profile_cannot_modify(self):
        id=self.store.save(self.a,'当前资料')
        for data in ({'status':'unknown'},{'status':'valid'},{'status':'outdated','status_reason':''},{'event_date':'2026-02-30'},{'event_date':None,'status_reason':[]},{'event_date':True}):
            with self.assertRaises(ValueError):self.store.request_save(self.a,'当前资料',id,**data)
        with self.assertRaises(ValueError):self.store.request_save(self.b,'别人的资料',id,status='valid',status_reason='假的')
        self.assertEqual(self.row(id)['content'],'当前资料')

    def test_conflict_scan_is_profile_scoped_active_only_and_not_all_likes_conflict(self):
        left,right=self.pair();self.store.save(self.b,'居住城市：杭州')
        self.store.save(self.a,'我喜欢咖啡');self.store.save(self.a,'我喜欢茶')
        pairs=self.store.conflicts(self.a)
        self.assertEqual(pairs['total'],1);self.assertEqual({pairs['pairs'][0]['left']['id'],pairs['pairs'][0]['right']['id']},{left,right})
        self.state(right,status='outdated',status_reason='已不适用')
        self.assertEqual(self.store.conflicts(self.a)['total'],0)

    def test_prefer_pair_is_confirmed_atomic_retains_originals_and_history(self):
        left,right=self.pair();before=self.store.path.read_bytes();plan=self.plan(left,right)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual({c['after']['status'] for c in plan['changes']},{'valid','outdated'})
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.b,plan['token'],True)
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.a,plan['token'],1)
        self.store.confirm_conflict(self.a,plan['token'],True)
        self.assertEqual(self.row(left)['status'],'valid');self.assertEqual(self.row(right)['status'],'outdated')
        self.assertEqual(len(self.store.memories(self.a)),2);self.assertEqual(self.row(right)['content'],'我住在上海')
        self.assertEqual(self.store.history(self.a,right)['entries'][0]['operation'],'conflict_prefer')
        self.assertEqual(self.store.conflicts(self.a)['total'],0)
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.a,plan['token'],True)

    def test_pair_failure_rolls_back_both_status_and_history(self):
        left,right=self.pair();plan=self.plan(left,right);before=self.store.memories(self.a);hist=[self.store.history(self.a,id) for id in (left,right)]
        real=self.store._write_memory;count=[0]
        def write(*args,**kwargs):
            count[0]+=1
            if count[0]==2:raise RuntimeError('synthetic failure')
            return real(*args,**kwargs)
        with patch.object(self.store,'_write_memory',side_effect=write):
            with self.assertRaises(RuntimeError):self.store.confirm_conflict(self.a,plan['token'],True)
        self.assertEqual(before,self.store.memories(self.a));self.assertEqual(hist,[self.store.history(self.a,id) for id in (left,right)])
        self.store.confirm_conflict(self.a,plan['token'],True)

    def test_keep_both_remains_pending_and_suspected_not_false_resolution(self):
        left,right=self.pair();plan=self.plan(left,right,action='keep_both',preferred_id=None)
        self.store.confirm_conflict(self.a,plan['token'],True)
        self.assertEqual({r['status'] for r in self.store.memories(self.a)},{'pending'})
        self.assertEqual(self.store.conflicts(self.a)['total'],1)

    def test_cancel_expire_state_changes_and_wrong_endpoint_reject_old_plan(self):
        left,right=self.pair();plan=self.plan(left,right)
        with self.assertRaises(ValueError):self.store.confirm_save(self.a,plan['token'],True,'replace',left)
        self.store.cancel_review(self.a,plan['token'])
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.a,plan['token'],True)
        plan=self.plan(left,right);self.store._reviews[plan['token']]['expires']=0
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.a,plan['token'],True)
        plan=self.plan(left,right);self.state(right,status='outdated',status_reason='另一窗口已核对')
        with self.assertRaises(ValueError):self.store.confirm_conflict(self.a,plan['token'],True)

    def test_import_export_states_compatible_with_old_files_and_duplicate_keeps_current(self):
        id=self.store.save(self.a,'我喜欢咖啡');self.state(id,status='valid',status_reason='本人确认')
        doc=self.store.export_memories(self.a);self.assertEqual(doc['version'],1);self.assertEqual(doc['memories'][0]['status'],'valid')
        plan=self.store.preview_import(self.b,doc);self.assertEqual(plan['items'][0]['memory_status'],'valid')
        self.store.confirm_import(self.b,plan['token'],True)
        self.assertEqual(self.store.memories(self.b)[0]['status'],'valid')
        changed=copy.deepcopy(doc);changed['memories'][0].update(status='outdated',status_reason='文件版本已过时')
        preview=self.store.preview_import(self.a,changed);self.assertTrue(preview['items'][0]['state_differs']);self.assertFalse(preview['can_import'])
        self.assertEqual(self.row(id)['status'],'valid')
        old=copy.deepcopy(doc);old['memories'][0]['content']='旧文件新增'
        for key in ('status','status_reason','event_date'):old['memories'][0].pop(key)
        preview=self.store.preview_import(self.b,old);self.store.confirm_import(self.b,preview['token'],True)
        self.assertEqual(next(r for r in self.store.memories(self.b) if r['content']=='旧文件新增')['status'],'pending')

    def test_migration_does_not_invent_historical_states_or_touch_voiceprints(self):
        path=Path(self.tmp.name)/'old.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE);CREATE TABLE memories(id TEXT PRIMARY KEY,profile TEXT,content TEXT,updated TEXT);INSERT INTO profiles VALUES('a','旧档案');INSERT INTO memories VALUES('m','a','原始资料','2025-01-01 00:00:00');")
        old=MemoryStore(path);self.assertEqual(old.memories('a')[0]['status'],'pending')
        history=old.history('a','m');self.assertIsNone(history['entries'][0]['after_status'])
        again=MemoryStore(path);self.assertEqual(again.history('a','m')['total'],1)

    def test_conflict_query_lists_both_claims_without_invoking_model(self):
        left,right=self.pair()
        class Never:
            def chat(self,*args,**kw):raise AssertionError('conflict must not choose a truth with a model')
        service=PersonalMemory(self.store,models=Never());result=service.answer(self.a,'我的居住城市是什么')
        self.assertEqual(set(result['answer_source_ids']),{left,right});self.assertIn('南京',result['answer']);self.assertIn('上海',result['answer'])
        self.assertIn('暂不能确定',result['answer']);self.assertEqual(result['source_usage'],'program_selected')

    def test_outdated_excluded_before_embeddings_and_allowed_only_for_history(self):
        old=self.store.save(self.a,'我的居住城市是南京');self.state(old,status='outdated',status_reason='过去的城市')
        provider=retrieval_fixture.SemanticProvider();retriever=MemoryRetriever({},Path(self.tmp.name)/'vectors.sqlite3',provider)
        service=PersonalMemory(self.store,retriever)
        result=service.search(self.a,'居住城市');self.assertEqual(result['sources'],[])
        self.assertNotIn('我的居住城市是南京',provider.seen)
        history=service.search(self.a,'居住城市',include_outdated=True)
        self.assertIn('我的居住城市是南京',provider.seen)
        self.assertEqual([r['id'] for r in history['sources']],[old]);self.assertEqual(history['sources'][0]['status'],'outdated')

    def test_query_guard_and_vector_invalidation_cover_state_only_changes(self):
        id=self.store.save(self.a,'我喜欢咖啡');service=PersonalMemory(self.store);snapshot=service.search(self.a,'喜欢咖啡')
        with patch.object(service.retriever,'invalidate') as invalid:
            self.store.personal_retriever=service.retriever;self.state(id,status='outdated',status_reason='旧偏好')
            self.assertTrue(invalid.called)
        with self.assertRaises(PersonalMemoryChanged):service.validate(snapshot)

    def test_keep_both_noop_preserves_history_and_preview_matches_write(self):
        left,right=self.pair();plan=self.plan(left,right,action='keep_both',preferred_id=None)
        self.store.confirm_conflict(self.a,plan['token'],True)
        history=[self.store.history(self.a,id) for id in (left,right)]
        second=self.plan(left,right,action='keep_both',preferred_id=None)
        result=self.store.confirm_conflict(self.a,second['token'],True)
        self.assertFalse(result['changed']);self.assertEqual(history,[self.store.history(self.a,id) for id in (left,right)])
        plan=self.store.request_save(self.a,'我不喜欢咖啡',status='valid',status_reason='已核对')
        # A non-conflicting verified new fact is still previewed, not immediately written.
        self.assertFalse(plan['changed']);self.assertEqual(plan['review']['status'],'valid')
        self.store.confirm_save(self.a,plan['review']['token'],True,'append')
        opposing=self.store.request_save(self.a,'我喜欢咖啡',status='valid',status_reason='另一种说法')['review']
        self.assertEqual(opposing['status'],'pending')
        result=self.store.confirm_save(self.a,opposing['token'],True,'append')
        self.assertEqual(self.row(result['id'])['status'],opposing['status'])

    def test_pair_payload_and_large_scan_are_bounded(self):
        left,right=self.pair();foreign=self.store.save(self.b,'居住城市：杭州')
        with self.assertRaises(ValueError):self.plan(left,foreign)
        with self.assertRaises(ValueError):self.plan(left,right,reason='')
        rows=[{'id':str(i),'content':'居住城市：地点'+str(i),'status':'pending'} for i in range(100)]
        result=conflict_pairs(rows);self.assertEqual(result['total'],4950);self.assertEqual(len(result['pairs']),50);self.assertTrue(result['limited'])


class PersonalStateHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)
    def post(self,path,data):
        import urllib.request,urllib.error
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,response.read().decode()
        except urllib.error.HTTPError as error:return error.code,error.read().decode()
    def test_state_and_pair_routes_are_previewed_profile_bound(self):
        a=json.loads(self.post('/api/profiles/create',{'name':'HTTP状态甲'})[1])['id']
        b=json.loads(self.post('/api/profiles/create',{'name':'HTTP状态乙'})[1])['id']
        left=self.app.STORE.save(a,'居住城市：南京');right=self.app.STORE.save(a,'我住在上海')
        listing=json.loads(self.post('/api/memory/list',{'profile_id':a})[1]);self.assertEqual(listing['conflicts']['total'],1)
        code,body=self.post('/api/memory/conflicts/preview',{'profile_id':a,'left_id':left,'right_id':right,'action':'prefer','preferred_id':left,'reason':'已核对'})
        self.assertEqual(code,200);plan=json.loads(body);self.assertEqual(self.app.STORE.memories(a)[0]['status'],'pending')
        self.assertEqual(self.post('/api/memory/conflicts/confirm',{'profile_id':b,'token':plan['token'],'confirmed':True})[0],400)
        self.assertEqual(self.post('/api/memory/conflicts/confirm',{'profile_id':a,'token':plan['token'],'confirmed':True})[0],200)
        self.assertEqual(self.post('/api/memory/conflicts',{'profile_id':a})[0],200)
        self.assertEqual(json.loads(self.post('/api/memory/list',{'profile_id':b})[1])['memories'],[])
    def test_ordinary_chat_with_personal_conflict_never_asks_model_to_pick_truth(self):
        a=json.loads(self.post('/api/profiles/create',{'name':'HTTP冲突对话'})[1])['id']
        self.app.STORE.save(a,'居住城市：南京');self.app.STORE.save(a,'我住在上海')
        with patch.object(self.app.SERVICES.models,'chat',side_effect=AssertionError('no model on conflict')):
            code,body=self.post('/api/chat',{'profile_id':a,'messages':[{'role':'user','content':'我的居住城市是什么'}]})
        self.assertEqual(code,200);event=json.loads(body);self.assertEqual(event['reply_mode'],'personal_conflict');self.assertIn('南京',event['message']['content']);self.assertIn('上海',event['message']['content'])
