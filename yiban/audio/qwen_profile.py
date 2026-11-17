"""Offline Qwen voice policy, shared by inference and status reporting."""
import re
import math

SEED = 42
STYLE = "像和熟悉的朋友聊天，自然、温和、平稳，语速适中。保持所选说话人的固定音色和音高范围，不变声，不做夸张表演。"
GENERATION_OPTIONS = {"temperature": .5, "top_k": 20, "top_p": .85, "repetition_penalty": 1.05}
MAX_SEGMENT_CHARS = 120


def generation_settings(value=None):
    """Validate device-level options before persisting or entering the worker."""
    defaults = {"seed": SEED, **GENERATION_OPTIONS}
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("声音生成设置格式不正确。")
    result = {**defaults, **value}
    bounds = {"seed": (0, 2147483647), "temperature": (.1, 1.5),
              "top_k": (1, 100), "top_p": (.1, 1), "repetition_penalty": (1, 1.5)}
    for key, (low, high) in bounds.items():
        number = result[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError("声音生成参数超出允许范围。")
        if key in ("seed", "top_k") and type(number) is not int:
            raise ValueError("种子和 top_k 须为整数。")
    return result


def speech_segments(text, limit=MAX_SEGMENT_CHARS):
    """Pack full sentences together; split only oversized spans, preserving text."""
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;\n]*|[。！？!?；;\n]+", text)
    result, pending = [], ""
    for sentence in sentences:
        if len(pending) + len(sentence) <= limit:
            pending += sentence
            continue
        if pending:
            result.append(pending)
            pending = ""
        while len(sentence) > limit:
            breaks = [m.end() for m in re.finditer(r"[，、,\s]", sentence[:limit]) if m.end() >= limit//2]
            cut = breaks[-1] if breaks else limit
            # Keep a score, number or English token whole at the boundary.
            for word in re.finditer(r"[A-Za-z0-9_.,:/+%\-]+", sentence):
                if word.start() < cut < word.end():
                    cut = word.start() if word.start() else word.end()
                    break
            result.append(sentence[:cut])
            sentence = sentence[cut:]
        pending = sentence
    if pending:
        result.append(pending)
    return result
