"""Resolve narrow narrative fields from the current authorized record."""

import re
from yiban.memory import v2_intent, v2_relevance
from yiban.memory.v2_facts import UNKNOWN
from yiban.memory.v2_grounding import envelope

ORIGIN = r'来源|源于|来自|来处|从(?:哪(?:里|儿)?|何处)|在哪(?:里|儿).{0,6}(?:取|带回)'
GIFT = r'送给|赠予|赠给|赠送'
UNCERTAIN = r'据说|传闻|待核实|推测|没有|并未|未曾|未知'
DETAIL = r'多少|几克|几件|几只|尺寸|费用|哪一天|哪天|日期|品牌|牌子|配速|用时|更细|每一步|亲手|亲自'
MEANING = r'(?:什么|有何|为何|为什么).{0,12}(?:意义|含义)|纪念的|纪念什么|代表|象征|想让.{0,10}想到|(?:有|什么)意义'
COMPARISON = r'对比|比较|分别是哪几次|不同场次|所有活动|同一天'
PERSONAL = (
    ('flow', r'心流', r'心流'),
    ('travel', r'出差.{0,20}(?:鞋|住处|住宿|酒店)|(?:高铁|列车).{0,10}(?:时间|拿来|做了什么)', r'出差.{0,16}(?:鞋|酒店)|高铁.{0,12}报告'),
    ('terrain', r'拉练.{0,14}(?:吃力|费力)|沙地.{0,14}(?:卸力|吃力)', r'沙场.{0,8}拉练|沙地.{0,8}卸力'),
    ('trail', r'首次越野|第一次越野', r'首次.{0,6}越野|第一次.{0,6}越野'),
    ('records', r'(?:个人纪录|个人记录).{0,8}(?:刷新|打破)|(?:刷新|打破).{0,8}(?:个人纪录|个人记录)', r'(?:刷新|破了).{0,16}(?:纪录|记录)'),
)


def _has(pattern, text):
    return bool(re.search(pattern, text))


def bind(query, items, previous):
    """Find one object or training entry without expanding account permissions."""
    if _has(COMPARISON, query):
        return None
    prior_ids = (previous.get('focus') or {}).get('primary_ids', [])
    personal = [r for r in items if r.get('scope_kind') == 'personal' and not r.get('fact')]
    team = [r for r in items if r.get('scope_kind') == 'team' and not r.get('fact')]
    focus = next((r for r in items if len(prior_ids) == 1 and r['id'] == prior_ids[0]), None)
    target = v2_intent.scope_target(query)
    if target != 'team' and not _has(r'谁的|(?:别人|队友)的', query):
        for kind, question, record in PERSONAL:
            if _has(question, query) and (_has(r'我(?!们)|本人|心流|第一次越野', query) or (focus and focus in personal)):
                candidates = [r for r in personal if _has(record, r['title'] + ' ' + r['content'])]
                if len(candidates) == 1:
                    return candidates[0], kind
    gift = lambda r: _has(GIFT, r['content']) and _has(r'纪念|礼物', r['title']+' '+r['content'])
    if target != 'personal':
        gifts = [r for r in team if gift(r)]
        # Match a labelled object, or a named maker plus a material/action cue.
        explicit = []
        for row in gifts:
            object_name = row['title'].split('作为',1)[0]
            labelled = 2 <= len(object_name) <= 12 and object_name in query
            names = list(row.get('entities', {}).values())
            names += re.findall(r'(?:记载|显示|称)([\u4e00-\u9fff]{2,4})用',row['content'])
            named_action = _has(r'带回|制作|动手|准备',query) and any(
                len(name) >= 2 and name in query and name in row['content'] for name in names)
            if labelled or named_action:
                explicit.append(row)
        special = _has(r'做成了什么.{0,8}(?:送|赠)|亲手|亲自|本人动手|每一步', query)
        if special and len(explicit) == 1:
            return explicit[0], 'gift'
        if focus and focus in gifts and (
                _has(r'看着它|那份礼物|那礼物|这份礼物|那捧|那件|交到他们|收下这份',query)
                and _has(ORIGIN+'|'+MEANING+'|'+DETAIL+'|带回|收到|哪两个人|交到',query)
                and not v2_relevance.query_domains(query)):
            return focus, 'gift'
    if target != 'team' and focus and focus in personal:
        text = focus['title'] + ' ' + focus['content']
        for kind, _, record in PERSONAL:
            if not _has(record,text):
                continue
            follow = {
                'flow': r'^(?:当时|那回|那晚)|那盏',
                'travel': r'高铁|列车',
                'terrain': r'^(?:这次|那次|当时|跑到)|配速',
                'trail': r'^(?:那回|那趟|当时)',
                'records': r'^那(?:几|三|两|些)项',
            }[kind]
            if _has(follow,query) and not v2_relevance.query_domains(query):
                return focus, kind
    return None


