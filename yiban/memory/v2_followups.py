"""Bind narrow narrative follow-ups to records in the current readable snapshot."""
import re

from yiban.memory import v2_intent, v2_relevance
from yiban.memory.v2_facts import UNKNOWN
from yiban.memory.v2_grounding import envelope

NAME = r'[\u4e00-\u9fff]{2,5}'
UNCERTAIN = r'据说|传闻|待核实|推测'
SCORE_LINE = r'分数线|合格线|及格线|晋级线|评分门槛'
MISSING_FIELDS = (
    (SCORE_LINE, r'(?:分数线|合格线|及格线|晋级线|门槛)[^，。；\n]{0,16}\d|超过[^，。；\n]{0,10}\d+分'),
    (r'(?:软件|应用|APP).{0,18}(?:打卡|记录)|(?:打卡|上传).{0,16}(?:软件|应用|APP)',
     r'(?:打卡|上传).{0,12}(?:软件|应用|APP)[是为：:]|(?:用|在)[^，。；\n]{2,16}(?:上打卡|上传打卡)'),
    (r'几分几秒|精确到秒的.{0,5}用时',
     r'\d+\s*(?:分(?:钟)?|[:：])\s*\d+\s*秒?'),
)


def _one(pattern, text):
    matches = list(dict.fromkeys(re.findall(pattern, text)))
    return matches[0] if len(matches) == 1 else None


def _meta(row):
    return row['title'] + ' ' + row['content']


def _event(row):
    return (row.get('event') or {}).get('title', '')


def _anchors(query, titles):
    """Score shared consecutive labels, avoiding isolated generic bigrams."""
    terms = v2_relevance.terms(query) - v2_relevance.terms(
        '亲自 确认 当时 这个 那个 班级 活动 比赛 训练 参赛 我们 名字 听见 团队 记录 队长 谁 个人 第三天 最后一天')
    return max((len(terms & v2_relevance.terms(t)) for t in titles), default=0)


def _pick(query, rows):
    ranked = [(_anchors(query, (_event(r), _meta(r))), r) for r in rows]
    best = max((score for score, _ in ranked), default=0)
    winners = [r for score, r in ranked if score == best and score > 0]
    return winners


def _bound(rows, pattern):
    return [r for r in rows if re.search(pattern, _meta(r))]


def _spec(field, matches, args=None):
    if len(matches) == 1:
        return field, matches, args


def _days(excerpt):
    """Read one explicitly paired two-day itinerary from its original excerpts."""
    lines = [s.strip() for s in excerpt.splitlines() if s.strip()]
    if len(lines) != 2:
        return []
    days = []
    for i, line in enumerate(lines):
        if i == 0:
            place = re.match(r'(?:第一天(?:的赛程)?(?:从|在)?\s*)?([\u4e00-\u9fff]{2,14})(?:[，,]|出发|起步|启程)', line)
        else:
            place = re.match(r'(?:次日|第二天)(?:的)?(?:赛程|路线)?从([\u4e00-\u9fff]{2,14})(?:出发|起步|启程)', line)
        distance = _one(r'(?<![\d.])(\d+(?:\.\d+)?)\s*公里', line)
        if not (place and distance):
            return []
        days.append((place[1], distance, line))
    return days


def _day_plan(query, focused, related, prior, read):
    other = re.search(r'另一天|另外一天|另一日', query)
    by_stairs = re.search(r'阶梯|台阶', query) and re.search(r'头一天|第一天|第二天|次日', query)
    if not (other or by_stairs):
        return None
    candidates = _bound(focused if other else related, r'(?:两段|两天|两程).{0,8}(?:路线|赛程)|(?:次日|第二天).{0,32}\d+(?:\.\d+)?公里')
    if len(candidates) > 12:
        return None
    versions = [(r, _days(read(r))) for r in candidates]
    found = [(r, parsed) for r, parsed in versions if parsed]
    if len(found) != 1:
        return None
    row, days = found[0]
    if other:
        anchor = prior.get('day_index')
        if anchor not in (0, 1) or row['id'] != prior.get('day_record'):
            return None
        return 'day_alternative', [row], 1-anchor
    stairs = [i for i, (_, _, line) in enumerate(days) if re.search(r'阶梯|台阶', line)]
    return _spec('day_stairs', [row], stairs[0]) if len(stairs) == 1 else None


