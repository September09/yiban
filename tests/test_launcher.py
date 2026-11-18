"""Launch defaults and explicit profiles; no processes or audio are started."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launcher
from yiban.settings import load_settings


class LauncherEnvironmentTests(unittest.TestCase):
    def test_direct_mac_restart_uses_same_voice_backend_as_desktop_entry(self):
        with patch.dict(os.environ, {}, clear=True), patch("launcher.platform.system", return_value="Darwin"):
            launcher.configure_launch_environment()
            settings = load_settings()
            self.assertEqual(settings["tts"]["backend"], "qwen3_tts")
            self.assertEqual(settings["tts"]["voice"], "Serena")
            self.assertEqual(settings["llm"]["model"], "qwen2.5:3b")
            self.assertNotIn("ASSISTANT_CONFIG", os.environ)

    def test_explicit_system_voice_is_respected(self):
        with patch.dict(os.environ, {"ASSISTANT_TTS_BACKEND": "macos"}, clear=True), patch("launcher.platform.system", return_value="Darwin"):
            launcher.configure_launch_environment()
            self.assertEqual(load_settings()["tts"]["backend"], "macos")

    def test_explicit_profile_is_not_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"tts": {"backend": "disabled"}}))
            with patch.dict(os.environ, {"ASSISTANT_CONFIG": str(path)}, clear=True), patch("launcher.platform.system", return_value="Darwin"):
                launcher.configure_launch_environment()
                self.assertEqual(load_settings()["tts"]["backend"], "disabled")
                self.assertNotIn("ASSISTANT_TTS_BACKEND", os.environ)

    def test_explicit_backend_can_override_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"tts": {"backend": "macos"}}))
            with patch.dict(os.environ, {"ASSISTANT_CONFIG": str(path), "ASSISTANT_TTS_BACKEND": "qwen3_tts"}, clear=True):
                launcher.configure_launch_environment()
                self.assertEqual(load_settings()["tts"]["backend"], "qwen3_tts")

    def test_non_mac_launch_does_not_enable_mac_voice_model(self):
        for system in ("Windows", "Linux"):
            with self.subTest(system=system), patch.dict(os.environ, {}, clear=True), patch("launcher.platform.system", return_value=system):
                launcher.configure_launch_environment()
                self.assertNotIn("ASSISTANT_TTS_BACKEND", os.environ)
