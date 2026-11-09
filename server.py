"""Loopback HTTP application; platform-specific work lives in backends/."""
import base64
import json
import re
import time
import uuid
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from actions import action_for_text
from settings import ROOT, load_settings
from backends import build_services
from memory_store import MemoryStore
from speakers import SpeakerService
from turns import TurnRegistry, Cancelled
from diagnostics import audio_metadata, log_audio_diagnostic

SETTINGS = load_settings()
if SETTINGS["audio"]["wake_enabled"]:
    from voice import manager as voice_manager, available as voice_available, WAKE_WORD
else:
    voice_manager = None
    WAKE_WORD = "你好小伴"

SERVICES = build_services(SETTINGS)
STORE = MemoryStore(SETTINGS["memory"]["database"])
SPEAKERS = SpeakerService(STORE, SETTINGS["speaker"])
TURNS = TurnRegistry()
PORT = SETTINGS["port"]
MODEL = SETTINGS["llm"]["model"]
VISION_MODEL = SETTINGS["llm"]["vision_model"]

SYSTEM = "你叫小伴，是中文语音助手。用自然、友好的中文交流，通常用2到4句话简洁回答，不用Markdown格式。你没有联网、实际机器人或操作电脑的能力。界面支持固定屏幕动作：点头、摇头、向左看、向右看、开心、回正；只有系统提供动作结果时才能说已完成。图片仅在用户本次提供时可见，不能持续观察摄像头，不要猜测没有提供的画面。不知道的事情请坦诚说明。"


def current_time_context():
    # Refresh for every request so a long-running service crosses midnight correctly.
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    weekday = "一二三四五六日"[now.weekday()]
    return (f"\n本轮电脑时钟：{now.year}年{now.month}月{now.day}日，星期{weekday}，"
            f"{now:%H:%M:%S}。时区：Asia/Shanghai（北京时间，UTC+08:00）。"
            "这是本程序读取的当前时间，不需要联网。用户询问今天日期、星期或当前时间时，"
            "请直接依据此信息回答；不要说因为不能联网所以不知道日期。"
            "历史对话或记忆中的日期不能替代本轮时钟。此信息不代表具备天气、新闻或网页搜索能力。\n")


def clock_reply(text):
    text = re.sub(r'[\s，。！？、：,:;.!?；]', '', text)
    text = re.sub(r'^(?:(?:请问|请|你知道|告诉我|帮我查一下|帮我看看|再确认一下))+', '', text)
    date_question = re.fullmatch(
        r'今天(?:是|的)?(?:几号|多少号|几月几号|几月几日|什么日期|日期|星期几|周几)'
        r'(?:和(?:星期几|周几))?(?:吗|呢|啊)?', text)
    time_question = re.fullmatch(r'(?:现在|当前)(?:是)?(?:几点|几点钟|几点几分|时间)(?:了|吗|呢|啊)?', text)
    if not date_question and not time_question:
        return None
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if time_question:
        return f'现在是北京时间 {now.hour} 点 {now.minute} 分。'
    return f'今天是 {now.year} 年 {now.month} 月 {now.day} 日，星期{"一二三四五六日"[now.weekday()]}。'


