"""Streaming wake word + VAD sessions; all audio stays in process memory."""
import base64
import io
from pathlib import Path
import re
import threading
import time
import uuid
import wave

import numpy as np
import sherpa_onnx

ROOT = Path(__file__).resolve().parent
KWS_DIR = ROOT / "models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
RATE = 16000
WAKE_WORD = "你好小伴"


def available():
    return (KWS_DIR / "tokens.txt").exists() and (ROOT / "models/silero_vad.onnx").exists()


def pcm_wav(samples):
    output = io.BytesIO()
    with wave.open(output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


class VoiceSession:
    def __init__(self, spotter, mode):
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(ROOT / "models/silero_vad.onnx")
        config.silero_vad.min_silence_duration = 0.9
        config.silero_vad.min_speech_duration = 0.2
        config.silero_vad.max_speech_duration = 29
        config.sample_rate = RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=35)
        self.spotter = spotter
        self.stream = spotter.create_stream()
        self.mode = mode
        self.last_used = time.monotonic()
        self.frames = []
        self.buffer = np.empty(0, np.float32)
        self.duration = 0
        self.speech = False
        self.sequence = 0
        self.identify_speaker = False

    def reset(self, mode="wake"):
        self.stream = self.spotter.create_stream()
        self.vad.reset()
        self.mode = mode
        self.frames = []
        self.buffer = np.empty(0, np.float32)
        self.duration = 0
        self.speech = False

    def feed(self, samples, transcribe):
        if self.mode == "paused":
            return {"event": "paused"}
        if self.mode == "wake":
            self.stream.accept_waveform(RATE, samples)
            self.duration += len(samples) / RATE
            while self.spotter.is_ready(self.stream):
                self.spotter.decode_stream(self.stream)
                if self.spotter.get_result(self.stream):
                    self.spotter.reset_stream(self.stream)
                    self.reset("command")
                    # Preserve the current chunk so a prompt directly following
                    # the wake word is not lost. Strip the wake phrase after ASR.
                    self.frames.append(samples)
                    return {"event": "wake", "keyword": WAKE_WORD}
            if self.duration > 60:
                self.reset()
            return {"event": "waiting"}

        self.frames.append(samples)
        self.duration += len(samples) / RATE
        self.buffer = np.concatenate((self.buffer, samples))
        window = self.vad.config.silero_vad.window_size
        ended = False
        while len(self.buffer) >= window:
            self.vad.accept_waveform(self.buffer[:window])
            self.buffer = self.buffer[window:]
            self.speech = self.speech or self.vad.is_speech_detected()
            if not self.vad.empty():
                ended = True
        if self.mode == "interrupt":
            if self.speech or ended:
                self.mode = "command"
                return {"event": "interrupt"}
            # Keep a short pre-roll, without unbounded buffers during playback.
            if self.duration > 1:
                self.frames = [np.concatenate(self.frames)[-RATE:]]
                self.duration = 1
            return {"event": "monitoring"}
        if ended or self.duration >= 30:
            audio = np.concatenate(self.frames)
            had_speech = self.speech or ended
            self.reset("paused")
            result = transcribe(pcm_wav(audio)) if had_speech else ""
            extra = result if isinstance(result, dict) else {}
            text = result.get('text', '') if isinstance(result, dict) else result
            text = re.sub(r"^[\s，。！？、]*(?:你好[，\s]*小[伴半办]|小[伴半办])[，。！？、\s]*", "", text).strip()
            return {**extra, "event": "utterance", "text": text}
        if self.duration >= 8 and not self.speech:
            duration = round(self.duration, 3)
            self.reset("paused")
            return {"event": "timeout", "diagnostics": {"code": "no_speech_timeout",
                    "audio_seconds": duration, "speech_seconds": 0}}
        return {"event": "listening", "speech": self.speech, "seconds": round(self.duration, 1)}


class VoiceManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.spotter = None
        self.sessions = {}

    def start(self, mode="wake", identify_speaker=False):
        if mode not in {"wake", "command", "interrupt"}:
            raise ValueError("无效的收音模式。")
        with self.lock:
            self.expire()
            if len(self.sessions) >= 4:
                raise ValueError("已有多个页面正在使用麦克风，请先关闭其他页面的监听。")
            if self.spotter is None:
                self.spotter = sherpa_onnx.KeywordSpotter(
                    tokens=str(KWS_DIR / "tokens.txt"),
                    encoder=str(KWS_DIR / "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
                    decoder=str(KWS_DIR / "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
                    joiner=str(KWS_DIR / "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"),
                    keywords_file=str(ROOT / "keywords.txt"), num_threads=2,
                    keywords_threshold=0.3, keywords_score=1.5)
            session_id = uuid.uuid4().hex
            self.sessions[session_id] = VoiceSession(self.spotter, mode)
            self.sessions[session_id].identify_speaker = identify_speaker is True
            return {"session": session_id, "keyword": WAKE_WORD, "sample_rate": RATE}

    def expire(self):
        now = time.monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if now - value.last_used < 90}

    def stop(self, session_id):
        with self.lock:
            self.sessions.pop(session_id, None)
        return {"stopped": True}

    def feed(self, data, transcribe):
        with self.lock:
            self.expire()
            session = self.sessions.get(data.get("session"))
            if session is None:
                raise ValueError("语音会话已结束，请重新开启监听。")
            sequence = data.get("sequence")
            if sequence != session.sequence:
                raise ValueError("录音数据顺序异常，请重新开启监听。")
            raw = base64.b64decode(data.get("pcm", ""), validate=True)
            if not raw or len(raw) > RATE * 2 or len(raw) % 2:
                raise ValueError("无效的录音片段。")
            session.sequence += 1
            session.last_used = time.monotonic()
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            return session.feed(samples, (lambda raw: transcribe(raw, identify_speaker=True)) if session.identify_speaker else transcribe)


manager = VoiceManager()
