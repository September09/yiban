"""Trace isolation, live stages, failure attribution, retention and HTTP integration."""
import io
import json
import sys
from pathlib import Path
import threading
import unittest
import urllib.request
import urllib.error
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.conversation_diagnostics import TraceStore, TRACES, CURRENT, measured, model_events
from yiban.turns import Cancelled
import test_personal_http as fixture


class TraceTests(unittest.TestCase):
    def test_bounds_expiry_and_unpredictable_lookup(self):
        clock=[0];store=TraceStore(limit=2,ttl=10,clock=lambda:clock[0])
        ids=[str(uuid.uuid4()) for _ in range(3)]
        for identity in ids:
            clock[0]+=1;store.begin(identity)
        self.assertFalse(store.read(ids[0])['available'])
        self.assertIsNone(store.begin('anything-private'))
        self.assertFalse(store.read(str(uuid.uuid4()))['available'])
        for _ in range(70):store.start(ids[2],'asr')
        self.assertEqual(len(store.read(ids[2])['steps']),64)
        clock[0]=14;self.assertFalse(store.read(ids[2])['available'])

    def test_thread_scoping_errors_and_content_exclusion(self):
        identities=[str(uuid.uuid4()) for _ in range(2)]
        def run(identity,stage):
            token=CURRENT.set(TRACES.begin(identity))
            try:
                def fail():raise ValueError('PRIVATE API KEY / utterance / vector')
                with self.assertRaises(ValueError):measured(stage,fail)
            finally:CURRENT.reset(token)
        threads=[threading.Thread(target=run,args=(i,s)) for i,s in zip(identities,['asr','speaker'])]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        for identity,stage in zip(identities,['asr','speaker']):
            result=TRACES.read(identity)
            self.assertEqual(result['steps'][0]['stage'],stage)
            self.assertEqual(result['steps'][0]['status'],'error')
            self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertIsNone(CURRENT.get())

    def test_model_first_text_and_cancellation_are_separate(self):
        identity=str(uuid.uuid4());token=CURRENT.set(TRACES.begin(identity))
        def generate():
            yield {'message':{'content':''}}
            yield {'message':{'content':'secret'}}
            raise Cancelled()
        try:
            events=model_events(generate())
            next(events);self.assertEqual(TRACES.read(identity)['steps'][0]['status'],'running')
            next(events)
            with self.assertRaises(Cancelled):next(events)
            steps=TRACES.read(identity)['steps']
            self.assertEqual([(s['stage'],s['status']) for s in steps],[('model_wait','done'),('model','cancelled')])
            self.assertNotIn('secret',json.dumps(steps))
        finally:CURRENT.reset(token)


class TraceHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,trace=None,token=None):
        headers={'Content-Type':'application/json'}
        if trace:headers['X-Conversation-Trace']=trace
        if token:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:
                body=response.read()
                if response.headers.get_content_type()=='application/x-ndjson':return response.status,[json.loads(line) for line in body.splitlines()]
                if response.headers.get_content_type()=='audio/wav':return response.status,body
                return response.status,json.loads(body)
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())

    def trace(self,identity):return self.post('/api/diagnostics/trace',{'trace_id':identity})[1]

    def test_poll_while_model_is_blocked_and_keep_responses_compatible(self):
        identity=str(uuid.uuid4());entered=threading.Event();release=threading.Event();result=[]
        def generate(*args,**kwargs):
            entered.set();release.wait(4)
            yield {'message':{'content':'private answer'},'done':True}
        with patch.object(self.app.SERVICES.models,'chat',side_effect=generate):
            thread=threading.Thread(target=lambda:result.append(self.post('/api/chat',{'messages':[{'role':'user','content':'private question'}]},identity)))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                steps=self.trace(identity)['steps']
                self.assertEqual(steps[-1]['stage'],'model_wait');self.assertEqual(steps[-1]['status'],'running')
                self.assertFalse(self.trace(str(uuid.uuid4()))['available'])
            finally:release.set();thread.join()
        self.assertEqual(result[0][0],200)
        self.assertEqual(result[0][1][0]['message']['content'],'private answer')
        data=self.trace(identity)
        self.assertNotIn('private',json.dumps(data))
        self.assertTrue(all(s['status']=='done' for s in data['steps']))
        self.assertEqual(self.post('/api/diagnostics/trace',[])[0],400)

    def test_asr_speaker_and_tts_share_trace_without_audio_or_names(self):
        identity=str(uuid.uuid4())
        req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/transcribe',data=b'fakeaudio',headers={'Content-Type':'audio/wav','X-Speaker-Recognition':'true','X-Conversation-Trace':identity})
        with patch.object(self.app.SERVICES.asr,'transcribe',return_value='private utterance'),patch.object(self.app.SPEAKERS,'identify',return_value={'matched':True,'id':'private','name':'secret name','diagnostics':{}}):
            with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['utterance_id'])
        with patch.object(self.app.SERVICES.tts,'synthesize',return_value=(b'wavbytes','audio/wav')):
            self.assertEqual(self.post('/api/speak',{'text':'private reply'},identity)[0],200)
        result=self.trace(identity)
        self.assertEqual([s['stage'] for s in result['steps']],['asr','speaker','tts'])
        self.assertNotIn('private',json.dumps(result));self.assertNotIn('name',json.dumps(result))

    def test_failures_record_real_stage_and_leave_next_request_clean(self):
        identity=str(uuid.uuid4())
        with patch.object(self.app.SERVICES.tts,'synthesize',side_effect=ValueError('secret failure')):
            self.assertEqual(self.post('/api/speak',{'text':'test'},identity)[0],400)
        self.assertEqual(self.trace(identity)['steps'][-1]['status'],'error')
        before=self.trace(identity)
        self.post('/api/chat',{'messages':[{'role':'user','content':'今天几号'}]})
        self.assertEqual(self.trace(identity),before)
        self.assertNotIn('secret',json.dumps(before))

    def test_shared_answer_trace_keeps_permissions(self):
        name='trace-team'
        self.app.SHARED.register({'name':name,'password':'trace-test-password'})
        token=self.app.SHARED.login({'name':name,'password':'trace-test-password'})['token']
        space=self.app.SHARED.create_space(token,{'name':'private team','kind':'team'})['id']
        identity=str(uuid.uuid4())
        status,result=self.post('/api/shared/ask',{'space_id':space,'query':'private query'},identity,token)
        self.assertEqual(status,200);self.assertEqual(result['sources'],[])
        self.assertEqual([s['stage'] for s in self.trace(identity)['steps']],['memory','access'])
        self.assertNotIn('private',json.dumps(self.trace(identity)))
        self.assertNotEqual(self.post('/api/shared/ask',{'space_id':space,'query':'test'},str(uuid.uuid4()))[0],200)


if __name__=='__main__':unittest.main()
