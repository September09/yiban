"""Isolated inference contract: no GPU, real model, microphone or playback required."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yiban.audio.speech_preferences import SpeechPreferences
from yiban.backends.qwen_speech import QwenSpeech
from yiban.settings import load_settings, ROOT


class QwenSpeechTests(unittest.TestCase):
    def setUp(self):
        self.speech = QwenSpeech({"voice": "Serena", "rate": 195})
        self.ready = patch.object(QwenSpeech, "available", return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.no_designed_voice = patch('yiban.backends.qwen_speech.bright_assets', side_effect=ValueError('not installed'))
        self.no_designed_voice.start()
        self.addCleanup(self.no_designed_voice.stop)

    def test_preferences_default_catalog_save_restore_and_old_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.json"
            prefs = SpeechPreferences(self.speech, self.speech.config, path)
            self.assertEqual(prefs.snapshot()["voice"], "Serena")
            self.assertEqual([v["name"] for v in prefs.voices()], ["Serena", "Vivian", "Uncle_Fu"])
            self.assertFalse(prefs.describe()["supports_rate"])
            prefs.save({"voice": "Vivian", "rate": 195, "revision": prefs.revision})
            restored = SpeechPreferences(self.speech, self.speech.config, path)
            self.assertEqual(restored.snapshot()["voice"], "Vivian")
            with self.assertRaises(ValueError):
                restored.validate({"voice": "Ryan", "rate": 195})
            with self.assertRaises(ValueError):
                restored.validate({"voice": "Serena", "rate": 160})
            path.write_text('{"voice":"Tingting","rate":195}')
            restored = SpeechPreferences(self.speech, self.speech.config, path)
            self.assertEqual(restored.snapshot()["voice"], "Serena")
            self.assertTrue(restored.warning)
            self.assertIn("Tingting", path.read_text(), "Old preferences must remain recoverable")

    def test_mac_profile_default(self):
        self.assertEqual(load_settings(ROOT / "config/mac.json")["tts"],
                         {"backend": "qwen3_tts", "voice": "Serena", "rate": 195})

    def test_generation_parameters_persist_and_preview_does_not_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'speech.json';prefs=SpeechPreferences(self.speech,self.speech.config,path)
            original=prefs.snapshot()
            selected={'voice':'Serena','rate':195,'generation':{'seed':7,'temperature':.6,'top_k':25,'top_p':.9}}
            with patch.object(self.speech,'synthesize',return_value=(b'a','audio/wav')) as synth:
                prefs.synthesize('preview',selection=selected)
                self.assertEqual(synth.call_args.kwargs['selection']['generation']['seed'],7)
            self.assertEqual(prefs.snapshot(),original);self.assertFalse(path.exists())
            prefs.save({**selected,'revision':prefs.revision})
            restored=SpeechPreferences(self.speech,self.speech.config,path)
            self.assertEqual(restored.snapshot()['generation']['seed'],7)
            self.assertEqual(restored.snapshot()['generation']['repetition_penalty'],1.05)
            before=path.read_bytes()
            with self.assertRaises(ValueError):prefs.save({**selected,'generation':{'seed':-1},'revision':prefs.revision})
            self.assertEqual(path.read_bytes(),before)

    def test_startup_override_changes_only_speech(self):
        with patch.dict(os.environ, {}, clear=True):
            original = load_settings()
            with patch.dict(os.environ, {"ASSISTANT_TTS_BACKEND": "qwen3_tts"}):
                selected = load_settings()
            self.assertEqual(selected.pop("tts")["voice"], "Serena")
            original.pop("tts")
            self.assertEqual(selected, original)
