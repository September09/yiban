"""Question selectors and arithmetic over authorized v2 facts, never model guesses.

Only bounded intent labels are retained between turns. Record contents and prior
answers are always read again by the caller from the current ACL snapshot.
"""
import re
from collections import defaultdict
from decimal import Decimal

UNKNOWN = '目前我能查看的记忆里，还没有足够资料回答这个问题，我暂时无法确认。'


def term_number(text):
    match = re.search(r'第([一二三四五六七八九十零\d]+)届|首届', text)
    if not match:
        return None
    value = match[1] or '一'
    if value.isdigit():
        return int(value)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    if '十' in value:
        left, right = value.split('十', 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value)


def role_names(query, items):
    names = {r['fact']['value'] for r in items if (r.get('fact') or {}).get('predicate') == 'holds_role'}
    # Longer labels occupy their own spans: 执行班长 must not also mean 班长.
    names.update(('执行班长', '班长'))
    pattern = '|'.join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n)))
    return sorted(set(re.findall(pattern, query))) if pattern else []


def request(query, items, previous=None):
    roles = role_names(query, items)
    # A person/committee doing something is not a request for its office holders.
    action=bool(re.search(r'如何|怎么|怎样|为什么|过程|设计|画图|讨论|修改|定稿|制作|排练|讲授|演讲|发言|筹备',query))
    office_question=bool(re.search(r'任职|职务|现任|谁担任|由谁担任|谁是|(?:班委|名单).{0,8}(?:谁|哪些|成员|组成)|(?:谁|哪些).{0,8}班委',query))
    if (roles and (not action or office_question)) or office_question or (
            '班委' in query and not action and re.search(r'谁|名单|哪几|成员|组成|第.{1,3}届|首届',query)):
        return {'kind': 'roles', 'roles': roles}
    book_context = bool(re.search(r'捐书|图书|书|本', query))
    non_count = bool(re.search(r'运单|快递|物流|单号|编号|价格|费用|多少钱|书名|作者', query))
    count_intent = bool(re.search(r'多少本|几本|书.{0,16}(?:多少|数量|合计|总共|加起来)|多少.{0,8}书|(?:签收|送达|接收).{0,12}\d+(?:多|余)?本', query))
    if book_context and count_intent and not non_count:
        return {'kind': 'quantities'}
    if re.search(r'比分|胜负|篮球|对阵|个人.{0,8}(?:得|分)|(?:得了|拿了|贡献了)多少分|(?:\d+[A-Za-z]+|那场).{0,18}(?:打成|输了几分|分差|\d+比\d+)', query):
        return {'kind': 'scores'}
    if previous and previous.get('kind') == 'quantities' and not non_count and re.search(r'新书|二手书|总数|合计|加总|总共|加起来|签收|送达', query):
        return {'kind': 'quantities'}
    if not previous or not re.match(r'^(?:那|那么|他|她|它|这|当时|还有|具体|所以|也就是说)', query):
        return None
    kind = previous.get('kind')
    if kind == 'roles' and term_number(query) is not None:
        return {'kind': kind, 'roles': previous.get('roles', [])}
    if kind == 'scores' and re.search(r'场|比分|胜负|\d+[A-Za-z]', query):
        return {'kind': kind}
    if kind == 'quantities' and not non_count and re.search(r'书|本|合计|加总|签收|送达', query):
        return {'kind': kind}
    return None


def label(row):
    return row['scope_name'] + ' · ' + (row['event']['title'] + ' · ' if row.get('event') else '')


def state(row):
    return {'pending': '【待核实】', 'disputed': '【有争议】', 'confirmed': ''}[row['verification']]


def render(rows, fact_text, note=''):
    return note + '\n'.join(fact_text(r) + state(r) for r in rows)


def insufficient(detail='', sources=()):
    return UNKNOWN, list(sources), {'kind': 'insufficient', 'detail': detail}


def roles_answer(query, facts, roles, today, fact_text):
    rows = [r for r in facts if r['fact']['predicate'] == 'holds_role'
            and (not roles or r['fact']['value'] in roles)]
    term = term_number(query)
    note = ''
    if term is not None:
        rows = [r for r in rows if term_number(r['fact']['term']) == term]
    elif re.search(r'现在|现任|目前|当前', query) or re.search(r'20\d{2}[年-]', query):
        from yiban.memory.v2_query import requested_dates
        dates = requested_dates(query)
        as_of = dates[0] if dates else today.isoformat()
        current = [r for r in rows if r['fact']['effective']['precision'] == 'range'
                   and r['fact']['effective']['basis'] == 'explicit'
                   and r['fact']['effective']['start'] <= as_of
                   and (not r['fact']['effective']['end'] or r['fact']['effective']['end'] >= as_of)]
        if current:
            rows = current
        else:
            note = '资料缺少足以确认现任的有效时间，以下按已记录届次列出，不直接把最新一条认定为现任。\n'
    if not rows:
        return insufficient()
    rows.sort(key=lambda r: (term_number(r['fact']['term']) or 0, r['fact']['value'], r['id']))
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r['fact']['term'], r['fact']['value'])].append(r)
    lines = [term + role + '是' + '、'.join(dict.fromkeys(r['entities'].get(r['fact']['subject_key'], '未匹配主体') for r in group)) + '。' + ''.join(sorted({state(r) for r in group}))
             for (term, role), group in grouped.items()]
    return note + '\n'.join(lines), rows, {'kind': 'structured_facts'}


def mentioned_opponents(query, rows):
    result = []
    for r in rows:
        name = r['entities'].get(r['fact']['object_key'], '')
        # Class suffixes are optional in everyday questions (e.g. 24D / 24D班).
        aliases = {name, re.sub(r'(?:班|队)$', '', name)} - {''}
        if any(re.search(r'(?<![A-Za-z0-9])' + re.escape(n) + r'(?![A-Za-z0-9])', query, re.I)
               for n in aliases):
            result.append(r)
    return result


