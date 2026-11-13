"""Local IP presentation preferences. Names, colors and bounded PNGs only."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import uuid

PRESETS = {
    'default': {'name': '小伴', 'primary': '#356b56', 'accent': '#97b48b', 'avatar': 'default', 'wake_word': '你好小伴'},
    'aifeng': {'name': '爱凤同学', 'primary': '#71529d', 'accent': '#d9a441', 'avatar': 'aifeng', 'wake_word': '爱凤同学'},
}
DEFAULT_CONFIG = dict(PRESETS['default'], preset='default')
WAKE_PHONEMES = {'你好小伴': 'n ǐ h ǎo x iǎo b àn', '爱凤同学': 'ài f èng t óng x ué'}

def strip_name_prefix(text, name):
    return re.sub(r'^\s*(?:' + re.escape(name) + r')[\s，。！？、,:：]*', '', text).strip()

def normalize(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULT_CONFIG):
        raise ValueError('IP设置包含未知字段。')
    current = dict(DEFAULT_CONFIG, **data)
    if not isinstance(current['preset'], str) or current['preset'] not in (*PRESETS, 'custom'):
        raise ValueError('请选择默认、爱凤或自定义IP。')
    name = current['name']
    if not isinstance(name, str) or len(name.strip()) > 32 or (name.strip() and not re.fullmatch(r'[\w\u3400-\u9fff ·.\-]+', name.strip())):
        raise ValueError('名称最多32字，只支持文字、数字、空格及简单连接符。')
    current['name'] = name.strip() or '小伴'
    for key in ('primary', 'accent'):
        if not isinstance(current[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', current[key]):
            raise ValueError('主题色须为六位十六进制颜色。')
        current[key] = current[key].lower()
    if not isinstance(current['avatar'], str) or current['avatar'] not in ('default', 'aifeng', 'custom'):
        raise ValueError('请选择默认机器人、爱凤或上传形象。')
    if not isinstance(current['wake_word'], str) or current['wake_word'] not in WAKE_PHONEMES:
        raise ValueError('当前支持“你好小伴”和“爱凤同学”两套本地唤醒词。')
    return current

def validate_png(encoded):
    if not isinstance(encoded, str) or len(encoded) > 2_000_000:
        raise ValueError('形象仅支持不超过1.5 MB的PNG图片。')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError('形象图片编码无效。') from None
    if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw) < 33 or raw[12:16] != b'IHDR':
        raise ValueError('请上传PNG原图，不能使用SVG、网址或其他文件。')
    width, height = struct.unpack('>II', raw[16:24])
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError('形象图片边长须在1–4096像素之间。')
    return raw

class Appearance:
    def __init__(self, config, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.current = normalize(config)
        self.logo = ''
        self.revision = uuid.uuid4().hex
        self.warning = ''
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                current = normalize(saved['current'])
                logo = saved.get('logo_base64', '')
                if logo: validate_png(logo)
                self.current, self.logo = current, logo
            except Exception:
                self.warning = '已保存的IP设置无法读取，暂用启动配置；原文件未覆盖。'

    def snapshot(self):
        with self.lock:
            result = dict(self.current)
            custom = result['avatar'] == 'custom' and bool(self.logo)
            result['avatar_url'] = ('/api/appearance/avatar?revision=' + self.revision) if custom else ('/aifeng.svg' if result['avatar'] == 'aifeng' else '')
            return result

    def describe(self):
        with self.lock:
            warning = self.warning
            if self.current['avatar'] == 'custom' and not self.logo:
                warning = '没有上传形象，当前使用默认机器人。'
            return {'current': self.snapshot(), 'revision': self.revision, 'presets': copy.deepcopy(PRESETS),
                    'has_custom_logo': bool(self.logo), 'warning': warning, 'max_logo_bytes': 1_500_000}

    def asset(self):
        with self.lock:
            return validate_png(self.logo) if self.logo else None

    def save(self, data):
        if not isinstance(data, dict) or set(data) - {'revision', 'current', 'logo_base64', 'clear_logo'}:
            raise ValueError('IP设置请求格式不正确。')
        if type(data.get('clear_logo', False)) is not bool:
            raise ValueError('移除形象开关须为布尔值。')
        current = normalize(data.get('current'))
        with self.lock:
            if data.get('revision') != self.revision:
                raise ValueError('IP设置已被其他页面更新，请重新加载。')
            logo = '' if data.get('clear_logo') else self.logo
            if 'logo_base64' in data and not isinstance(data['logo_base64'], str):
                raise ValueError('形象图片编码无效。')
            if data.get('logo_base64'):
                validate_png(data['logo_base64'])
                logo = data['logo_base64']
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, prefix='.appearance-', delete=False) as fp:
                    temporary = fp.name
                    json.dump({'current': current, 'logo_base64': logo}, fp, ensure_ascii=False)
                    fp.flush(); os.fsync(fp.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and os.path.exists(temporary): os.unlink(temporary)
            self.current, self.logo = current, logo
            self.revision = uuid.uuid4().hex
            self.warning = ''
            return self.describe()
