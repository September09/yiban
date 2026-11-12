"""Persistence, speaker rejection and cancellation regression tests."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import subprocess
import sqlite3
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore
from yiban.audio.speakers import SpeakerService
from yiban.settings import ROOT, load_settings
from yiban.turns import Cancellation, Cancelled, TurnRegistry
from yiban.backends.models import ModelClient


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.a = self.store.create_profile('甲')['id']
        self.b = self.store.create_profile('乙')['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_old_profile_database_migrates_without_losing_profiles(self):
        path = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE)')
            db.execute('INSERT INTO profiles VALUES (?, ?)', ('existing', '甲'))
        migrated = MemoryStore(path)
        self.assertEqual(migrated.profile('existing'), {'id': 'existing', 'name': '甲', 'salutation': ''})
        migrated.set_salutation('existing', '李女士')
        reopened = MemoryStore(path)
        self.assertEqual(reopened.profile('existing')['salutation'], '李女士')
        self.assertEqual(reopened.create_profile('乙')['salutation'], '')

    def test_explicit_commands_persistence_and_isolation(self):
        self.assertIsNone(self.store.command(self.a, '我喜欢喝茶'))
        self.assertEqual(self.store.memories(self.a), [])
        self.assertIn('已保存', self.store.command(self.a, '记住：我喜欢喝茶'))
        reopened = MemoryStore(self.store.path)
        self.assertIn('喝茶', reopened.context(self.a, '我喜欢喝什么'))
        self.assertNotIn('喝茶', reopened.context(self.b, '我喜欢喝什么'))
        self.assertEqual(reopened.context(None, '我喜欢什么'), '')
        self.assertIn('先', reopened.command(None, '记住：不能写入访客'))
        self.assertEqual(reopened.memories(self.b), [])

    def test_update_delete_scope_and_cascade(self):
        identity = self.store.save(self.a, '偏好旧内容')
        with self.assertRaises(ValueError):
            self.store.save(self.b, '尝试覆盖', identity)
        self.assertFalse(self.store.delete_memory(self.b, identity))
        self.store.save(self.a, '偏好新内容', identity)
        self.assertNotIn('旧内容', self.store.context(self.a, '偏好'))
        self.store.enroll(self.a, [1, 0, 0], 'test')
        self.store.delete_profile(self.a)
        self.assertEqual(self.store.voices('test'), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 0)

    def test_deduplicate_and_forget_exact(self):
        one = self.store.save(self.a, '机器人叫豆豆')
        self.assertEqual(one, self.store.save(self.a, '机器人叫豆豆'))
        self.store.command(self.a, '忘掉：机器人')
        self.assertEqual(len(self.store.memories(self.a)), 1)
        self.store.command(self.a, '忘掉：机器人叫豆豆')
        self.assertEqual(self.store.memories(self.a), [])

    def test_voice_threshold_margin_model_version_and_consent(self):
        import numpy as np
        speaker = SpeakerService(self.store, load_settings()['speaker'])
        with self.assertRaisesRegex(ValueError, '同意'):
            speaker.enroll(self.a, [], False)
        a = {'id': self.a, 'name': '甲', 'embedding': '[1,0,0]'}
        b = {'id': self.b, 'name': '乙', 'embedding': '[0.99,0.1,0]'}
        self.assertTrue(speaker.match(np.array([1,0,0]), [a])['matched'])
        self.assertFalse(speaker.match(np.array([1,0,0]), [a,b])['matched'])
        self.assertIsNone(speaker.match(np.array([0,1,0]), [a])['id'])
        self.store.enroll(self.a, [1,0,0], 'old-model')
        self.assertEqual(self.store.voices('new-model'), [])

    def test_cancel_before_start_and_turn_isolation(self):
        registry = TurnRegistry()
        registry.cancel('cancel-before-start')
        with self.assertRaises(Cancelled):
            registry.get('cancel-before-start').check()
        registry.get('different-turn-123').check()
        invoked = []
        token = registry.get('different-turn-123')
        token.attach(lambda: invoked.append(True))
        token.cancel()
        self.assertTrue(invoked)

    def test_cancel_stops_speech_subprocess(self):
        from yiban.backends.speech import MacSpeech
        started, finished = threading.Event(), threading.Event()
        original = subprocess.Popen
        processes, errors = [], []
        token = Cancellation()
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            started.set()
            return process
        def run():
            try:
                MacSpeech.run([sys.executable, '-c', 'import time; time.sleep(10)'], None, token, 12)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        with patch('yiban.backends.speech.subprocess.Popen', side_effect=launch):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(started.wait(2))
            token.cancel()
            self.assertTrue(finished.wait(2))
            worker.join()
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(any(isinstance(error, Cancelled) for error in errors))

    def test_vad_interrupt_preserves_speech_before_transcription(self):
        import numpy as np
        import wave
        from yiban.audio.voice import VoiceManager
        fixture = ROOT / '.runtime/test-audio/xiaoban-command.wav'
        if not fixture.exists():
            self.skipTest('先运行 tests/make_audio_fixtures.py 生成合成语音')
        with wave.open(str(fixture)) as audio:
            speech = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
        samples = np.concatenate([np.zeros(32000, dtype=np.int16), speech, np.zeros(24000, dtype=np.int16)])
        manager = VoiceManager()
        identity = manager.start('interrupt')['session']
        events, recordings = [], []
        def transcribe(raw):
            recordings.append(raw)
            return {'text': '请点头', 'speaker': {'id': None, 'matched': False}}
        try:
            for seq, offset in enumerate(range(0, len(samples), 4096)):
                data = {'session': identity, 'sequence': seq, 'pcm': base64.b64encode(samples[offset:offset+4096].tobytes()).decode()}
                event = manager.feed(data, transcribe)
                events.append(event['event'])
                if event['event'] == 'interrupt':
                    self.assertFalse(recordings, 'interrupt must arrive before ASR')
            self.assertLess(events.index('interrupt'), events.index('utterance'))
            self.assertEqual(len(recordings), 1)
            with wave.open(io.BytesIO(recordings[0])) as audio:
                received = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
            self.assertEqual(int(np.max(np.abs(received))), int(np.max(np.abs(speech))) - 1 if np.max(np.abs(speech)) else 0)
        finally:
            manager.stop(identity)

    def test_real_speaker_model_enrollment_and_heldout_audio(self):
        from yiban.audio.voice import pcm_wav
        import numpy as np
        fixture = ROOT / '.runtime/test-audio'
        speaker = SpeakerService(self.store, load_settings()['speaker'])
        if not speaker.available() or not (fixture/'speaker-heldout.wav').exists():
            self.skipTest('需要声纹模型和 make_audio_fixtures.py 的合成语音')
        with self.assertRaisesRegex(ValueError, '有效说话时间不足'):
            speaker.embedding(pcm_wav(np.zeros(16000*5, dtype=np.float32)), enrollment=True)
        recordings = [(fixture/f'speaker-{i}.wav').read_bytes() for i in (1,2,3)]
        speaker.enroll(self.a, recordings, True)
        identified = speaker.identify((fixture/'speaker-heldout.wav').read_bytes())
        self.assertEqual(identified['id'], self.a)
        self.store.forget_voice(self.a)
        self.assertFalse(speaker.identify(recordings[0])['matched'])


class CancellationHTTPTests(unittest.TestCase):
    def test_cancel_blocked_before_headers_and_between_chunks(self):
        for headers_first in (False, True):
            with self.subTest(headers_first=headers_first):
                entered, release = threading.Event(), threading.Event()
                class Model(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        if headers_first:
                            self.send_response(200)
                            self.end_headers()
                            self.wfile.write(b'{"message":{"content":"partial"},"done":false}\n')
                            self.wfile.flush()
                        entered.set()
                        release.wait(4)
                server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                config = load_settings()['llm']
                config['base_url'] = f'http://127.0.0.1:{server.server_port}'
                token, result = Cancellation(), []
                def consume():
                    try:
                        result.extend(ModelClient(config).chat([], cancel=token))
                    except Exception:
                        result.append('cancelled')
                worker = threading.Thread(target=consume)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    # Ensure the header-first path has attached its stream socket.
                    if headers_first:
                        limit = time.monotonic()+2
                        while not result and time.monotonic()<limit:
                            time.sleep(.01)
                    token.cancel()
                    worker.join(1)
                    self.assertFalse(worker.is_alive(), 'cancel should interrupt blocked HTTP read')
                    self.assertFalse(any(isinstance(x,dict) and x.get('done') for x in result))
                finally:
                    release.set()
                    server.shutdown()
                    server.server_close()
                    worker.join(3)
                    thread.join()


if __name__ == '__main__':
    unittest.main()
