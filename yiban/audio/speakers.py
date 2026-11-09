"""Opt-in speaker matching with local embeddings, VAD and unknown rejection."""
import hashlib
import importlib.util
import io
import json
import threading
import time
import wave
from pathlib import Path
from yiban.settings import ROOT


class SpeakerService:
    def __init__(self, store, config):
        self.store = store
        self.config = config
        self.path = Path(config['model'])
        self.extractor = None
        self.lock = threading.Lock()
        self.model_id = None

    def available(self):
        return (self.config['enabled'] and self.path.is_file()
                and (ROOT / 'models/silero_vad.onnx').is_file()
                and importlib.util.find_spec('numpy') is not None
                and importlib.util.find_spec('sherpa_onnx') is not None)

    def embedding(self, raw, enrollment=False, diagnostics=None):
        import numpy as np
        import sherpa_onnx
        diagnostics = diagnostics if diagnostics is not None else {}
        def fail(code, message):
            diagnostics['code'] = code
            raise ValueError(message)
        if not self.available():
            fail('model_unavailable', '声纹模型尚未安装或未启用。')
        with wave.open(io.BytesIO(raw)) as audio:
            diagnostics.update(sample_rate=audio.getframerate(), channels=audio.getnchannels(),
                               sample_width=audio.getsampwidth(),
                               audio_seconds=round(audio.getnframes()/audio.getframerate(), 3))
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
                fail('invalid_format', '声纹录音需为 16kHz 单声道 PCM16。')
            if audio.getnframes() > 16000 * 35:
                fail('too_long', '声纹录音过长。')
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
        diagnostics.update(peak=round(float(np.max(np.abs(samples))), 4) if len(samples) else 0,
                           rms_dbfs=round(float(20*np.log10(max(float(np.sqrt(np.mean(samples**2))), 1e-6))), 1) if len(samples) else -120,
                           clipping_ratio=round(float(np.mean(np.abs(samples) > .98)), 4) if len(samples) else 0)
        if not len(samples):
            fail('empty_audio', '录音为空，请检查麦克风。')
        if diagnostics['clipping_ratio'] > .05:
            fail('clipped_audio', '录音声音失真，请降低输入音量或稍微远离麦克风。')
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(ROOT / 'models/silero_vad.onnx')
        cfg.silero_vad.min_speech_duration = .2
        cfg.silero_vad.min_silence_duration = .3
        cfg.sample_rate = 16000
        vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=40)
        padded = np.concatenate([samples, np.zeros(8000, np.float32)])
        window = cfg.silero_vad.window_size
        for offset in range(0, len(padded), window):
            part = padded[offset:offset+window]
            if len(part) < window:
                part = np.pad(part, (0, window-len(part)))
            vad.accept_waveform(part)
        vad.flush()
        chunks = []
        while not vad.empty():
            chunks.append(np.array(vad.front.samples, dtype=np.float32, copy=True))
            vad.pop()
        speech = np.concatenate(chunks) if chunks else np.empty(0, np.float32)
        diagnostics.update(speech_seconds=round(len(speech)/16000, 3),
                           min_speech_seconds=2.5 if enrollment else 1.2)
        if len(speech) < 16000 * (2.5 if enrollment else 1.2):
            fail('insufficient_speech', '有效说话时间不足，请在安静环境连续说几句话。')
        with self.lock:
            if self.extractor is None:
                self.model_id = hashlib.sha256(self.path.read_bytes()).hexdigest()
                cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.path), num_threads=2, provider='cpu')
                if not cfg.validate():
                    raise ValueError('声纹模型配置无效。')
                self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            stream = self.extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=speech)
            stream.input_finished()
            if not self.extractor.is_ready(stream):
                raise ValueError('声纹录音不足，请重新录入。')
            embedding = np.asarray(self.extractor.compute(stream), dtype=np.float32)
        norm = np.linalg.norm(embedding)
        if not np.isfinite(embedding).all() or norm < 1e-6:
            raise ValueError('无法提取有效声纹，请重新录音。')
        return embedding / norm

    def enroll(self, identity, recordings, consent):
        import numpy as np
        self.store.profile(identity)
        if consent is not True:
            raise ValueError('请本人确认同意在本机保存声纹特征。')
        if len(recordings) != 3:
            raise ValueError('需要本人录制三段不同内容的语音。')
        vectors = [self.embedding(raw, enrollment=True) for raw in recordings]
        if min(float(a @ b) for i, a in enumerate(vectors) for b in vectors[i+1:]) < .45:
            raise ValueError('三段声音差异较大，请由同一人在安静环境重新录入。')
        average = np.mean(vectors, axis=0)
        average /= np.linalg.norm(average)
        self.store.enroll(identity, average.tolist(), self.model_id)

    def identify(self, raw):
        started = time.monotonic()
        diagnostics = {'threshold': self.config['threshold'], 'margin_threshold': self.config['margin'],
                       'registered_count': sum(bool(p['enrolled']) for p in self.store.profiles())}
        unknown = {'id': None, 'name': '访客', 'matched': False}
        if not self.available():
            diagnostics['code'] = 'model_unavailable'
            result = {**unknown, 'reason': '声纹模型不可用'}
        else:
            try:
                vector = self.embedding(raw, diagnostics=diagnostics)
                result = self.match(vector, self.store.voices(self.model_id))
                diagnostics.update(result.pop('diagnostics'))
            except (ValueError, wave.Error, EOFError) as error:
                diagnostics.setdefault('code', 'invalid_audio')
                result = {**unknown, 'reason': str(error)}
        diagnostics.update(elapsed_ms=round((time.monotonic()-started)*1000), matched=result['matched'])
        return {**result, 'diagnostics': diagnostics}

    def match(self, vector, profiles):
        import numpy as np
        ranked = []
        for profile in profiles:
            stored = np.asarray(json.loads(profile['embedding']), dtype=np.float32)
            if stored.shape != vector.shape or not np.isfinite(stored).all():
                continue
            ranked.append((float(vector @ stored), profile))
        ranked.sort(key=lambda item: item[0], reverse=True)
        diagnostics = {'compatible_count': len(ranked)}
        if not ranked:
            return {'id': None, 'name': '访客', 'matched': False, 'reason': '尚无兼容声纹',
                    'diagnostics': {**diagnostics, 'code': 'no_compatible_voiceprint'}}
        score, profile = ranked[0]
        margin = score - ranked[1][0] if len(ranked) > 1 else 1
        matched = score >= self.config['threshold'] and margin >= self.config['margin']
        code = 'matched' if matched else ('low_score' if score < self.config['threshold'] else 'ambiguous')
        diagnostics.update(code=code, top_score=round(score, 4),
                           runner_up_score=round(ranked[1][0], 4) if len(ranked)>1 else None,
                           score_gap=round(margin, 4) if len(ranked)>1 else None)
        return {'id': profile['id'] if matched else None, 'name': profile['name'] if matched else '访客',
                'matched': matched, 'score': round(score, 3), 'diagnostics': diagnostics,
                'reason': '' if matched else ('声纹相似度不足，未读取个人记忆' if code=='low_score' else '两个候选声音太接近，未读取个人记忆')}
