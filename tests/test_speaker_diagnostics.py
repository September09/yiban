import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.audio.diagnostics import audio_metadata, safe_record
from yiban.memory.memory_store import MemoryStore
from yiban.settings import load_settings
from yiban.audio.speakers import SpeakerService


class SpeakerDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name)/'test.sqlite3')
        self.service = SpeakerService(self.store, load_settings()['speaker'])

    def tearDown(self):
        self.temp.cleanup()

    def test_logs_exclude_private_content_and_features(self):
        data = safe_record({'trace_id': 'abc', 'speech_seconds': .5, 'code': 'insufficient_speech',
                            'text': 'private words', 'embedding': [1, 2], 'name': 'private name',
                            'raw': b'secret audio', 'profile_id': 'private id'})
        self.assertEqual(set(data), {'time_utc', 'trace_id', 'speech_seconds', 'code'})
        self.assertNotIn('private', json.dumps(data))

    def test_real_silent_audio_reports_duration_and_rejection(self):
        if not self.service.available():
            self.skipTest('local speaker/VAD models unavailable')
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as w:
            w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            w.writeframes(bytes(16000*2*5))
        self.assertEqual(audio_metadata(raw.getvalue())['audio_seconds'], 5)
        result = self.service.identify(raw.getvalue())
        d = result['diagnostics']
        self.assertFalse(result['matched'])
        self.assertEqual(d['code'], 'insufficient_speech')
        self.assertEqual(d['audio_seconds'], 5)
        self.assertEqual(d['speech_seconds'], 0)
        self.assertEqual(d['min_speech_seconds'], 1.2)
        self.assertEqual(d['peak'], 0)
        self.assertNotIn('embedding', d)

    def test_matching_reasons_are_distinct(self):
        import numpy as np
        a = {'id': 'a', 'name': 'A', 'embedding': '[1,0]'}
        b = {'id': 'b', 'name': 'B', 'embedding': '[0.99,0.1]'}
        empty = self.service.match(np.array([1,0]), [])
        self.assertEqual(empty['diagnostics']['code'], 'no_compatible_voiceprint')
        low = self.service.match(np.array([0,1]), [a])
        self.assertEqual(low['diagnostics']['code'], 'low_score')
        ambiguous = self.service.match(np.array([1,0]), [a,b])
        self.assertEqual(ambiguous['diagnostics']['code'], 'ambiguous')
        self.assertLess(ambiguous['diagnostics']['score_gap'], .08)
        matched = self.service.match(np.array([1,0]), [a])
        self.assertEqual(matched['diagnostics']['code'], 'matched')
        self.assertEqual(matched['diagnostics']['compatible_count'], 1)
        self.assertIsNone(matched['diagnostics']['runner_up_score'])


if __name__ == '__main__':
    unittest.main()
