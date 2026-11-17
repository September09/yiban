"""Versioned candidate contract. Text, extraction confidence and verification are distinct."""
import copy
import hashlib
import json
import math
import re
from datetime import date

from yiban.memory.shared_memory import SharedError

SCHEMA = 'yiban.memory-candidates.v2'
KINDS = {'event', 'fact', 'quote', 'reflection'}
PREDICATES = {'holds_role', 'quantity', 'score', 'relationship', 'preference', 'statement'}


def fail(message):
    raise SharedError(message)


def text(value, label, limit=1200, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit:
        fail(f'{label}需为{0 if empty else 1}–{limit}字的文字。')
    return value.strip()


def key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value):
        fail('编号需为1–100位字母、数字、点、冒号、横线或下划线。')
    return value


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        fail(label+'包含未知字段或不是对象，请核对格式版本。')
    return value


def strings(value, label, limit=20, length=80):
    if not isinstance(value, list) or len(value) > limit:
        fail(label+'需为有限的文字列表。')
    result = [text(x, label, length) for x in value]
    if len(set(result)) != len(result):
        fail(label+'存在重复项。')
    return result


def temporal(value):
    value = fields(value, {'raw', 'start', 'end', 'precision', 'basis'}, '时间')
    precision = value.get('precision', 'unknown')
    basis = value.get('basis', 'unknown')
    if precision not in {'day', 'month', 'year', 'range', 'unknown'} or basis not in {'explicit', 'inferred', 'unknown'}:
        fail('时间精度或依据无效。')
    start, end = value.get('start'), value.get('end')
    pattern = {'day': r'\d{4}-\d{2}-\d{2}', 'month': r'\d{4}-\d{2}', 'year': r'\d{4}', 'range': r'\d{4}-\d{2}-\d{2}'}
    if precision == 'unknown':
        if start is not None or end is not None:
            fail('未知日期不能填确定的起止日期。')
    else:
        if not isinstance(start, str) or not re.fullmatch(pattern[precision], start):
            fail('时间值与精度不匹配；缺年份时请保留原表达并标未知。')
        try:
            date.fromisoformat(start + {'year': '-01-01', 'month': '-01', 'day': '', 'range': ''}[precision])
            if end is not None:
                if precision != 'range':
                    fail('只有时间范围可以填写结束日期。')
                date.fromisoformat(end)
                if end < start:
                    fail('结束日期早于开始日期。')
        except (ValueError, TypeError):
            fail('日期无效。')
    return {'raw': text(value.get('raw', ''), '原始时间', 200, True), 'start': start, 'end': end,
            'precision': precision, 'basis': basis}


def fact(value, entity_keys):
    v = fields(value, {'subject_key', 'object_key', 'predicate', 'value', 'unit', 'metric', 'term', 'effective'}, '事实')
    if v.get('subject_key') not in entity_keys or (v.get('object_key') is not None and v['object_key'] not in entity_keys):
        fail('事实中的人物／组织编号未定义。')
    predicate = v.get('predicate')
    if predicate not in PREDICATES:
        fail('事实类型未注册，请先扩展校验规则。')
    result = {'subject_key': v['subject_key'], 'object_key': v.get('object_key'), 'predicate': predicate,
              'unit': text(v.get('unit', ''), '单位', 40, True), 'metric': text(v.get('metric', ''), '统计口径', 100, True),
              'term': text(v.get('term', ''), '届次', 80, True), 'effective': temporal(v.get('effective', {}))}
    number = v.get('value')
    if predicate == 'quantity':
        if type(number) not in (float, int) or not math.isfinite(number) or number < 0 or not result['unit'] or not result['metric']:
            fail('数量需为非负有限数字，且必须说明单位和统计口径。')
    elif predicate == 'score':
        fields(number, {'own', 'opponent', 'result'}, '比分')
        for k in ('own', 'opponent'):
            if number.get(k) is not None and (type(number[k]) is not int or not 0 <= number[k] <= 9999):
                fail('比分需为非负整数；未记载请用null。')
        outcome = number.get('result', 'unknown')
        if outcome not in {'win', 'loss', 'draw', 'unknown'} or not result['object_key']:
            fail('比赛需注明对手，胜负未记载请用unknown。')
        if number.get('own') is not None and number.get('opponent') is not None:
            expected = 'win' if number['own'] > number['opponent'] else 'loss' if number['own'] < number['opponent'] else 'draw'
            if outcome not in {'unknown', expected}:
                fail('比分与胜负矛盾，请核对。')
        number = {'own': number.get('own'), 'opponent': number.get('opponent'), 'result': outcome}
    else:
        number = text(number, '事实值', 500)
        if predicate == 'relationship' and not result['object_key']:
            fail('人物关系必须指定关系的另一方。')
    result['value'] = number
    return result


