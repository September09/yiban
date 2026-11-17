"""Exercise real private worker pipes with a synthetic model, not a GPU or playback."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import base64
import time
from unittest.mock import patch

from yiban.backends.qwen_speech import QwenSpeech
from yiban.turns import Cancellation, Cancelled


FAKE = '''
import os, sys, time, types, runpy
from pathlib import Path
import numpy as np
root=Path(sys.argv.pop(1));sys.path.insert(0,str(root))
log=Path(__file__).with_suffix('.loads')
class Decoder:
 def reset_streaming_state(self):pass
class Model:
 speech_tokenizer=types.SimpleNamespace(decoder=Decoder())
 def generate_custom_voice(self, text, speaker, **kw):
  with log.with_suffix('.calls').open('a') as f:f.write(repr((text,kw))+'\\n')
  if text == 'crash':os._exit(7)
  if text == 'hang':time.sleep(.7)
  for i in range(100 if text == 'slow' else 3):
   time.sleep(.01)
   yield types.SimpleNamespace(audio=np.ones(240,dtype=np.float32)*(.1 if speaker=='Serena' else .2),sample_rate=24000,token_count=1000 if text=='runaway' else 0)
def load_model(path):
 with log.open('a') as f:f.write(str(os.getpid())+'\\n')
 if 'gpu-fail' in str(log):raise RuntimeError('No Metal device available')
 if 'slow-load' in str(log):time.sleep(.25)
 print('synthetic progress must stay off the protocol')
 return Model()
for name in ['mlx_audio','mlx_audio.tts','mlx_audio.tts.utils']:sys.modules[name]=types.ModuleType(name)
sys.modules['mlx_audio.tts.utils'].load_model=load_model
core=types.ModuleType('mlx.core');core.random=types.SimpleNamespace(seed=lambda n:None)
sys.modules['mlx']=types.ModuleType('mlx');sys.modules['mlx.core']=core
# The main test environment need not have the separate MLX environment's soundfile.
import wave
sf=types.ModuleType('soundfile')
def write(path,samples,rate,subtype):
 with wave.open(path,'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
  w.writeframes((samples*32767).astype('<i2').tobytes())
sf.write=write;sys.modules['soundfile']=sf
runpy.run_module('tools.qwen_tts_worker',run_name='__main__')
'''


class ResidentSpeechTests(unittest.TestCase):
    def setUp(self):
        from yiban.settings import ROOT
        self.root = ROOT
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.speech = QwenSpeech({'voice': 'Serena', 'rate': 195})
        self.addCleanup(self.speech.close)
        self.ready = patch.object(self.speech, 'available', return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.configure('worker')

    def configure(self, name):
        self.fixture = Path(self.tmp.name)/(name+'.py')
        self.fixture.write_text(FAKE)
        self.speech._command = lambda: [sys.executable, str(self.fixture), str(self.root), '--serve']

    def test_reuses_one_loaded_model_across_requests_and_voices(self):
        self.speech.warm()
        pid = self.speech.process.pid
        a, mime = self.speech.synthesize('hello')
        b, _ = self.speech.synthesize('world', selection={'voice': 'Vivian'})
        self.assertEqual(mime, 'audio/wav')
        self.assertTrue(a.startswith(b'RIFF'))
        self.assertNotEqual(a, b)
        self.assertEqual(self.speech.process.pid, pid)
        self.assertEqual(len(self.fixture.with_suffix('.loads').read_text().splitlines()), 1)
        self.assertEqual(self.speech.runtime_status()['state'], 'ready')
        self.assertIn('synthetic progress', ''.join(self.speech.errors))

    def test_active_cancellation_keeps_resident_model_and_discards_old_audio(self):
        self.speech.warm();pid = self.speech.process.pid
        cancel = Cancellation()
        timer = threading.Timer(.15, cancel.cancel);timer.start()
        try:
            with self.assertRaises(Cancelled):
                self.speech.synthesize('slow', cancel=cancel)
        finally:
            timer.join()
        self.assertEqual(self.speech.process.pid, pid)
        self.assertTrue(self.speech.synthesize('next')[0].startswith(b'RIFF'))
        self.assertEqual(len(cancel.callbacks), 0)

    def test_cancelled_waiter_does_not_discard_shared_loading(self):
        self.configure('slow-load')
        cancel = Cancellation();timer = threading.Timer(.05, cancel.cancel);timer.start()
        try:
            with self.assertRaises(Cancelled):self.speech.synthesize('hello', cancel=cancel)
        finally:timer.join()
        pid = self.speech.process.pid
        self.speech.synthesize('next')
        self.assertEqual(self.speech.process.pid, pid)

    def test_cancel_before_start_never_spawns(self):
        cancel = Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):self.speech.synthesize('hello', cancel=cancel)
        self.assertIsNone(self.speech.process)

    def test_cancel_queued_request_never_reaches_model(self):
        self.speech.lock.acquire()
        cancel = Cancellation();timer = threading.Timer(.05, cancel.cancel);timer.start()
        try:
            with self.assertRaises(Cancelled):self.speech.synthesize('hello', cancel=cancel)
        finally:
            timer.join();self.speech.lock.release()
        self.assertIsNone(self.speech.process)

    def test_crash_is_explicit_and_next_request_recovers(self):
        self.speech.warm();pid = self.speech.process.pid
        with self.assertRaisesRegex(RuntimeError, '异常'):self.speech.synthesize('crash')
        self.assertTrue(self.speech.synthesize('next')[0].startswith(b'RIFF'))
        self.assertNotEqual(self.speech.process.pid, pid)

    def test_timeout_reaps_worker_and_recovers(self):
        self.speech.warm();self.speech.REQUEST_TIMEOUT=.05
        process = self.speech.process
        with self.assertRaises(TimeoutError):self.speech.synthesize('hang')
        self.assertIsNotNone(process.poll())
        self.speech.REQUEST_TIMEOUT=2
        self.assertTrue(self.speech.synthesize('next')[0].startswith(b'RIFF'))

    def test_gpu_failure_is_explicit_without_system_voice_fallback(self):
        self.configure('gpu-fail')
        with self.assertRaisesRegex(RuntimeError, 'Metal GPU'):self.speech.synthesize('hello')
        self.assertIsNone(self.speech.process)

    def test_close_reaps_idle_worker(self):
        self.speech.warm();process = self.speech.process
        self.speech.close()
        self.assertIsNotNone(process.poll())
        self.assertIsNone(self.speech.process)

    def test_invalid_voice_never_starts_worker(self):
        with self.assertRaises(ValueError):self.speech.synthesize('hello', selection={'voice':'Ryan'})
        self.assertIsNone(self.speech.process)

    def test_stream_emits_first_audio_before_done_in_one_generation(self):
        self.speech.warm();pid=self.speech.process.pid
        stream=self.speech.stream_synthesize('Full sentence. Another sentence.')
        first=next(stream)
        self.assertEqual(first['type'],'audio');self.assertEqual(first['sequence'],0)
        self.assertEqual(len(base64.b64decode(first['audio'])),first['samples']*4)
        time.sleep(.05)
        rest=list(stream)
        self.assertEqual([v['sequence'] for v in rest[:-1]],[1,2])
        self.assertEqual(rest[-1],{'type':'done','chunks':3,'total_samples':720,'model_calls':1})
        calls=self.fixture.with_suffix('.calls').read_text().splitlines()
        self.assertEqual(len(calls),2,'One warm-up and one complete utterance')
        self.assertIn('Full sentence. Another sentence.',calls[-1])
        self.assertEqual(self.speech.process.pid,pid)

    def test_close_stream_cancels_and_drains_frames_without_reloading(self):
        self.speech.warm();pid=self.speech.process.pid
        stream=self.speech.stream_synthesize('slow');next(stream);time.sleep(.04);stream.close()
        self.assertEqual(self.speech.process.pid,pid)
        following=list(self.speech.stream_synthesize('next'))
        self.assertEqual(following[0]['sequence'],0);self.assertEqual(following[-1]['chunks'],3)
        self.assertEqual(self.speech.process.pid,pid)

    def test_runaway_fails_explicitly_and_next_request_keeps_model(self):
        from yiban.backends.qwen_speech import SpeechLengthError
        self.speech.warm();pid=self.speech.process.pid
        with self.assertRaises(SpeechLengthError):list(self.speech.stream_synthesize('runaway'))
        self.assertEqual(self.speech.process.pid,pid)
        self.assertEqual(list(self.speech.stream_synthesize('next'))[-1]['type'],'done')
        self.assertEqual(self.speech.process.pid,pid)

    def test_exact_completed_audio_cache_is_bounded_and_voice_specific(self):
        self.speech.warm()
        first=list(self.speech.stream_synthesize('same'))
        calls=self.fixture.with_suffix('.calls').read_text()
        self.assertEqual(list(self.speech.stream_synthesize('same')),first)
        self.assertEqual(self.fixture.with_suffix('.calls').read_text(),calls)
        list(self.speech.stream_synthesize('same',selection={'voice':'Vivian'}))
        self.assertNotEqual(self.fixture.with_suffix('.calls').read_text(),calls)
        self.speech.CACHE_TTL=-1
        list(self.speech.stream_synthesize('expire'));before=self.fixture.with_suffix('.calls').read_text()
        list(self.speech.stream_synthesize('expire'))
        self.assertNotEqual(self.fixture.with_suffix('.calls').read_text(),before)

    def test_bounded_wait_reports_queue_without_discarding_active_worker(self):
        self.speech.warm();pid=self.speech.process.pid;self.speech.QUEUE_TIMEOUT=.12
        active=self.speech.stream_synthesize('slow');next(active)
        queued=self.speech.stream_synthesize('next')
        self.assertEqual(next(queued),{'type':'status','state':'queued','position':1})
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'等待已停止'):list(queued)
        self.assertLess(time.monotonic()-start,.3)
        self.assertEqual(self.speech.process.pid,pid);active.close()
        self.assertEqual(list(self.speech.stream_synthesize('next'))[-1]['type'],'done')

    def test_stream_cancel_and_invalid_options(self):
        self.speech.warm();pid=self.speech.process.pid;cancel=Cancellation()
        stream=self.speech.stream_synthesize('slow',cancel=cancel);next(stream);cancel.cancel()
        with self.assertRaises(Cancelled):next(stream)
        self.assertEqual(len(cancel.callbacks),0)
        self.assertEqual(self.speech.process.pid,pid)
        for settings in [{'seed':True},{'top_k':1.5},{'temperature':float('nan')},{'top_p':2}]:
            with self.assertRaises(ValueError):list(self.speech.stream_synthesize('hello',selection={'voice':'Serena','generation':settings}))
        with self.assertRaises(ValueError):list(self.speech.stream_synthesize('a'*1201))


if __name__ == '__main__':unittest.main()
