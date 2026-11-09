"""Speech adapters. Import inference libraries only when transcription is requested."""
import io
import platform
from pathlib import Path
import subprocess
import tempfile
import threading
import wave
from yiban.turns import Cancellation


class SenseVoiceASR:
    def __init__(self, config):
        self.model_dir = Path(config["model_dir"])
        self.lock = threading.Lock()
        self.recognizer = None

    def available(self):
        return all((self.model_dir / name).exists() for name in ("model.int8.onnx", "tokens.txt"))

    def transcribe(self, raw):
        import numpy as np
        import sherpa_onnx
        with wave.open(io.BytesIO(raw)) as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError("请使用单声道 16 位 WAV 音频。")
            rate = wav.getframerate()
            if not 8000 <= rate <= 96000 or wav.getnframes() > rate * 35:
                raise ValueError("每次录音请控制在 30 秒以内。")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if len(samples) < rate * 0.2:
            raise ValueError("录音太短了，请再说一次。")
        if float(np.max(np.abs(samples))) < 0.002:
            return ""
        with self.lock:
            if self.recognizer is None:
                self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(self.model_dir / "model.int8.onnx"),
                    tokens=str(self.model_dir / "tokens.txt"), num_threads=4,
                    use_itn=True, language="auto", provider="cpu")
            stream = self.recognizer.create_stream()
            stream.accept_waveform(rate, samples)
            self.recognizer.decode_stream(stream)
            return stream.result.text.strip()


class MacSpeech:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()

    def available(self):
        return platform.system() == "Darwin" and Path("/usr/bin/say").exists() and Path("/usr/bin/afconvert").exists()

    def synthesize(self, text, cancel=None, selection=None):
        if not self.available():
            raise RuntimeError("Mac 朗读不可用；Linux 语音服务尚待接入。")
        selected = dict(selection or self.config)
        cancel = cancel or Cancellation()
        while not self.lock.acquire(timeout=.1):
            cancel.check()
        try:
            cancel.check()
            with tempfile.TemporaryDirectory(prefix="assistant-tts-") as tmp:
                aiff, wav = Path(tmp) / "speech.aiff", Path(tmp) / "speech.wav"
                self.run(["/usr/bin/say", "-v", selected["voice"], "-r", str(selected["rate"]), "-o", str(aiff)], text, cancel, 60)
                self.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff), str(wav)], None, cancel, 20)
                cancel.check()
                return wav.read_bytes(), "audio/wav"
        finally:
            self.lock.release()

    @staticmethod
    def run(args, text, cancel, timeout):
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def abort():
            if process.poll() is None:
                process.terminate()
        try:
            cancel.attach(abort)
            process.communicate(input=text, timeout=timeout)
            cancel.check()
            if process.returncode:
                raise RuntimeError('语音合成失败，请检查系统声音。')
        finally:
            cancel.detach(abort)
            if process.poll() is None:
                process.kill()
            process.communicate()


class DisabledASR:
    def available(self):
        return False

    def transcribe(self, raw):
        raise RuntimeError("当前配置未启用语音识别。")


class DisabledSpeech:
    def available(self):
        return False

    def synthesize(self, text, cancel=None):
        raise RuntimeError("当前配置未启用语音合成；请先使用文字对话。")