def record(value, entity_keys, event_keys, sources):
    v = fields(value, {'key', 'type', 'title', 'summary', 'detail', 'participants', 'event_key', 'fact', 'emotions', 'tags',
                       'verification', 'verification_reason', 'sensitivity', 'confidence', 'evidence'}, '候选记忆')
    kind = v.get('type')
    if kind not in KINDS:
        fail('记忆类型需为事件、事实、引语或感受。')
    participants = strings(v.get('participants', []), '参与者编号', 80, 100)
    if set(participants) - entity_keys:
        fail('参与者编号未定义。')
    event_key = v.get('event_key')
    if event_key is not None and event_key not in event_keys:
        fail('关联事件编号未定义。')
    if kind == 'event' and event_key is None:
        fail('事件记忆必须关联一个明确事件；阶段总结请标为感受。')
    verification = v.get('verification', 'pending')
    reason = text(v.get('verification_reason', ''), '核实说明', 500, True)
    if verification not in {'pending', 'confirmed', 'disputed'} or (verification != 'pending' and not reason):
        fail('已确认或有争议的记忆必须填写核实说明。')
    sensitivity = v.get('sensitivity', 'normal')
    if sensitivity not in {'normal', 'personal'}:
        fail('敏感级别无效。')
    confidence = v.get('confidence')
    if confidence is not None and (type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        fail('提取置信度需为0到1，不能代替核实状态。')
    evidence = v.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
        fail('每条记忆需提供1–12段依据。')
    spans = []
    for span in evidence:
        fields(span, {'source_key', 'start', 'end', 'text'}, '原文位置')
        source = sources.get(span.get('source_key'))
        start, end = span.get('start'), span.get('end')
        if not source or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source['text']) or end-start > 4000:
            fail('原文编号、摘录位置或长度无效。')
        if source['text'][start:end] != span.get('text'):
            fail('摘录与所给原文不一致，不能作为依据。')
        spans.append(copy.deepcopy(span))
    if kind != 'fact' and v.get('fact') is not None:
        fail('只有事实记忆可以填写结构化事实。')
    return {'key': key(v.get('key')), 'type': kind, 'title': text(v.get('title'), '标题', 160),
            'summary': text(v.get('summary'), '摘要'), 'detail': text(v.get('detail', ''), '细节', 4000, True),
            'participants': participants, 'event_key': event_key, 'fact': fact(v.get('fact'), entity_keys) if kind == 'fact' else None,
            'emotions': strings(v.get('emotions', []), '情绪'), 'tags': strings(v.get('tags', []), '标签'),
            'verification': verification, 'verification_reason': reason, 'sensitivity': sensitivity,
            'confidence': confidence, 'evidence': spans}


def _validate(document):
    d = fields(document, {'schema', 'batch_name', 'sources', 'entities', 'events', 'records'}, '导入包')
    if d.get('schema') != SCHEMA:
        fail('请选择yiban.memory-candidates.v2格式的新版候选包。')
    def rows(name, limit):
        values = d.get(name, [])
        if not isinstance(values, list) or len(values) > limit:
            fail(name+'数量超出本批范围。')
        return values
    sources = {}
    for raw in rows('sources', 20):
        s = fields(raw, {'key', 'name', 'kind', 'text', 'sha256', 'published_at', 'url'}, '来源')
        identity = key(s.get('key'))
        if identity in sources:
            fail('来源编号重复。')
        original = text(s.get('text'), '来源全文', 200000)
        # Preserve whitespace: offsets and hash refer to exactly supplied Unicode text.
        original = s['text']
        if s.get('sha256') != hashlib.sha256(original.encode('utf-8')).hexdigest():
            fail('来源摘要与全文不匹配。')
        if s.get('kind', 'original') not in {'original', 'candidate', 'manual'}:
            fail('来源类型无效。')
        sources[identity] = {'key': identity, 'name': text(s.get('name'), '来源名称', 200), 'kind': s.get('kind', 'original'),
                             'text': original, 'sha256': s['sha256'], 'published_at': text(s.get('published_at', ''), '发表日期原文', 100, True),
                             'url': text(s.get('url', ''), '来源地址', 1000, True)}
    if not sources or sum(len(s['text']) for s in sources.values()) > 400000:
        fail('每包需有来源，全文合计最多40万字。')
    entities = {}
    for raw in rows('entities', 500):
        e = fields(raw, {'key', 'kind', 'name', 'aliases'}, '人物／组织')
        identity = key(e.get('key'))
        if identity in entities or e.get('kind') not in {'person', 'organization', 'character'}:
            fail('人物／组织编号重复或类型无效。')
        entities[identity] = {'key': identity, 'kind': e['kind'], 'name': text(e.get('name'), '名称', 100),
                              'aliases': strings(e.get('aliases', []), '别名')}
    events = {}
    for raw in rows('events', 200):
        e = fields(raw, {'key', 'kind', 'title', 'time', 'state', 'location', 'parent_key'}, '事件')
        identity = key(e.get('key'))
        if identity in events or e.get('state', 'unknown') not in {'held', 'planned', 'cancelled', 'unknown'}:
            fail('事件编号重复或发生状态无效。')
        events[identity] = {'key': identity, 'kind': text(e.get('kind'), '事件类别', 80), 'title': text(e.get('title'), '事件标题', 160),
                            'time': temporal(e.get('time', {})), 'state': e.get('state', 'unknown'),
                            'location': text(e.get('location', ''), '地点', 200, True), 'parent_key': e.get('parent_key')}
    for event in events.values():
        seen = {event['key']}; parent = event['parent_key']
        while parent:
            if parent not in events or parent in seen:
                fail('父事件未定义或事件关系形成循环。')
            seen.add(parent); parent = events[parent]['parent_key']
    records = [record(r, set(entities), set(events), sources) for r in rows('records', 200)]
    if not records or len({r['key'] for r in records}) != len(records):
        fail('候选记忆不能为空，且编号不得重复。')
    return {'schema': SCHEMA, 'batch_name': text(d.get('batch_name'), '批次名称', 160), 'sources': list(sources.values()),
            'entities': list(entities.values()), 'events': list(events.values()), 'records': records}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate(document):
    try:
        return _validate(document)
    except (TypeError, KeyError, ValueError, OverflowError):
        raise SharedError('候选字段类型无效，请核对新版格式。') from None
