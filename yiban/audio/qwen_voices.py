"""Local Qwen voice resources. Designed references contain synthetic speech only."""
import hashlib
import json
from pathlib import Path
import wave

from yiban.settings import ROOT

BUILTIN_VOICES = ("Serena", "Vivian", "Uncle_Fu")
BRIGHT_VOICE = "Bright_Female"
VOICES = (*BUILTIN_VOICES, BRIGHT_VOICE)
CUSTOM_MODEL = ROOT / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit"
BRIGHT_MODEL = ROOT / "models/tts-role-audition/base"
BRIGHT_DIRECTORY = ROOT / "data/tts-voices/bright_female"


def bright_assets():
    try:
        profile = json.loads((BRIGHT_DIRECTORY / "profile.json").read_text())
        reference = BRIGHT_DIRECTORY / "reference.wav"
        text = profile["reference_text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError()
        if reference.stat().st_size > 4_000_000:
            raise ValueError()
        digest = hashlib.sha256(reference.read_bytes()).hexdigest()
        if profile.get("version") != 1 or profile.get("reference_sha256") != digest:
            raise ValueError()
        if not (BRIGHT_MODEL / "config.json").is_file():
            raise ValueError()
        with wave.open(str(reference), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or not 3 <= wav.getnframes() / wav.getframerate() <= 20:
                raise ValueError()
        fingerprint = hashlib.sha256((digest + text + profile.get("model_revision", "")).encode()).hexdigest()
        return {"model": BRIGHT_MODEL, "reference": reference, "text": text, "fingerprint": fingerprint}
    except (OSError, ValueError, KeyError, TypeError, wave.Error):
        raise ValueError("明亮女声的参考音频或Base模型不可用，请重新安装该声音。") from None


def validate_voice(voice):
    if voice not in VOICES:
        raise ValueError("请选择声音列表中的音色。")
    return bright_assets() if voice == BRIGHT_VOICE else None
