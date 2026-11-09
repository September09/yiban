"""HTTP speech receipts are minted from server ASR, never supplied text or profile IDs."""
import io
import json
import unittest
import urllib.error
import urllib.request
import wave
from unittest.mock import patch

import test_personal_http as fixture


class SharedVoiceHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,token=None,audio=False):
        headers={'Content-Type':'audio/wav' if audio else 'application/json'}
        if audio:headers['X-Speaker-Recognition']='true'
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=data if audio else json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())

    def setup_voice(self,suffix):
        name='voice-http-'+suffix
        self.app.SHARED.register({'name':name,'password':'voice-http-test'})
        token=self.app.SHARED.login({'name':name,'password':'voice-http-test'})['token']
        space=self.app.SHARED.create_space(token,{'name':'HTTP语音测试','kind':'family'})['id']
        profile=self.app.STORE.create_profile(name)['id']
        self.app.STORE.enroll(profile,[1,0,0],'test')
        status,result=self.post('/api/shared/voice/start',{'space_id':space,'profile_id':profile},token)
        self.assertEqual(status,200)
        return token,space,profile,result['session']

    def speech(self,text,profile):
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x00\x00'*32000)
        with patch.object(self.app.SERVICES.asr,'transcribe',return_value=text),patch.object(self.app.SPEAKERS,'identify',return_value={'matched':True,'id':profile,'diagnostics':{}}):
            status,result=self.post('/api/transcribe',output.getvalue(),audio=True)
        self.assertEqual(status,200);self.assertTrue(result['utterance_id'])
        return result['utterance_id']

    def test_asr_receipt_publish_and_edit_through_real_routes(self):
        token,space,profile,session=self.setup_voice('write')
        def say(text):
            return self.post('/api/shared/voice/utterance',{'session':session,'utterance_id':self.speech(text,profile)},token)
        status,plan=say('记住，周末活动是周日散步')
        self.assertEqual(status,200);self.assertTrue(plan['needs_confirmation'])
        self.assertEqual(self.app.SHARED.memories(token,space)['memories'],[])
        self.assertEqual(self.post('/api/shared/voice/played',{'session':session,'review_id':plan['review_id']},token)[0],200)
        status,result=say('我确认保存这条共同记忆')
        self.assertEqual(status,200);self.assertTrue(result['saved'])
        status,plan=say('把周末活动改成周末活动是周六跑步')
        self.assertEqual(status,200)
        self.post('/api/shared/voice/played',{'session':session,'review_id':plan['review_id']},token)
        status,result=say('我确认修改这条共同记忆')
        self.assertTrue(result['saved'])
        self.assertEqual(self.app.SHARED.memories(token,space)['memories'][0]['content'],'周末活动是周六跑步')

    def test_forged_and_consumed_receipts_are_rejected(self):
        token,space,profile,session=self.setup_voice('forged')
        status,_=self.post('/api/shared/voice/utterance',{'session':session,'text':'确认保存','speaker':{'matched':True,'id':profile}},token)
        self.assertEqual(status,409)
        receipt=self.speech('记住，测试内容',profile)
        data={'session':session,'utterance_id':receipt}
        self.assertEqual(self.post('/api/shared/voice/utterance',data,token)[0],200)
        self.assertEqual(self.post('/api/shared/voice/utterance',data,token)[0],409)
        self.assertEqual(self.app.SHARED.memories(token,space)['memories'],[])

    def test_public_voice_question_does_not_send_selected_memory_to_model(self):
        token,space,profile,session=self.setup_voice('query')
        for content,visibility in [('公开活动','all'),('限定内容不能朗读','selected')]:
            plan=self.app.SHARED.preview(token,{'space_id':space,'content':content,'visibility':visibility,'edit_policy':'author'})
            self.app.SHARED.confirm(token,{'space_id':space,'token':plan['token'],'confirmed':True})
        seen=[]
        def chat(messages,**kwargs):
            seen.extend(messages)
            yield {'message':{'content':'活动信息'},'done':True}
        receipt=self.speech('最近有什么活动',profile)
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/api/shared/voice/utterance',{'session':session,'utterance_id':receipt},token)
        self.assertEqual(status,200);self.assertEqual(len(result['sources']),1)
        prompt=json.dumps(seen,ensure_ascii=False)
        self.assertIn('公开活动',prompt);self.assertNotIn('限定内容不能朗读',prompt)

    def test_cancel_during_voice_query_prevents_reply(self):
        token,space,profile,session=self.setup_voice('cancel')
        plan=self.app.SHARED.preview(token,{'space_id':space,'content':'公开资料','visibility':'all','edit_policy':'author'})
        self.app.SHARED.confirm(token,{'space_id':space,'token':plan['token'],'confirmed':True})
        def chat(messages,**kwargs):
            self.app.SHARED_VOICE.cancel(token,{'session':session})
            yield {'message':{'content':'过时答案'},'done':True}
        receipt=self.speech('有什么资料',profile)
        with patch.object(self.app.SERVICES.models,'chat',side_effect=chat):
            status,result=self.post('/api/shared/voice/utterance',{'session':session,'utterance_id':receipt},token)
        self.assertEqual(status,409);self.assertNotIn('过时答案',json.dumps(result,ensure_ascii=False))
