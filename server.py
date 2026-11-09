"""Loopback HTTP application; platform-specific work lives in backends/."""
import base64
import json
import re
import time
import uuid
from pathlib import Path
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from yiban.actions import action_for_text
from yiban.settings import ROOT, load_settings
from yiban.backends import build_services
from yiban.audio.speech_preferences import SpeechPreferences
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.shared_voice import SharedVoice
from yiban.memory.shared_import import SharedImport
from yiban.audio.speakers import SpeakerService
from yiban.turns import TurnRegistry, Cancelled
from yiban.audio.diagnostics import audio_metadata, log_audio_diagnostic
from yiban.conversation_diagnostics import TRACES, CURRENT, measured, model_events

SETTINGS = load_settings()
if SETTINGS["audio"]["wake_enabled"]:
    from yiban.audio.voice import manager as voice_manager, available as voice_available, WAKE_WORD
else:
    voice_manager = None
    WAKE_WORD = "你好小伴"

SERVICES = build_services(SETTINGS)
SPEECH = SpeechPreferences(SERVICES.tts, SETTINGS["tts"], Path(SETTINGS["memory"]["database"]).parent / "speech-settings.json")
STORE = MemoryStore(SETTINGS["memory"]["database"])
SHARED = SharedMemory(STORE)
SHARED_VOICE = SharedVoice(SHARED)
SHARED_IMPORT = SharedImport(SHARED, SERVICES.models)
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
        text = measured('asr', SERVICES.asr.transcribe, raw)
    except Exception:
        log_audio_diagnostic({**diagnostic, 'code': 'asr_error', 'asr_status': 'error'})
        raise
    result = {"text": text}
    diagnostic['asr_status'] = 'text_detected' if text else 'empty'
    if identify_speaker:
        result["speaker"] = measured('speaker', SPEAKERS.identify, raw)
        diagnostic.update(result['speaker']['diagnostics'])
    result['utterance_id'] = SHARED_VOICE.receipt(text, result.get('speaker', {}))
    result['diagnostics'] = diagnostic
    log_audio_diagnostic(diagnostic)
    return result


def shared_answer(token, data, public_only=False):
    if not isinstance(data, dict):
        raise SharedError('请求格式不正确。')
    context = measured('memory', SHARED.question_context, token, data.get('space_id'), data.get('query'), public_only=public_only,include_outdated=data.get('include_outdated') is True)
    cancel = TURNS.get(data.get('turn_id'))
    cancel.check()
    if not context['sources']:
        answer = '当前可见范围内没有适用于本次问答的记忆。可检查记忆状态；回顾旧安排时，请明确要求查询过时记忆。'
    else:
        answer, completed = '', False
        events = model_events(SERVICES.models.chat([
            {'role':'system','content':SYSTEM + current_time_context() + context['context']},
            {'role':'user','content':context['query']}], cancel=cancel))
        try:
            for event in events:
                cancel.check()
                if event.get('error'):
                    raise ValueError('共同记忆问答暂时失败，请稍后重试。')
                answer += event.get('message', {}).get('content', '')
                completed = completed or bool(event.get('done'))
                if len(answer) > 6000:
                    cancel.cancel()
                    raise ValueError('回答过长，请缩小问题范围后重试。')
        finally:
            events.close()
        if not completed or not answer.strip():
            raise ValueError('回答未完成，请重新提问。')
    # Never deliver a response computed under revoked or changed permissions.
    cancel.check()
    measured('access', SHARED.validate_answer, token, context['guard'])
    states={item['status'] for item in context['sources']}
    notice=[]
    if 'pending' in states:notice.append('本次参考资料包含待核实记录，相关内容尚不能确认。')
    if 'outdated' in states:notice.append('本次参考资料包含过时记录，仅供历史回顾。')
    if notice:answer=''.join(notice)+'\n'+answer
    return {'answer':answer, 'sources':context['sources']}


