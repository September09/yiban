"""Integration checks against the running localhost demo, using synthetic fixtures."""
import base64
import io
import json
from pathlib import Path
import struct
import sys
import urllib.error
import urllib.request
import wave
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def post(path, data):
    request = urllib.request.Request(BASE + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def chat(text, **extra):
    rows = [json.loads(line) for line in post("/api/chat", {"messages": [{"role": "user", "content": text}], **extra}).splitlines()]
    assert rows[-1].get("done"), rows
    assert not any(row.get("error") for row in rows), rows
    return "".join(row.get("message", {}).get("content", "") for row in rows), rows


def read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getnframes() > 0
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def voice_case(samples, expected):
    session = json.loads(post("/api/listen/start", {"mode": "wake"}))["session"]
    events = []
    try:
        for sequence, offset in enumerate(range(0, len(samples), 8192)):
            data = {"session": session, "sequence": sequence,
                    "pcm": base64.b64encode(samples[offset:offset + 8192].astype("<i2").tobytes()).decode()}
            event = json.loads(post("/api/listen/chunk", data))
            if event["event"] not in {"waiting", "listening", "paused"}:
                events.append(event)
        if expected:
            assert any(e["event"] == "wake" for e in events), events
            assert any(e["event"] == "utterance" and "点" in e.get("text", "") for e in events), events
        else:
            assert not events, events
        print("voice", json.dumps(events, ensure_ascii=False))
    finally:
        post("/api/listen/stop", {"session": session})


def test_picture():
    # No camera or user image: a red circle on the left, blue square on the right.
    image = np.full((256, 512, 3), 255, dtype=np.uint8)
    yy, xx = np.ogrid[:256, :512]
    image[(xx - 128) ** 2 + (yy - 128) ** 2 < 70 ** 2] = [230, 30, 30]
    image[58:198, 314:454] = [30, 60, 230]
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 512, 256, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row.tobytes() for row in image))) + chunk(b"IEND", b"")
    (ROOT / ".runtime/vision-test.png").write_bytes(png)
    return base64.b64encode(png).decode()


if __name__ == "__main__":
    if "--vision" in sys.argv:
        answer, _ = chat("请说出图片左边和右边分别是什么颜色、什么形状。", image=test_picture())
        print("vision:", answer)
        assert all(word in answer for word in ("红", "蓝", "圆", "方")), answer
        sys.exit(0)
    text, rows = chat("请点点头")
    assert rows[0]["action"]["action"] == "nod" and rows[0]["action"]["target"] == "screen"
    print("action:", text)
    text, rows = chat("不要点头，只回答好的。")
    assert not any(r.get("action") for r in rows)
    print("negative action:", text)
    for action in ("nod", "shake", "left", "right", "happy", "reset"):
        assert json.loads(post("/api/action", {"action": action}))["target"] == "screen"
    with wave.open(io.BytesIO(post("/api/speak", {"text": "语音测试。"}))) as audio:
        assert audio.getnframes() > 0 and audio.getsampwidth() == 2
    print("speech synthesis: valid PCM16 WAV")
    try:
        post("/api/action", {"action": "run_shell"})
        raise AssertionError("Unknown action accepted")
    except urllib.error.HTTPError as error:
        assert error.code == 400
    fixtures = ROOT / '.runtime/test-audio'
    voice_case(np.concatenate([read_wav(fixtures/'xiaoban-wake.wav'), np.zeros(16000, np.int16),
                               read_wav(fixtures/'xiaoban-command.wav'), np.zeros(24000, np.int16)]), True)
    voice_case(np.concatenate([read_wav(fixtures/'xiaoban-negative.wav'), np.zeros(32000, np.int16)]), False)
    session = json.loads(post("/api/listen/start", {"mode": "command"}))["session"]
    silence = base64.b64encode(np.zeros(8192, dtype="<i2").tobytes()).decode()
    for sequence in range(16):
        result = json.loads(post("/api/listen/chunk", {"session": session, "sequence": sequence, "pcm": silence}))
    assert result["event"] == "timeout", result
    post("/api/listen/stop", {"session": session})
    print("silence timeout: passed")
    print("Pipeline checks passed.")
