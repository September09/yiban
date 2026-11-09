import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from yiban.audio.speech_preferences import SpeechPreferences
from yiban.turns import Cancellation, Cancelled

class SpeechPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"speech-settings.json"
        self.speech = Mock()
        self.speech.available.return_value = True
        self.config = {"voice":"Tingting", "rate":195}
        self.catalog = patch("yiban.audio.speech_preferences.subprocess.run", return_value=Mock(stdout="Tingting  zh_CN  # test\nEddy (中文（中国大陆）)  zh_CN  # test\nSamantha en_US # test\n"))
        self.catalog.start();self.addCleanup(self.catalog.stop)
        self.prefs = SpeechPreferences(self.speech, self.config, self.path)

    def test_preview_is_not_saved_and_normal_utterance_uses_saved_preferences(self):
        selected={"voice":"Eddy (中文（中国大陆）)","rate":165}
        self.prefs.synthesize("preview",selection=selected)
        self.speech.synthesize.assert_called_with("preview",cancel=None,selection=selected)
        self.assertFalse(self.path.exists())
        result=self.prefs.save({**selected,"revision":self.prefs.revision})
        self.assertEqual(result["current"], selected)
        restored=SpeechPreferences(self.speech,self.config,self.path)
        restored.synthesize("normal")
        self.speech.synthesize.assert_called_with("normal",cancel=None,selection=selected)
        self.assertEqual(self.config["rate"],195)

    def test_invalid_selection_and_stale_save_do_not_write(self):
        for values in [{"voice":"injected","rate":195},{"voice":"Tingting","rate":True},{"voice":"Tingting","rate":99},{"voice":"Tingting","rate":301},{"voice":"Tingting","rate":195.5}]:
            with self.assertRaises(ValueError):self.prefs.save({**values,"revision":self.prefs.revision})
        with self.assertRaises(ValueError):self.prefs.save({**self.config,"revision":"stale"})
        self.assertFalse(self.path.exists())

    def test_failed_atomic_write_keeps_last_saved_state(self):
        self.prefs.save({**self.config,"revision":self.prefs.revision})
        before=self.path.read_bytes();revision=self.prefs.revision
        with patch("yiban.audio.speech_preferences.os.replace",side_effect=OSError("disk error")):
            with self.assertRaises(OSError):self.prefs.save({"voice":"Tingting","rate":150,"revision":revision})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.prefs.snapshot(),self.config)
        self.assertEqual(self.prefs.revision,revision)
        self.assertEqual(list(self.path.parent.glob(".speech-*")),[])

    def test_corrupt_or_removed_voice_falls_back_with_warning(self):
        for data in ["broken",json.dumps({"voice":"removed","rate":195})]:
            self.path.write_text(data)
            prefs=SpeechPreferences(self.speech,self.config,self.path)
            self.assertEqual(prefs.snapshot(),self.config)
            self.assertTrue(prefs.warning)
            self.assertEqual(self.path.read_text(),data)

    def test_disabled_platform_and_cancellation_propagate(self):
        self.speech.available.return_value=False
        self.assertFalse(self.prefs.describe()["available"])
        with self.assertRaises(ValueError):self.prefs.validate(self.config)
        self.speech.available.return_value=True
        cancel=Cancellation();cancel.cancel()
        self.speech.synthesize.side_effect=lambda text,cancel,selection:cancel.check()
        with self.assertRaises(Cancelled):self.prefs.synthesize("sample",cancel=cancel,selection=self.config)
