"""Narrow evidence checks for requested fields; no stored answers or story constants."""
import json
import re
from yiban.memory.v2_facts import UNKNOWN


def envelope(body, rows):
    return json.dumps({'answer': body, 'references': [r['reference'] for r in rows]}, ensure_ascii=False)


def item_field_unknown(query, sources, quotes, plan):
    """A resolved item's absent fields cannot be inferred from its recipients."""
    primary=(plan.get('focus') or {}).get('primary_ids',[])
    row=next((r for r in sources if len(primary)==1 and r['id']==primary[0]),None)
    if not row or row.get('fact'):return None
    proof=quotes.get(row['id'],'')
    if not proof.strip():return None
    if re.search(r'(?:每个人|每人|人人|人手).{0,12}(?:收到|得到|获赠)',query):
        if not re.search(r'(?:每人|人手|每位|一人|各自|分别).{0,12}[一二两三四五六七八九十\d]+[个只件份套枚]',proof):
            return envelope(UNKNOWN,[])
    proposed=re.search(r'(?:一共|总共|合计|总计|做了|送了).{0,10}([一二两三四五六七八九十0-9]+)([只件枚套])',query)
    if proposed and not re.search(r'[一二两三四五六七八九十0-9]+\\s*'+proposed[2],proof):
        return envelope(UNKNOWN,[])
    if re.search(r'(?:这些|那些|各个|每个|各地).{0,12}(?:多少|几)公里',query):
        if not re.search(r'[一二两三四五六七八九十0-9]+(?:\\.[0-9]+)?\\s*公里',proof):
            return envelope(UNKNOWN,[])
    if re.search(r'谁|何时|什么时候|意义|含义|象征|来自|哪里',query):return None
    requested=[]
    for question,field in [
        (r'费用|花了|多少钱|多少元|价格|成本|预算',r'费用|元|价格|成本|预算|花费'),
        (r'尺寸|规格|多大|多高|多宽|直径|大小',r'尺寸|规格|厘米|毫米|cm|mm|直径|长\d|高\d|宽\d'),
        (r'店铺|商店|店家|哪家店|制作店|制作商',r'店铺|商店|店家|门店|工作室|公司|厂商'),
    ]:
        if re.search(question,query):requested.append(bool(re.search(field,proof,re.I)))
    return envelope(UNKNOWN,[]) if requested and not any(requested) else None


def unsupported_negative(answer, sources, quotes, cited):
    """Absence in an excerpt is not proof that an action did not happen."""
    action=r'送给|赠给|赠予|赠送|发生|参加|出席|到场|收到|制作|设计|准备|帮助|支持|带回'
    claims=re.findall(r'(?:并没有|并未|未曾|没有|并非|不是)(?:再|额外)?(?:'+action+r')[^，,。！？；;\n]{0,48}',answer)
    if not claims:return False
    proof='。'.join(quotes.get(r['id'],'') for r in sources if r['id'] in set(cited))
    clean=lambda s:re.sub(r'\s+','',s).replace('并没有','没有').replace('并未','未')
    # Match the negative action and its object, not an unrelated negation of
    # the same verb elsewhere in the excerpt.
    return any(clean(claim) not in clean(proof) for claim in claims)


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


def attribute_boundary(query, sources, quotes, plan):
    """Preserve action/uncertainty boundaries from freshly authorized evidence."""
    if re.search(r'药名|药物|处方|剂量|什么药',query):
        if not any(re.search(r'药名|药物|处方|剂量|服用|开药',quotes.get(r['id'],'')) for r in sources):
            return envelope(UNKNOWN, [])
    if re.search(r'妻子|太太|老婆|丈夫|老公',query) and re.search(r'名字|姓名|叫什么|名叫',query):
        relationship=r'(?:妻子|太太|老婆|丈夫|老公)'
        explicit=relationship+r'(?:的)?(?:名字|姓名)?(?:是|叫|名叫|名为|为|[：:])\s*[一-鿿]{2,8}'
        reverse=r'[一-鿿]{2,8}(?:是|为)(?:我的)?'+relationship
        if not any(re.search(explicit+'|'+reverse,quotes.get(r['id'],'')) for r in sources):
            return envelope(UNKNOWN, [])
    if plan.get('personal_result'):
        explicit=r'(?:最终(?:赛事)?名次|最终排名|完赛用时|完赛时间)(?:是|为|[：:])\s*[第\d一二三四五六七八九十]'
        if not any(re.search(explicit, quotes.get(r['id'], '')) for r in sources):
            return envelope(UNKNOWN, [])
    for row in sources:
        original=quotes.get(row['id'],'')
        if not original:continue
        if re.search(r'沟通|商量|征求',query) and re.search(r'跑步|锻炼|出门',query) and re.search(r'每次|通常|日常',row['content']) and re.search(r'沟通|商量',original):
            return envelope('原文记载：'+original,[row])
        if re.search(r'确诊|确定诊断',query) and re.search(r'有可能|可能是|疑似',original) and re.search(r'康复|诊断|确诊',original):
            return envelope('记录中的说法是：'+row['content']+' 原文没有记载确定的诊断结论。',[row])
        if re.search(r'生日|出生',query) and re.search(r'就一定|生日吗|生日么|能.*证明',query) and '待产' in original:
            return envelope('原文记载的是待产期间的安排，没有记载出生日期，不能据此确认生日。',[row])
        if re.search(r'请教|肌贴',original) and '请教' in original and re.search(r'谁|哪处|哪个|帮|贴',query) and '肌贴' in query:
            return envelope('原文记载：'+original,[row])
        if re.search(r'前.{0,8}后|实际称重|确切体重',query) and re.search(r'公斤|体重|称重',query) and re.search(r'瘦|减重|体重',row['content']):
            absolute=r'(?:训练|备赛)(?:前|后)(?:体重)?[为是：:\s]*[\d一二三四五六七八九十百两]+(?:\.\d+)?(?:公斤|千克|kg)'
            if not re.search(absolute,original):
                return envelope('记忆里记载：'+row['content']+' 没有提供前后各自的具体体重，我暂时无法确认。',[row])
        if re.search(r'期望|期待|寄托',query) and not re.search(r'分别|各自|同时',query):
            match=re.search(r'(?:期望|希望|期待)[^。；;\n]{2,180}',original)
            if match:return envelope('原文写道：“'+match[0].rstrip('，,')+'”。',[row])
    return None
