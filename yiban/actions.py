"""Finite screen actions. No serial, motor or arbitrary code execution."""
import re

ACTIONS = {
    "nod": {"label": "点头", "duration_ms": 1600, "reply": "好呀，给你点点头。"},
    "shake": {"label": "摇头", "duration_ms": 1600, "reply": "看，我在摇头。"},
    "left": {"label": "向左看", "duration_ms": 1600, "reply": "我向左看看。"},
    "right": {"label": "向右看", "duration_ms": 1600, "reply": "我向右看看。"},
    "happy": {"label": "开心", "duration_ms": 2000, "reply": "见到你真开心！"},
    "reset": {"label": "回正", "duration_ms": 400, "reply": "好，已经回正啦。"},
}


def action_for_text(text):
    text = re.sub(r"[\s，。！？!?,.]", "", text)
    if any(word in text for word in ("不要", "别", "不许", "不能", "停止", "解释", "为什么", "什么意思", "如果", "怎么")):
        return None
    # Only short direct commands trigger motion; discussing actions does not.
    text = re.sub(r"^(?:小伴|你好小伴)?(?:请你|请|给我|帮我|你)?", "", text)
    for action, pattern in (
        ("nod", r"点(?:点)?头(?:一下|两下|给我看|吧)?"),
        ("shake", r"摇(?:摇)?头(?:一下|两下|给我看|吧)?"),
        ("left", r"(?:向|往)?左(?:边)?(?:看|转)(?:一下|看|头|吧)?"),
        ("right", r"(?:向|往)?右(?:边)?(?:看|转)(?:一下|看|头|吧)?"),
        ("happy", r"(?:笑一个|笑一笑|开心一点|开心一下)"),
        ("reset", r"(?:回正|恢复原位|回到原位)"),
    ):
        if re.fullmatch(pattern, text):
            return action
    return None


def describe(action):
    if action not in ACTIONS:
        raise ValueError("不支持这个动作。")
    return {"action": action, "target": "screen", **ACTIONS[action]}