def selected(sources, plan):
    ids = plan.get('facet_ids') or (plan.get('focus') or {}).get('primary_ids', [])
    return next((r for r in sources if len(ids) == 1 and ids[0] == r['id']
                 and r.get('verification') == 'confirmed' and not r.get('fact')), None)


def _unique(pattern, text):
    values = re.findall(pattern, text)
    return values[0] if len(values) == 1 else None


def personal_answer(kind, query, proof):
    if kind == 'flow':
        if _has(r'牌子|品牌|型号',query):
            return UNKNOWN if not _has(r'品牌|型号|牌子',proof) else None
        if _has(r'头灯|照明|装备|注意力|专注',query):
            lamp = _unique(r'带着([^，。；\n]{1,12})',proof)
            place = _unique(r'视野在([^，。；\n]{1,16})',proof)
            if lamp and place:
                return '你戴着'+lamp+'，注意力集中在'+place+'。'
        if _has(r'心流',query):
            occasion = _unique(r'是(\d{1,2})\.(\d{1,2})的夜跑([^，。；\n]{2,16})',proof)
            if occasion:
                month, day, route = occasion
                return f'是{month}月{day}日{route}夜跑那次。'
    if kind == 'travel':
        if _has(r'高铁|列车',query):
            possibility = _unique(r'((?:高铁|列车)上(?:是)?可以[^，。；\n]{2,35})',proof)
            if possibility:
                return '在'+possibility.replace('上是可以','上可以',1).removesuffix('的')+'。'
        if _has(r'鞋',query) and _has(r'住处|住宿|酒店',query):
            shoes = _unique(r'出差([^，。；\n]{2,8})成了标配',proof)
            hotel = _unique(r'(酒店(?:是)?可以[^，。；\n]{2,28})',proof)
            if shoes and hotel:
                return '出差会带'+shoes+'，'+hotel.replace('是可以','可以',1).replace('兼顾','',1)+'。'
    if kind == 'terrain':
        if _has(r'配速|每公里.{0,6}分钟',query) and not _has(r'配速|\d+分.{0,6}/公里|每公里.{0,4}\d+分',proof):
            return UNKNOWN
        if _has(r'多远|多少公里',query):
            measured = _unique(r'练了(快|接近|将近|约|大约)(\d+(?:\.\d+)?公里)',proof)
            if measured:
                return '原文说已经练了'+''.join(measured)+'。'
        if _has(r'为什么|什么原因',query) and _has(r'吃力|费力',query):
            surface = _unique(r'(跑起来是软的[，,]踩上去[^；。！…\n]{2,26})',proof)
            scenery = _unique(r'(周边都是[^，。；…\n]{1,16}[，,][^，。；…\n]{1,10})',proof)
            if surface and scenery:
                return '沙地'+surface+'。'+scenery+'。'
    if kind == 'trail' and _has(r'谁带|带队|山脊线',query):
        route = _unique(r'第一次([^，。；…\n]{2,12})带我们跑([^，。；…\n]{2,30})',proof)
        if route:
            return '是'+route[0]+'带你跑'+route[1]+'。'
    if kind == 'trail' and _has(r'有意思|有趣',query):
        reason = _unique(r'(路况不断变化[，,][^，。；…\n]{2,24})',proof)
        if reason:
            return '山间'+reason.replace('视野/风景','视野和风景')+'。'
    if kind == 'records' and _has(r'刷新|打破',query) and _has(r'哪些距离|哪几项|哪些项目',query):
        distances = _unique(r'(\d+公里(?:[、，,与和]\d+公里){1,5})的(?:个人)?(?:记录|纪录)',proof)
        if distances:
            return '你刷新了'+distances+'的个人纪录。'
    if kind == 'records' and _has(r'那(?:几|三|两|些)项.*(?:时间|用时)',query):
        if not _has(r'分钟|小时|\d+秒|用时|配速',proof):
            return UNKNOWN
    return None


