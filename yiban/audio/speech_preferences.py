"""Local device speech preferences, independent of models and personal memories."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid
from yiban.audio.qwen_profile import generation_settings


class SpeechPreferences:
    MIN_RATE, MAX_RATE = 100, 300
    SAMPLE = "你好，我是你的陪伴助手。很高兴和你聊天，今天过得怎么样？"

    def __init__(self, speech, config, path):
        self.speech, self.path = speech, Path(path)
        self.lock = threading.RLock()
        self.current = {"voice": config["voice"], "rate": config["rate"]}
        self.revision = uuid.uuid4().hex
        self.warning = ""
        self._voices = None
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.current = self.validate(saved)
            except Exception:
                self.warning = "已保存的声音设置无法读取或音色不可用，暂用配置中的声音。请重新选择并保存。"

    def voices(self):
        with self.lock:
            if self._voices is None:
                if not self.speech.available():
                    return []
                if not getattr(type(self.speech), "supports_rate", True):
                    self._voices = self.speech.voices()
                    return list(self._voices)
                result = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True, text=True, timeout=10, check=True)
                voices = []
                for line in result.stdout.splitlines():
                    match = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Za-z_]+)\s+#", line)
                    if match:
                        voices.append({"name": match[1].strip(), "locale": match[2]})
                if not voices:
                    raise ValueError("没有找到系统音色，请检查 Mac 的系统语音设置。")
                self._voices = voices
            return list(self._voices)

    def validate(self, data):
        if not self.speech.available():
            raise ValueError("语音合成环境不可用，请检查本机语音环境。")
        if not isinstance(data, dict):
            raise ValueError("声音设置格式不正确。")
        voice, rate = data.get("voice"), data.get("rate")
        if not isinstance(voice, str) or voice not in {v["name"] for v in self.voices()}:
            raise ValueError("请选择本机音色列表中的声音。")
        if type(rate) is not int or not self.MIN_RATE <= rate <= self.MAX_RATE:
            raise ValueError("语速须为 100 到 300 之间的整数。")
        if not getattr(type(self.speech), "supports_rate", True) and rate != 195:
            raise ValueError("Qwen3-TTS 当前使用自然语速，请重新加载声音设置。")
        selected = {"voice": voice, "rate": rate}
        if getattr(type(self.speech), "engine_name", "") == "Qwen3-TTS":
            selected["generation"] = generation_settings(data.get("generation"))
        return selected

    def snapshot(self):
        with self.lock:
            selected = dict(self.current)
            if getattr(type(self.speech), "engine_name", "") == "Qwen3-TTS":
                selected["generation"] = generation_settings(selected.get("generation"))
            return selected

    def streaming_available(self):
        return self.speech.available() and callable(getattr(type(self.speech), "stream_synthesize", None))

    def stream_synthesize(self, text, cancel=None):
        selected = self.snapshot()
        if not self.streaming_available():
            raise ValueError("当前声音引擎不支持连续音频，请重新加载页面。")
        yield from self.speech.stream_synthesize(text, cancel=cancel, selection=selected)

    def describe(self):
        with self.lock:
            runtime = self.speech.runtime_status() if callable(getattr(type(self.speech), "runtime_status", None)) else None
            if runtime is not None and getattr(type(self.speech), "engine_name", "") == "Qwen3-TTS":
                runtime = {**runtime, "generation": {**runtime.get("generation", {}), **self.snapshot()["generation"]},
                           "streaming": "single_utterance_pcm"}
            return {"available": self.speech.available(), "voices": self.voices(),
                    "current": self.snapshot(), "revision": self.revision,
                    "generation_defaults": generation_settings() if getattr(type(self.speech), "engine_name", "") == "Qwen3-TTS" else None,
                    "min_rate": self.MIN_RATE, "max_rate": self.MAX_RATE, "warning": self.warning,
                    "supports_rate": getattr(type(self.speech), "supports_rate", True),
                    "engine": getattr(type(self.speech), "engine_name", "macOS"),
                    "runtime": runtime}

    def save(self, data):
        with self.lock:
            selected = self.validate(data)
            if data.get("revision") != self.revision:
                raise ValueError("声音设置已被其他页面更新，请重新加载后再保存。")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".speech-", delete=False) as f:
                    name = f.name
                    json.dump(selected, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(name, self.path)
            finally:
                if name and os.path.exists(name):
                    os.unlink(name)
            self.current = selected
            self.revision = uuid.uuid4().hex
            self.warning = ""
            return self.describe()

    def synthesize(self, text, cancel=None, selection=None):
        # Each utterance takes one snapshot; changing settings never alters in-flight audio.
        selected = self.validate(selection) if selection is not None else self.snapshot()
        if not self.speech.available():
            return self.speech.synthesize(text, cancel=cancel)
        return self.speech.synthesize(text, cancel=cancel, selection=selected)
