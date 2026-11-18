"""Loopback HTTP application; platform-specific work lives in backends/."""
from contextvars import ContextVar
from contextlib import nullcontext
import base64
from itertools import chain
import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from yiban.appearance import Appearance, strip_name_prefix
from yiban.character_memory import CharacterMemory
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
from yiban.memory.memory_service import MemoryService
from yiban.memory.voice_account import VoiceAccount
from yiban.audio.speakers import SpeakerService
from yiban.turns import TurnRegistry, Cancelled
from yiban.audio.diagnostics import audio_metadata, log_audio_diagnostic
from yiban.conversation_diagnostics import TRACES, CURRENT, measured, model_events

SETTINGS = load_settings()
APPEARANCE = Appearance(SETTINGS["appearance"], Path(SETTINGS["memory"]["database"]).parent / "appearance.json")
PERSONA = ContextVar("ip_persona", default=None)
CHARACTER = ContextVar("ip_character", default=None)
CHARACTER_MEMORY = CharacterMemory(Path(SETTINGS["memory"]["database"]).parent / "character-memory.json")
if SETTINGS["audio"]["wake_enabled"]:
    from yiban.audio.voice import manager as voice_manager, available as voice_available
    voice_manager.configure(APPEARANCE.snapshot()["wake_word"])
else:
    voice_manager = None


def shared_request_summary(data):
    """共同记忆请求的关键字段摘要；只记长度，不记正文。"""
    if not isinstance(data, dict):
        return {"body": type(data).__name__}
    summary = {key: data.get(key) for key in
               ("space_id", "id", "version", "status", "visibility", "edit_policy", "event_date")}
    content, reason, viewers = data.get("content"), data.get("status_reason"), data.get("viewers")
    summary["content_len"] = len(content) if isinstance(content, str) else None
    summary["status_reason_len"] = len(reason) if isinstance(reason, str) else None
    summary["viewers"] = len(viewers) if isinstance(viewers, list) else viewers
    return summary


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
MEMORY = MemoryService(SHARED, SERVICES.models)
STRUCTURED_MEMORY = MEMORY  # Internal alias for existing integration fixtures.
VOICE_ACCOUNT = VoiceAccount(SHARED, MEMORY)
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

SYSTEM = "是中文语音助手。用自然、友好的中文交流，通常用2到4句话简洁回答，不用Markdown格式。你没有联网、实际机器人或操作电脑的能力。界面支持固定屏幕动作：点头、摇头、向左看、向右看、开心、回正；只有系统提供动作结果时才能说已完成。图片仅在用户本次提供时可见，不能持续观察摄像头，不要猜测没有提供的画面。询问当前说话人时只依据当前用户档案，未确认时不猜身份，不建议用摄像头辨认人的身份。不知道的事情请坦诚说明。"