def transcribe(raw, identify_speaker=False):
    diagnostic = {**audio_metadata(raw), 'trace_id': uuid.uuid4().hex[:12],
                  'speaker_enabled': identify_speaker, 'code': 'recognition_disabled'}
    try:
        text = SERVICES.asr.transcribe(raw)
    except Exception:
        log_audio_diagnostic({**diagnostic, 'code': 'asr_error', 'asr_status': 'error'})
        raise
    result = {"text": text}
    diagnostic['asr_status'] = 'text_detected' if text else 'empty'
    if identify_speaker:
        result["speaker"] = SPEAKERS.identify(raw)
        diagnostic.update(result['speaker']['diagnostics'])
    result['diagnostics'] = diagnostic
    log_audio_diagnostic(diagnostic)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if self.path == "/api/listen/chunk":
            return
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def send_bytes(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, data, status=200):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def local_request(self):
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if self.headers.get("Host") not in allowed:
            self.json_response({"error": "Only localhost is allowed"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            self.json_response({"error": "Cross-origin requests are disabled"}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        if self.path == "/api/status":
            status = {"chat": False, "vision": False, "model": MODEL, "vision_model": VISION_MODEL,
                      "asr": SERVICES.asr.available(),
                      "wake": SETTINGS["audio"]["wake_enabled"] and voice_available(),
                      "tts": SERVICES.tts.available(), "wake_word": WAKE_WORD, "version": 4,
                      "remote_model": urlsplit(SETTINGS['llm']['base_url']).hostname not in {'localhost', '127.0.0.1', '::1'},
                      "backends": {key: SETTINGS[key]["backend"] for key in ("llm", "asr", "tts", "actions")},
                      "capabilities": {"audio_input": "browser", "barge_in": SETTINGS["audio"]["wake_enabled"], "memory": True, "speaker": SPEAKERS.available(), "hardware_actions": False}}
            try:
                status.update(SERVICES.models.status())
            except Exception:
                pass
            self.json_response(status)
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/personal.js": ("personal.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8"),
                 "/recorder.js": ("recorder.js", "text/javascript; charset=utf-8")}
        if self.path in files:
            name, mime = files[self.path]
            self.send_bytes(200, (ROOT / "static" / name).read_bytes(), mime)
        else:
            self.json_response({"error": "Not found"}, 404)

    def manage_personal(self, data):
        if not isinstance(data, dict):
            raise ValueError("请求格式不正确。")
        profile = data.get("profile_id")
        if self.path == "/api/profiles/list":
            result = {"profiles": STORE.profiles()}
        elif self.path == "/api/profiles/create":
            result = STORE.create_profile(data.get("name"))
        elif self.path == "/api/profiles/salutation":
            result = STORE.set_salutation(profile, data.get("salutation"))
        elif self.path == "/api/profiles/delete":
            STORE.delete_profile(profile)
            result = {"deleted": True}
        elif self.path == "/api/memory/list":
            result = {"memories": STORE.memories(profile)}
        elif self.path == "/api/memory/save":
            result = {"id": STORE.save(profile, data.get("content"), data.get("id"))}
        elif self.path == "/api/memory/delete":
            result = {"deleted": STORE.delete_memory(profile, data.get("id"))}
        elif self.path == "/api/speaker/enroll":
            recordings = data.get("recordings")
            if not isinstance(recordings, list) or len(recordings) != 3 or any(not isinstance(x, str) or len(x) > 1500000 for x in recordings):
                raise ValueError("请录入三段语音。")
            SPEAKERS.enroll(profile, [base64.b64decode(x, validate=True) for x in recordings], data.get("consent"))
            result = {"enrolled": True}
        elif self.path == "/api/speaker/delete":
            STORE.forget_voice(profile)
            result = {"deleted": True}
        else:
            raise ValueError("未知的用户或记忆操作。")
        self.json_response(result)

    def do_POST(self):
        if not self.local_request():
            return
        streaming = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError("请求为空或过大。")
            raw = self.rfile.read(length)
            if self.path.startswith("/api/listen/") and voice_manager is None:
                raise ValueError("当前配置未开启语音监听。")
            if self.path.startswith(("/api/profiles/", "/api/memory/", "/api/speaker/")):
                self.manage_personal(json.loads(raw))
            elif self.path == "/api/turn/cancel":
                identity = json.loads(raw).get("turn_id")
                if not identity:
                    raise ValueError("缺少对话编号。")
                TURNS.cancel(identity)
                self.json_response({"cancelled": True})
            elif self.path == "/api/listen/start":
                data = json.loads(raw)
                self.json_response(voice_manager.start(data.get("mode", "wake"), data.get("identify_speaker", False)))
            elif self.path == "/api/listen/stop":
                self.json_response(voice_manager.stop(json.loads(raw).get("session")))
            elif self.path == "/api/listen/chunk":
                self.json_response(voice_manager.feed(json.loads(raw), transcribe))
            elif self.path == "/api/action":
                self.json_response(SERVICES.actions.execute(json.loads(raw).get("action")))
            elif self.path == "/api/transcribe":
                start = time.monotonic()
                self.json_response({**transcribe(raw, self.headers.get("X-Speaker-Recognition") == "true"), "seconds": round(time.monotonic() - start, 2)})
            elif self.path == "/api/chat":
                data = json.loads(raw)
                cancel = TURNS.get(data.get("turn_id"))
                cancel.check()
                messages = data.get("messages", [])
                if not isinstance(messages, list) or not messages:
                    raise ValueError("请先输入问题。")
                clean = []
                for msg in messages[-16:]:
                    if msg.get("role") not in {"user", "assistant"} or not isinstance(msg.get("content"), str):
                        raise ValueError("对话格式不正确。")
                    clean.append({"role": msg["role"], "content": msg["content"][:6000]})
                if clean[-1]["role"] != "user":
                    raise ValueError("最后一条消息必须是用户问题。")
                picture = data.get("image")
                profile = data.get("profile_id") or None
                if profile:
                    STORE.profile(profile)
                reply = STORE.command(profile, clean[-1]["content"]) if not picture else None
                memory_changed = reply is not None
                if reply is None and not picture:
                    reply = clock_reply(clean[-1]["content"])
                if reply is None and not picture:
                    reply = STORE.identity_reply(profile, clean[-1]["content"])
                if reply is not None:
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": reply}, "memory_changed": memory_changed, "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                system = SYSTEM + current_time_context() + STORE.context(profile, clean[-1]["content"])
                if picture:
                    if not isinstance(picture, str) or len(picture) > 3_000_000:
                        raise ValueError("图片过大，请重新拍照。")
                    image_bytes = base64.b64decode(picture, validate=True)
                    if not image_bytes.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
                        raise ValueError("只支持 JPEG 或 PNG 图片。")
                    clean[-1]["images"] = [picture]
                    if not VISION_MODEL:
                        raise ValueError("当前配置未启用视觉模型。")
                    system += "本次用户提供了一张照片，请结合照片回答。只描述看得清的内容；模糊时说明不确定。"
                else:
                    system += "本次没有提供图片；若用户要求看东西，请让用户先打开摄像头并拍照。"
                action = action_for_text(clean[-1]["content"]) if not picture else None
                if action:
                    event = SERVICES.actions.execute(action)
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": event["reply"]},
                        "action": event, "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                events = SERVICES.models.chat([{"role": "system", "content": system}] + clean, vision=bool(picture), cancel=cancel)
                try:
                    first = next(events)  # Fail before sending HTTP 200 when the backend rejects the request.
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    streaming = True
                    self.wfile.write((json.dumps(first, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
                    for event in events:
                        cancel.check()
                        self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                        self.wfile.flush()
                finally:
                    events.close()
            elif self.path == "/api/speak":
                data = json.loads(raw)
                cancel = TURNS.get(data.get("turn_id"))
                cancel.check()
                text = data.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                    raise ValueError("朗读内容为空或过长。")
                audio, mime = SERVICES.tts.synthesize(text, cancel=cancel)
                self.send_bytes(200, audio, mime)
            else:
                self.json_response({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            message = "本轮已打断。" if isinstance(error, Cancelled) else str(error)
            if isinstance(error, urllib.error.URLError):
                message = "配置的模型服务暂时不可用，请检查服务地址、模型和运行日志。"
            if streaming:
                try:
                    self.wfile.write((json.dumps({"error": message}) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.json_response({"error": message}, 400)


if __name__ == "__main__":
    print(f"小伴已启动：http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
