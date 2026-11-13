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
from yiban.backends.model_settings import ModelSettings, REMOTE_REQUEST
from yiban.memory.memory_agent import has_write_intent
from yiban.audio.speech_preferences import SpeechPreferences
from yiban.audio.devices import AudioDevices
from yiban.memory.memory_store import MemoryStore
from yiban.memory.personal_state import conflict_pairs
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.retrieval import MemoryRetriever
from yiban.memory.answer_evidence import ANSWER_SCHEMA, REFERENCE_INSTRUCTION, answer_question, resolve_answer
from yiban.memory.shared_voice import SharedVoice
from yiban.memory.shared_import import SharedImport
from yiban.memory.read_only_agent import ReadOnlyAgent
from yiban.memory.memory_agent import MemoryAgent
from yiban.memory.agent_audit import AgentAudit
from yiban.memory.answer_feedback import AnswerFeedback
from yiban.memory.shared_backup import SharedBackup
from yiban.memory.personal_retrieval import PersonalMemory
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
MODEL_SETTINGS = ModelSettings(SETTINGS["llm"], Path(SETTINGS["memory"]["database"]).parent / "model-settings.json")
SERVICES.models = MODEL_SETTINGS
AUDIO_DEVICES = AudioDevices()
SPEECH = SpeechPreferences(SERVICES.tts, SETTINGS["tts"], Path(SETTINGS["memory"]["database"]).parent / "speech-settings.json")
STORE = MemoryStore(SETTINGS["memory"]["database"])
RETRIEVER = MemoryRetriever(SETTINGS['retrieval'], STORE.path.parent/'retrieval/vectors.sqlite3') if SETTINGS['retrieval']['enabled'] else None
PERSONAL_MEMORY = PersonalMemory(STORE, RETRIEVER, SERVICES.models)
STORE.personal_retriever = PERSONAL_MEMORY.retriever
SHARED = SharedMemory(STORE, retriever=RETRIEVER)
SHARED_BACKUP = SharedBackup(SHARED)
SHARED_VOICE = SharedVoice(SHARED)
SHARED_IMPORT = SharedImport(SHARED, SERVICES.models)
READ_AGENT = ReadOnlyAgent(SHARED, SERVICES.models)
MEMORY_AGENT = MemoryAgent(SHARED, SERVICES.models)
AGENT_AUDIT = AgentAudit(SHARED, STORE.path.parent / "agent-diagnostics.sqlite3")
FEEDBACK = AnswerFeedback(SHARED, STORE.path.parent / "answer-feedback.sqlite3")
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
    cancel = TURNS.get(data.get('turn_id'))
    cancel.check()
    context = measured('memory', SHARED.question_context, token, data.get('space_id'), data.get('query'), public_only=public_only,include_outdated=data.get('include_outdated') is True,cancel=cancel,conversation=data.get('conversation_mode') is True,conversation_id=data.get('conversation_id'),choice_id=data.get('choice_id'))
    retrieval=TRACES.retrieval(CURRENT.get(),context.get('retrieval'))
    cancel.check()
    citation={'answer_source_ids':[], 'source_usage':'not_needed'}
    if context.get('clarification'):
        answer=context['clarification']
    elif not context['sources']:
        answer = '当前可见范围内没有适用于本次问答的记忆。可检查记忆状态；回顾旧安排时，请明确要求查询过时记忆。'
    else:
        TRACES.materials(CURRENT.get(),context['sources'],'read')
        answer, completed = '', False
        events = model_events(SERVICES.models.chat([
            {'role':'system','content':SYSTEM + current_time_context() + context['context'] + REFERENCE_INSTRUCTION},
            {'role':'user','content':answer_question(context['query'])}], cancel=cancel,json_schema=ANSWER_SCHEMA))
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
    if context['sources']:
        citation=resolve_answer(answer,context['sources'])
        answer=citation.pop('answer')
    dialogue={}
    if context.get('dialogue_plan'):
        cancel.check()
        dialogue=SHARED.dialogue.finish(context['dialogue_plan'])
    return {'answer':answer, 'sources':context['sources'], 'retrieval':retrieval, 'conversation':dialogue, **citation}



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
        binding=MODEL_SETTINGS.bind()
        try:self.get_request()
        finally:MODEL_SETTINGS.unbind(binding)

    def get_request(self):
        if not self.local_request():
            return
        if self.path == "/api/audio/devices":
            self.json_response({**AUDIO_DEVICES.describe(), "services":{"asr":SERVICES.asr.available(),"tts":SERVICES.tts.available(),"wake":SETTINGS["audio"]["wake_enabled"] and voice_available()}})
            return
        if self.path == "/api/models/settings":
            self.json_response(MODEL_SETTINGS.describe())
            return
        if self.path == "/api/speech/settings":
            try:
                self.json_response(SPEECH.describe())
            except Exception:
                self.json_response({"error": "无法读取系统音色，请稍后重新加载。"}, 400)
            return
        if self.path == "/api/status":
            active_llm = MODEL_SETTINGS.config
            status = {"chat": False, "vision": False, "model": active_llm["model"], "vision_model": active_llm["vision_model"],
                      "asr": SERVICES.asr.available(),
                      "wake": SETTINGS["audio"]["wake_enabled"] and voice_available(),
                      "tts": SERVICES.tts.available(), "wake_word": WAKE_WORD, "version": 28,
                      "retrieval": {"enabled": RETRIEVER is not None, "strategy": SETTINGS["retrieval"]["strategy"], "model": SETTINGS["retrieval"]["model"]},
                      "remote_model": urlsplit(active_llm['base_url']).hostname not in {'localhost', '127.0.0.1', '::1'},
                      "backends": {**{key: SETTINGS[key]["backend"] for key in ("asr", "tts", "actions")}, "llm":active_llm["backend"]},
                      "capabilities": {"audio_input": "browser", "barge_in": SETTINGS["audio"]["wake_enabled"], "memory": True, "shared_memory": True, "shared_memory_voice": True, "material_import": True, "memory_provenance": True, "memory_status": True, "conversation_diagnostics": True, "speaker": SPEAKERS.available(), "hardware_actions": False}}
            try:
                status.update(SERVICES.models.status())
            except Exception:
                pass
            self.json_response(status)
            return
        files = {"/personal-state.js": ("personal-state.js", "text/javascript; charset=utf-8"),"/audio-devices.js": ("audio-devices.js", "text/javascript; charset=utf-8"),"/model-settings.js": ("model-settings.js", "text/javascript; charset=utf-8"),"/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
                 "/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
                 "/": ("index.html", "text/html; charset=utf-8"),
                 "/speech-settings.js": ("speech-settings.js", "text/javascript; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/conversation-diagnostics.js": ("conversation-diagnostics.js", "text/javascript; charset=utf-8"),
                 "/shared.js": ("shared.js", "text/javascript; charset=utf-8"),
            "/shared-replacement.js": ("shared-replacement.js", "text/javascript; charset=utf-8"),
            "/shared-agent.js": ("shared-agent.js", "text/javascript; charset=utf-8"),
            "/diagnostic-overview.js": ("diagnostic-overview.js", "text/javascript; charset=utf-8"),
            "/shared-audit.js": ("shared-audit.js", "text/javascript; charset=utf-8"),
            "/shared-feedback.js": ("shared-feedback.js", "text/javascript; charset=utf-8"),
            "/shared-backup.js": ("shared-backup.js", "text/javascript; charset=utf-8"),
            "/shared-batches.js": ("shared-batches.js", "text/javascript; charset=utf-8"),
            "/shared-recovery.js": ("shared-recovery.js", "text/javascript; charset=utf-8"),
            "/personal-retrieval.js": ("personal-retrieval.js", "text/javascript; charset=utf-8"),
                 "/shared-answer-sources.js": ("shared-answer-sources.js", "text/javascript; charset=utf-8"),
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
            items=STORE.memories(profile)
            result = {"memories":items,"conflicts":conflict_pairs(items)}
        elif self.path == "/api/memory/ask":
            result = PERSONAL_MEMORY.answer(profile, data.get("query"), TURNS.get(data.get("turn_id")),include_outdated=data.get('include_outdated') is True)
            TRACES.retrieval(CURRENT.get(), result.get("retrieval"))
        elif self.path == "/api/memory/search":
            result = PERSONAL_MEMORY.public(PERSONAL_MEMORY.search(profile, data.get("query"), TURNS.get(data.get("turn_id")),include_outdated=data.get('include_outdated') is True))
        elif self.path == "/api/memory/export":
            result = STORE.export_memories(profile)
        elif self.path == "/api/memory/import/preview":
            result = STORE.preview_import(profile, data.get("document"))
        elif self.path == "/api/memory/import/confirm":
            result = STORE.confirm_import(profile, data.get("token"), data.get("confirmed"), data.get("acknowledge_conflicts", False))
        elif self.path == "/api/memory/save":
            result = STORE.request_save(profile, data.get("content"), data.get("id"), data.get("expected_revision"),status=data.get("status"),status_reason=data.get("status_reason"),event_date=data.get("event_date"))
        elif self.path == '/api/memory/conflicts':
            result=STORE.conflicts(profile)
        elif self.path == '/api/memory/conflicts/preview':
            result=STORE.request_conflict(profile,data.get('left_id'),data.get('right_id'),data.get('action'),data.get('preferred_id'),data.get('reason'))
        elif self.path == '/api/memory/conflicts/confirm':
            result=STORE.confirm_conflict(profile,data.get('token'),data.get('confirmed'))
        elif self.path == '/api/memory/review/cancel':
            result=STORE.cancel_review(profile,data.get('token'))
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
        model_binding = MODEL_SETTINGS.bind()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            maximum = 32_000_000 if self.path in {'/api/shared/backup/inspect', '/api/shared/backup/preview'} else 8_000_000
            if not 0 < length <= maximum:
                raise ValueError("请求为空或过大。")
            raw = self.rfile.read(length)
            if self.path.startswith("/api/listen/") and voice_manager is None:
                raise ValueError("当前配置未开启语音监听。")
            if self.path.startswith('/api/models/'):
                data = json.loads(raw)
                if not isinstance(data,dict):raise ValueError('请求格式不正确。')
                cancel = TURNS.get(data.get('turn_id'))
                if self.path == '/api/models/save': result = MODEL_SETTINGS.save(data)
                elif self.path == '/api/models/probe': result = MODEL_SETTINGS.probe(data,cancel)
                elif self.path in ('/api/models/compare/preview','/api/models/compare/confirm','/api/models/compare/cancel'):
                    auth=self.headers.get('Authorization','')
                    token=auth[7:] if auth.startswith('Bearer ') else None
                    if self.path.endswith('/preview'):result=MODEL_SETTINGS.prepare(data,SHARED,token,cancel)
                    elif self.path.endswith('/cancel'):result=MODEL_SETTINGS.discard(data,token)
                    else:result=MODEL_SETTINGS.compare(data,SHARED,token,cancel)
                else:raise ValueError('未知的模型设置操作。')
                self.json_response(result)
            elif self.path == '/api/diagnostics/trace':
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确。')
                self.json_response(TRACES.read(data.get('trace_id')))
            elif self.path.startswith("/api/shared/"):
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else None
                data = json.loads(raw)
                feedback_guard = None
                if self.path in {'/api/shared/ask', '/api/shared/agent/ask', '/api/shared/agent/memory/ask'}:
                    if not isinstance(data, dict):raise SharedError('请求格式不正确。')
                    feedback_guard = SHARED.question_guard(token, data.get('space_id'))
                if self.path == '/api/shared/diagnostics/overview':
                    result = AGENT_AUDIT.overview(token,data)
                elif self.path.startswith('/api/shared/batches/'):
                    result = SHARED.batches.dispatch(self.path[len('/api/shared/batches/'):], token, data)
                elif self.path.startswith('/api/shared/recovery/'):
                    result = SHARED.recovery.dispatch(self.path[len('/api/shared/recovery/'):], token, data)
                elif self.path.startswith('/api/shared/backup/'):
                    result = SHARED_BACKUP.dispatch(self.path[len('/api/shared/backup/'):], token, data)
                elif self.path.startswith('/api/shared/feedback/'):
                    result = FEEDBACK.dispatch(self.path[len('/api/shared/feedback/'):], token, data)
                elif self.path.startswith('/api/shared/import/'):
                    result = SHARED_IMPORT.dispatch(self.path[len('/api/shared/import/'):],token,data,TURNS.get(data.get('turn_id')) if isinstance(data,dict) else None)
                elif self.path == '/api/shared/agent/memory/ask':
                    if not isinstance(data,dict):raise SharedError('请求格式不正确。')
                    if REMOTE_REQUEST.get():
                        if data.get('selection_token') or has_write_intent(data.get('query','')):
                            raise SharedError('云端档位暂不处理记忆写入草稿，请切回本机模型；原有预览与确认流程保留。')
                        result = AGENT_AUDIT.run(token,data,'read',lambda:READ_AGENT.answer(token,data,TURNS.get(data.get('turn_id'))))
                    else:
                        result = AGENT_AUDIT.run(token,data,'select' if data.get('selection_token') else 'ask',lambda:MEMORY_AGENT.answer(token,data,TURNS.get(data.get('turn_id'))))
                elif self.path == '/api/shared/agent/memory/replace-preview':
                    result = AGENT_AUDIT.run(token,data,'ask',lambda:MEMORY_AGENT.replacement_preview(token,data))
                elif self.path == '/api/shared/agent/memory/confirm':
                    result = AGENT_AUDIT.run(token,data,'confirm',lambda:measured('commit',MEMORY_AGENT.confirm,token,data))
                elif self.path == '/api/shared/agent/memory/cancel':
                    result = AGENT_AUDIT.run(token,data,'cancel',lambda:measured('cancel',MEMORY_AGENT.cancel,token,data))
                elif self.path == '/api/shared/agent/records':
                    result = AGENT_AUDIT.list(token,data)
                elif self.path == '/api/shared/agent/ask':
                    if not isinstance(data,dict):raise SharedError('请求格式不正确。')
                    result = AGENT_AUDIT.run(token,data,'read',lambda:READ_AGENT.answer(token,data,TURNS.get(data.get('turn_id'))))
                elif self.path.startswith('/api/shared/voice/'):
                    result = shared_voice_request(token,self.path,data)
                else:
                    result = AGENT_AUDIT.run(token,data,'read',lambda:shared_answer(token,data),mirror_public=True) if self.path == '/api/shared/ask' else SHARED.dispatch(self.path[len('/api/shared'):], data, token)
                if feedback_guard is not None:
                    result = FEEDBACK.offer(token, data, result, feedback_guard)
                self.json_response(result)
            elif self.path.startswith(("/api/profiles/", "/api/memory/", "/api/speaker/")):
                if REMOTE_REQUEST.get() and self.path == '/api/memory/ask':
                    raise ValueError('个人记忆仅在本机模型档位回答，请先切回本机模型。')
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
                if REMOTE_REQUEST.get() and data.get('profile_id'):
                    raise ValueError('当前选中了个人档案，为保留个人资料在本机，请切回本机模型；云端普通聊天请先切为访客。')
                messages = data.get("messages", [])
                if REMOTE_REQUEST.get() and isinstance(messages,list):
                    messages = messages[-1:]  # Never export local chat history on a model switch.
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
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": reply}, "memory_changed": memory_changed, "memory_review": memory_review, "reply_mode": "memory_command" if memory_changed or memory_review else "rule", "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                personal = measured('memory', PERSONAL_MEMORY.search, profile, clean[-1]['content'][:1200], cancel, not bool(picture) and not bool(action_for_text(clean[-1]['content'])))
                TRACES.retrieval(CURRENT.get(), personal['retrieval'])
                if personal.get('conflicts') and re.search(r'我|个人|记忆|偏好|喜欢|住哪|居住',clean[-1]['content']):
                    cancel.check();result=PERSONAL_MEMORY.conflict_answer(personal);PERSONAL_MEMORY.validate(personal)
                    self.send_bytes(200,(json.dumps({'message':{'role':'assistant','content':result['answer']},'personal_memory':result,'reply_mode':'personal_conflict','done':True},ensure_ascii=False)+'\n').encode(),'application/x-ndjson; charset=utf-8')
                    return
                system = SYSTEM + current_time_context() + personal['context']
                if picture:
                    if not isinstance(picture, str) or len(picture) > 3_000_000:
                        raise ValueError("图片过大，请重新拍照。")
                    image_bytes = base64.b64decode(picture, validate=True)
                    if not image_bytes.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
                        raise ValueError("只支持 JPEG 或 PNG 图片。")
                    clean[-1]["images"] = [picture]
                    if not MODEL_SETTINGS.config["vision_model"]:
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
                    if personal['sources']:
                        buffered = []
                        for event in events:
                            cancel.check()
                            buffered.append(event)
                        PERSONAL_MEMORY.validate(personal)
                        cancel.check()
                        events = iter(buffered)
                    first = next(events)  # Fail before sending HTTP 200 when the backend rejects the request.
                    first = {**first, 'personal_memory': PERSONAL_MEMORY.public(personal)}
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
                    if hasattr(events, 'close'):events.close()
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
            self.json_response({"error":str(error),**({"diagnostic":error.agent_diagnostic} if hasattr(error,"agent_diagnostic") else {})}, error.status)
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
                self.json_response({"error": message,**({"diagnostic":error.agent_diagnostic} if hasattr(error,"agent_diagnostic") else {})}, 400)
        finally:
            MODEL_SETTINGS.unbind(model_binding)
            CURRENT.reset(trace_token)


if __name__ == "__main__":
    print(f"小伴已启动：http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
