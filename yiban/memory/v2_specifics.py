"""Resolve specific narrative attributes against current, authorized evidence."""
import re

from yiban.memory import v2_intent, v2_relevance
from yiban.memory.v2_facts import UNKNOWN
from yiban.memory.v2_grounding import envelope


# Subject labels and attribute signatures aid routing only. Values always come
# from the authorized original excerpt, never from these patterns or old turns.
FIELDS = (
    ('rehearsal_speed', r'(?:伴奏|音乐|排练).{0,18}(?:放慢|倍速|原速|几成)', r'排练|练舞|彩排'),
    ('motif_origin', r'(?:眉心|额头|额间|额上).{0,15}(?:花纹|纹样|图案).{0,24}(?:灵感|来源|来自)', r'(?:形象|造型|角色|吉祥物).{0,90}(?:纹|图案)'),
    ('fabric', r'面料.{0,10}(?:成分|材质)|(?:衣服|外袍|服装).{0,10}(?:成分表|面料)', r'外袍|服装|面料|书卷袍'),
    ('meal_location', r'(?:在哪|哪里|哪儿|何处).{0,18}(?:吃(?:的)?饭|聚餐|小聚|用餐)|(?:聚餐|小聚|用餐).{0,18}(?:在哪|哪里|哪儿|何处)', r'小聚|聚餐|用餐|饭馆'),
    ('route_direction', r'(?:岔路|岔口|拐点|通道).{0,12}(?:哪边|哪侧|哪个方向|往左|往右)', r'路线选择|岔路|拐点|分岔'),
    ('route_confirmation', r'^(?:那|这)条(?:路|路线).{0,25}(?:绕错|走对|走错|放心|松口气)', r'路线选择|岔路|拐点|分岔'),
    ('arrival_items', r'(?:集合|到场|签到).{0,18}(?:发给|领到|收到|送给).{0,20}(?:小物|纪念品|礼物|物品)', r'纪念品|签到礼|伴手礼'),
    ('preparation_reason', r'(?:为什么|什么原因).{0,15}(?:出发|赛前).{0,10}(?:手忙脚乱|匆忙|来不及)', r'出发准备|赛前准备|备装|强制装备'),
    ('checkpoint', r'(?:哪个|哪一处|哪一个).{0,5}(?:补给点|打卡点|检查点).{0,24}(?:打气|鼓励|鼓舞)', r'陪跑|加油|受到.{0,12}鼓励'),
    ('host', r'^(?:那|这)场.{0,20}(?:谁|哪.{0,3}位|哪.{0,3}人).{0,12}主持', r'主持'),
    ('decision_rule', r'(?:重大事项|重大决策|大决定).{0,18}(?:怎么定|如何定|谁来定|怎么表决|如何表决)', r'重大事项.{0,14}(?:投票|表决)'),
    ('recognition_reason', r'(?:为什么|什么原因).{0,18}(?:颁|发|授|给).{0,12}奖', r'贡献奖|特别表彰|特别奖|荣誉奖'),
)


def select(query, items, previous):
    """Find a unique attribute in scope before narrowing to an event overview."""
    if re.search(r'分别|各自|对比|比较|同一天|上一届|下一届', query):
        return None
    focus = previous.get('focus') or {}
    primary = set(focus.get('primary_ids', []))
    related = set(focus.get('ids', []))
    scope = v2_intent.scope_target(query)
    year_dates = re.findall(r'20\d{2}-\d{2}-\d{2}|20\d{2}年\d{1,2}月\d{1,2}日', query)
    if year_dates:
        return None  # Leave date-bound requests to the existing disambiguator.
    requested_dates = set(re.findall(r'\d{1,2}月\d{1,2}日', query))
    ordinal = re.search(r'第[一二三四五六七八九十\d]+天', query)
    for field, request, label in FIELDS:
        if not re.search(request, query):
            continue
        eligible = []
        for row in items:
            if (row.get('fact') or {}).get('predicate') not in (None, 'statement') or row.get('verification') != 'confirmed':
                continue
            if scope and row['scope_kind'] != scope:
                continue
            if field in {'route_direction', 'route_confirmation', 'preparation_reason', 'checkpoint'} and row['scope_kind'] != 'personal':
                continue
            if field in {'recognition_reason', 'arrival_items', 'meal_location'} and row['scope_kind'] != 'team':
                continue
            text = row['title'] + ' ' + row['content']
            event = row.get('event') or {}
            event_label = event.get('title', '')
            if not re.search(label, text):
                continue
            if requested_dates and not requested_dates & set(re.findall(r'\d{1,2}月\d{1,2}日', text)):
                continue
            if ordinal and ordinal[0] not in event_label + ' ' + text:
                continue
            eligible.append(row)

        if field in {'host', 'route_confirmation'}:
            pool = primary if field == 'route_confirmation' else related
            eligible = [r for r in eligible if r['id'] in pool]
        elif field == 'rehearsal_speed' and not re.search(r'啦啦|舞蹈|彩排|排练', query):
            eligible = [r for r in eligible if r['id'] in related]
        elif field == 'decision_rule':
            if re.search(r'周边|文创', query):
                eligible = [r for r in eligible if '文创' in (r.get('event') or {}).get('title', '') + ' ' + r['content']]
        elif field in {'arrival_items', 'meal_location', 'recognition_reason', 'motif_origin', 'fabric'}:
            # Distinguish subject anchors from generic field vocabulary. A typed
            # request for a different subject cannot steal the previous answer.
            noisy = {'奖项', '贡献', '给他', '饭馆', '聚餐', '小聚', '用餐', '纪念', '面料', '图案', '班徽', '主持', '饭店', '团队', '户外', '记录', '有同', '哪儿'}
            query_terms = v2_relevance.terms(query) - noisy
            eligible = [r for r in eligible if query_terms & (
                v2_relevance.terms(r['title'] + ' ' + (r.get('event') or {}).get('title', '') + ' ' + r['content']) - noisy)]
        if len(eligible) == 1:
            return eligible[0], field
        return None
    return None