def _series(row):
    event = row.get('event') or {}
    key = event.get('parent_key') or event.get('key')
    return (row.get('scope_key'), key) if key else None


def _supplementary_fields(query, personal, team, focused, target):
    # Match the requested facet before generic event or title ranking.
    if target != 'team' and re.search(r'除了|除去|之外', query) and re.search(
            r'(?:日常|其他|哪些).{0,8}(?:空间|场地|场所)', query) and re.search(r'跑步|锻炼|训练', query):
        return _spec('everyday_places', _bound(personal, r'训练.{0,10}(?:地点|环境)|地点包括'))
    if target != 'team' and re.search(r'犹豫|纠结', query) and re.search(
            r'(?:可以|本可|能).{0,12}(?:干|做|拿来|用来)', query):
        return _spec('time_options', _bound(personal, r'时间管理.{0,8}方法|减少犹豫|能.*碎片时间'))
    if target != 'team' and '跑步' in query and re.search(r'(?:跟|和|与).{0,8}谁.{0,6}对话', query):
        return _spec('running_reflection', _bound(personal, r'内心.{0,4}对话|想法源泉'))
    if re.search(r'感悟|体会|领悟', query) and re.search(r'(?:没|没有|缺乏).{0,6}条件', query):
        return _spec('create_conditions', _bound(focused, r'感悟|领悟|反思'))
    if target != 'team' and re.search(r'爬升', query) and re.search(r'哪.{0,3}榜|(?:班里|全班).{0,8}第一', query):
        return _spec('climbing_rank', _pick(query, _bound(personal, r'爬升.{0,14}(?:训练榜|榜单|榜第)')))
    if re.search(r'楼梯', query) and re.search(r'爬山|上山|去.{0,8}的山', query) and re.search(r'时间|多久|用时', query):
        return _spec('climbing_duration', _bound(focused, r'爬升.{0,14}(?:训练榜|榜单|榜第)'))

    if target != 'personal' and '单项奖' in query and re.search(r'校园总决赛|校级总决赛', query):
        head = re.split(r'[。？?；;]', query, maxsplit=1)[0]
        local = [r for r in team if any(name in head for name in r.get('entities',{}).values()
                                        if len(name)>1) and re.search(r'班级.{0,12}(?:突围|选拔)', _event(r))
                 and '奖' in r['title']]
        final = _bound(team, r'校园总决赛(?:冠军|亚军|季军|第[一二三四五六七八九十\d]+名)')
        pairs = [(a,b) for a in local for b in final if _series(a) and _series(a)==_series(b)]
        if len(pairs)==1:
            return 'award_stages', list(pairs[0]), None
    if target != 'personal' and re.search(r'(?:这|那)(?:份|篇|则).{0,6}预告', query) and re.search(
            r'命题|赛题|题目',query) and re.search(r'时间|教室|地点',query):
        series = {_series(r) for r in focused} - {None}
        if len(series)==1:
            candidates = [r for r in _bound(team, r'预告.{0,12}(?:时间|地点|题目|命题)') if _series(r) in series]
            return _spec('announcement_fields', candidates)
    if target != 'personal' and re.search(r'拍摄|取景',query) and re.search(r'什么方向|哪个方向|哪边',query):
        series = {_series(r) for r in focused} - {None}
        if len(series)==1 and re.search(r'萌宠|宠物|猫|狗',query):
            photos = [r for r in _bound(team, r'萌宠|宠物') if _series(r) in series and '人气奖' in r['title']]
            return _spec('photo_direction', photos)
    return None


