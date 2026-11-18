"""Bounded query intent hints, never facts, authority or remembered answers."""
import re
from yiban.memory import v2_relevance


TRAITS = (
    ('weight', r'体重|减重|重量|称重|轻了|瘦了|多少公斤|几公斤', r'体重|瘦|减重|公斤'),
    ('partner', r'搭子|搭档|陪练|(?:训练|练|陪我练).{0,12}伙伴', r'搭子|搭档|陪练|伙伴'),
    ('joining', r'(?:当初|最初|起初).{0,20}(?:打动|踏上|开始|入门)|带我入门|加入|(?:参加|促使).{0,12}俱乐部|(?:备战|备赛|参赛).{0,12}(?:最初|起初|一开始|最早).{0,8}(?:怎么|如何|开始)', r'加入.{0,12}俱乐部|看到.{0,18}宣传'),
    ('communication', r'沟通|商量|征求|(?:跑步|锻炼|出门).{0,12}(?:支持|同意)', r'沟通|商量'),
    ('health', r'确诊|诊断|康复师|膝痛|膝盖|疼痛', r'康复|膝痛|膝盖|疼痛'),
    ('training_places', r'(?=.*(?:训练|跑步|锻炼|练跑|慢跑))(?:平时|日常|通常|平常|平日|经常).{0,14}(?:哪里|哪儿|哪些地方|哪些.{0,6}场所|在哪)|(?:在哪些|哪里|哪儿).{0,8}(?:日常场所|跑过)', r'训练.{0,8}(?:地点|环境)|地点包括|日常训练'),
    ('reflection', r'(?:悟到|体会|领悟).{0,20}碎片时间|(?:时间管理.{0,10}坚持|坚持.{0,10}时间管理).{0,12}(?:感悟|领悟|体会)|(?:感悟|领悟|体会).{0,16}(?:时间管理|坚持)', r'感悟|领悟|反思'),
    ('time_management', r'时间管理|挤(?:出)?时间|碎片时间|出差.{0,16}(?:训练|锻炼)', r'时间管理|出差.{0,12}(?:运动鞋|跑鞋)|酒店.{0,12}公园'),
    ('training_rank', r'我(?!们).{0,20}(?:训练|爬升).{0,6}榜', r'(?:训练|爬升).{0,10}榜'),
    ('expectation', r'名字|取名|期望|期待|寄托', r'取名|名字|寓意'),
)


def questions(query):
    """Split independently asked clauses only; shared-predicate comparisons stay planned."""
    parts = [re.sub(r'^(?:和|以及|还有|另外)', '', p.strip()) for p in re.split(r'[，,；;]', query)]
    if len(parts)==3 and re.fullmatch(r'(?:分别|各自)(?:是|为|都)?(?:什么|谁)[？?]?',parts[-1]):
        tail=re.sub(r'^(?:分别|各自)','',parts[-1])
        parts=[part+tail for part in parts[:2]]
    if not 2 <= len(parts) <= 3 or not all(re.search(r'谁|什么|哪|多少|几[个本位公斤]', p) for p in parts):
        return []
    # One event's who-made/who-received question is a single coherent request.
    if not any(re.search(r'我(?!们)|个人|本人', p) for p in parts):
        return []
    if re.search(r'我(?!们)|个人|本人',parts[0]) and not any(re.search(r'班里|班级|团队|全班|共同记忆',p) for p in parts[1:]):
        return []
    return parts


def scope_target(query):
    """Explicit conversational target narrows the existing ACL snapshot only."""
    personal=bool(re.search(r'我自己|我个人|本人|我的|个人(?:记忆|经历|训练|备赛|成绩)',query))
    team=bool(re.search(r'我们班|咱班|班里|班级|团队|全班',query))
    return ('personal' if personal else 'team') if personal!=team else None


