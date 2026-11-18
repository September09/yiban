"""Real HTTP joining routes, origin checks and static assets; synthetic local accounts."""
import json
import secrets
import unittest
import urllib.request
import urllib.error
import test_personal_http as fixture


class JoiningHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,token=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if origin:headers['Origin']=origin
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)

    def test_join_request_preview_confirmation_and_auto_scope(self):
        actors=[]
        for label in ['owner','newcomer']:
            name='http-join-'+label+'-'+secrets.token_hex(4);password='synthetic-only-password'
            self.post('/api/shared/auth/register',{'name':name,'password':password})
            status,actor=self.post('/api/shared/auth/login',{'name':name,'password':password});self.assertEqual(status,200);actors.append(actor)
        owner,applicant=[a['token'] for a in actors]
        _,space=self.post('/api/shared/spaces/create',{'name':'虚构HTTP空间','kind':'team'},owner)
        self.assertEqual(self.post('/api/shared/joining/code/create',{'space_id':space['id']},applicant)[0],403)
        status,code=self.post('/api/shared/joining/code/create',{'space_id':space['id']},owner);self.assertEqual(status,200)
        status,preview=self.post('/api/shared/joining/lookup',{'code':code['code']},applicant);self.assertEqual(status,200)
        self.assertEqual(self.post('/api/shared/joining/request',{'preview_token':preview['preview_token'],'confirmed':False},applicant)[0],400)
        _,request=self.post('/api/shared/joining/request',{'preview_token':preview['preview_token'],'confirmed':True},applicant)
        _,spaces=self.post('/api/shared/spaces/list',{},applicant);self.assertEqual(spaces['spaces'],[])
        _,plan=self.post('/api/shared/joining/review/preview',{'space_id':space['id'],'request_id':request['request_id'],'decision':'approve'},owner)
        self.assertEqual(self.post('/api/shared/joining/review/confirm',{'preview_token':plan['preview_token'],'confirmed':True},applicant)[0],403)
        status,_=self.post('/api/shared/joining/review/confirm',{'preview_token':plan['preview_token'],'confirmed':True},owner);self.assertEqual(status,200)
        _,spaces=self.post('/api/shared/spaces/list',{},applicant);self.assertEqual(spaces['spaces'][0]['role'],'reader')
        self.assertEqual(self.post('/api/shared/joining/inbox',{'space_id':space['id']},applicant)[0],403)

    def test_auth_origin_and_member_ui_are_served(self):
        self.assertEqual(self.post('/api/shared/joining/mine',{})[0],401)
        self.assertEqual(self.post('/api/shared/joining/mine',{},origin='https://example.com')[0],403)
        for path,needle in [('/','joinRequestConsent'),('/member-onboarding.js','membershipUI'),('/workspace.css','getting-started-steps')]:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.http.server_port}'+path,timeout=5) as response:
                self.assertEqual(response.status,200);self.assertIn(needle,response.read().decode())
