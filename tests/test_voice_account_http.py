"""HTTP authorization and trusted ASR receipts; identity detection is mocked, not a hardware test."""
import io,json,wave,urllib.request,urllib.error,unittest
from unittest.mock import patch
import test_personal_http as fixture
from test_memory_v2 import packet

class VoiceAccountHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,token=None,extra=None):
        headers={'Content-Type':'application/json',**(extra or {})}
        if token:headers['Authorization']='Bearer '+token
        body=data if isinstance(data,bytes) else json.dumps(data).encode()
        req=urllib.request.Request('http://127.0.0.1:'+str(self.http.server_port)+path,data=body,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())

    def actor(self,name):
        self.app.SHARED.register({'name':name,'password':'voice-http-test-password'})
        return self.app.SHARED.login({'name':name,'password':'voice-http-test-password'})['token']

    def test_actual_routes_require_preview_consent_and_trusted_asr_receipt(self):
        actor=self.actor('voice-http-a');profile=self.app.STORE.create_profile('HTTP voice A')['id']
        self.app.STORE.enroll(profile,[1,0,0],'synthetic')
        self.assertEqual(self.post('/api/voice-memory/status',{})[0],401)
        status,plan=self.post('/api/voice-memory/preview',{'profile_id':profile},actor);self.assertEqual(status,200)
        self.assertEqual(self.post('/api/voice-memory/confirm',{'preview_token':plan['preview_token'],'confirmed':True},actor)[0],400)
        self.assertEqual(self.post('/api/voice-memory/confirm',{'preview_token':plan['preview_token'],'confirmed':True,'consent':True},actor)[0],200)
        _,p=self.post('/api/memories/preview',{'scope_kind':'personal','document':packet()},actor)
        self.assertEqual(self.post('/api/memories/confirm',{'scope_kind':'personal','token':p['token'],'confirmed':True,'acknowledge_warnings':True},actor)[0],200)
        audio=io.BytesIO()
        with wave.open(audio,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*48000)
        with patch.object(self.app.SERVICES.asr,'transcribe',return_value=self.app.APPEARANCE.snapshot()['name']+'，第二届班长是谁？'),patch.object(self.app.SPEAKERS,'identify',return_value={'matched':True,'id':profile,'name':'HTTP voice A','diagnostics':{'code':'matched'}}):
            status,speech=self.post('/api/transcribe',audio.getvalue(),extra={'Content-Type':'audio/wav','X-Speaker-Recognition':'true'})
        self.assertEqual(status,200);self.assertTrue(speech['voice_receipt_id'])
        data={'receipt_id':speech['voice_receipt_id']}
        self.assertEqual(self.post('/api/voice-memory/ask',{**data,'profile_id':profile},actor)[0],400)
        status,response=self.post('/api/voice-memory/ask',data,actor)
        self.assertEqual(status,200);self.assertIn('角色乙',response['answer']);self.assertTrue(response['answer_source_ids'])
        self.assertEqual(response['voice_identity']['account_name'],'voice-http-a')
        self.assertEqual(self.post('/api/voice-memory/ask',data,actor)[0],409)
        self.assertEqual(self.post('/api/voice-memory/revoke',{},actor)[0],200)
        self.assertEqual(self.post('/api/voice-memory/ask',{'receipt_id':'made-up'},actor)[0],403)

    def test_unknown_speaker_is_rejected_without_memory_query(self):
        actor=self.actor('voice-http-b');profile=self.app.STORE.create_profile('HTTP voice B')['id'];self.app.STORE.enroll(profile,[0,1,0],'synthetic')
        p=self.app.VOICE_ACCOUNT.preview(actor,{'profile_id':profile})
        self.app.VOICE_ACCOUNT.confirm(actor,{'preview_token':p['preview_token'],'confirmed':True,'consent':True})
        receipt=self.app.VOICE_ACCOUNT.receipt('读取私人资料',{'matched':False})
        with patch.object(self.app.MEMORY,'dispatch') as query:
            status,result=self.post('/api/voice-memory/ask',{'receipt_id':receipt},actor)
            self.assertEqual(status,403);self.assertTrue(result['voice_memory']);query.assert_not_called()

    def test_frontend_asset_is_served(self):
        with urllib.request.urlopen('http://127.0.0.1:'+str(self.http.server_port)+'/voice-account.js') as r:
            self.assertEqual(r.status,200);self.assertIn('voiceMemoryUI',r.read().decode())

if __name__=='__main__':unittest.main()
