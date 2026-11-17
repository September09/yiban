"""Explicit device-local IP role facts; not user identity or inferred kinship."""
import json
import re
from pathlib import Path

ROLE_LABELS = {'mother': '妈妈', 'maternal_aunt': '小姨'}

class CharacterMemory:
    def __init__(self, path):
        self.path = Path(path)

    def lookup(self, appearance):
        if not self.path.exists():return {}
        try:
            if self.path.stat().st_size > 32768:return {}
            document = json.loads(self.path.read_text(encoding='utf-8'))
            if document.get('version') != 1:return {}
            for key, record in document['characters'].items():
                aliases = record.get('names', [])
                if appearance.get('preset') != key and appearance.get('name') not in aliases:continue
                values = record.get('relationships', {})
                if not isinstance(values, dict):return {}
                result = {}
                for role in ROLE_LABELS:
                    name = values.get(role, '')
                    if isinstance(name, str) and re.fullmatch(r'[\w\u3400-\u9fff ·.\-]{1,32}', name):result[role] = name
                return result
        except (OSError, ValueError, KeyError, AttributeError, TypeError):pass
        return {}

    @staticmethod
    def context(facts):
        if not facts:return '\n尚未提供此IP的亲属角色设定，请勿编造。'
        return '\n本机已确认的IP角色设定：' + '；'.join(ROLE_LABELS[k]+'是'+v for k,v in facts.items()) + '。这是IP角色关系，不用于推断现实人物的亲属关系或授予权限。'

    @staticmethod
    def reply(text, name, facts):
        compact=re.sub(r'[\s，。！？、,:：!?\.]','',text)
        prefix=r'(?:你的|你|'+re.escape(name)+r'的)'
        for role, words in (('mother',r'(?:妈妈|母亲)'),('maternal_aunt',r'(?:小姨|小姨妈)')):
            if re.fullmatch(prefix+words+r'(?:是谁|是哪位|叫什么|叫什么名字)(?:呀|啊|呢|吗)?',compact):
                return '我的'+ROLE_LABELS[role]+'是'+facts[role]+'。' if facts.get(role) else '我的'+ROLE_LABELS[role]+'角色还没有设置。'
        return None
