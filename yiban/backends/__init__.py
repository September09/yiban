"""Platform implementations, constructed once and injected into HTTP handlers."""
from dataclasses import dataclass

from yiban.actions import describe
from .models import ModelClient
from .speech import SenseVoiceASR, MacSpeech, DisabledASR, DisabledSpeech
from .qwen_speech import QwenSpeech


class ScreenActions:
    def execute(self, action):
        return describe(action)


@dataclass
class Services:
    models: object
    asr: object
    tts: object
    actions: object


def build_services(settings):
    asr = SenseVoiceASR(settings["asr"]) if settings["asr"]["backend"] == "sensevoice" else DisabledASR()
    tts = {"macos": MacSpeech, "qwen3_tts": QwenSpeech}.get(settings["tts"]["backend"], lambda config: DisabledSpeech())(settings["tts"])
    return Services(ModelClient(settings["llm"]), asr, tts, ScreenActions())
