"""IP configuration, real keyword vocabulary and local-only routes; no microphone/playback."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from yiban.appearance import Appearance, DEFAULT_CONFIG, PRESETS, normalize, validate_png, strip_name_prefix, WAKE_PHONEMES
from yiban.actions import action_for_text
from yiban.settings import ROOT, load_settings
import test_personal_http as fixture

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='

class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'appearance.json'
        self.manager=Appearance(DEFAULT_CONFIG,self.path)

    def save(self,**extra):
        return self.manager.save({'revision':self.manager.revision,'current':dict(PRESETS['aifeng'],preset='aifeng'),**extra})

    def test_blank_and_default_fallback(self):
        self.assertEqual(normalize({'name':'  '})['name'],'小伴')
        self.assertEqual(self.manager.snapshot()['avatar_url'],'')
        self.assertEqual(load_settings()['appearance']['name'],'小伴')

    def test_reject_invalid_configuration_and_external_sources(self):
        for data in ({'name':'<script>'},{'name':'x'*33},{'primary':'url(a)'},{'accent':'#123'},{'avatar':'https://example.com/a.png'},{'wake_word':'任意同学'},{'unknown':True}):
            with self.subTest(data=data),self.assertRaises(ValueError):normalize(data)
        for image in ('<svg/>','https://example.com/a.png','x'*2_000_001,base64.b64encode(b'<svg/>').decode()):
            with self.assertRaises(ValueError):validate_png(image)
        with self.assertRaises(ValueError):self.save(logo_base64=None)
        with self.assertRaises(ValueError):self.save(clear_logo='yes')

    def test_persist_reload_and_stale_revision(self):
        old=self.manager.revision;result=self.save(logo_base64=PNG)
        self.assertEqual(result['current']['name'],'爱凤同学')
        self.assertNotIn(PNG,json.dumps(result));self.assertNotIn(str(self.path),json.dumps(result))
        self.assertEqual(Appearance(DEFAULT_CONFIG,self.path).snapshot()['name'],'爱凤同学')
        with self.assertRaisesRegex(ValueError,'更新'):self.manager.save({'revision':old,'current':DEFAULT_CONFIG})
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_missing_logo_clear_and_atomic_failure(self):
        self.save(current=dict(DEFAULT_CONFIG,avatar='custom'))
        self.assertEqual(self.manager.snapshot()['avatar_url'],'');self.assertIn('默认机器人',self.manager.describe()['warning'])
        self.save(current=dict(DEFAULT_CONFIG,avatar='custom'),logo_base64=PNG)
        self.assertTrue(self.manager.snapshot()['avatar_url'].startswith('/api/appearance/avatar?'))
        before=self.path.read_bytes();revision=self.manager.revision
        with patch('yiban.appearance.os.replace',side_effect=OSError('unit-test')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual(self.manager.revision,revision);self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(list(self.path.parent.glob('.appearance-*'))),0)
        self.save(clear_logo=True,current=dict(DEFAULT_CONFIG,avatar='custom'))
        self.assertIsNone(self.manager.asset())

    def test_corrupt_preferences_preserve_original(self):
        self.path.write_text('broken')
        instance=Appearance(DEFAULT_CONFIG,self.path)
        self.assertIn('无法读取',instance.warning);self.assertEqual(self.path.read_text(),'broken')

    def test_custom_names_preserve_action_and_identity_routing(self):
        self.assertEqual(action_for_text('爱凤同学，请向左看'),'left')
        self.assertEqual(action_for_text('星星同学，请点头','星星同学'),'nod')
        self.assertIsNone(action_for_text('爱凤同学，不要点头'))
        self.assertEqual(strip_name_prefix('爱凤同学，你能认出我是谁吗？','爱凤同学'),'你能认出我是谁吗？')

    def test_real_keyword_vocabulary_and_active_session_guard(self):
        from yiban.audio.voice import VoiceManager, strip_wake_prefix, KWS_DIR
        manager=VoiceManager();manager.configure('爱凤同学')
        self.assertEqual(manager.wake_word,'爱凤同学')
        self.assertEqual(strip_wake_prefix('爱凤同学，请点头','爱凤同学'),'请点头')
        self.assertEqual(strip_wake_prefix('其他同学，请点头','爱凤同学'),'其他同学，请点头')
        manager.sessions={'active':type('Session',(),{'last_used':time.monotonic()})()}
        with self.assertRaisesRegex(ValueError,'关闭'):manager.configure('你好小伴')
        if not (KWS_DIR/'tokens.txt').exists():self.skipTest('local KWS tokens absent')
        vocabulary={line.split()[0] for line in (KWS_DIR/'tokens.txt').read_text().splitlines() if line.strip()}
        self.assertTrue(set(WAKE_PHONEMES['爱凤同学'].split())<=vocabulary)
        self.assertEqual((ROOT/'config/keywords-aifeng.txt').read_text().strip(),'ài f èng t óng x ué :1.5 #0.3 @爱凤同学')

class AppearanceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.PersonalHTTPTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixture.PersonalHTTPTests.tearDownClass.__func__(cls)
    post=fixture.PersonalHTTPTests.post
    def get(self,path):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.http.server_port}'+path) as response:return response.read()

    def test_routes_status_persona_and_origin_protection(self):
        previous=json.loads(self.get('/api/appearance'))
        config=dict(PRESETS['aifeng'],preset='aifeng')
        result=self.post('/api/appearance/save',{'revision':previous['revision'],'current':config})[0]
        status=json.loads(self.get('/api/status'))
        self.assertEqual(status['wake_word'],'爱凤同学');self.assertEqual(status['appearance']['name'],'爱凤同学')
        self.assertIn('你叫爱凤同学',self.app.system_prompt())
        self.assertIn(b'<svg',self.get('/aifeng.svg'))
        self.assertIn(b'const branding',self.get('/branding.js'))
        self.assertEqual(self.post('/api/chat',{'messages':[{'role':'user','content':'爱凤同学，请点头'}]})[0]['action']['action'],'nod')
        req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/appearance/save',data=json.dumps({'revision':result['revision'],'current':DEFAULT_CONFIG}).encode(),headers={'Origin':'https://example.com'})
        with self.assertRaises(urllib.error.HTTPError) as rejected:urllib.request.urlopen(req)
        self.assertEqual(rejected.exception.code,403)
        self.assertEqual(json.loads(self.get('/api/appearance'))['current']['name'],'爱凤同学')

    def test_logo_route_and_reject_while_another_tab_listens(self):
        current=json.loads(self.get('/api/appearance'))
        class Voice:
            def __init__(self):
                import threading
                self.lock=threading.RLock();self.sessions={'active':object()}
            def expire(self):pass
            def configure(self,word):raise AssertionError('must not configure active session')
        with patch.object(self.app,'voice_manager',Voice()):
            with self.assertRaises(urllib.error.HTTPError):self.post('/api/appearance/save',{'revision':current['revision'],'current':DEFAULT_CONFIG})
        self.assertEqual(json.loads(self.get('/api/appearance'))['revision'],current['revision'])
        updated=self.post('/api/appearance/save',{'revision':current['revision'],'current':dict(DEFAULT_CONFIG,avatar='custom'),'logo_base64':PNG})[0]
        self.assertEqual(self.get(updated['current']['avatar_url']),base64.b64decode(PNG))

if __name__=='__main__':unittest.main()