def select(query, items, previous, read):
    """Choose reviewed fields or a bounded set of originals before coarse event narrowing."""
    rows = [r for r in items if r.get('verification') == 'confirmed'
            and (r.get('fact') or {}).get('predicate') in (None, 'statement')]
    prior = previous.get('focus') or {}
    primary = set(prior.get('primary_ids', ()))
    related_ids = set(prior.get('ids', ())) | primary
    focused = [r for r in rows if r['id'] in primary]
    related = [r for r in rows if r['id'] in related_ids]
    target = v2_intent.scope_target(query)
    if target != 'personal' and re.search(r'摄影|拍照', query) and re.search(
            r'多少幅|多少件|几幅|几件', query) and re.search(r'评委|评审', query):
        counts = [r for r in items if r.get('verification') == 'confirmed' and r.get('scope_kind') == 'team'
                  and (r.get('fact') or {}).get('predicate') == 'quantity']
        photo = _spec('photo_entries_and_judges', _bound(counts, r'摄影.{0,12}(?:收到|征集).{0,9}(?:投稿|作品)'))
        if photo:
            return photo
    personal = [r for r in rows if r.get('scope_kind') == 'personal']
    team = [r for r in rows if r.get('scope_kind') == 'team']

    referring_back = re.search(r'那天|当时|这个|那个|那场|这场|那次|这次|上述|刚才|刚说|他|她', query)
    for question, answer_marker in MISSING_FIELDS:
        if referring_back and related and re.search(question, query, re.I):
            # Require a currently accessible same-subject record and check all
            # its related excerpts before declaring any field unrecorded.
            if len(related) <= 12 and not any(re.search(answer_marker, read(r), re.I) for r in related):
                return 'missing', [], None

    fields = _supplementary_fields(query, personal, team, focused, target)
    if fields:
        return fields

    day = _day_plan(query, focused, related, prior, read)
    if day:
        return day

    if target != 'team' and re.search(r'谁.{0,9}(?:鼓励|肯定)|(?:鼓励|肯定).{0,8}(?:是谁|哪位)', query):
        return _spec('encourager', _pick(query, _bound(personal, r'拉练.{0,25}(?:鼓励|肯定)')))
    if target != 'team' and re.search(r'谁.{0,16}(?:返身|回头|返回|折回|回来).{0,7}接', query):
        return _spec('returning_runner', _pick(query, _bound(personal, r'(?:回来|返回|照应).{0,10}(?:队友|接|照应)')))
    if re.search(r'^(?:他|她).{0,24}(?:往回|回头|折回|返回|回去).{0,12}(?:找|接)', query):
        return _spec('returning_action', _bound(focused, r'(?:回来|返回|照应).{0,10}(?:队友|接|照应)'))
    if target != 'team' and re.search(r'平常|平时|通常', query) and re.search(r'哪.{0,5}(?:城|市)|哪儿|哪里', query):
        return _spec('training_city', _pick(query, _bound(personal, r'训练榜样|平时.{0,12}训练|通常.{0,12}训练')))
    if re.search(r'(?:关键|重点).{0,10}拉练.{0,25}(?:哪.{0,5}(?:城|市)|哪儿|哪里)', query):
        return _spec('meeting_city', _bound(focused, r'训练榜样|关键.{0,14}拉练'))
    if target != 'team' and re.search(r'听见|听到', query) and re.search(r'主持人.{0,5}(?:播报|报出|报什么)', query):
        contenders = _bound(personal, r'冲线|冲刺')
        if len(contenders) <= 12:
            return _spec('finish_announcement', [r for r in contenders
                if re.search(r'听(?:见|到)主持人(?:播报|报出|报)', read(r))])
    if target != 'team' and re.search(r'原本以为|原以为|本来以为', query) and re.search(r'遇雨|下雨|雨天', query) and '教练' in query:
        return _spec('rain_training', _pick(query, _bound(personal, r'爬楼训练|雨天.{0,8}训练')))

    quote = _one(r'[“「]([^”」]{2,32})[”」]', query)
    if quote and re.search(r'同一个人|同一位|是谁说|谁说的', query):
        return _spec('compare_speakers', [r for r in focused if quote in read(r)], quote)

    if target != 'personal' and re.search(r'哪(?:一)?支队.{0,12}(?:队长|带队)|(?:队长|带队).{0,12}哪(?:一)?支队', query):
        squads = _bound(team, r'(?:队长|领队)')
        if len(squads) <= 24:
            named = [r for r in squads
                     if (captain := _one(r'队长[：:]\s*('+NAME+r')', read(r))) and captain in query]
            if named:
                return _spec('captain_team', named)

    songs = list(dict.fromkeys(re.findall(r'《([^》]{2,32})》', query)))
    if target != 'personal' and 2 <= len(songs) <= 4 and re.search(r'唱|演唱', query) and '奖' in query:
        awards = [r for r in _bound(team, '奖') if _anchors(query, [_event(r)]) >= 2]
        if 1 <= len(awards) <= 24:
            matches = [[r for r in awards if '《'+song+'》' in read(r)] for song in songs]
            if all(len(m) == 1 for m in matches):
                chosen = [m[0] for m in matches]
                if len({r['id'] for r in chosen}) == len(songs):
                    return 'song_awards', chosen, songs
    if target != 'personal' and re.search(r'班级整体|集体|全班', query) and re.search(r'组织.{0,16}什么奖', query):
        parent_ids = {(r.get('event') or {}).get('parent_key') for r in focused} - {None}
        awards = _bound(team, r'组织奖')
        matches = [r for r in awards if (r.get('event') or {}).get('key') in parent_ids]
        return _spec('organization_award', matches or _pick(query, awards))

    if target != 'personal' and re.search(r'谁.{0,6}带队', query) and re.search(r'谁.{0,8}(?:专题)?分享', query):
        leaders = _pick(query, _bound(team, '带队'))
        talks = _pick(query, _bound(team, '分享'))
        if len(leaders) == len(talks) == 1 and leaders[0]['id'] != talks[0]['id'] and _event(leaders[0]) == _event(talks[0]):
            return 'visit_roles', leaders+talks, None
    if re.search(r'刚说|刚才|那场|这个', query) and re.search(r'等号|等同', query):
        return _spec('talk_equation', [r for r in related if re.search(r'专题|主题',_meta(r)) and '=' in read(r)])

    return None


