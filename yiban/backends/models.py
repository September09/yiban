"""Translate model streams to the app's existing NDJSON event contract."""
import base64
import copy
import json
import os
import http.client
import socket
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit, unquote
import urllib.request


class ModelHTTPError(RuntimeError):
    def __init__(self, status):
        self.code = status
        super().__init__(f'模型服务请求失败（HTTP {status}）。')


class ModelClient:
    def __init__(self, config):
        self.config = config
        self._health_lock = threading.Lock()
        self._health_checking = False
        self._health_at = None
        self._health_ok = False

    def request(self, path, data=None, timeout=None):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.config["api_key_env"])
        if key and self.config["backend"] == "openai_compatible":
            headers["Authorization"] = "Bearer " + key
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.config["base_url"] + path, data=body, headers=headers)
        return urllib.request.urlopen(request, timeout=timeout or self.config["timeout"])

    def status(self):
        if self.config.get('status_check') == 'completion':
            # Some compatible providers do not expose a model catalog. Check an
            # actual short response in the background; page/liveness stays fast.
            with self._health_lock:
                ttl = 300 if self._health_ok else 30
                if not self._health_checking and (self._health_at is None or time.monotonic() - self._health_at >= ttl):
                    self._health_checking = True
                    threading.Thread(target=self._probe_completion, daemon=True).start()
                return {'chat': self._health_ok, 'vision': False}
        compatible = self.config["backend"] == "openai_compatible"
        with self.request("/models" if compatible else "/api/tags", timeout=3) as response:
            data = json.load(response)
        names = {item["id"] for item in data.get("data", [])} if compatible else {item["name"] for item in data.get("models", [])}
        return {"chat": self.config["model"] in names,
                "vision": bool(self.config["vision_model"]) and self.config["vision_model"] in names}

    def _probe_completion(self):
        ok = False
        try:
            ok = ModelClient(dict(self.config, max_tokens=24)).verify_completion()
        except Exception:
            pass
        finally:
            with self._health_lock:
                self._health_ok = ok
                self._health_at = time.monotonic()
                self._health_checking = False

    def verify_completion(self):
        from yiban.turns import Cancellation
        has_text, complete = False, False
        for event in self.chat([{'role': 'user', 'content': '连接测试，请仅回复：连接成功。'}], cancel=Cancellation()):
            has_text = has_text or bool(event.get('message', {}).get('content', '').strip())
            complete = complete or bool(event.get('done'))
        return has_text and complete

    @contextmanager
    def stream_request(self, path, payload, cancel=None, method="POST"):
        if cancel is None:
            with self.request(path, payload) as response:
                yield response
            return
        cancel.check()
        url = urlsplit(self.config['base_url'] + path)
        cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        connection = cls(url.hostname, url.port, timeout=self.config['timeout'])
        target = url.path
        # Match urllib's system/environment proxy routing while retaining socket
        # cancellation. HTTPS CONNECT keeps TLS verification for the target host.
        proxy = urllib.request.getproxies().get(url.scheme)
        if proxy and not urllib.request.proxy_bypass(url.hostname):
            proxy_url = urlsplit(proxy if '://' in proxy else 'http://' + proxy)
            if proxy_url.scheme != 'http' or not proxy_url.hostname:
                raise ValueError('流式模型连接需要 HTTP 代理，请检查系统代理设置。')
            proxy_headers = {}
            if proxy_url.username is not None:
                credentials = unquote(proxy_url.username) + ':' + unquote(proxy_url.password or '')
                proxy_headers['Proxy-Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode()
            connection = cls(proxy_url.hostname, proxy_url.port or 80, timeout=self.config['timeout'])
            if url.scheme == 'https':
                connection.set_tunnel(url.hostname, url.port or 443, headers=proxy_headers)
            else:
                target = self.config['base_url'] + path
        else:
            proxy_headers = {}
        response = None
        def abort():
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        try:
            connection.connect()
            cancel.attach(abort)
            cancel.check()
            headers = {'Content-Type': 'application/json'}
            if url.scheme == 'http':
                headers.update(proxy_headers)
            key = os.environ.get(self.config['api_key_env'])
            if key and self.config['backend'] == 'openai_compatible':
                headers['Authorization'] = 'Bearer ' + key
            connection.request(method, target, json.dumps(payload).encode() if payload is not None else None, headers)
            response = connection.getresponse()
            # HTTP/1.0 servers detach the socket from the connection at headers.
            stream_socket = getattr(getattr(response.fp, 'raw', None), '_sock', None)
            def abort_stream():
                if stream_socket is not None:
                    try:
                        stream_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            cancel.attach(abort_stream)
            try:
                cancel.check()
                if response.status >= 400:
                    raise ModelHTTPError(response.status)
                yield response
            finally:
                cancel.detach(abort_stream)
        finally:
            cancel.detach(abort)
            if response is not None:
                response.close()
            connection.close()

    def chat(self, messages, vision=False, cancel=None, json_schema=None):
        from yiban.conversation_diagnostics import TRACES, CURRENT
        TRACES.model_identity(CURRENT.get(),self.config,vision)
        model = self.config["vision_model"] if vision else self.config["model"]
        if not model:
            raise ValueError("当前配置未启用视觉模型。")
        if self.config["backend"] == "ollama":
            payload = {"model": model, "messages": messages, "stream": True, "keep_alive": "15m",
                       "options": {"num_ctx": 4096, "num_predict": self.config["max_tokens"],
                                   "temperature": self.config["temperature"]}}
            if json_schema is not None:
                payload["format"] = json_schema
            with self.stream_request("/api/chat", payload, cancel) as response:
                for line in response:
                    if cancel:
                        cancel.check()
                    if line.strip():
                        event = json.loads(line)
                        if event.get("error"):
                            raise RuntimeError(event["error"])
                        yield event
                        if event.get("done"):
                            return
        else:
            payload = {"model": model, "messages": self.convert_messages(messages), "stream": True,
                       "max_tokens": self.config["max_tokens"], "temperature": self.config["temperature"]}
            if self.config.get('enable_thinking') is not None:
                payload['enable_thinking'] = self.config['enable_thinking']
            if self.config.get('thinking_type') is not None:
                payload['thinking'] = {'type': self.config['thinking_type']}
            with self.stream_request("/chat/completions", payload, cancel) as response:
                for data in self.sse_data(response):
                    if cancel:
                        cancel.check()
                    if data == "[DONE]":
                        yield {"done": True}
                        return
                    event = json.loads(data)
                    if event.get("error"):
                        raise RuntimeError("模型服务返回错误。")
                    for choice in event.get("choices", []):
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta", {})
                        if delta.get("tool_calls") or delta.get("function_call"):
                            raise RuntimeError("当前对话接口不支持模型工具调用。")
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield {"message": {"role": "assistant", "content": content}, "done": False}
                        reason = choice.get("finish_reason")
                        if reason in {"stop", "length"}:
                            yield {"done": True, "done_reason": reason}
                            return
                        if reason:
                            raise RuntimeError("模型未正常结束回复：" + reason)
        raise RuntimeError("模型回复连接提前结束，请重试。")

    @staticmethod
    def convert_messages(messages):
        converted = copy.deepcopy(messages)
        for message in converted:
            images = message.pop("images", [])
            if images:
                content = [{"type": "text", "text": message["content"]}]
                for picture in images:
                    mime = "image/png" if base64.b64decode(picture, validate=True).startswith(b"\x89PNG") else "image/jpeg"
                    content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{picture}"}})
                message["content"] = content
        return converted

    @staticmethod
    def sse_data(response):
        lines = []
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if lines:
                    yield "\n".join(lines)
                    lines = []
            elif line.startswith("data:"):
                lines.append(line[5:].removeprefix(" "))
        if lines:
            yield "\n".join(lines)
