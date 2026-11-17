"""Narrow evidence checks for requested fields; no stored answers or story constants."""
import json
import re
from yiban.memory.v2_facts import UNKNOWN


def envelope(body, rows):
    return json.dumps({'answer': body, 'references': [r['reference'] for r in rows]}, ensure_ascii=False)


def identifier_missing(query, sources, quotes):
    # A logistics identifier is not a book quantity. Do not substitute other digits.
    if not re.search(r'运单(?:号|号码)|快递(?:单号|.{0,4}号码)|物流(?:单号|编号)', query):
        return None
    relevant = [r for r in sources if re.search(r'运单|快递单号|物流单号',
                r['content'] + ' ' + quotes.get(r['id'], ''))]
    explicit = any(re.search(r'(?:运单(?:号|号码)|快递单号|物流单号)[：:\s为是]*[A-Za-z0-9-]{6,}',
                            r['content'] + ' ' + quotes.get(r['id'], '')) for r in relevant)
    return None if explicit else envelope(UNKNOWN, [])


def attendance_unknown(query, sources, quotes):
    if not re.search(r'全班|全员|所有人|所有同学', query) or not re.search(r'出席|参加|到场|到了|到齐|都到', query):
        return None
    relevant = [r for r in sources if (r.get('event') or {}).get('kind') == 'meal'
                and re.search(r'部分|有时间的同学', r['content'] + ' ' + quotes.get(r['id'], ''))]
    if not relevant:
        return None
    if any(re.search(r'全班|全员|全部同学|所有同学', quotes.get(r['id'], '')) for r in relevant):
        return None
    # Partial attendance is neither proof of all attending nor proof someone was absent.
    return envelope(UNKNOWN, relevant)


RANK = r'冠军|亚军|季军|第[一二三四五六七八九十\d]+名'


def reviewed_rank(query, items, sources, quotes):
    if not re.search(r'第一|冠军|亚军|季军|第几|名次', query):
        return None
    if re.search(r'分别|比较|对比|奖金|多少钱|什么时候|为什么|原因|地点', query):
        return None
    # Only a uniquely recorded outcome for ONE resolved event. All readable event
    # records participate, including competing records outside retrieval top hits.
    events = {(r['scope_key'], r.get('event_key')) for r in items if r.get('event_key')}
    if len(events) != 1:
        return None
    rows = [r for r in items if (not r.get('fact') or r['fact']['predicate']=='statement')
            and re.search(RANK, r['content']+' '+str((r.get('fact') or {}).get('value','')))]
    if len(rows) != 1:
        return None
    row = next((r for r in sources if r['id'] == rows[0]['id']), None)
    if not row or row['verification'] != 'confirmed':
        return None
    fact=row.get('fact') or {}
    if fact.get('predicate')=='statement' and isinstance(fact.get('value'),str):
        subject=row.get('entities',{}).get(fact['subject_key'])
        if not subject:return None
        statement=subject+'：'+fact['value'].rstrip('。')+'。'
    else:
        statement = re.split(r'[；;]', row['content'], maxsplit=1)[0].strip().rstrip('。')+'。'
    values = set(re.findall(RANK, statement))
    excerpt = quotes.get(row['id'], '')
    if not excerpt or not values or not values <= set(re.findall(RANK, excerpt)):
        return None
    if re.search(r'不详|未知|可能|推测|不是|并非|未获|没有获得', row['content']):
        return None
    # Preserve stage, team and exact outcome from the currently reviewed record.
    # Never transform third place into first place, even if the question suggests it.
    return envelope(statement, [row])


