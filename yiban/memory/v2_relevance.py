"""Lexical ranking and event hints over the current authorized snapshot only."""
import math
import re
from collections import Counter
from yiban.memory.retrieval import bigrams, normalize

# Vocabulary, not facts: aliases never supply people, dates, quantities or answers.
ALIASES = (
    ('摄影', '拍照', '照相'),
    ('案例', '案例比赛', '案例大赛', '案例赛'),
    ('电竞', '王者荣耀', '王者'),
    ('总决赛', '学校总决赛', '校园总决赛'),
    ('赛后', '比赛后', '赛事结束后'),
    ('聚餐', '小聚', '聚会吃饭'),
    ('距离', '里程', '公里', '路线'),
    ('分组', '赛道', '组别'),
    ('萌宠', '小动物', '宠物'),
    ('风景', '自然风光', '景色'),
)
STOP = ('请问', '请', '帮我', '一下', '我们', '大家', '同学', '个人', '班级',
        '商学院', '共同记忆', '活动', '回顾', '报道', '记载', '第一届', '第二届',
        '哪个', '哪些', '什么', '时候', '多少', '是谁', '分别', '那次', '那天',
        '可以', '是不是', '还是', '比赛', '挑战赛', '大赛', '结束', '之后')


def expand(text):
    extra = [group[0] for group in ALIASES if any(word in text for word in group)]
    if re.search(r'[两一二三四五六七八九十0-9]+个组|哪些组|哪几个组',text):extra.append('分组')
    return text + ' ' + ' '.join(extra)


def terms(text):
    text = expand(text)
    text = re.sub(r'第[一二三四五六七八九十零\d]+届|首届', '', text)
    for word in sorted(STOP, key=len, reverse=True):
        text = text.replace(word, ' ')
    # Do not create artificial bigrams across removed words.
    return set().union(*(bigrams(part) for part in re.split(r'[\s，。！？、,:：.!?；;]+', text) if part))


def weights(texts):
    docs = [terms(text) for text in texts]
    frequency = Counter(t for doc in docs for t in doc)
    return {t: 1 + math.log((len(docs)+1)/(n+1)) for t,n in frequency.items()}


def event_kind(query):
    for pattern, kind in [
        (r'聚餐|小聚|联谊|晚宴|吃饭', 'meal'),
        (r'摄影|拍照|照相', 'photography'),
        (r'王者|电竞', 'esports'),
        (r'徒步|路线|距离|里程|公里|挑战赛', 'hiking'),
        (r'捐书|捐赠|公益', 'charity'),
        (r'班会', 'meeting'),
        (r'品牌|班徽|文创|吉祥物', 'project'),
    ]:
        if re.search(pattern, query):
            return kind
    return None


def compatible_kind(kind, wanted):
    if not wanted:
        return True
    return kind == wanted or (wanted in ('photography', 'esports') and kind == 'competition')



def document_facets(query,items):
    # An explicit document field overrides an unrelated topic, even when the
    # note is attached to a meeting. Labels select records, never stored answers.
    fields=[word for word in ('公约','章程','愿景','目标','宗旨','口号') if word in query]
    return [r for r in items if any(word in r['title'] for word in fields)]


def requested_month(query):
    matches=re.findall(r'(十一|十二|十|[一二三四五六七八九]|1[0-2]|0?[1-9])月',query)
    values={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'十一':11,'十二':12}
    return {int(m) if m.isdigit() else values[m] for m in matches}


def query_domains(query):
    patterns=(r'摄影|拍照|照相',r'案例|Robotaxi',r'品牌|班徽|文创|吉祥物',
              r'捐书|捐赠|公益',r'晚宴|聚餐|联谊',r'生日|庆生',r'班会',r'篮球|比分')
    return {i for i,p in enumerate(patterns) if re.search(p,query,re.I)}


def stage_score(query, title):
    local = bool(re.search(r'班里|班内|班级', query))
    campus = bool(re.search(r'全校|校级|学校总决赛|校园|总决赛', query))
    if local and not campus:
        return -20 if re.search(r'校园|总决赛', title) else (6 if '班级' in title else 0)
    if campus:
        return 8 if '总决赛' in title else -8
    return 0


