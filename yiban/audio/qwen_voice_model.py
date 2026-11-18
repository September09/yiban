"""One resident model at a time; preserve legacy CustomVoice and designed Base voices."""
from yiban.audio.qwen_voices import BRIGHT_VOICE, CUSTOM_MODEL, bright_assets, validate_voice


class VoiceModel:
    def __init__(self, loader, initial_voice="Serena"):
        self.loader, self.model, self.path = loader, None, None
        resources = validate_voice(initial_voice)
        self._switch(resources["model"] if resources else CUSTOM_MODEL)

    def _switch(self, path):
        if self.path == path and self.model is not None:
            return
        if self.model is not None:
            import gc
            import mlx.core as mx
            self.model = None
            self.path = None
            gc.collect()
            mx.clear_cache()
        self.model = self.loader(str(path))
        self.path = path

    @property
    def speech_tokenizer(self):
        return self.model.speech_tokenizer

    def generate_custom_voice(self, text, speaker, language="Chinese", instruct=None, **options):
        validate_voice(speaker)
        if speaker == BRIGHT_VOICE:
            assets = bright_assets()
            self._switch(assets["model"])
            # Base has no preset speaker. Use the selected synthetic voice reference.
            yield from self.model.generate(text=text, lang_code=language,
                ref_audio=str(assets["reference"]), ref_text=assets["text"], **options)
        else:
            self._switch(CUSTOM_MODEL)
            yield from self.model.generate_custom_voice(text=text, speaker=speaker,
                language=language, instruct=instruct, **options)