def item_followup(query):
    """Recognize grammatical anaphora, not new named subjects or event dates."""
    if re.match(r'^(?:这些|那些)(?:地方|地点|场所)',query) and not re.search(r'对比|比较|与|和',query):
        return True
    if re.search(r'那天|当天|那次|这次|同一天|分别|各自|对比|比较',query):return False
    return bool(re.search(r'(?:这些|那些)(?:地方|地点|场所)|这份心意|那份心意|那两位|这两位|其他人.{0,8}(?:收到|没收到)|一共.{0,8}[一二两三四五六七八九十0-9]+[只件枚套]',query) or re.match(r'^(?:那|那么)?(?:这(?:个|件|份)?|那(?:个|件|份)|它|他|她)(?:是|的|想|有|从|要|会|怎么|如何|给|被|由|[，,])',query)
        or re.search(r'(?:这|那)(?:个|件|份)(?:礼物|物品|纪念品)|里面|其中|装进去',query)
        or re.match(r'^(?:具体)?(?:送给|赠予|赠给|赠送给|赠与)',query)
        or re.match(r'^(?:每个人|每人|人人|人手).{0,12}(?:收到|得到|获赠)',query)
        or re.search(r'(?:他|她)(?:怎么|如何|给了|对我)',query))


def partner_support_followup(query):
    """A support question about the just-cited partner, not another attribute."""
    return bool(re.fullmatch(
        r'(?:(?:临近比赛|工作繁忙|工作忙|忙工作|又|的时候|时|备赛期间|赛前|训练强度大|[，, ])+)?'
        r'(?:他|她|对方)(?:(?:怎么|如何)(?:支持|帮助|鼓励)我|的坚持.{0,4}帮我.{0,4}什么处境)[的呢吗呀啊？?。]*',
        query.strip()))


def personal_result(query):
    return bool(re.search(r'我(?!们)|本人', query) and re.search(r'最终.{0,8}名次|完赛(?:用时|时间|成绩|名次)|最终成绩', query))


def personal_facets(query, items):
    # This is retrieval preference within the existing ACL snapshot, not a grant.
    if re.search(r'我们班|班里|班级|团队|各自|分别', query):
        return []
    trait = next((row for row in TRAITS if re.search(row[1], query)), None)
    if not trait:
        return []
    if trait[0] == 'partner' and re.search(r'搭档.{0,5}(?:主持|表演|演出|参赛)|(?:主持|表演|演出).{0,8}搭档', query):
        return []
    # Explicit named third parties must not be rebound to the login account.
    own_names={name for r in items if r.get('scope_kind')=='personal' for name in r.get('entities',{}).values()}
    if any(name in query and name not in own_names for r in items if r.get('scope_kind') == 'team'
           for key, name in r.get('entities', {}).items()
           if r.get('entity_kinds', {}).get(key) == 'person' and len(name) > 1):
        return []
    rows = [r for r in items if r.get('scope_kind') == 'personal'
            and re.search(trait[2], r['title']+' '+r['content'])]
    if trait[0]=='expectation':
        relations=[g for g in v2_relevance.PERSON_RELATIONS if any(w in query for w in g)]
        rows=[r for r in rows if all(any(w in r['title']+' '+r['content'] for w in group) for group in relations)]
    if not rows:
        return []
    if trait[0]=='communication' and not re.search(r'\d+月|那晚|当晚|当天|那天',query):
        routine=[r for r in rows if re.search(r'每次|通常|日常',r['content'])]
        if routine:rows=routine
    if trait[0]=='time_management' and re.search(r'怎么|如何|方法|安排',query):
        practical=[r for r in rows if re.search(r'方法|安排|出差|酒店',r['title']+' '+r['content'])]
        if practical:rows=practical
    ranked = v2_relevance.rank(query, rows)
    return ranked or rows


def record_topic(row):
    return {'key': 'record:'+row['id'], 'title': row['title'],
            'label': row['scope_name']+' · '+row['title'], 'ids': [row['id']],
            'primary_ids': [row['id']], 'scope_key': row['scope_key'], 'kind': 'record', 'date': None}
