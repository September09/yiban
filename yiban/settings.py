"""Load optional JSON settings; the default profile keeps the Mac demo working."""
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from yiban.memory.retrieval import DEFAULT_CONFIG as RETRIEVAL_DEFAULTS, validate_config as validate_retrieval

from yiban.appearance import DEFAULT_CONFIG as APPEARANCE_DEFAULTS, normalize as normalize_appearance

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "port": 8765,
    "appearance": copy.deepcopy(APPEARANCE_DEFAULTS),
    "retrieval": copy.deepcopy(RETRIEVAL_DEFAULTS),
    "llm": {"backend": "ollama", "base_url": "http://127.0.0.1:11434",
            "model": "qwen2.5:3b", "vision_model": "qwen2.5vl:3b",
            "api_key_env": "ASSISTANT_MODEL_API_KEY", "timeout": 180,
            "max_tokens": 384, "temperature": 0.6, "manage_local_ollama": True,
            "enable_thinking": None, "thinking_type": None, "status_check": "models"},
    "asr": {"backend": "sensevoice", "model_dir": "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"},
    "tts": {"backend": "macos", "voice": "Tingting", "rate": 195},
    "actions": {"backend": "screen"},
    "audio": {"input": "browser", "wake_enabled": True},
    "memory": {"database": "data/assistant.sqlite3"},
    "speaker": {"enabled": True, "model": "models/speaker.onnx", "threshold": 0.65, "margin": 0.08},
}


def load_settings(path=None):
    result = copy.deepcopy(DEFAULTS)
    path = path or os.environ.get("ASSISTANT_CONFIG")
    if path:
        supplied = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("配置必须是 JSON 对象。")
        for key, value in supplied.items():
            if key not in result:
                raise ValueError(f"未知配置项：{key}")
            if isinstance(result[key], dict):
                if not isinstance(value, dict) or set(value) - set(result[key]):
                    raise ValueError(f"配置项格式错误或包含未知字段：{key}")
                result[key].update(value)
            else:
                result[key] = value
    if "ASSISTANT_PORT" in os.environ:
        result["port"] = int(os.environ["ASSISTANT_PORT"])
    if type(result["port"]) is not int or not 1 <= result["port"] <= 65535:
        raise ValueError("port 必须在 1–65535 之间。")
    for section, supported in (("llm", {"ollama", "openai_compatible"}),
                               ("asr", {"sensevoice", "disabled"}),
                               ("tts", {"macos", "disabled"}),
                               ("actions", {"screen"})):
        if result[section]["backend"] not in supported:
            raise ValueError(f"尚未实现 {section} 后端：{result[section]['backend']}")
    llm = result["llm"]
    url = urlsplit(llm["base_url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("模型 base_url 必须是无凭据、查询参数的 HTTP(S) 地址。")
    llm["base_url"] = llm["base_url"].rstrip("/")
    for key in ("model", "api_key_env"):
        if not isinstance(llm[key], str) or not llm[key].strip():
            raise ValueError(f"llm.{key} 不能为空。")
    if not isinstance(llm["vision_model"], str):
        raise ValueError("vision_model 必须是字符串；空字符串表示禁用。")
    if type(llm["manage_local_ollama"]) is not bool or type(result["audio"]["wake_enabled"]) is not bool:
        raise ValueError("启动管理和唤醒开关必须使用布尔值。")
    if llm["manage_local_ollama"] and (llm["backend"] != "ollama" or llm["base_url"] != "http://127.0.0.1:11434"):
        raise ValueError("外部模型服务必须设置 manage_local_ollama=false。")
    for key in ("timeout", "max_tokens"):
        if type(llm[key]) is not int or llm[key] <= 0:
            raise ValueError(f"llm.{key} 必须为正整数。")
    if type(llm["temperature"]) not in {int, float} or not 0 <= llm["temperature"] <= 2:
        raise ValueError("temperature 必须在 0–2 之间。")
    if llm['enable_thinking'] is not None and type(llm['enable_thinking']) is not bool:
        raise ValueError('enable_thinking 必须是布尔值或 null。')
    if llm['thinking_type'] not in (None, 'enabled', 'disabled'):
        raise ValueError('thinking_type 必须为 enabled、disabled 或 null。')
    if llm['thinking_type'] is not None and llm['enable_thinking'] is not None:
        raise ValueError('不能同时使用两种服务商的思考参数。')
    if llm['status_check'] not in ('models', 'completion'):
        raise ValueError('status_check 必须为 models 或 completion。')
    if llm['status_check'] == 'completion' and (llm['backend'] != 'openai_compatible' or llm['vision_model']):
        raise ValueError('completion 状态检查仅支持 OpenAI 兼容的纯文字配置。')
    if type(result["tts"]["rate"]) is not int or result["tts"]["rate"] <= 0:
        raise ValueError("tts.rate 必须为正整数。")
    if result["audio"]["input"] != "browser":
        raise ValueError("目前仅实现 browser 音频入口，USB/ESP32 入口待接入。")
    if result["audio"]["wake_enabled"] and result["asr"]["backend"] == "disabled":
        raise ValueError("禁用 ASR 时也需要关闭 wake_enabled。")
    model_dir = Path(result["asr"]["model_dir"]).expanduser()
    result["asr"]["model_dir"] = str(model_dir if model_dir.is_absolute() else ROOT / model_dir)
    for section, key in (("memory", "database"), ("speaker", "model")):
        path = Path(result[section][key]).expanduser()
        result[section][key] = str(path if path.is_absolute() else ROOT / path)
    if type(result["speaker"]["enabled"]) is not bool:
        raise ValueError("speaker.enabled 必须是布尔值。")
    for key in ("threshold", "margin"):
        if type(result["speaker"][key]) not in {int, float} or not 0 <= result["speaker"][key] <= 1:
            raise ValueError(f"speaker.{key} 必须在 0–1 之间。")
    result["retrieval"] = validate_retrieval(result["retrieval"])
    result["appearance"] = normalize_appearance(result["appearance"])
    return result