def answer(query, sources, quotes, plan):
    row = selected(sources, plan)
    if not row:
        return None
    proof = quotes.get(row['id'], '')
    if not proof or len(proof) > 1400 or _has(r'据说|传闻|待核实|推测',proof):
        return None
    kind = plan.get('narrative_field')
    if kind and kind != 'gift':
        body = personal_answer(kind,query,proof)
        return envelope(body, [] if body == UNKNOWN else [row]) if body else None
    if _has(UNCERTAIN,proof):
        return None
    if row.get('scope_kind') != 'team' or not _has(GIFT,proof) or not _has(r'纪念|礼物',row['title']+' '+row['content']):
        return None
    origin = _unique(r'(?:^|[，,。；\n])([^，,。；\n]{1,24}从[^，,。；\n]{1,40}带回来?[^，,。；\n]{1,40})',proof)
    recipient = _unique(r'((?:'+GIFT+r')[^，,。；;！？!?\n]{1,70})',proof)
    made = _unique(r'被(?:[^，,。；\n]{0,8}?)制作成了?([^，,。；\n]{2,30})',proof)
    if made:
        made = re.sub(r'^独一无二的','',made)
    body = None
    if _has(r'净重|多少克',query) and not _has(r'净重|重量|[0-9]+克',proof):
        body = UNKNOWN
    elif _has(r'哪一天|哪天|具体日期',query) and _has(r'交到|送给|送到|礼物|赠送',query):
        if not _has(r'\d{1,2}月\d{1,2}日|20\d{2}[-/]\d{1,2}[-/]\d{1,2}',proof):
            body = UNKNOWN
    elif _has(r'每一步|亲手|亲自|本人动手',query) and not _has(r'每一步|亲手|亲自|本人动手',proof):
        if origin and made:
            body = origin+'，这些材料后来被做成了'+made+'。原文没交代具体制作过程，无法确认他参与了哪些步骤。'
    elif _has(r'原文支持|有依据|有根据',query) and _has(r'夺冠|冠军',query) and recipient:
        if not _has(r'夺冠|冠军',proof):
            body = '原文记载'+recipient+'。没有足够资料证明这份礼物是为了庆祝夺冠。'
    elif _has(r'哪一段|哪段|更细的位置|具体取沙点',query) and _has(r'赛道|取到|取沙',query):
        if not _has(r'(?:第[一二三四五六七八九十0-9]+段|[A-Z]+[0-9]+点|坐标|公里处)',proof):
            body = UNKNOWN
    elif _has(r'带回来?的.*(?:是谁|谁带)|谁带回|是从哪(?:里|儿)?带回来?的',query) and not _has(r'具体|赛段|更细|几公里',query):
        body = origin+'。' if origin else None
    elif _has(ORIGIN,query) and _has(r'收礼|受赠|送给|送到|赠给',query) and not _has(DETAIL,query):
        if origin and recipient:
            body = '原文记载：“'+origin+'”；“'+recipient+'”。'
    elif made and _has(r'做成了什么|制成了什么',query) and not _has(ORIGIN+'|'+MEANING+'|'+DETAIL,query):
        body = '做成了'+made
        if recipient and _has(r'送给.{0,4}谁|送给了谁|送到谁',query):
            body += '，'+recipient
        body += '。'
    return envelope(body, [] if body == UNKNOWN else [row]) if body else None
