"""Bounded local audio diagnostics: never persist audio, text or voiceprints."""
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from yiban.settings import ROOT
import threading
import wave
from datetime import datetime, timezone

FIELDS = {
    'trace_id', 'speaker_enabled', 'asr_status', 'code', 'audio_seconds',
    'speech_seconds', 'min_speech_seconds', 'sample_rate', 'channels', 'sample_width',
    'rms_dbfs', 'peak', 'clipping_ratio', 'registered_count', 'compatible_count',
    'threshold', 'margin_threshold', 'top_score', 'runner_up_score', 'score_gap',
    'elapsed_ms', 'matched',
}
_lock = threading.Lock()
_logger = None
LOG_PATH = ROOT / '.runtime' / 'speaker-diagnostics.jsonl'


def audio_metadata(raw):
    try:
        with wave.open(io.BytesIO(raw)) as audio:
            return {'audio_seconds': round(audio.getnframes()/audio.getframerate(), 3),
                    'sample_rate': audio.getframerate(), 'channels': audio.getnchannels(),
                    'sample_width': audio.getsampwidth()}
    except (wave.Error, EOFError, ZeroDivisionError):
        return {}


def safe_record(data):
    result = {key: value for key, value in data.items()
              if key in FIELDS and (value is None or type(value) in (str, bool, int, float))}
    return {'time_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), **result}


def log_audio_diagnostic(data):
    global _logger
    try:
        with _lock:
            if _logger is None:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                # Create with private permissions before writing any records.
                import os
                fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.close(fd)
                logger = logging.getLogger('assistant.speaker_diagnostics')
                logger.setLevel(logging.INFO)
                logger.propagate = False
                handler = RotatingFileHandler(LOG_PATH, maxBytes=256_000, backupCount=2, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(message)s'))
                logger.addHandler(handler)
                _logger = logger
            _logger.info(json.dumps(safe_record(data), ensure_ascii=False, allow_nan=False))
            LOG_PATH.chmod(0o600)
    except (OSError, ValueError):
        # Diagnostics must not prevent a conversation if disk writing fails.
        pass
