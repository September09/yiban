"""Model switching, network boundaries and same-snapshot comparison, with synthetic data."""
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yiban.settings import DEFAULTS
from yiban.backends.model_settings import ModelSettings, REMOTE_REQUEST
from yiban.backends.models import ModelHTTPError
from yiban.turns import Cancellation, Cancelled
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
import test_personal_http as fixture


class StubClient:
    calls=[]
    mode='normal'
    callback=None
    def __init__(self,config):self.config=copy.deepcopy(config)
    @contextmanager
    def stream_request(self,path,payload,cancel,method='POST'):
        self.calls.append(('catalog',self.config['model']))
        cancel.check()
        if self.mode=='401':raise ModelHTTPError(401)
        if self.mode=='404':raise ModelHTTPError(404)
        yield io.BytesIO(json.dumps({'models':[{'name':self.config['model']}]}).encode())
    def chat(self,messages,**kw):
        self.calls.append(('chat',self.config['model'],copy.deepcopy(messages)))
        kw['cancel'].check()
        if self.callback:self.callback(self.config)
        if self.mode=='bad' or (self.mode=='local_fail' and self.config['model']=='local-test'):raise ModelHTTPError(429)
        if self.mode=='truncate':
            yield {'message':{'content':'不可交付的半句'}}
            return
        if kw.get('json_schema'):
            answer=json.dumps({'answer':'测试集合地点是北门。','references':[1]},ensure_ascii=False)
        else:answer='连接成功。'
        yield {'message':{'content':answer},'done':True}
    def status(self):return {'chat':True,'vision':False}


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'model-settings.json'
        StubClient.calls=[];StubClient.mode='normal';StubClient.callback=None
        self.addCleanup(setattr,StubClient,'callback',None)
        self.manager=ModelSettings(dict(DEFAULTS['llm'],model='local-test'),self.path,StubClient)
        self.env=patch.dict(os.environ,{'ZHIPU_API_KEY':'dummy-unit-secret'});self.env.start();self.addCleanup(self.env.stop)
        self.store=MemoryStore(self.root/'isolated.sqlite3');self.shared=SharedMemory(self.store)
        for name in ('owner','reader'):
            self.shared.register({'name':name,'password':'unit-test-password'})
        self.owner=self.shared.login({'name':'owner','password':'unit-test-password'})['token']
        login=self.shared.login({'name':'reader','password':'unit-test-password'});self.reader=login['token'];self.reader_id=login['account']['id']
        self.space=self.shared.create_space(self.owner,{'name':'虚构测试空间','kind':'team'})['id']
        self.shared.set_member(self.owner,{'space_id':self.space,'name':'reader','role':'reader'})
    def publish(self,text,visibility='all'):
        plan=self.shared.preview(self.owner,{'space_id':self.space,'content':text,'visibility':visibility,'viewers':[],'edit_policy':'author'})
        self.shared.confirm(self.owner,{'space_id':self.space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
    def plan(self,scope='general',token=None):
        return self.manager.prepare({'query':'测试集合地点在哪里','scope':scope,'space_id':self.space},self.shared,token,Cancellation())
    def compare(self,plan,token=None,**extra):
        return self.manager.compare(dict(preview_token=plan['preview_token'],confirmed=True,allow_remote=True,**extra),self.shared,token,Cancellation())
    def save(self,active='cloud',**extra):
        cfg=copy.deepcopy(self.manager.profiles['cloud'])
        return self.manager.save(dict({'profile':'cloud','config':cfg,'active':active,'revision':self.manager.revision},**extra))

    def test_switch_is_persisted_without_secrets_and_request_stays_pinned(self):
        bound=self.manager.bind()
        try:
            result=self.save(allow_remote=True)
            self.assertEqual(result['active'],'cloud')
            self.assertEqual(self.manager.config['model'],'local-test')
            self.assertFalse(REMOTE_REQUEST.get())
        finally:self.manager.unbind(bound)
        self.assertEqual(self.manager.config['model'],'glm-4.7-flash')
        restored=ModelSettings(DEFAULTS['llm'],self.path,StubClient)
        self.assertEqual(restored.active,'cloud')
        self.assertNotIn('dummy-unit-secret',self.path.read_text()+json.dumps(result))
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_save_validation_revision_and_consent(self):
        with self.assertRaises(ValueError):self.save()
        with self.assertRaises(ValueError):self.save(revision='old')
        for change in ({'base_url':'http://example.invalid/v1'},{'api_key':'secret'}, {'api_key_env':'a-real-key.with-dots'}, {'temperature':float('nan')}, {'base_url':'https://key@host.invalid/v1'}, {'base_url':'https://example.invalid:bad/v1'}, {'base_url':'https://example.invalid:0/v1'}):
            with self.assertRaises(ValueError):ModelSettings.validate('cloud',dict(self.manager.profiles['cloud'],**change))
        with self.assertRaises(ValueError):ModelSettings.validate('local',dict(DEFAULTS['llm'],base_url='https://example.invalid'))

    def test_corrupt_settings_fall_back_with_notice(self):
        self.path.write_text('{broken')
        manager=ModelSettings(DEFAULTS['llm'],self.path,StubClient)
        self.assertTrue(manager.describe()['warning'])
        self.assertEqual(manager.active,'local')

    def test_probe_401_fails_and_404_requires_real_completion(self):
        StubClient.mode='401'
        result=self.manager.probe({'profile':'local'},Cancellation())
        self.assertFalse(result['ok']);self.assertFalse(any(c[0]=='chat' for c in StubClient.calls))
        StubClient.calls=[];StubClient.mode='404'
        result=self.manager.probe({'profile':'local'},Cancellation())
        self.assertTrue(result['ok']);self.assertEqual(result['checks'][1]['status'],'unavailable')
        StubClient.mode='truncate'
        self.assertFalse(self.manager.probe({'profile':'local'},Cancellation())['ok'])

    def test_remote_probe_requires_key_and_consent(self):
        with self.assertRaises(ValueError):self.manager.probe({'profile':'cloud'},Cancellation())
        with patch.dict(os.environ,{},clear=True):
            result=self.manager.probe({'profile':'cloud','allow_remote':True},Cancellation())
        self.assertFalse(result['ok']);self.assertEqual(StubClient.calls,[])

    def test_general_compare_uses_identical_payload_no_history_and_one_confirmation(self):
        plan=self.plan();self.assertEqual(StubClient.calls,[])
        result=self.compare(plan)
        calls=[c for c in StubClient.calls if c[0]=='chat']
        self.assertEqual(len(calls),2);self.assertEqual(calls[0][2],calls[1][2])
        self.assertEqual(len(calls[0][2]),2)
        self.assertTrue(all(x['ok'] for x in result['results']))
        with self.assertRaises(ValueError):self.compare(plan)
        self.assertFalse(self.path.exists())

    def test_comparison_excludes_selected_even_for_owner_and_does_not_write(self):
        self.publish('测试集合地点是北门。')
        self.publish('不可发送的私人测试秘密','selected')
        before=self.shared.memories(self.owner,self.space)['memories']
        plan=self.plan('shared',self.owner)
        self.assertEqual(len(plan['sources']),1)
        result=self.compare(plan,self.owner)
        self.assertNotIn('私人测试秘密',json.dumps(StubClient.calls,ensure_ascii=False)+json.dumps(result,ensure_ascii=False))
        self.assertTrue(all(x['source_usage']=='model_reported' for x in result['results']))
        self.assertIn('尚待核实',result['results'][0]['answer'])
        self.assertEqual(before,self.shared.memories(self.owner,self.space)['memories'])

    def test_auth_scope_token_binding_and_revocation_before_confirmation(self):
        self.publish('测试集合地点是北门。')
        with self.assertRaises(SharedError):self.plan('shared')
        plan=self.plan('shared',self.reader)
        with self.assertRaises(ValueError):self.compare(plan,self.owner)
        self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.reader_id},True)
        with self.assertRaises(SharedError):self.compare(plan,self.reader)
        self.assertEqual(StubClient.calls,[])

    def test_revoke_between_models_never_calls_cloud_and_hides_local_answer(self):
        self.publish('测试集合地点是北门。');plan=self.plan('shared',self.reader)
        def callback(cfg):
            self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.reader_id},True)
        StubClient.callback=staticmethod(callback)
        with self.assertRaises(SharedError):self.compare(plan,self.reader)
        self.assertEqual(len(StubClient.calls),1)
        self.assertEqual(StubClient.calls[0][1],'local-test')

    def test_expired_changed_config_and_cancelled_preview_never_call_models(self):
        plan=self.plan();self.manager.previews[plan['preview_token']]['created']-=601
        with self.assertRaises(ValueError):self.compare(plan)
        plan=self.plan();self.save(active='local')
        with self.assertRaises(ValueError):self.compare(plan)
        plan=self.plan();cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):self.manager.compare({'preview_token':plan['preview_token'],'confirmed':True,'allow_remote':True},self.shared,None,cancel)
        self.assertEqual(StubClient.calls,[])

    def test_missing_confirmation_no_model_and_local_failure_no_hidden_fallback(self):
        plan=self.plan()
        with self.assertRaises(ValueError):self.manager.compare({'preview_token':plan['preview_token'],'confirmed':True},self.shared,None,Cancellation())
        self.assertEqual(StubClient.calls,[])
        StubClient.mode='local_fail';result=self.compare(plan)
        self.assertFalse(result['results'][0]['ok']);self.assertTrue(result['results'][1]['ok'])
        self.assertEqual(len(StubClient.calls),2)

    def test_missing_key_prevents_activation_and_chat_before_transmission(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):self.save(allow_remote=True)
            self.assertEqual(self.manager.active,'local')
            self.manager.active='cloud'
            with self.assertRaises(ValueError):self.manager.chat([{'role':'user','content':'synthetic'}],cancel=Cancellation())
        self.assertEqual(StubClient.calls,[])

    def test_discard_confirmation_token_is_actor_bound(self):
        plan=self.plan('shared',self.owner)
        self.manager.discard({'preview_token':plan['preview_token']},self.reader)
        self.assertIn(plan['preview_token'],self.manager.previews)
        self.manager.discard({'preview_token':plan['preview_token']},self.owner)
        with self.assertRaises(ValueError):self.compare(plan,self.owner)
        self.assertEqual(StubClient.calls,[])

    def test_no_public_materials_is_zero_calls_and_remote_filter_applies_before_retrieval(self):
        self.publish('私人测试秘密','selected');plan=self.plan('shared',self.owner)
        result=self.compare(plan,self.owner);self.assertEqual(result['results'],[]);self.assertEqual(StubClient.calls,[])
        setting=REMOTE_REQUEST.set(True)
        try:self.assertEqual(self.shared.question_context(self.owner,self.space,'秘密')['sources'],[])
        finally:REMOTE_REQUEST.reset(setting)


class ModelSettingsHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)
    def post(self,path,data,**headers):
        import urllib.request,urllib.error
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**headers})
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,response.read().decode()
        except urllib.error.HTTPError as error:return error.code,error.read().decode()
    def test_routes_origin_settings_redaction_and_bad_body(self):
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{self.http.server_port}/api/models/settings') as response:
            self.assertEqual(json.load(response)['active'],'local')
        status,body=self.post('/api/models/compare/preview',{'scope':'shared','query':'测试','space_id':'bogus'})
        self.assertEqual(status,401)
        self.assertEqual(self.post('/api/models/probe',[],Origin='http://evil.invalid')[0],403)
        self.assertEqual(self.post('/api/models/probe',[])[0],400)
    def test_remote_chat_drops_history_and_rejects_personal_profile_before_call(self):
        manager=self.app.MODEL_SETTINGS;client=manager.clients['cloud']
        def chat(messages,**kw):
            self.assertNotIn('本地历史秘密',json.dumps(messages,ensure_ascii=False))
            yield {'message':{'content':'已收到当前问题'},'done':True}
        with patch.dict(os.environ,{'ZHIPU_API_KEY':'dummy-http-key'}),patch.object(manager,'active','cloud'),patch.object(client,'chat',side_effect=chat) as call:
            code,body=self.post('/api/chat',{'messages':[{'role':'user','content':'本地历史秘密'},{'role':'user','content':'你好'}]})
            self.assertEqual(code,200);self.assertIn('已收到当前问题',body)
            before=call.call_count
            code,body=self.post('/api/chat',{'profile_id':'synthetic-personal','messages':[{'role':'user','content':'测试'}]})
            self.assertEqual(code,400);self.assertEqual(call.call_count,before)
            self.assertEqual(self.post('/api/memory/ask',{'profile_id':'synthetic-personal','query':'秘密'})[0],400)

    def test_local_probe_uses_real_http_catalog_and_completion(self):
        code,body=self.post('/api/models/probe',{'profile':'local'})
        self.assertEqual(code,200);self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(self.model.request_path,'/v1/chat/completions')

    def test_remote_shared_read_filters_selected_and_write_tool_does_not_call_model(self):
        def shared(path,data,token=None):
            return self.post('/api/shared'+path,data,**({'Authorization':'Bearer '+token} if token else {}))
        shared('/auth/register',{'name':'cloud-scope-owner','password':'http-test-password'})
        login=json.loads(shared('/auth/login',{'name':'cloud-scope-owner','password':'http-test-password'})[1]);token=login['token']
        space=json.loads(shared('/spaces/create',{'name':'虚构云端范围测试','kind':'team'},token)[1])['id']
        for content,visibility in [('测试集合在北门。','all'),('私人不可发送测试秘密','selected')]:
            plan=json.loads(shared('/memories/preview',{'space_id':space,'content':content,'visibility':visibility,'viewers':[],'edit_policy':'author'},token)[1])
            shared('/memories/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True},token)
        manager=self.app.MODEL_SETTINGS;client=manager.clients['cloud']
        def chat(messages,**kw):
            self.assertNotIn('私人不可发送测试秘密',json.dumps(messages,ensure_ascii=False))
            yield {'message':{'content':json.dumps({'answer':'在北门集合。','references':[1]},ensure_ascii=False)},'done':True}
        with patch.dict(os.environ,{'ZHIPU_API_KEY':'dummy-http-key'}),patch.object(manager,'active','cloud'),patch.object(client,'chat',side_effect=chat) as call:
            code,body=shared('/ask',{'space_id':space,'query':'测试集合'},token)
            self.assertEqual(code,200);self.assertEqual(len(json.loads(body)['sources']),1)
            before=call.call_count
            code,body=shared('/agent/memory/ask',{'space_id':space,'query':'记住：私人秘密'},token)
            self.assertEqual(code,400);self.assertEqual(call.call_count,before)