def shared_voice_request(token, path, data):
    if not isinstance(data, dict):
        raise SharedError('请求格式不正确。')
    if path == '/api/shared/voice/start':
        return SHARED_VOICE.start(token, data)
    if path == '/api/shared/voice/stop':
        return SHARED_VOICE.cancel(token, data, stop=True)
    if path == '/api/shared/voice/cancel':
        return SHARED_VOICE.cancel(token, data)
    if path == '/api/shared/voice/played':
        return SHARED_VOICE.played(token, data)
    if path != '/api/shared/voice/utterance':
        raise SharedError('未知的共同记忆语音操作。',404)
    cancel = TURNS.get(data.get('turn_id'))
    result = measured('access', SHARED_VOICE.process, token,data,cancel)
    if 'query' in result:
        answer = shared_answer(token,{**result,'turn_id':data.get('turn_id')},public_only=result['public_only'])
        SHARED_VOICE.validate_query(token,data,result['generation'])
        cancel.check()
        return {'reply':answer['answer'],'sources':answer['sources'],'saved':False}
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
        if self.path == "/api/speech/settings":
            try:
                self.json_response(SPEECH.describe())
            except Exception:
                self.json_response({"error": "无法读取系统音色，请稍后重新加载。"}, 400)
            return
        if self.path == "/api/status":
            status = {"chat": False, "vision": False, "model": MODEL, "vision_model": VISION_MODEL,
                      "asr": SERVICES.asr.available(),
                      "wake": SETTINGS["audio"]["wake_enabled"] and voice_available(),
                      "tts": SERVICES.tts.available(), "wake_word": WAKE_WORD, "version": 9,
                      "remote_model": urlsplit(SETTINGS['llm']['base_url']).hostname not in {'localhost', '127.0.0.1', '::1'},
                      "backends": {key: SETTINGS[key]["backend"] for key in ("llm", "asr", "tts", "actions")},
                      "capabilities": {"audio_input": "browser", "barge_in": SETTINGS["audio"]["wake_enabled"], "memory": True, "shared_memory": True, "shared_memory_voice": True, "material_import": True, "memory_provenance": True, "memory_status": True, "conversation_diagnostics": True, "speaker": SPEAKERS.available(), "hardware_actions": False}}
            try:
                status.update(SERVICES.models.status())
            except Exception:
                pass
            self.json_response(status)
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/speech-settings.js": ("speech-settings.js", "text/javascript; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/conversation-diagnostics.js": ("conversation-diagnostics.js", "text/javascript; charset=utf-8"),
                 "/shared.js": ("shared.js", "text/javascript; charset=utf-8"),
                 "/shared-evidence.js": ("shared-evidence.js", "text/javascript; charset=utf-8"),
                 "/material-import.js": ("material-import.js", "text/javascript; charset=utf-8"),
                 "/shared-voice.js": ("shared-voice.js", "text/javascript; charset=utf-8"),
                 "/memory-review.js": ("memory-review.js", "text/javascript; charset=utf-8"),
                 "/memory-import.js": ("memory-import.js", "text/javascript; charset=utf-8"),
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
        elif self.path == "/api/memory/export":
            result = STORE.export_memories(profile)
        elif self.path == "/api/memory/import/preview":
            result = STORE.preview_import(profile, data.get("document"))
        elif self.path == "/api/memory/import/confirm":
            result = STORE.confirm_import(profile, data.get("token"), data.get("confirmed"), data.get("acknowledge_conflicts", False))
        elif self.path == "/api/memory/save":
            result = STORE.request_save(profile, data.get("content"), data.get("id"), data.get("expected_revision"))
        elif self.path == "/api/memory/history":
            result = STORE.history(profile, data.get("id"))
        elif self.path == "/api/memory/review/confirm":
            result = STORE.confirm_save(profile, data.get("token"), data.get("confirmed"), data.get("resolution"), data.get("replace_id"))
        elif self.path == "/api/memory/delete":
            result = {"deleted": STORE.delete_memory(profile, data.get("id"), data.get("expected_revision"))}
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
        trace_token = CURRENT.set(TRACES.begin(self.headers.get('X-Conversation-Trace')))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError("请求为空或过大。")
            raw = self.rfile.read(length)
            if self.path.startswith("/api/listen/") and voice_manager is None:
                raise ValueError("当前配置未开启语音监听。")
            if self.path == '/api/diagnostics/trace':
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确。')
                self.json_response(TRACES.read(data.get('trace_id')))
            elif self.path.startswith("/api/shared/"):
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else None
                data = json.loads(raw)
                if self.path.startswith('/api/shared/import/'):
                    result = SHARED_IMPORT.dispatch(self.path[len('/api/shared/import/'):],token,data,TURNS.get(data.get('turn_id')) if isinstance(data,dict) else None)
                elif self.path.startswith('/api/shared/voice/'):
                    result = shared_voice_request(token,self.path,data)
                else:
                    result = shared_answer(token, data) if self.path == '/api/shared/ask' else SHARED.dispatch(self.path[len('/api/shared'):], data, token)
                self.json_response(result)
            elif self.path.startswith(("/api/profiles/", "/api/memory/", "/api/speaker/")):
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
                reply = measured('routing', STORE.command, profile, clean[-1]["content"]) if not picture else None
                memory_changed = reply is not None
                memory_review = None
                if isinstance(reply, dict):
                    memory_review, reply = reply['review'], reply['reply']
                    memory_changed = False
                if reply is None and not picture:
                    reply = clock_reply(clean[-1]["content"])
                if reply is None and not picture:
                    reply = STORE.identity_reply(profile, clean[-1]["content"])
                if reply is not None:
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": reply}, "memory_changed": memory_changed, "memory_review": memory_review, "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                system = SYSTEM + current_time_context() + measured('memory', STORE.context, profile, clean[-1]["content"])
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
                events = model_events(SERVICES.models.chat([{"role": "system", "content": system}] + clean, vision=bool(picture), cancel=cancel))
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
            elif self.path == "/api/speech/settings/save":
                self.json_response(SPEECH.save(json.loads(raw)))
            elif self.path == "/api/speech/preview":
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("请求格式不正确。")
                cancel = TURNS.get(data.get("turn_id"))
                cancel.check()
                audio, mime = SPEECH.synthesize(SPEECH.SAMPLE, cancel=cancel, selection=data)
                self.send_bytes(200, audio, mime)
            elif self.path == "/api/speak":
                data = json.loads(raw)
                cancel = TURNS.get(data.get("turn_id"))
                cancel.check()
                text = data.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                    raise ValueError("朗读内容为空或过长。")
                audio, mime = measured('tts', SPEECH.synthesize, text, cancel=cancel)
                self.send_bytes(200, audio, mime)
            else:
                self.json_response({"error": "Not found"}, 404)
        except SharedError as error:
            self.json_response({"error":str(error)}, error.status)
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
        finally:
            CURRENT.reset(trace_token)


if __name__ == "__main__":
    print(f"小伴已启动：http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