def event_scores(query, topics, items=()):
    titles = [t['title'] for t in topics.values()]
    idf = weights(titles)
    anchor_query=re.sub(r'第[一二三四五六七八九十\d]+(?:学年|学期)|本学年|本学期|年度','',query)
    q = terms(anchor_query)
    wanted = event_kind(query)
    months=requested_month(query)
    has_month=any(t.get('date') and int(t['date'][5:7]) in months for t in topics.values())
    result = {}
    for key, topic in topics.items():
        title = topic['title']
        if wanted in ('charity','meeting','project') and topic.get('kind')!=wanted:
            result[key]=0;continue
        overlap = q & terms(title)
        score = sum(idf.get(t, 1) for t in overlap)
        clean = re.sub(r'第[一二三四五六七八九十零\d]+届|首届', '', title)
        if normalize(clean) and normalize(clean) in normalize(expand(query)):
            score += 25
        codes = re.findall(r'[\u4e00-\u9fffA-Za-z]+\d+[A-Za-z0-9]*', title)
        if any(code.lower() in query.lower() for code in codes):
            score += 25
        if score > 0:
            # Prefer the event containing the requested facet over a same-named
            # follow-on dinner. Contents are read now and never kept in topic state.
            facet = max((len(q & terms(r['title']+' '+r['content'])) for r in items if r['id'] in topic['ids']), default=0)
            score += facet * .5
            score += stage_score(query, title)
            # A distance question must not anchor a same-named post-hike dinner.
            if wanted == 'hiking' and topic.get('kind') == 'meal':
                score = 0
        if score>0 and has_month:
            when=topic.get('date') or ''
            if when and int(when[5:7]) in months:score+=30
            elif when:score=0
        result[key] = max(score, 0)
    return result


def rank(query, items, exclude=(), rank_facet=False):
    """An event's title participates in every related record, not just its overview."""
    q = terms(query) - set(exclude)
    corpus = [r['title'] + ' ' + r['content'] + ' ' +
              (r.get('event') or {}).get('title', '') for r in items]
    idf = weights(corpus)
    ranked = []
    for row in items:
        title = terms(row['title'])
        content = terms(row['content'])
        event = terms((row.get('event') or {}).get('title', ''))
        score = sum(idf.get(t, 1) * (3*(t in title) + (t in content) + 2*(t in event))
                    for t in q & (title | content | event))
        if re.search(r'冠军|第一(?!届|次|学年|学期)|第几|名次|拿第一', query) and re.search(r'冠军|亚军|季军|第[一二三\d]+名', row['content']):
            score += 3 if score or rank_facet else 0
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['key']))
    return [row for _, row in ranked]


# Person fields require a matching subject, not just an event sharing "birthday".
# This bounded binding check intentionally does not infer family relationships
# across unrelated notes or convert attendance at a birthday party into a DOB.
PERSON_RELATIONS = (
    ('孩子', '儿子', '女儿', '宝宝'),
    ('母亲', '妈妈'), ('父亲', '爸爸'),
    ('妻子', '太太', '老婆'), ('丈夫', '先生', '老公'),
)

def personal_date_target(query, items):
    if not re.search(r'出生|生日|生辰|生于', query):
        return None
    names = {name for r in items for key, name in r.get('entities', {}).items()
             if r.get('entity_kinds', {}).get(key) == 'person' and len(name) >= 2 and name in query}
    relations = [group for group in PERSON_RELATIONS if any(word in query for word in group)]
    if not names and not relations:
        return None
    return {'names': names, 'relations': relations}


def matches_personal_date(target, text):
    for clause in re.split(r'[。；;\n]', text):
        if any(name not in clause for name in target['names']):
            continue
        if any(not any(word in clause for word in group) for group in target['relations']):
            continue
        # Require an asserted date field within the same subject clause.
        if re.search(r'(?:不详|未知|未记载|没有记载|未确认|可能|推测)', clause):
            continue
        if re.search(r'(?:出生(?:日期|时间)?(?:为|是|于|在|[：:])*|生于|生日(?:为|是|在|[：:])+|生辰(?:为|是|在|[：:])+)\s*(?:20\d{2}[年-]|\d{1,2}月)', clause):
            return True
    return False