def _response(field, text, row, arg):
    if field == 'everyday_places':
        extra = _one(r'(?:而且|还|也)(?:曾)?在([^。；…\n]{2,70}?)(?:都)?有我跑过', text)
        if extra:
            places = [p.strip().removeprefix('在') for p in re.split(r'[，,、]', extra) if p.strip()]
            if len(places)>=2:
                return '你还在'+'、'.join(places)+'跑过。'
    if field == 'time_options':
        options = _one(r'(?:刚好|足够)能([^。；…\n]{2,48}(?:[，,]能[^。；…\n]{2,16}){1,3})(?=[。；…\n]|$)',text)
        if options:
            return '那段时间本可以'+re.sub(r'[，,]能(?:去)?','、',options)+'。'
    if field == 'running_reflection':
        reflection = _one(r'(?:和|与)([^，。；\n]{2,16})的对话[，,]\s*([^，。；\n]{2,18})的源泉',text)
        if reflection:
            subject, benefit = reflection
            return '你把跑步当成和'+subject+'对话，也能从跑步中获得'+benefit.replace('/','和')+'。'
    if field == 'create_conditions':
        insight = _one(r'(没条件创造条件)的(?:决心|想法)',text)
        if insight:
            return '你的想法是，没条件就创造条件。'
    if field == 'climbing_rank':
        board = _one(r'(?:爬升|爬高)(?:到达|达到|排到)([^。；，、\n]{1,18}榜)第一',text)
        if board:
            return '原文写的是“'+board+'第一”。这条记录无法确认榜单的范围，也无法据此确认班级排名。'
    if field == 'climbing_duration':
        stairs = _one(r'楼梯一爬就是([^，。；\n]{2,16})',text)
        hill = _one(r'山一去就是([^，。；\n]{2,16})',text)
        if stairs and hill:
            return '爬小区楼梯一爬就是'+stairs+'，去县城的山一去就是'+hill+'。'
    if field == 'announcement_fields':
        when = _one(r'时间[：:]\s*([^*\n]{6,36})',text)
        room = _one(r'地点[：:]\s*([^*\n]{3,30})',text)
        topic = _one(r'[“「]([^”」]{3,35})[”」]\s*(?:的)?(?:创新)?命题',text)
        if when and room and topic:
            return '预告安排在'+when.strip()+'，地点是'+room.strip()+'，命题为“'+topic+'”。'
    if field == 'photo_entries_and_judges':
        count = _one(r'(?:共)?收到\s*(\d+)\s*幅[^。；\n]{0,25}投稿',text)
        judges = _one(r'邀请到[^，。；\n]{0,24}?级\s*('+NAME+r')(?:学长|学姐|老师)?[与和、及](?:.*?级)?\s*('+NAME+r')(?:学长|学姐|老师)?担任评委',text)
        if count and judges:
            names = [re.sub(r'(?:学长|学姐|老师)$','',name) for name in judges]
            return '一共收到'+count+'幅投稿，评委是'+'和'.join(names)+'。'
    if field == 'photo_direction':
        subject = row.get('entities',{}).get((row.get('fact') or {}).get('subject_key'))
        if subject:
            parts = re.split(r'\**创作者[：:]\**\s*',text)
            matching = [p for p in parts[1:] if re.match(re.escape(subject)+r'(?=\s|\*)', p)]
            if len(matching)==1:
                angle = _one(r'从([前后左右上下](?:方|面|侧))拍摄',matching[0])
                if angle:
                    return subject+'的那张作品是从'+angle+'拍摄的。'
    if field == 'encourager':
        name = _one(r'[，,]\s*('+NAME+r')对我(?:的)?鼓励', text)
        if name:
            body = name+'鼓励了你，也是对你训练的肯定。'
            if '当面' in arg and '当面' not in text:
                body += '原文没说是否当面。'
            return body
    if field in {'returning_runner', 'returning_action'}:
        handoff = _one(r'(?:^|[。；…\n])('+NAME+r')[，,](?:是|曾)[^。；…\n]{0,80}(?:回来|折回|返回)接(?:我们|队友|大家)', text)
        if handoff:
            return handoff+'会回来接队友。'
    if field in {'training_city', 'meeting_city'}:
        pattern = r'平时(?:他|她)?在([\u4e00-\u9fff]{2,8})训练' if field=='training_city' else r'(?:能|会|就)来([\u4e00-\u9fff]{2,8})一起拉练'
        city = _one(pattern, text)
        if city:
            return ('平时在'+city+'训练。' if field=='training_city' else '关键拉练时会来'+city+'会合。')
    if field == 'compare_speakers':
        author = _one(r'(?:小学|学校|中学|^|[。；\n])('+NAME+r')老师随后发来一句话[：:][“「]([^”」]{2,32})', text)
        sender = _one(r'(?:的|^|[。；\n])('+NAME+r')老师从[^，。；\n]{0,18}传来[^。；\n]{0,28}(?:视频|照片)', text)
        if author and sender and arg in author[1]:
            return ('是同一位。' if author[0] == sender else '是两位不同的老师。')+arg+'是'+author[0]+'老师说的，视频和照片由'+sender+'老师传回。'
    if field == 'captain_team':
        squad = _one(r'(?:队伍|战队)[：:]\s*(\S.{1,34})$', row['title'])
        name = _one(r'队长[：:]\s*('+NAME+r')', text)
        if squad and name and name in arg:
            return name+'是'+squad+'的队长。'
    if field == 'finish_announcement':
        word = _one(r'听(?:见|到)主持人(?:播报|报出|报)([^，。；\n]{3,30})', text)
        if word:
            return '你听到主持人报出'+word+'后，放下顾虑全力冲刺。'
    if field == 'rain_training':
        expected = _one(r'(?:以为|觉得)([^，。；\n]{2,18}(?:会|要|可能)取消)', text)
        coach = _one(r'教练[^“。；\n]{0,25}[“「]([^”」]+)[”」][“「]([^”」]+)[”」]', text)
        if expected and coach:
            return '你原本以为'+expected+'。教练说：“'+coach[0]+'”，还说“'+coach[1]+'”。'
    if field == 'organization_award':
        award = _one(r'(?:获(?:得)?|斩获|拿下)(最佳[^，。；\n]{1,12}奖)', text)
        if award:
            return '班级获得了'+award+'。'
    if field == 'talk_equation':
        equation = _one(r'[“「]([^“”」，、\n]{1,12}[=＝][^“”」，、\n]{1,12})', text)
        if equation:
            return '专题把'+equation.replace('=','和').replace('＝','和')+'画了等号。'
    return None