def model_payload(sources, quotes, budget=2800):
    """Bound evidence context without changing stored excerpts or reference IDs.

    The local model has a 4096-token context. This conservative estimate reserves
    space for instructions/schema/answer. Omit low-ranked candidates before sending,
    never ask the model to silently drop authoritative constraints.
    """
    payload=[];chosen=[];cost=0
    for row in sources:
        event=row.get('event')
        brief_event={k:event[k] for k in ('title','time','state','location') if k in event} if event else None
        item={'reference':len(chosen)+1,'scope':row['scope_name'],'title':row['title'],
              'content':row['content'][:1600], 'verification':row['verification'],
              'event':brief_event,'original_excerpt':quotes[row['id']][:1200]}
        if row.get('fact'):item['fact']=row['fact']
        if row.get('entities'):item['entities']=row['entities']
        def estimate():
            return sum(1.5 if ord(char)>127 else .25 for char in json.dumps(item,ensure_ascii=False))
        needed=estimate()
        for field in ('original_excerpt','content'):
            while needed>budget and len(item[field])>200:
                cut=max(200,len(item[field])*3//4)
                fragment=item[field][:cut]
                boundary=max(fragment.rfind(mark) for mark in ('。','；','\n','，',',',' '))
                item[field]=fragment[:boundary+1] if boundary>=100 else ''
                item['partial_excerpt']=True
                needed=estimate()
        if payload and cost+needed>budget:
            continue
        payload.append(item);chosen.append(row);cost+=needed
    return chosen,payload

def unsupported_answer(body):
    """Normalize a pure absence statement, not a supported mixed/negative fact."""
    return bool(re.fullmatch(r'(?:资料|原文|记忆)(?:中)?(?:未|没有)(?:记载|记录|提供|说明)[^。！？!?；;\\n]{1,400}[。！？!?]?',body.strip())
                and not re.search(r'但|不过|然而|其中',body))



def complete_literal_references(answer,sources,quotes,cited):
    """Supplement a short literal claim from already-read evidence only.

    No answer text or values are changed. This is a narrow omission check, not
    semantic entailment: a weak/ambiguous match cannot authorize a reference.
    """
    from difflib import SequenceMatcher
    from yiban.memory import v2_relevance
    if not cited:return cited
    def clean(text):
        text=re.sub(r'组织方式包括|并进行了|并进行|另外|同时|以及|进行了|进行|的|了','',text)
        for word in sorted(v2_relevance.STOP,key=len,reverse=True):text=text.replace(word,'')
        return re.sub(r'[^一-鿿A-Za-z0-9]','',text)
    def strength(clause,text):
        other=clean(text)
        match=SequenceMatcher(None,clause,other,autojunk=False).find_longest_match()
        shared=clause[match.a:match.a+match.size]
        return match.size/len(clause) if len(re.findall(r'[一-鿿]',shared))>=4 else 0
    selected=set(cited)
    names={n for r in sources for n in r.get('entities',{}).values() if len(n)>=2}
    for segment in re.split(r'[，,。；;！!？?\n]',answer):
        if re.search(r'(?:中|时|里|方面)$',segment.strip()):continue
        clause=clean(segment)
        if not 4<=len(clause)<=48:continue
        current=[r for r in sources if r['id'] in selected]
        if any(strength(clause,r['content']+' '+quotes.get(r['id'],''))>=.65 for r in current):continue
        candidates=[]
        for r in sources:
            if r['id'] in selected:continue
            excerpt=quotes.get(r['id'],'')
            if not excerpt:continue
            if any(n in segment and n not in excerpt for n in names):continue
            if any(number not in clean(excerpt) for number in re.findall(r'\d+',segment)):continue
            score=strength(clause,excerpt)
            if score>=.65:candidates.append((score,r,excerpt))
        if not candidates:continue
        best=max(score for score,_,_ in candidates)
        winners=[(r,e) for score,r,e in candidates if score==best]
        # Multiple records with the very same immutable excerpt are one proof.
        if len({clean(e) for _,e in winners})!=1:continue
        selected.add(winners[0][0]['id'])
    return [r['id'] for r in sources if r['id'] in selected]


def personal_date_unknown(target, sources, quotes):
    if not target:
        return None
    from yiban.memory.v2_relevance import matches_personal_date
    # A title/summary match cannot authorize a private date absent from the
    # freshly permitted original excerpt. Never read a whole private source
    # just because a public team event happens to share a keyword.
    if not any(matches_personal_date(target, quotes.get(r['id'], '')) for r in sources):
        return envelope(UNKNOWN, [])
    return None
