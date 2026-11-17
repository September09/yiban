"""Private offline line protocol; a single MLX model serves sequential requests."""
from contextlib import redirect_stdout
import json
import math
import base64
import os
import queue
import sys
import threading
import time

from yiban.settings import ROOT
from yiban.audio.qwen_profile import GENERATION_OPTIONS, SEED, STYLE, speech_segments, generation_settings

VOICES = ("Serena", "Vivian", "Uncle_Fu")


class SpeechLengthError(RuntimeError):
    pass


def synthesize(model, data, cancelled, emit=None):
    import numpy as np
    import soundfile as sf
    import mlx.core as mx
    text, voice = data.get("text"), data.get("voice")
    if voice not in VOICES or not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError("Invalid speech request")
    streaming = data.get("stream") is True
    if streaming and len(text) > 1200:
        raise ValueError("Streaming utterance too long")
    # Transport frames all come from ONE generation/decoder context.
    segments = [text] if streaming else speech_segments(text)
    options = generation_settings(data.get("generation"))
    mx.random.seed(options.pop("seed"))
    audio, sample_rate = [], None
    sequence = total = tokens = 0
    # Codec runs at 12.5 frames/second. A short answer must not have a
    # 2048-token (roughly 164-second) minimum, nor report truncation as success.
    max_seconds = min(45, max(6, len(text) * .5 + 4))
    token_budget = math.ceil(max_seconds * 12.5)
    for segment in segments:
        if cancelled.is_set():
            return False
        generated = model.generate_custom_voice(
            text=segment, speaker=voice, language="Chinese", instruct=STYLE,
            max_tokens=token_budget if streaming else 2048,
            stream=True, streaming_interval=.5, **options)
        try:
            for result in generated:
                if cancelled.is_set():
                    return False
                if sample_rate is not None and sample_rate != result.sample_rate:
                    raise RuntimeError("Changed sample rate")
                sample_rate = int(result.sample_rate)
                samples = np.asarray(result.audio, dtype='<f4').reshape(-1)
                if not len(samples) or not np.isfinite(samples).all():
                    raise RuntimeError("Invalid audio")
                if streaming:
                    tokens += int(getattr(result, 'token_count', 0))
                    if tokens >= token_budget or (total + len(samples)) / sample_rate > max_seconds:
                        raise SpeechLengthError('Speech exceeded the utterance budget')
                    emit({"event": "audio", "id": data["id"], "sequence": sequence,
                          "format": "f32le", "sample_rate": sample_rate, "samples": len(samples),
                          "audio": base64.b64encode(np.clip(samples, -1, 1).astype('<f4').tobytes()).decode('ascii')})
                    sequence += 1
                    total += len(samples)
                else:
                    audio.append(samples)
        finally:
            generated.close()
            # A cancelled generator may not reach the decoder's own cleanup.
            model.speech_tokenizer.decoder.reset_streaming_state()
    if cancelled.is_set():
        return False
    if streaming:
        if not total:
            raise RuntimeError("Empty audio")
        return {"chunks": sequence, "total_samples": total, "model_calls": 1}
    if not audio:
        raise RuntimeError("Empty audio")
    samples = np.concatenate(audio)
    if not np.isfinite(samples).all():
        raise RuntimeError("Non-finite audio")
    sf.write(data["output"], samples, sample_rate, subtype="PCM_16")
    return True


def serve():
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    protocol = sys.stdout
    jobs = queue.Queue(maxsize=8)
    events, event_lock, stopped = {}, threading.Lock(), threading.Event()

    def emit(value):
        protocol.write(json.dumps(value) + "\n")
        protocol.flush()

    def receive():
        try:
            for line in sys.stdin:
                data = json.loads(line)
                identity = data["id"]
                with event_lock:
                    if len(events) >= 256:
                        raise ValueError("Too many requests")
                    event = events.setdefault(identity, threading.Event())
                    if data.get("op") == "cancel":
                        event.set()
                        continue
                if data.get("op") != "synthesize":
                    raise ValueError("Invalid command")
                jobs.put_nowait((data, event))
        except Exception:
            stopped.set()
        finally:
            stopped.set()
            with event_lock:
                for event in events.values():
                    event.set()
            try:
                jobs.put_nowait(None)
            except queue.Full:
                pass

    threading.Thread(target=receive, daemon=True).start()
    try:
        # Keep third-party progress messages off the private response pipe.
        with redirect_stdout(sys.stderr):
            from mlx_audio.tts.utils import load_model
            import mlx.core as mx
            started = time.monotonic()
            model = load_model(str(ROOT / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit"))
            if stopped.is_set():
                return
            # Compile the generation/decoder path once, silently; never play or
            # persist warm-up audio and never use personal text for warm-up.
            mx.random.seed(SEED)
            generated = model.generate_custom_voice(
                text="你好。", speaker="Serena", language="Chinese", instruct=STYLE,
                max_tokens=128, stream=True, streaming_interval=.5, **GENERATION_OPTIONS)
            try:
                for _ in generated:
                    if stopped.is_set():
                        return
            finally:
                generated.close()
                model.speech_tokenizer.decoder.reset_streaming_state()
        emit({"event": "ready", "warm_seconds": round(time.monotonic()-started, 3)})
        while not stopped.is_set():
            job = jobs.get()
            if job is None:
                break
            data, cancelled = job
            try:
                with redirect_stdout(sys.stderr):
                    completed = synthesize(model, data, cancelled, emit)
                emit({"event": "done", "id": data["id"], "cancelled": not completed,
                      **(completed if isinstance(completed, dict) else {})})
            except SpeechLengthError:
                # A bounded failed utterance is not a crashed model. It can be
                # reused after the decoder's finally cleanup.
                emit({"event": "done", "id": data["id"], "failure": "length_limit", "cancelled": False})
            with event_lock:
                events.pop(data["id"], None)
    except Exception as error:
        # No request text is printed in diagnostics or error responses.
        emit({"event": "error", "error": "metal" if "metal" in str(error).lower() else "worker"})


def main():
    if sys.argv[1:] != ["--serve"]:
        raise ValueError("Private worker requires --serve")
    serve()


if __name__ == "__main__":
    main()