def answer(query, sources, quotes, plan):
    selected = plan.get('followup_fields')
    if not selected:
        return None
    field, ids, arg = selected
    index = {r['id']:r for r in sources if r.get('verification')=='confirmed'}
    if any(identity not in index for identity in ids):
        return None
    rows = [index[k] for k in ids]
    excerpts = [quotes.get(k, '') for k in ids]
    if any(not text or re.search(UNCERTAIN,text) for text in excerpts):
        return None
    if field in {'day_stairs','day_alternative'}:
        days = _days(excerpts[0])
        if len(days) != 2:
            return None
        place, dist, _ = days[arg]
        day = '第一天' if arg==0 else '次日'
        result = day+'从'+place+'出发。' if field=='day_stairs' else day+'是'+place+'那段，'+dist+'公里。'
        plan['day_anchor'] = (ids[0],arg)
    elif field == 'award_stages':
        local = _one(r'(最佳[^—*\n]{2,18}奖)[—–：: #]+([^*\n]{2,20})',excerpts[0])
        final = _one(r'(校园总决赛(?:冠军|亚军|季军|第[一二三四五六七八九十\d]+名))',excerpts[1])
        if not local or not final:
            return None
        result = '班级突围赛中，'+local[1].strip()+'获得'+local[0]+'。年度回顾提到的'+final.removeprefix('校园总决赛')+'，是校园总决赛的成绩。'
    elif field == 'song_awards':
        parts = []
        for song, text in zip(arg, excerpts):
            person = _one(r'#?\s*('+NAME+r')\*{0,4}\s*《'+re.escape(song)+r'》', text)
            award = _one(r'(?:勇夺|获得|荣获|摘得)[^，。；\n]{0,14}?([金银铜]奖)', text)
            if not person or not award:
                return None
            parts.append(person+'唱《'+song+'》，获'+award)
        result = '。'.join(parts)+'。'
    elif field == 'visit_roles':
        leader = _one(r'(?:导师|主任|院长|系|由|、)\s*('+NAME+r')教授带队', excerpts[0])
        speaker = _one(r'(?:主席|经理|总监|主任|顾问|^|[，。；\n])('+NAME+r')热情接待[^。；\n]{0,24}做了[“「][^”」]+[”」]的专题分享', excerpts[1])
        if not leader or not speaker:
            return None
        result = leader+'教授带队，'+speaker+'做专题分享。'
    else:
        result = _response(field, excerpts[0], rows[0], arg if arg is not None else query)
    return envelope(result,rows) if result else None