def scores_answer(query, facts, today, fact_text, date_matches):
    rows = [r for r in facts if r['fact']['predicate'] == 'score']
    named = mentioned_opponents(query, rows)
    if named:
        rows = named
    elif re.search(r'\d+[A-Za-z]+', query):
        return insufficient()  # An unrecorded opponent must not return every game.
    rows = [r for r in rows if not r.get('event') or date_matches(r['event']['time'], query, today) is not False]
    if not rows:
        return insufficient()
    if re.search(r'个人.{0,8}(得|分)|(?:得了|拿了|贡献了)多少分|谁得了多少分', query):
        # Team totals cannot answer an individual player's score.
        individual = [r for r in rows if r.get('entity_kinds',{}).get(r['fact']['subject_key']) == 'person'
                      and r['entities'].get(r['fact']['subject_key'], '') in query]
        if not individual:
            return insufficient('记忆里提到了这场比赛，但没有记载你询问的个人得分，我暂时无法确认。', rows)
        rows = individual
    if (re.search(r'比分|几分|分差|打成|\d+比\d+', query) or all(r['fact']['value']['result']=='unknown' for r in rows)) and any(r['fact']['value']['own'] is None or r['fact']['value']['opponent'] is None for r in rows):
        if not any(r['fact']['value']['result'] != 'unknown' for r in rows):
            return insufficient('这场比赛未记载完整比分，不能计算分差或套用其他场次。', rows)
        # A missing total does not erase a reviewed win/loss. Render each game's
        # own known fields, explicitly retaining missing scores; never borrow
        # another opponent's totals or calculate a margin from absent operands.
        body = render(rows, fact_text)
        if re.search(r'分差|输了几分|赢了几分|几分之差', query):
            body += '\n完整比分未记载，分差暂时无法确认。'
        return body, rows, {'kind': 'structured_partial', 'missing': ['complete_score']}
    return render(rows, fact_text), rows, {'kind': 'structured_facts'}


def quantity_stage(text):
    for pattern, name in [(r'签收', '签收'), (r'送达|交付|实际收到|接收', '送达'),
                          (r'筛选|精选|优质', '筛选'), (r'征集|募集|筹集', '征集')]:
        if re.search(pattern, text):
            return name
    return ''


def category(metric):
    if '二手' in metric:
        return '二手书'
    if '新书' in metric:
        return '新书'
    return ''


def sum_books(rows):
    """Add only disjoint new/used components of the same scope/event/subject/stage.

    Duplicate reports of the same component are deduplicated; conflicting values,
    approximate metrics and totals never become additional summands.
    """
    groups = defaultdict(list)
    for r in rows:
        f = r['fact'];stage = quantity_stage(f['metric'])
        if (not stage or not r.get('event_key') or not category(f['metric'])
                or re.search(r'合计|总计|总数|共计|约|余|多|近', f['metric'] + r['title'])):
            continue
        groups[(r['scope_key'], r['event_key'], f['subject_key'], f['unit'], stage)].append(r)
    results = [];sources = [];lines = []
    for key, members in groups.items():
        components = {k: [r for r in members if category(r['fact']['metric']) == k] for k in ('新书', '二手书')}
        if any(not part or len({Decimal(str(r['fact']['value'])) for r in part}) != 1 for part in components.values()):
            continue
        parts = [sorted(part, key=lambda r: r['id'])[0] for part in components.values()]
        total = sum(Decimal(str(r['fact']['value'])) for r in parts)
        fmt = lambda n: format(n, 'f').rstrip('0').rstrip('.') if '.' in format(n, 'f') else format(n, 'f')
        expression = '+'.join(fmt(Decimal(str(r['fact']['value']))) for r in parts) + '=' + fmt(total) + key[3]
        qualifier = '；不代表签收数量' if key[4] != '签收' else ''
        lines.append(expression + '（' + key[4] + '后新书与二手书合计' + qualifier + '）。' + ''.join(sorted({state(r) for r in parts})))
        sources.extend(parts)
        results.append({'scope_key': key[0], 'event_key': key[1], 'subject_key': key[2], 'unit': key[3],
                        'metric': key[4] + '后合计', 'value': float(total),
                        'operands': [{'record_id': r['id'], 'value': r['fact']['value']} for r in parts]})
    if not results:
        return insufficient('目前我能查看的记忆里，缺少同一活动、同一口径且互不重复的分项，暂时无法确认合计数量。', rows)
    return '\n'.join(lines), sources, {'kind': 'quantity_sum', 'groups': results}


def quantities_answer(query, facts, today, fact_text, date_matches):
    rows = [r for r in facts if r['fact']['predicate'] == 'quantity'
            and (r['fact']['unit'] == '本' or '书' in r['fact']['metric'])]
    rows = [r for r in rows if not r.get('event') or date_matches(r['event']['time'], query, today) is not False]
    stage = quantity_stage(query)
    if stage:
        related = rows
        rows = [r for r in rows if quantity_stage(r['fact']['metric']) == stage]
        if not rows:
            return insufficient('记忆里有捐书活动的相关记录，但没有记载' + stage + '的准确数量，我暂时无法确认。', related) if related else insufficient()
    if re.search(r'一共|合计|加总|总共|加起来|总数|总计|总量', query):
        return sum_books(rows)
    wanted = [k for k in ('新书', '二手书') if k in query]
    if len(wanted) == 1:
        rows = [r for r in rows if category(r['fact']['metric']) == wanted[0]]
    if not rows:
        return insufficient()
    return render(rows, fact_text), rows, {'kind': 'structured_facts'}
