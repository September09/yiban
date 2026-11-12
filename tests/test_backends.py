"""Local HTTP contract tests; no board, model downloads or microphone required."""
import base64
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.settings import ROOT, load_settings
from yiban.backends import build_services
from yiban.backends.models import ModelClient, ModelHTTPError
from yiban.turns import Cancellation


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "test-chat"}, {"id": "test-vision"}]}).encode())

    def do_POST(self):
        self.server.request_path = self.path
        self.server.request_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.authorization = self.headers.get("Authorization")
        self.send_response(self.server.response_status)
        self.end_headers()
        self.wfile.write(self.server.response_body)


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        self.config = load_settings(ROOT / "config/board-text.example.json")
        self.config["llm"].update(base_url=f"http://127.0.0.1:{self.http.server_port}/v1",
                                   model="test-chat", vision_model="test-vision")
        self.client = ModelClient(self.config["llm"])
        self.http.response_status = 200
        self.http.response_body = (
            ': heartbeat\r\n\r\ndata: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\r\n\r\n'
            'data: {"choices":[{"index":0,"delta":{"content":"你好"}}]}\r\n\r\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n'
        ).encode()

    def test_model_list_and_stream_and_auth(self):
        self.assertEqual(self.client.status(), {"chat": True, "vision": True})
        with patch.dict(os.environ, {"ASSISTANT_MODEL_API_KEY": "test-key"}):
            events = list(self.client.chat([{"role": "user", "content": "你好"}]))
        self.assertEqual(events[0]["message"]["content"], "你好")
        self.assertTrue(events[-1]["done"])
        self.assertEqual(self.http.request_path, "/v1/chat/completions")
        self.assertEqual(self.http.authorization, "Bearer test-key")
        self.assertNotIn("options", self.http.request_body)
        self.assertNotIn("enable_thinking", self.http.request_body)

    def test_bailian_non_thinking_stream_with_cancellation(self):
        self.config['llm']['enable_thinking'] = False
        with patch.dict(os.environ, {'ASSISTANT_MODEL_API_KEY': 'test-key'}):
            events = list(self.client.chat([{'role': 'user', 'content': '你好'}], cancel=Cancellation()))
        self.assertFalse(self.http.request_body['enable_thinking'])
        self.assertEqual(self.http.authorization, 'Bearer test-key')
        self.assertEqual(events[0]['message']['content'], '你好')
        self.assertTrue(events[-1]['done'])
        self.http.response_status = 401
        self.http.response_body = b'private provider error body'
        with self.assertRaises(ModelHTTPError) as caught:
            list(self.client.chat([], cancel=Cancellation()))
        self.assertEqual(caught.exception.code, 401)
        self.assertNotIn('private', str(caught.exception))

    def test_picture_conversion_does_not_change_input(self):
        picture = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
        messages = [{"role": "user", "content": "看图", "images": [picture]}]
        list(self.client.chat(messages, vision=True))
        self.assertEqual(self.http.request_body["model"], "test-vision")
        image = self.http.request_body["messages"][0]["content"][1]
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(messages[0]["images"], [picture])

    def test_zhipu_payload_and_completion_health_cache(self):
        self.config['llm'].update(thinking_type='disabled', status_check='completion', vision_model='')
        events = list(self.client.chat([], cancel=Cancellation()))
        self.assertTrue(events[-1]['done'])
        self.assertEqual(self.http.request_body['thinking'], {'type': 'disabled'})
        self.assertNotIn('enable_thinking', self.http.request_body)
        with patch.object(self.client, 'request', side_effect=AssertionError('no model catalog')):
            self.assertFalse(self.client.status()['chat'])
        import time
        until = time.monotonic() + 2
        while self.client._health_checking and time.monotonic() < until:
            time.sleep(.01)
        self.assertTrue(self.client.status()['chat'])
        with patch.object(ModelClient, 'verify_completion', side_effect=AssertionError('cached')):
            self.assertEqual(self.client.status(), {'chat': True, 'vision': False})
        self.http.response_body = b'data: [DONE]\n\n'
        self.client._probe_completion()
        self.assertFalse(self.client.status()['chat'], 'empty completion is not healthy')

    def test_https_proxy_tunnel_keeps_auth_at_destination(self):
        config = dict(self.config['llm'], base_url='https://open.bigmodel.cn/api/paas/v4')
        with patch('yiban.backends.models.urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7897'}), \
             patch('yiban.backends.models.urllib.request.proxy_bypass', return_value=False), \
             patch('yiban.backends.models.http.client.HTTPSConnection') as connection, \
             patch.dict(os.environ, {'ASSISTANT_MODEL_API_KEY': 'test-key'}):
            response = connection.return_value.getresponse.return_value
            response.status = 200
            with ModelClient(config).stream_request('/chat/completions', {}, Cancellation()):
                pass
            connection.return_value.set_tunnel.assert_called_once_with('open.bigmodel.cn', 443, headers={})
            args = connection.return_value.request.call_args.args
            self.assertEqual(args[1], '/api/paas/v4/chat/completions')
            self.assertEqual(args[3]['Authorization'], 'Bearer test-key')

    def test_truncated_stream_is_not_reported_complete(self):
        self.http.response_body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        iterator = self.client.chat([])
        self.assertEqual(next(iterator)["message"]["content"], "partial")
        with self.assertRaisesRegex(RuntimeError, "提前结束"):
            next(iterator)

    def test_error_and_unsupported_tool_call(self):
        for data in ('{"error":{"message":"failure"}}', '{"choices":[{"delta":{"tool_calls":[{}]}}]}'):
            with self.subTest(data=data):
                self.http.response_body = ("data: " + data + "\n\n").encode()
                with self.assertRaises(RuntimeError):
                    list(self.client.chat([]))

    def test_sse_multiline_and_done(self):
        data = io.BytesIO(b': ping\n\ndata: {"choices":\ndata: []}\n\ndata: [DONE]\n')
        self.assertEqual(list(ModelClient.sse_data(data)), ['{"choices":\n[]}', '[DONE]'])
        self.http.response_body = b'data: [DONE]\n\n'
        self.assertEqual(list(self.client.chat([])), [{"done": True}])

    def test_ollama_contract(self):
        config = load_settings()["llm"]
        config["base_url"] = f"http://127.0.0.1:{self.http.server_port}"
        self.http.response_body = b'{"message":{"content":"hello"},"done":false}\n{"done":true}\n'
        events = list(ModelClient(config).chat([{"role": "user", "content": "hello"}]))
        self.assertTrue(events[-1]["done"])
        self.assertEqual(self.http.request_path, "/api/chat")
        self.assertEqual(self.http.request_body["keep_alive"], "15m")
        self.assertEqual(self.http.request_body["options"]["num_ctx"], 4096)

    def test_disabled_features_fail_explicitly(self):
        services = build_services(self.config)
        self.assertFalse(services.asr.available())
        self.assertFalse(services.tts.available())
        with self.assertRaises(RuntimeError):
            services.tts.synthesize("你好")
        with self.assertRaises(ValueError):
            services.actions.execute("run_shell")
        self.assertEqual(services.actions.execute("nod")["target"], "screen")

    def test_invalid_settings(self):
        for supplied in ({"actions": {"backend": "servo"}}, {"audio": {"input": "usb"}},
                         {"llm": {"backend": "openai_compatible"}}, {"unknown": 1},
                         {"llm": {"base_url": "http://user:password@localhost"}},
                         {"llm": {"enable_thinking": "false"}},
                         {"llm": {"thinking_type": "bad"}},
                         {"llm": {"thinking_type": "disabled", "enable_thinking": False}},
                         {"llm": {"status_check": "invalid"}}):
            with self.subTest(supplied=supplied), tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(supplied, f)
                f.flush()
                with self.assertRaises(ValueError):
                    load_settings(f.name)

    def test_text_profile_import_without_optional_packages(self):
        # Importing server initializes its stores; never let this test use the real database.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = load_settings(ROOT / "config/board-text.example.json")
            config["memory"]["database"] = str(folder / "import-only.sqlite3")
            config_path = folder / "config.json"
            config_path.write_text(json.dumps(config))
            env = os.environ.copy()
            env["ASSISTANT_CONFIG"] = str(config_path)
            result = subprocess.run([sys.executable, "-S", "-c",
                "import sys, server; assert 'sherpa_onnx' not in sys.modules; assert 'numpy' not in sys.modules"],
                cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((folder / "import-only.sqlite3").exists())

    def test_text_profile_http_app(self):
        config = load_settings(ROOT / "config/board-text.example.json")
        config["llm"].update(base_url=self.config["llm"]["base_url"], model="test-chat")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
            config["memory"]["database"] = f.name + '.sqlite3'
            json.dump(config, f)
            f.flush()
            with patch.dict(os.environ, {"ASSISTANT_CONFIG": f.name}):
                spec = importlib.util.spec_from_file_location("test_app", ROOT / "server.py")
                app = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(app)
            app.Handler.log_message = lambda *args: None
            http = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            app.PORT = http.server_port
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{http.server_port}"
            def request(path, data=None, headers=None):
                body = json.dumps(data).encode() if data is not None else None
                return urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers=headers or {}), timeout=5)
            try:
                with request("/api/status") as response:
                    status = json.load(response)
                self.assertTrue(status["chat"])
                self.assertFalse(status["tts"])
                self.assertFalse(status["wake"])
                with request("/api/chat", {"messages": [{"role": "user", "content": "你好"}]}) as response:
                    events = [json.loads(row) for row in response]
                self.assertEqual(events[0]["message"]["content"], "你好")
                self.assertTrue(events[-1]["done"])
                for path, data in (("/api/speak", {"text": "你好"}),
                                   ("/api/listen/start", {"mode": "wake"}),
                                   ("/api/action", {"action": "run_shell"})):
                    with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                        request(path, data)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request("/api/status", headers={"Origin": "http://other.invalid"})
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
                self.http.response_body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                with request("/api/chat", {"messages": [{"role": "user", "content": "你好"}]}) as response:
                    events = [json.loads(row) for row in response]
                self.assertIn("error", events[-1])
                self.assertFalse(any(event.get("done") for event in events))
            finally:
                http.shutdown()
                http.server_close()
                thread.join()
                Path(config["memory"]["database"]).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