def system_prompt():
    return "你叫" + (PERSONA.get() or APPEARANCE.snapshot()["name"]) + "，" + SYSTEM + CHARACTER_MEMORY.context(CHARACTER.get() if CHARACTER.get() is not None else CHARACTER_MEMORY.lookup(APPEARANCE.snapshot()))


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
    voice_versions=VOICE_ACCOUNT.versions() if identify_speaker else {}
    if identify_speaker:
        result["speaker"] = measured('speaker', SPEAKERS.identify, raw)
        diagnostic.update(result['speaker']['diagnostics'])
    result['utterance_id'] = SHARED_VOICE.receipt(text, result.get('speaker', {}))
    active_ip=APPEARANCE.snapshot()
    prefix='|'.join(re.escape(active_ip[k]) for k in ('name','wake_word'))
    voice_text=re.sub(r'^\s*(?:你好[，, ]*)?(?:'+prefix+r')[\s，。！？、,:：]*','',text).strip()
    result['voice_receipt_id'] = VOICE_ACCOUNT.receipt(voice_text, result.get('speaker', {}), versions=voice_versions)
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
        answer = ('当前可见范围内没有适用于本次问答的记忆。如果内容设为「指定成员」可见，需要作者把你加入名单；'
                  '共同记忆语音的公开朗读模式也只使用「全体成员」可见的记忆。可检查记忆状态；回顾旧安排时，请明确要求查询过时记忆。')
    else:
        TRACES.materials(CURRENT.get(),context['sources'],'read')
        answer, completed = '', False
        events = model_events(SERVICES.models.chat([
            {'role':'system','content':system_prompt() + current_time_context() + context['context'] + REFERENCE_INSTRUCTION},
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
        if self.path == "/api/appearance":
            self.json_response(APPEARANCE.describe())
            return
        if urlsplit(self.path).path == "/api/appearance/avatar":
            asset = APPEARANCE.asset()
            self.send_bytes(200 if asset else 404, asset or b"", "image/png")
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
            active_ip = APPEARANCE.snapshot()
            status = {"chat": False, "vision": False, "model": active_llm["model"], "vision_model": active_llm["vision_model"],
                      "asr": SERVICES.asr.available(),
                      "wake": SETTINGS["audio"]["wake_enabled"] and voice_available(),
                      "tts": SERVICES.tts.available(), "tts_streaming": SPEECH.streaming_available(), "wake_word": active_ip["wake_word"], "appearance": active_ip, "version": 55, "memory_schema_version": 2,
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
        files = {"/md-import.js": ("md-import.js", "text/javascript; charset=utf-8"), "/structured-memory.js": ("structured-memory.js", "text/javascript; charset=utf-8"), "/companion-memory.js": ("companion-memory.js", "text/javascript; charset=utf-8"), "/branding.js": ("branding.js", "text/javascript; charset=utf-8"), "/branding.css": ("branding.css", "text/css; charset=utf-8"), "/aifeng.svg": ("aifeng.svg", "image/svg+xml"),"/personal-state.js": ("personal-state.js", "text/javascript; charset=utf-8"),"/audio-devices.js": ("audio-devices.js", "text/javascript; charset=utf-8"),"/model-settings.js": ("model-settings.js", "text/javascript; charset=utf-8"),"/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
                 "/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
                 "/": ("index.html", "text/html; charset=utf-8"),
                 "/speech-settings.js": ("speech-settings.js", "text/javascript; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/speech-stream.js": ("speech-stream.js", "text/javascript; charset=utf-8"),
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
                 "/member-onboarding.js": ("member-onboarding.js", "text/javascript; charset=utf-8"),
                 "/voice-account.js": ("voice-account.js", "text/javascript; charset=utf-8"),
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
        if self.path.startswith('/api/memory/'):
            raise SharedError('记忆入口已统一，请登录记忆账号后在记忆库中操作。',410)
        if self.path == "/api/profiles/list":
            result = {"profiles": STORE.profiles()}
        elif self.path == "/api/profiles/create":
            result = STORE.create_profile(data.get("name"))
        elif self.path == "/api/profiles/salutation":
            result = STORE.set_salutation(profile, data.get("salutation"))
        elif self.path == "/api/profiles/delete":
            STORE.delete_profile(profile)
            result = {"deleted": True}
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
        ip_snapshot = APPEARANCE.snapshot()
        persona_binding = PERSONA.set(ip_snapshot["name"])
        character_binding = CHARACTER.set(CHARACTER_MEMORY.lookup(ip_snapshot))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            maximum = 32_000_000 if self.path in {'/api/shared/backup/inspect', '/api/shared/backup/preview'} else 8_000_000
            if not 0 < length <= maximum:
                raise ValueError("请求为空或过大。")
            raw = self.rfile.read(length)
            if self.path.startswith("/api/listen/") and voice_manager is None:
                raise ValueError("当前配置未开启语音监听。")
            if self.path == '/api/appearance/save':
                # Share the voice lock so no session can start between checking and saving.
                with voice_manager.lock if voice_manager else nullcontext():
                    if voice_manager:
                        voice_manager.expire()
                        if voice_manager.sessions:
                            raise ValueError('请先关闭各页面的语音唤醒和录音，再更换IP设置。')
                    result = APPEARANCE.save(json.loads(raw))
                    if voice_manager: voice_manager.configure(result['current']['wake_word'])
                self.json_response(result)
            elif self.path.startswith('/api/models/'):
                data = json.loads(raw)
                if not isinstance(data,dict):raise ValueError('请求格式不正确。')
                cancel = TURNS.get(data.get('turn_id'))
                if self.path == '/api/models/save': result = MODEL_SETTINGS.save(data)
                elif self.path == '/api/models/probe': result = MODEL_SETTINGS.probe(data,cancel)
                elif self.path in ('/api/models/compare/preview','/api/models/compare/confirm','/api/models/compare/cancel'):
                    auth=self.headers.get('Authorization','')
                    token=auth[7:] if auth.startswith('Bearer ') else None
                    if self.path.endswith('/preview'):result=MODEL_SETTINGS.prepare(data,MEMORY,token,cancel)
                    elif self.path.endswith('/cancel'):result=MODEL_SETTINGS.discard(data,token)
                    else:result=MODEL_SETTINGS.compare(data,MEMORY,token,cancel)
                else:raise ValueError('未知的模型设置操作。')
                self.json_response(result)
            elif self.path == '/api/diagnostics/trace':
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确。')
                self.json_response(TRACES.read(data.get('trace_id')))
            elif self.path.startswith('/api/voice-memory/'):
                data=json.loads(raw)
                auth=self.headers.get('Authorization','');token=auth[7:] if auth.startswith('Bearer ') else None
                try:
                    context=(system_prompt()+current_time_context()) if not REMOTE_REQUEST.get() else '你是中文记忆助手。'
                    result=VOICE_ACCOUNT.dispatch(self.path[len('/api/voice-memory/'):],token,data,
                        TURNS.get(data.get('turn_id')) if isinstance(data,dict) else None,context)
                except SharedError as error:
                    self.json_response({'error':str(error),'voice_memory':True},error.status);return
                self.json_response(result)
            elif self.path.startswith(('/api/memories/', '/api/memory/v2/')):
                data=json.loads(raw)
                auth=self.headers.get('Authorization','');token=auth[7:] if auth.startswith('Bearer ') else None
                context=(system_prompt()+current_time_context()) if not REMOTE_REQUEST.get() else '你是中文记忆助手。'
                prefix='/api/memories/' if self.path.startswith('/api/memories/') else '/api/memory/v2/'
                result=MEMORY.dispatch(self.path[len(prefix):],token,data,TURNS.get(data.get('turn_id')) if isinstance(data,dict) else None,context)
                self.json_response(result)
            elif self.path in ('/api/companion/ask','/api/companion/reset'):
                data=json.loads(raw)
                if not isinstance(data,dict):raise SharedError('请求格式不正确。')
                auth=self.headers.get('Authorization','');token=auth[7:] if auth.startswith('Bearer ') else None
                if self.path.endswith('/reset'):
                    result=MEMORY.dispatch('reset',token,data)
                else:
                    context=(system_prompt()+current_time_context()) if not REMOTE_REQUEST.get() else '你是中文陪伴助手。'
                    result=MEMORY.dispatch('ask',token,data,TURNS.get(data.get('turn_id')),context)
                self.json_response(result)
            elif self.path.startswith("/api/shared/"):
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else None
                data = json.loads(raw)
                if self.path in ('/api/shared/ask','/api/shared/agent/ask','/api/shared/agent/memory/ask','/api/shared/ask/reset'):
                    if not isinstance(data,dict):raise SharedError('请求格式不正确。')
                    data={**data,'scope_kind':'team'}
                    context=(system_prompt()+current_time_context()) if not REMOTE_REQUEST.get() else '你是中文记忆助手。'
                    result=MEMORY.dispatch('reset' if self.path.endswith('/reset') else 'ask',token,data,TURNS.get(data.get('turn_id')),context)
                    self.json_response(result);return
                retired=('/api/shared/import/','/api/shared/voice/','/api/shared/agent/memory/replace-preview','/api/shared/agent/memory/confirm')
                if self.path.startswith(retired) or self.path in ('/api/shared/memories/preview','/api/shared/memories/confirm'):
                    SHARED.me(token)
                    raise SharedError('保存与修改已统一到记忆库，请核对候选并预览确认；本次没有写入。',410)
                if self.path=='/api/shared/memories/list':
                    result=MEMORY.dispatch('list',token,{'scope_kind':'team','space_id':data.get('space_id')})
                    with STORE.connect() as db:
                        actor=SHARED._actor(db,token);space=SHARED._member(db,actor['id'],data.get('space_id'))
                    self.json_response({'space':space,'memories':result['records'],'scope_name':result['scope_name']});return
                if self.path=='/api/shared/memories/evidence':
                    with STORE.connect() as db:
                        current=bool(db.execute('SELECT 1 FROM memory_v2_records WHERE id=?',(data.get('id'),)).fetchone())
                    if current:
                        scope={'scope_kind':'team','space_id':data.get('space_id'),'id':data.get('id'),'expected_version':data.get('expected_version')}
                        result=MEMORY.evidence(token,scope)
                        can_full=False;full=None
                        try:
                            full=MEMORY.read_trace(token,scope,'source');can_full=True
                        except SharedError as error:
                            if error.status!=403:raise
                        result.update(source_name='；'.join(dict.fromkeys(q['source_name'] for q in result['quotes'])),captured_content='',created='',content_changed=False,can_read_full=can_full)
                        if data.get('include_full'):
                            if not can_full:raise SharedError('完整原文仅作者和管理员可查看。',403)
                            result['source_text']='\n\n'.join(x['text'] for x in full['sources'])
                        self.json_response(result);return
                if self.path=='/api/shared/spaces/delete':
                    with STORE.connect() as db:
                        populated=bool(db.execute('SELECT 1 FROM memory_v2_records WHERE scope=? LIMIT 1',('team:'+str(data.get('space_id')),)).fetchone())
                    if populated:
                        SHARED.question_guard(token,data.get('space_id'))
                        raise SharedError('这个空间含统一记忆，请先完整备份并在记忆库中归档；此入口不会删除空间。',409)
                # 出错时把关键字段（不含正文）打进日志，便于定位“点了没反应”的请求。
                self.shared_request = data
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
                identity_response = False
                reply = ('请登录记忆账号，在记忆库中生成候选并预览确认保存。' if not picture and has_write_intent(clean[-1]['content']) else None)
                memory_changed = False
                memory_review = None
                if isinstance(reply, dict):
                    memory_review, reply = reply['review'], reply['reply']
                    memory_changed = False
                if reply is None and not picture:
                    reply = CHARACTER_MEMORY.reply(clean[-1]["content"], PERSONA.get(), CHARACTER.get())
                if reply is None and not picture:
                    reply = clock_reply(strip_name_prefix(clean[-1]["content"], PERSONA.get()))
                if reply is None and not picture:
                    reply = STORE.identity_reply(profile, strip_name_prefix(clean[-1]["content"], PERSONA.get()), assistant_name=PERSONA.get())
                    identity_response = reply is not None
                if reply is not None:
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": reply}, "memory_changed": memory_changed, "memory_review": memory_review, "identity_greeting": bool(identity_response and profile), "reply_mode": "memory_command" if memory_changed or memory_review else "rule", "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                # Ordinary chat has no saved-memory context. Authenticated memory
                # questions use MEMORY through /api/memories/ask in every UI mode.
                if data.get('shared_token') or data.get('shared_space_id'):
                    raise ValueError('请使用统一记忆问答入口；普通聊天不读取账号记忆。')
                personal={'sources':[]}
                shared_context=None
                system=system_prompt()+'\n本轮为普通聊天，没有提供已保存记忆；不能声称记得某条个人或团队资料。'
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
                # 时钟带秒，放在最末位：前面那段尽量逐轮不变，才能命中模型的前缀缓存。
                system += current_time_context()
                action = action_for_text(clean[-1]["content"], PERSONA.get()) if not picture else None
                if action:
                    event = SERVICES.actions.execute(action)
                    self.send_bytes(200, (json.dumps({"message": {"role": "assistant", "content": event["reply"]},
                        "action": event, "done": True}, ensure_ascii=False) + "\n").encode(), "application/x-ndjson; charset=utf-8")
                    return
                events = model_events(SERVICES.models.chat([{"role": "system", "content": system}] + clean, vision=bool(picture), cancel=cancel))
                try:
                    # 含记忆的回答先完成生成、复核快照再发送，避免生成中删改资料或撤权后泄露旧内容。
                    first = next(events)
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
            elif self.path == "/api/speak/stream":
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("请求格式不正确。")
                cancel = TURNS.get(data.get("turn_id"))
                events = SPEECH.stream_synthesize(data.get("text", ""), cancel=cancel)
                try:
                    first = next(events)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    streaming = True
                    for event in chain((first,), events):
                        cancel.check()
                        self.wfile.write((json.dumps(event) + "\n").encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    cancel.cancel()
                    raise
                finally:
                    events.close()
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
            # 页面上的提示可能被滚动位置挡住，日志里也留一份，方便排查“点了没反应”。
            if self.path.startswith('/api/shared/'):
                print(f'[shared-error] {self.path} -> {error.status}: {error} | '
                      f'{shared_request_summary(getattr(self, "shared_request", None))}', flush=True)
            self.json_response({"error":str(error),**({"diagnostic":error.agent_diagnostic} if hasattr(error,"agent_diagnostic") else {})}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            message = "本轮已打断。" if isinstance(error, Cancelled) else str(error)
            if isinstance(error, urllib.error.URLError):
                message = "配置的模型服务暂时不可用，请检查服务地址、模型和运行日志。"
            if self.path.startswith('/api/shared/') and not isinstance(error, (Cancelled, urllib.error.URLError)):
                traceback.print_exc()
            if streaming:
                try:
                    self.wfile.write((json.dumps({"error": message}) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.json_response({"error": message,**({"diagnostic":error.agent_diagnostic} if hasattr(error,"agent_diagnostic") else {})}, 400)
        finally:
            CHARACTER.reset(character_binding)
            PERSONA.reset(persona_binding)
            MODEL_SETTINGS.unbind(model_binding)
            CURRENT.reset(trace_token)


def warm_models():
    """Load local speech models in the background; the first turn should not pay for it."""
    try:
        warm = getattr(SERVICES.asr, "warm", None)
        if callable(warm):
            warm()
    except Exception:
        pass
    try:
        if voice_manager is not None:
            voice_manager.warm()
    except Exception:
        pass


if __name__ == "__main__":
    def warm_speech():
        try:
            warm = getattr(SERVICES.tts, "warm", None)
            if callable(warm):
                warm()
        except Exception:
            print("Speech warm-up failed; retry on next request.", flush=True)
    threading.Thread(target=warm_speech, daemon=True).start()
    threading.Thread(target=warm_models, daemon=True).start()
    print(f"{APPEARANCE.snapshot()['name']}已启动：http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
