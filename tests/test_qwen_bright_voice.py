"""Verify designed voice routing and reference integrity without MLX or playback."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from yiban.audio.qwen_voice_model import VoiceModel
from yiban.audio.qwen_voices import BRIGHT_VOICE, bright_assets, CUSTOM_MODEL
from yiban.audio.speech_preferences import SpeechPreferences
from yiban.backends.qwen_speech import QwenSpeech


class BrightVoiceTests(unittest.TestCase):
    def test_reference_hash_and_duration_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);model=folder/'base';model.mkdir();(model/'config.json').write_text('{}')
            ref=folder/'reference.wav'
            with wave.open(str(ref),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*96000)
            profile={'version':1,'reference_text':'你好，今天辛苦了。',
                     'reference_sha256':hashlib.sha256(ref.read_bytes()).hexdigest(),'model_revision':'test'}
            (folder/'profile.json').write_text(json.dumps(profile))
            with patch('yiban.audio.qwen_voices.BRIGHT_DIRECTORY',folder),patch('yiban.audio.qwen_voices.BRIGHT_MODEL',model):
                assets=bright_assets();self.assertEqual(assets['reference'],ref)
                ref.write_bytes(ref.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError,'参考音频'):bright_assets()

    def test_base_receives_reference_without_custom_speaker(self):
        assets={'model':Path('/mock/base'),'reference':Path('/mock/reference.wav'),'text':'参考文字','fingerprint':'one'}
        base=Mock();base.generate.return_value=iter(['base audio'])
        custom=Mock();custom.generate_custom_voice.return_value=iter(['custom audio'])
        loader=Mock(side_effect=[base,custom])
        core=ModuleType('mlx.core');core.clear_cache=Mock()
        mlx=ModuleType('mlx');mlx.core=core
        with patch('yiban.audio.qwen_voice_model.validate_voice',return_value=assets), \
             patch('yiban.audio.qwen_voice_model.bright_assets',return_value=assets), \
             patch.dict('sys.modules',{'mlx':mlx,'mlx.core':core}):
            resident=VoiceModel(loader,BRIGHT_VOICE)
            self.assertEqual(list(resident.generate_custom_voice(text='新回答',speaker=BRIGHT_VOICE,stream=True)),['base audio'])
            base.generate.assert_called_once_with(text='新回答',lang_code='Chinese',ref_audio='/mock/reference.wav',ref_text='参考文字',stream=True)
            self.assertEqual(loader.call_count,1,'Repeated Base use must not reload the model')
            self.assertEqual(list(resident.generate_custom_voice(text='恢复',speaker='Serena',instruct='自然')),['custom audio'])
            self.assertEqual(loader.call_args.args[0],str(CUSTOM_MODEL))
            self.assertIs(resident.model,custom)
            core.clear_cache.assert_called_once()

    def test_saved_voice_controls_startup_and_catalog_label(self):
        assets={'model':Path('/mock/base'),'reference':Path('/mock/ref.wav'),'text':'你好','fingerprint':'one'}
        speech=QwenSpeech({'voice':'Serena','rate':195})
        self.addCleanup(speech.close)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(speech,'available',return_value=True), \
             patch('yiban.backends.qwen_speech.bright_assets',return_value=assets), \
             patch('yiban.audio.qwen_voices.bright_assets',return_value=assets):
            path=Path(tmp)/'speech.json'
            prefs=SpeechPreferences(speech,speech.config,path)
            saved=prefs.save({'voice':BRIGHT_VOICE,'rate':195,'revision':prefs.revision})
            self.assertEqual(speech.preferred_voice,BRIGHT_VOICE)
            self.assertEqual(saved['voices'][-1]['label'],'明亮女声')
            restored=SpeechPreferences(speech,speech.config,path)
            self.assertEqual(restored.snapshot()['voice'],BRIGHT_VOICE)

    def test_missing_reference_does_not_start_worker(self):
        speech=QwenSpeech({'voice':'Serena','rate':195})
        self.addCleanup(speech.close)
        with patch.object(speech,'available',return_value=True), \
             patch('yiban.audio.qwen_voices.bright_assets',side_effect=ValueError('参考音频不可用')):
            with self.assertRaises(ValueError):list(speech.stream_synthesize('测试',selection={'voice':BRIGHT_VOICE}))
            self.assertIsNone(speech.process)
