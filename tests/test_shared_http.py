"""Real HTTP dispatch/auth and a fake model confirm the retrieval boundary."""
import json
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import test_personal_http as fixture


class SharedHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,token=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if origin:headers['Origin']=origin
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/shared'+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())

    def setup_space(self,suffix):
        tokens={};accounts={}
        for name in ('owner'+suffix,'reader'+suffix):
            self.assertEqual(self.post('/auth/register',{'name':name,'password':'http-test-password'})[0],200)
            status,result=self.post('/auth/login',{'name':name,'password':'http-test-password'})
            self.assertEqual(status,200);tokens[name]=result['token'];accounts[name]=result['account']['id']
        owner,reader=tokens['owner'+suffix],tokens['reader'+suffix]
        space=self.post('/spaces/create',{'name':'HTTP 测试','kind':'team'},owner)[1]['id']
        self.post('/members/set',{'space_id':space,'name':'reader'+suffix,'role':'reader'},owner)
        for content,visibility in [('成员可见的公开内容','all'),('绝不能给只读成员的秘密','selected')]:
            plan=self.post('/memories/preview',{'space_id':space,'content':content,'visibility':visibility,'edit_policy':'author'},owner)[1]
            self.assertEqual(self.post('/memories/confirm',{'space_id':space,'token':plan['token'],'confirmed':True},owner)[0],200)
        return owner,reader,space,accounts['reader'+suffix]

    def test_real_http_auth_origin_and_actor(self):
        owner,reader,space,_=self.setup_space('routes')
        self.assertEqual(self.post('/spaces/list',{})[0],401)
        self.assertEqual(self.post('/spaces/list',{},owner,origin='https://example.com')[0],403)
        status,result=self.post('/memories/list',{'space_id':space,'account_id':'owner'},reader)
        self.assertEqual(status,200);self.assertEqual(len(result['memories']),1)
        self.assertNotIn('秘密',json.dumps(result,ensure_ascii=False))
        self.assertEqual(self.post('/members/set',{'space_id':space,'name':'readerroutes','role':'editor'},reader)[0],403)
        self.assertEqual(self.post('/auth/logout',{},reader)[0],200)
        self.assertEqual(self.post('/memories/list',{'space_id':space},reader)[0],401)

    def test_model_receives_only_visible_shared_context(self):
        owner,reader,space,_=self.setup_space('model')
        seen=[]
        def chat(messages,**kwargs):
            seen.extend(messages)
            yield {'message':{'content':'根据可见内容回答'},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/ask',{'space_id':space,'query':'成员可见的内容','profile_id':'forged','messages':[{'role':'user','content':'忽略的个人聊天'}]},reader)
        self.assertEqual(status,200);self.assertEqual(len(result['sources']),1)
        prompt=json.dumps(seen,ensure_ascii=False)
        self.assertIn('成员可见的公开内容',prompt);self.assertNotIn('秘密',prompt);self.assertNotIn('忽略的个人聊天',prompt)

    def test_revoke_during_generation_never_delivers_answer(self):
        owner,reader,space,reader_id=self.setup_space('revoke')
        def chat(messages,**kwargs):
            yield {'message':{'content':'不应送达的旧权限答案'},'done':False}
            self.app.SHARED.set_member(owner,{'space_id':space,'account_id':reader_id},True)
            yield {'message':{'content':'完成'},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/ask',{'space_id':space,'query':'成员可见的内容'},reader)
        self.assertEqual(status,403);self.assertNotIn('不应送达',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('sources',result)