def _one(pattern, excerpt):
    values = list(dict.fromkeys(re.findall(pattern, excerpt)))
    return values[0] if len(values) == 1 else None


def _answer(field, excerpt):
    if field == 'fabric':
        if not re.search(r'面料成分|(?:面料|材质|材料)(?:是|为|采用)|(?:棉|纤维|涤纶|麻|丝)\s*\d+\s*%', excerpt):
            return UNKNOWN
    elif field == 'rehearsal_speed':
        value = _one(r'(?:从|以|按|调到)\s*(\d+(?:\.\d+)?)\s*倍速[^。；\n]{0,12}(?:开始|慢练)', excerpt)
        if value:
            return f'最开始按原速的{value}倍练。'
    elif field == 'motif_origin':
        motif = _one(r'(?:眉心|额头|额间|额上)([^，。；\n]{2,16})[，,](?:设计)?灵感(?:来自|源于)([^，。；\n]{2,24})', excerpt)
        if motif:
            return re.sub(r'^一点', '', motif[0]) + '的灵感来自' + motif[1] + '。'
    elif field == 'meal_location':
        restaurant = _one(r'(?:于是)?在([^。；，：\n]{2,25})(?:寻了|找了|选了)(一家[^。；，：\n]{2,12})(?:聚餐|吃饭|小聚|坐下来)', excerpt)
        if restaurant:
            return '在' + restaurant[0] + '的' + restaurant[1] + '小聚。'
    elif field == 'route_direction':
        side = _one(r'(?:我|叙述者)(?:选的|选择了?|走了?)\s*([左右])(?:边|侧)', excerpt)
        if side:
            return '你选了' + side + '边。'
    elif field == 'route_confirmation':
        action = _one(r'((?:翻过|越过|转过)[^，。；\n]{1,18}[，,]看到[^。；，\n]{4,40}?)(?:我)?才(?:放心|松口气|松了口气)', excerpt)
        if action:
            return action.replace('我的方向', '你这边') + '，你才松了口气。'
    elif field == 'arrival_items':
        given = _one(r'送上[^。；\n]{0,28}(?:纪念品|小礼物)[—–：: ]+([^，。；\n]{3,72})', excerpt)
        if given:
            return '发的是' + re.sub(r'"([^"]+)"', r'“\1”', given) + '。'
    elif field == 'preparation_reason':
        pending = _one(r'([^，。；\n]{2,22}?(?:装备|物资)(?:都)?还没准备好)', excerpt)
        reason = _one(r'((?:低估了|没料到)[^，。；\n]{2,20})', excerpt)
        if pending and reason:
            # The check-in clause describes the competition starting early. It
            # cannot establish when somebody prepared equipment or warmed up.
            pending = re.sub(r'^(?:我的|我)', '', pending)
            return '你的' + pending + '，你也' + reason + '。'
    elif field == 'checkpoint':
        point = _one(r'(进入|经过|通过|到达)\s*([A-Za-z]{1,5}\s*\d{1,3})\s*(?:号点)?后[^。；\n]{0,55}(?:鼓励|打气|加油)', excerpt)
        if point:
            return '是在' + point[0] + ' ' + point[1].upper().replace(' ', '') + ' 后。'
    elif field == 'host':
        names = _one(r'主持人[：:\s]*([\u4e00-\u9fff]{2,5}(?:[、与和及][\u4e00-\u9fff]{2,5})+?)(?:同学|的|[，。；\n])', excerpt)
        if names:
            return '开场主持是' + names.removesuffix('同学') + '。'
    elif field == 'decision_rule':
        rule = _one(r'(重大事项(?:由)?全员(?:投票|表决)(?:表决)?)', excerpt)
        if rule and not re.search(r'未定|草案|拟议|并非|不是|不再', excerpt):
            return rule + '。'
    elif field == 'recognition_reason':
        award = _one(r'设立([^，。；\n]{2,25}奖)[，,]表彰([^，。；\n]{2,24})[。；]', excerpt)
        reason = _one(r'感谢(?:其|他|她)([^。；\n]{4,90})[。；]', excerpt)
        if award and reason:
            return award[1] + '获得' + award[0] + '，因为' + reason + '。'
    return None


def answer(field, sources, quotes, record_ids):
    if not field or len(record_ids) != 1:
        return None
    row = next((r for r in sources if r['id'] == record_ids[0] and r.get('verification') == 'confirmed'), None)
    if not row:
        return None
    excerpt = quotes.get(row['id'], '')
    if not excerpt or re.search(r'传闻|据说|待核实|推测', excerpt):
        return None
    result = _answer(field, excerpt)
    return envelope(result, [] if result == UNKNOWN else [row]) if result else None
