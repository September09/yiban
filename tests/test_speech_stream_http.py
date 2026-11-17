"""Real loopback streaming with synthetic PCM; no model or personal database."""
import json
import time
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch
import test_personal_http as fixture


class SpeechStreamHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def request(self, data, origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        return urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/speak/stream',data=json.dumps(data).encode(),headers=headers)

    def test_http_flushes_before_complete_and_closes_generator(self):
        gate=threading.Event();closed=threading.Event()
        def stream(text,cancel):
            try:
                yield {'type':'audio','sequence':0,'audio':'AAAAAA=='}
                gate.wait(2)
                yield {'type':'done','model_calls':1}
            finally:closed.set()
        try:
            with patch.object(self.app.SPEECH,'stream_synthesize',side_effect=stream):
                with urllib.request.urlopen(self.request({'text':'synthetic'}),timeout=5) as res:
                    self.assertIn('ndjson',res.headers['Content-Type'])
                    first=json.loads(res.readline());self.assertEqual(first['type'],'audio')
                    self.assertFalse(closed.is_set(),'First frame must arrive before synthesis completes')
                    gate.set();self.assertEqual(json.loads(res.readline())['type'],'done')
                    self.assertEqual(res.read(),b'')
                self.assertTrue(closed.wait(1))
        finally:gate.set()

    def test_disconnect_cancels_generation_and_origin_is_enforced(self):
        entered=threading.Event();cancelled=threading.Event();closed=threading.Event();release=threading.Event()
        def stream(text,cancel):
            def on_cancel():cancelled.set()
            cancel.attach(on_cancel)
            try:
                entered.set();yield {'type':'audio','audio':'x'*1000}
                release.wait(2)
                for _ in range(64):yield {'type':'audio','audio':'x'*65536}
            finally:cancel.detach(on_cancel);closed.set()
        with patch.object(self.app.SPEECH,'stream_synthesize',side_effect=stream):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.request({'text':'synthetic'},'https://example.com'),timeout=5)
            self.assertEqual(error.exception.code,403);self.assertFalse(entered.is_set())
            res=urllib.request.urlopen(self.request({'text':'synthetic','turn_id':'synthetic-disconnect-0001'}),timeout=5)
            self.assertTrue(res.readline());res.close();release.set()
            self.assertTrue(closed.wait(3));self.assertTrue(cancelled.is_set())

    def test_validation_happens_before_http_success(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.request([]),timeout=5)
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.request({'text':'test'}),timeout=5)
        self.assertEqual(error.exception.code,400,'Unsupported backend must not silently pick another voice')
