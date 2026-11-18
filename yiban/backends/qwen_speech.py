"""One offline MLX worker per web service; models stay out of the HTTP process."""
import atexit
from collections import deque
import json
import hashlib
import os
from pathlib import Path
import platform
import queue
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import base64
import struct
import math

from yiban.settings import ROOT
from yiban.turns import Cancellation, Cancelled
from yiban.audio.qwen_profile import GENERATION_OPTIONS, MAX_SEGMENT_CHARS, SEED, generation_settings
from yiban.audio.qwen_voices import BUILTIN_VOICES, BRIGHT_VOICE, VOICES, bright_assets, validate_voice


class SpeechLengthError(RuntimeError):
    pass


class QwenSpeech:
    supports_rate = False
    engine_name = "Qwen3-TTS"
    VOICES = VOICES
    LOAD_TIMEOUT, REQUEST_TIMEOUT, CANCEL_TIMEOUT = 180, 120, 3
    QUEUE_TIMEOUT, MAX_WAITERS, CACHE_TTL = 8, 4, 180

    def __init__(self, config):
        self.config = config
        self.preferred_voice = config.get("voice", "Serena")
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.admission = threading.Condition()
        self.waiters = deque()
        self.audio_cache = {}
        self.cache_bytes = 0
        self.python = ROOT / ".runtime/qwen3-tts/.venv/bin/python"
        self.model = ROOT / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit"
        self.process = None
        self.responses = None
        self.state = "idle"
        self.warm_seconds = None
        self.errors = deque(maxlen=24)
        atexit.register(self.close)

    def available(self):
        return platform.system() == "Darwin" and self.python.is_file() and (self.model / "config.json").is_file()

    def voices(self):
        result = [{"name": voice, "locale": "zh_CN"} for voice in BUILTIN_VOICES]
        try:
            bright_assets()
            result.append({"name": BRIGHT_VOICE, "label": "明亮女声", "locale": "zh_CN"})
        except ValueError:
            pass
        return result

    def set_default_voice(self, voice):
        validate_voice(voice)
        self.preferred_voice = voice

    def runtime_status(self):
        state = self.state
        if self.process is not None and self.process.poll() is not None:
            state = "error"
        return {"state": state, "resident": state in ("loading", "ready"), "warm_seconds": self.warm_seconds,
                "generation": {**GENERATION_OPTIONS, "seed": SEED, "max_segment_chars": MAX_SEGMENT_CHARS}}

    def _acquire(self, cancel):
        ticket = object()
        started = time.monotonic()
        with self.admission:
            if len(self.waiters) >= self.MAX_WAITERS:
                raise RuntimeError("声音服务正忙，请稍后点击再听一遍。")
            self.waiters.append(ticket)
        announced = False
        try:
            while True:
                cancel.check()
                with self.admission:
                    if self.waiters[0] is ticket and self.lock.acquire(blocking=False):
                        self.waiters.popleft()
                        return
                    position = list(self.waiters).index(ticket) + 1
                if time.monotonic() - started >= self.QUEUE_TIMEOUT:
                    raise RuntimeError("声音服务正忙，等待已停止；请稍后点击再听一遍。")
                if not announced:
                    announced = True
                    yield {"type": "status", "state": "queued", "position": position}
                with self.admission:
                    self.admission.wait(.05)
        finally:
            with self.admission:
                if ticket in self.waiters:
                    self.waiters.remove(ticket)
                self.admission.notify_all()

    def _release(self):
        self.lock.release()
        with self.admission:
            self.admission.notify_all()

    def _cached(self, key):
        with self.admission:
            now = time.monotonic()
            for old in list(self.audio_cache):
                if self.audio_cache[old][0] < now:
                    _, packets, size = self.audio_cache.pop(old)
                    self.cache_bytes -= size
            value = self.audio_cache.get(key)
            return value[1] if value else None

    def _remember(self, key, packets):
        size = sum(len(p.get("audio", "")) for p in packets)
        if size > 8_000_000:
            return
        with self.admission:
            while self.audio_cache and (len(self.audio_cache) >= 12 or self.cache_bytes + size > 24_000_000):
                _, _, old_size = self.audio_cache.pop(next(iter(self.audio_cache)))
                self.cache_bytes -= old_size
            if key in self.audio_cache:
                self.cache_bytes -= self.audio_cache.pop(key)[2]
            self.audio_cache[key] = (time.monotonic() + self.CACHE_TTL, tuple(dict(p) for p in packets), size)
            self.cache_bytes += size

    def _command(self):
        return [str(self.python), "-m", "tools.qwen_tts_worker", "--serve"]

    @staticmethod
    def _read_responses(process, responses):
        try:
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError()
                    responses.put(value)
                except (ValueError, TypeError):
                    responses.put({"event": "error", "error": "protocol"})
                    break
        finally:
            responses.put({"event": "exit"})

    @staticmethod
    def _read_errors(process, errors):
        for line in process.stderr:
            errors.append(line[-500:])

    def _start(self):
        self.close()
        self.state = "loading"
        self.errors = deque(maxlen=24)
        self.responses = queue.Queue()
        self.process = subprocess.Popen(
            self._command(), cwd=ROOT,
            env=dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", YIBAN_TTS_INITIAL_VOICE=self.preferred_voice),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._read_responses, args=(self.process, self.responses), daemon=True).start()
        threading.Thread(target=self._read_errors, args=(self.process, self.errors), daemon=True).start()

    def _send(self, data):
        with self.write_lock:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("Qwen3-TTS 声音服务已退出，下次朗读会重新准备。")
            self.process.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _receive(self, deadline, cancel=None):
        while time.monotonic() < deadline:
            if cancel:
                cancel.check()
            try:
                result = self.responses.get(timeout=.05)
            except queue.Empty:
                continue
            if result.get("event") in ("error", "exit"):
                if result.get("error") == "metal" or any("Metal" in x or "metal::" in x for x in self.errors):
                    raise RuntimeError("Qwen3-TTS 无法访问 Mac Metal GPU，请从本机终端重新启动助手后重试。")
                raise RuntimeError("Qwen3-TTS 声音服务异常，下次朗读会重新准备。")
            return result
        raise TimeoutError("Qwen3-TTS 准备或合成超时，请稍后重试。")

    def _ensure_ready(self, cancel):
        if self.process is None or self.process.poll() is not None:
            self._start()
        if self.state == "ready":
            return
        try:
            result = self._receive(time.monotonic() + self.LOAD_TIMEOUT, cancel)
            if result.get("event") != "ready":
                raise RuntimeError("Qwen3-TTS 声音服务响应不正确。")
            self.state = "ready"
            self.warm_seconds = result.get("warm_seconds")
        except Cancelled:
            # Loading is shared work, so a cancelled waiter must not discard it.
            raise
        except Exception:
            self.close()
            self.state = "error"
            raise

    def warm(self):
        if not self.available():
            return
        with self.lock:
            self._ensure_ready(Cancellation())

    def synthesize(self, text, cancel=None, selection=None):
        if not self.available():
            raise RuntimeError("Qwen3-TTS 独立环境或模型未安装，请先完成本机安装。")
        selected = dict(selection or self.config)
        if selected.get("voice") not in self.VOICES:
            raise ValueError("请选择声音列表中的音色。")
        validate_voice(selected["voice"])
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError("朗读内容为空或过长。")
        cancel = cancel or Cancellation()
        cancel.check()
        for _ in self._acquire(cancel):
            pass
        try:
            cancel.check()
            self._ensure_ready(cancel)
            identity = uuid.uuid4().hex
            with tempfile.TemporaryDirectory(prefix="yiban-qwen-tts-") as tmp:
                output = Path(tmp) / "speech.wav"
                def abort():
                    try:
                        self._send({"op": "cancel", "id": identity})
                    except (OSError, RuntimeError):
                        pass
                sent = received = False
                try:
                    cancel.attach(abort)
                    cancel.check()
                    self._send({"op": "synthesize", "id": identity, "text": text,
                                "voice": selected["voice"], "generation": generation_settings(selected.get("generation")), "output": str(output)})
                    sent = True
                    result = self._receive(time.monotonic() + self.REQUEST_TIMEOUT, cancel)
                    received = True
                    cancel.check()
                    if result.get("id") != identity or result.get("event") != "done" or result.get("cancelled"):
                        raise RuntimeError("Qwen3-TTS 返回了无效的合成结果。")
                    with wave.open(str(output), "rb") as wav:
                        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getnframes() == 0:
                            raise RuntimeError("Qwen3-TTS 返回了无效音频。")
                    cancel.check()
                    return output.read_bytes(), "audio/wav"
                except Cancelled:
                    if sent and not received:
                        # Drain the cancelled job before reusing the model. Never
                        # let a late response become the next request's audio.
                        try:
                            result = self._receive(time.monotonic() + self.CANCEL_TIMEOUT)
                            if result.get("id") != identity or result.get("event") != "done":
                                raise RuntimeError()
                        except Exception:
                            self.close()
                    raise
                except Exception:
                    self.close()
                    self.state = "error"
                    raise
                finally:
                    cancel.detach(abort)
        finally:
            self._release()

    def stream_synthesize(self, text, cancel=None, selection=None):
        if not self.available():
            raise RuntimeError("Qwen3-TTS 独立环境或模型未安装。")
        selected = dict(selection or self.config)
        if selected.get("voice") not in self.VOICES:
            raise ValueError("请选择声音列表中的音色。")
        resources = validate_voice(selected["voice"])
        if not isinstance(text, str) or not text.strip() or len(text) > 1200:
            raise ValueError("单次连续朗读须为 1 到 1200 字，请缩短回答后重试。")
        options = generation_settings(selected.get("generation"))
        cancel = cancel or Cancellation()
        cancel.check()
        # Only exact completed audio is cached, briefly in RAM. No memory,
        # answer, authentication or permission decision is cached here.
        key = hashlib.sha256(json.dumps([str(self.model), text, selected["voice"], options,
                resources["fingerprint"] if resources else None], sort_keys=True).encode()).hexdigest()
        cached = self._cached(key)
        if cached:
            for packet in cached:
                cancel.check()
                yield dict(packet)
            return
        yield from self._acquire(cancel)
        cached = self._cached(key)
        if cached:
            self._release()
            for packet in cached:
                cancel.check()
                yield dict(packet)
            return
        packets = []
        sent = completed = False
        identity = uuid.uuid4().hex
        def abort():
            try:
                self._send({"op": "cancel", "id": identity})
            except (OSError, RuntimeError):
                pass
        try:
            cancel.check()
            self._ensure_ready(cancel)
            cancel.attach(abort)
            cancel.check()
            self._send({"op": "synthesize", "id": identity, "text": text,
                        "voice": selected["voice"], "generation": options, "stream": True})
            sent = True
            deadline = time.monotonic() + 240
            sequence = total = 0
            sample_rate = None
            while True:
                result = self._receive(deadline, cancel)
                if result.get("id") != identity:
                    raise RuntimeError("连续声音响应不匹配。")
                if result.get("event") == "done":
                    completed = True
                    cancel.check()
                    if result.get("failure") == "length_limit":
                        raise SpeechLengthError("本次声音生成异常，已停止过长朗读；文字回答仍可查看。")
                    if result.get("cancelled") or not total or result.get("chunks") != sequence or result.get("total_samples") != total or result.get("model_calls") != 1:
                        raise RuntimeError("连续声音未完整生成。")
                    final = {"type": "done", "chunks": sequence, "total_samples": total, "model_calls": 1}
                    packets.append(final)
                    self._remember(key, packets)
                    yield final
                    return
                count, rate = result.get("samples"), result.get("sample_rate")
                if result.get("event") != "audio" or result.get("format") != "f32le" or result.get("sequence") != sequence or type(count) is not int or not 0 < count <= 192000 or type(rate) is not int or rate not in (16000, 22050, 24000, 44100, 48000) or (sample_rate is not None and sample_rate != rate):
                    raise RuntimeError("连续音频格式不正确。")
                samples = base64.b64decode(result["audio"], validate=True)
                if len(samples) != count*4 or any(not math.isfinite(x[0]) or abs(x[0]) > 1 for x in struct.iter_unpack('<f', samples)):
                    raise RuntimeError("连续音频数据不正确。")
                sample_rate = rate
                total += count
                sequence += 1
                packet = {"type": "audio", **{key: result[key] for key in ("sequence", "format", "sample_rate", "samples", "audio")}}
                packets.append(packet)
                yield packet
        except (Cancelled, GeneratorExit, SpeechLengthError):
            raise
        except Exception:
            self.close()
            self.state = "error"
            raise
        finally:
            cancel.detach(abort)
            if sent and not completed and self.process is not None:
                abort()
                try:
                    deadline = time.monotonic() + self.CANCEL_TIMEOUT
                    while True:
                        result = self._receive(deadline)
                        if result.get("id") != identity:
                            raise RuntimeError("Unexpected cancelled response")
                        if result.get("event") == "done":
                            break
                except Exception:
                    self.close()
            self._release()

    def close(self):
        process, self.process = self.process, None
        self.state = "idle"
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=1)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
