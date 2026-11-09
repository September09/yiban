"""Cloud onboarding tests without a key, external requests or service restarts."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import cloud_setup
from yiban.settings import load_settings
from yiban.backends.models import ModelHTTPError


class CloudSetupTests(unittest.TestCase):
    def test_zhipu_uses_separate_key_and_disables_thinking(self):
        config = cloud_setup.make_config('https://open.bigmodel.cn/api/paas/v4', 'glm-4.7-flash', 'zhipu')
        self.assertEqual(config['llm']['api_key_env'], 'ZHIPU_API_KEY')
        self.assertEqual(config['llm']['thinking_type'], 'disabled')
        with self.assertRaises(ValueError):
            cloud_setup.make_config('https://dashscope.aliyuncs.com/compatible-mode/v1', 'glm-4.7-flash', 'zhipu')
        with patch.object(cloud_setup, 'ModelClient') as client:
            client.return_value.status.side_effect = AssertionError('no models endpoint required')
            client.return_value.chat.return_value = [{'message': {'content': '成功'}, 'done': True}]
            cloud_setup.probe(load_settings(cloud_setup.ZHIPU_TEMPLATE)['llm'])

    def test_cloud_profile_keeps_local_personal_and_audio_features(self):
        cloud = load_settings(cloud_setup.TEMPLATE)
        local = load_settings()
        for section in ('asr', 'tts', 'memory', 'speaker', 'audio'):
            self.assertEqual(cloud[section], local[section])
        self.assertFalse(cloud['llm']['manage_local_ollama'])
        self.assertFalse(cloud['llm']['enable_thinking'])
        self.assertEqual(cloud['llm']['vision_model'], '')

    def test_reject_key_destinations_and_malformed_configuration(self):
        for url in ('http://dashscope.aliyuncs.com/compatible-mode/v1',
                    'https://dashscope.aliyuncs.com.evil.invalid/compatible-mode/v1',
                    'https://user:password@dashscope.aliyuncs.com/compatible-mode/v1',
                    'https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret',
                    'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                    'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cloud_setup.make_config(url, 'qwen-plus')
        config = cloud_setup.make_config('https://test-space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/', 'qwen-plus')
        self.assertTrue(config['llm']['base_url'].endswith('/v1'))

    def test_probe_requires_actual_complete_text(self):
        with patch.object(cloud_setup, 'ModelClient') as client:
            client.return_value.status.return_value = {'chat': True}
            for events in ([{'done': True}], [{'message': {'content': 'partial'}}]):
                client.return_value.chat.return_value = events
                with self.assertRaises(ValueError):
                    cloud_setup.probe(load_settings(cloud_setup.TEMPLATE)['llm'])
            client.return_value.chat.return_value = [{'message': {'content': '连接成功。'}, 'done': True}]
            cloud_setup.probe(load_settings(cloud_setup.TEMPLATE)['llm'])

    def run_setup(self, config_path, failure=None):
        output = io.StringIO()
        with patch.object(cloud_setup, 'CONFIG', config_path), \
             patch.object(sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=['', '']), \
             patch.object(cloud_setup.getpass, 'getpass', return_value='fake-test-key'), \
             patch.dict(os.environ, {'ASSISTANT_MODEL_API_KEY': ''}), \
             patch.object(cloud_setup, 'probe', side_effect=failure), \
             patch.object(cloud_setup.subprocess, 'call', return_value=0) as launch, \
             contextlib.redirect_stdout(output):
            result = cloud_setup.main()
        return result, output.getvalue(), launch

    def test_failed_probe_does_not_save_or_switch_service(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'cloud.local.json'
            original = cloud_setup.TEMPLATE.read_text()
            config.write_text(original)
            result, output, launch = self.run_setup(config, ModelHTTPError(401))
            self.assertEqual(result, 1)
            self.assertEqual(config.read_text(), original)
            launch.assert_not_called()
            self.assertIn('密钥认证失败', output)
            self.assertNotIn('fake-test-key', output)

    def test_success_saves_no_key_and_passes_it_only_to_child_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'cloud.local.json'
            result, output, launch = self.run_setup(config)
            self.assertEqual(result, 0)
            self.assertNotIn('fake-test-key', output + config.read_text())
            self.assertEqual(json.loads(config.read_text())['llm']['model'], 'qwen-plus')
            args, kwargs = launch.call_args
            self.assertIn('--restart-web', args[0])
            self.assertNotIn('fake-test-key', ' '.join(args[0]))
            self.assertEqual(kwargs['env']['ASSISTANT_MODEL_API_KEY'], 'fake-test-key')
            self.assertEqual(kwargs['env']['ASSISTANT_CONFIG'], str(config))


if __name__ == '__main__':
    unittest.main()
