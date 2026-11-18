"""Evidence-aware object retrieval and narrow relation boundaries.

All input rows already passed the current account ACL. Descriptions select IDs,
not answers. No corpus names, objects, quantities or demo wording are stored.
"""
import re
from yiban.memory import v2_relevance, v2_fields
from yiban.memory.v2_facts import UNKNOWN
from yiban.memory.v2_grounding import envelope

OBJECT = r'纪念品|纪念物|礼物|心意|物品'
GIFT = r'赠予|赠给|赠送|送给'


def returned_object(query, items, previous, service=None, token=None):
    if not re.search(r'切回|说回|回到|再看',query) or not re.search(OBJECT,query):
        return None
    if v2_relevance.query_domains(query) or re.search(r'比较|对比|所有|各自',query):
        return None
    description=re.search(r'(?:这|那)(?:件|份|个)([^，,。？?]{0,10}?)(?:纪念品|纪念物|礼物|心意|物品)',query)
    descriptor=re.sub(r'里面的|里的|的','',description[1]) if description else ''
    descriptor=descriptor.replace('计时','时间')
    allowed={r['id']:r for r in items}
    for topic in reversed(previous.get('recent',[])):
        ids=topic.get('primary_ids',[])
        if len(ids)!=1 or ids[0] not in allowed:
            continue
        row=allowed[ids[0]]
        if not row.get('fact') and re.search(OBJECT+'|纪念|'+GIFT,row['title']+' '+row['content']):
            if descriptor:
                if service is None or not hasattr(service,'evidence'):continue
                evidence=service.evidence(token,{'scope_kind':row['scope_kind'],'space_id':row['space_id'],
                    'id':row['id'],'expected_version':row['version']})
                proof=' '.join(q['text'] for q in evidence['quotes'])
                if not v2_relevance.terms(descriptor) & v2_relevance.terms(proof):continue
            return row
    return None


def descriptive_object(service, token, query, items):
    # A vague pronoun alone cannot identify an object. Read bounded original
    # excerpts only for descriptive queries about an object, inside the scope.
    if not re.search(OBJECT,query) or v2_relevance.query_domains(query):
        return None
    if re.search(r'多少|几只|几件|费用|尺寸|店铺|对比|比较|所有|各自',query):
        return None
    if not hasattr(service,'evidence'):
        return None
    generic=v2_relevance.terms(' '.join(('纪念品','纪念物','礼物','心意','物品','共同经历','能看见','留在','什么','里面','最后','到了','手上')))
    terms=v2_relevance.terms(query)-generic
    if len(terms)<2:
        return None
    candidates=[r for r in items if not r.get('fact')
                and re.search(OBJECT+'|纪念|'+GIFT,r['title']+' '+r['content'])]
    if not 1<=len(candidates)<=64:
        return None
    ranked=[]
    for row in candidates:
        evidence=service.evidence(token,{'scope_kind':row['scope_kind'],'space_id':row['space_id'],
            'id':row['id'],'expected_version':row['version']})
        text=' '.join(q['text'] for q in evidence['quotes'])[:1800]
        overlap=terms & v2_relevance.terms(row['title']+' '+row['content']+' '+text)
        if len(overlap)>=2:
            ranked.append((len(overlap),row))
    ranked.sort(key=lambda x:-x[0])
    if ranked and (len(ranked)==1 or ranked[0][0]>ranked[1][0]):
        return ranked[0][1]
    return None


def gift_boundary(query, sources, quotes, plan):
    # A known recipient does not prove an exclusive list, nor a count of gifts.
    ids=(plan.get('focus') or {}).get('primary_ids',[]) or plan.get('facet_ids',[])
    row=next((r for r in sources if len(ids)==1 and r['id']==ids[0]),None)
    if not row or row.get('fact') or row['verification']!='confirmed':
        return None
    proof=quotes.get(row['id'],'')
    if not proof:
        return None
    other=bool(re.search(r'其他人|其他同学|其余人|除此之外',query)
               and re.search(r'收到|送给|获赠|没收到',query))
    proposed=bool(re.search(GIFT+r'.{1,18}[吗么？?]',query) and not re.search(r'谁|哪',query))
    if not (other or proposed):
        return None
    clauses=re.findall(r'(?:'+GIFT+r')[^，,。；;！？!?\n]{1,70}',proof)
    if len(clauses)!=1 or re.search(r'可能|据说|计划|没有|并未|未曾',clauses[0]):
        return None
    # Explicit exhaustive/exclusion evidence must keep the ordinary answer path.
    if re.search(r'仅送|只送|没有送|未送|其他人.{0,8}未|所有人|全员|每人|人手',proof):
        return None
    return envelope('原文记载：“'+clauses[0]+'”。至于其他人是否也收到，'+UNKNOWN,[row])


def unsupported_detail(answer, sources, quotes, cited):
    proof='。'.join(quotes.get(r['id'],'') for r in sources if r['id'] in set(cited))
    # An active maker, manual work, and someone arranging manufacture are
    # different claims. Do not add intensifiers absent from the original.
    for marker in ('亲手','亲自','独自','唯一','全部'):
        if marker in answer and marker not in proof:
            return True
    claims=re.findall(r'[一二两三四五六七八九十0-9]+(?:只|件|枚|套)(?:[^，。；！？\n]{0,8})',answer)
    return any(claim not in re.sub(r'\s+','',proof) for claim in claims)


def prune_references(answer, sources, quotes, cited):
    """Drop a redundant reference only when another excerpt supports each clause.

    This does not validate arbitrary entailment. Weak or ambiguous support keeps
    the original references, so omission cannot silently remove necessary proof.
    """
    from difflib import SequenceMatcher
    def clean(text):
        return re.sub(r'[^一-鿿A-Za-z0-9]','',re.sub(r'原文记载|资料记载|根据资料|收礼人|里的|是|的|了','',text))
    clauses=[clean(c) for c in re.split(r'[，,。；;\n]',answer) if len(clean(c))>=4]
    if not clauses:return cited
    keep=[r for r in sources if r['id'] in cited]
    for row in list(keep):
        others=[r for r in keep if r['id']!=row['id']]
        if not others:continue
        supported=True
        for clause in clauses:
            if not any(SequenceMatcher(None,clause,clean(r['content']+' '+quotes.get(r['id'],'')),autojunk=False).find_longest_match().size/len(clause)>=.6 for r in others):
                supported=False;break
        if supported:keep.remove(row)
    return [r['id'] for r in keep]


def origin_and_recipient(query, sources, quotes, plan):
    """Two explicit fields in one currently readable, reviewed original.

    No inferred maker, number, exclusive list or omitted extra question.
    """
    if not re.search(r'来源|来处|来自',query) or not re.search(r'受赠|收礼|送给|赠给|赠予',query):
        return None
    if re.search(r'多少|几只|几件|费用|尺寸|店铺|何时|哪天|总共|其他|唯一',query):
        return None
    ids=plan.get('facet_ids',[]) or (plan.get('focus') or {}).get('primary_ids',[])
    row=next((r for r in sources if len(ids)==1 and r['id']==ids[0]),None)
    if not row or row.get('fact') or row['verification']!='confirmed':
        return None
    original=quotes.get(row['id'],'')
    if not original or len(original)>1200 or re.search(r'可能|推测|据说|计划|并未|没有|未曾|不详|未知',original):
        return None
    origins=[]
    for segment in re.split(r'[，,。；;！？!?\n]',original):
        segment=segment.strip()
        if re.fullmatch(r'[^，,。；\n]{1,24}从[^，,。；\n]{1,40}带回[^，,。；\n]{1,40}',segment):
            origins.append(segment)
    recipients=re.findall(r'(?:'+GIFT+r')[^，,。；;！？!?\n]{1,70}',original)
    if len(origins)!=1 or len(recipients)!=1:
        return None
    return envelope('原文记载：“'+origins[0]+'”；“'+recipients[0]+'”。',[row])


def reviewed_object_meaning(query, sources, quotes, plan):
    """Use the original purpose sentences for one resolved reviewed object."""
    if not re.search(v2_fields.MEANING+r'|留存|哪件|什么.{0,4}纪念品',query):
        return None
    if re.search(v2_fields.ORIGIN+r'|'+v2_fields.DETAIL+r'|谁|何时|哪天|哪里|怎么制作|如何制作',query):
        return None
    ids=plan.get('facet_ids',[]) or (plan.get('focus') or {}).get('primary_ids',[])
    row=next((r for r in sources if len(ids)==1 and r['id']==ids[0]),None)
    if not row or row.get('fact') or row['verification']!='confirmed':
        return None
    original=quotes.get(row['id'],'')
    if not original or len(original)>1000 or not re.search(GIFT,original):
        return None
    if re.search(r'可能|推测|据说|计划|并未|没有|未曾|不详|未知',original):
        return None
    if re.search(r'哪件|什么.{0,4}纪念品',query):
        if len(row['content'])>200:return None
        return envelope(re.sub(r'^(?:报道记载|资料记载|记录显示)[：:]?','',row['content']),[row])
    compact=brief_object_meaning(original)
    if compact:
        return envelope(compact,[row])
    sentences=[s.strip() for s in re.split(r'[。\n]',original) if s.strip()]
    selected=[s for s in sentences if re.search(r'为了|提醒|象征|集体.{0,4}记忆',s)]
    if not selected or sum(map(len,selected))>260:
        return None
    return envelope('原文写道：“'+'；'.join(selected)+'”。',[row])


def brief_object_meaning(original):
    """Join only two explicit purpose fields; do not invent a meaning or agent.

    Keep both fields' qualifiers and wording. Uncertain/long/multiple meanings
    keep the existing original-excerpt path instead of a guessed summary.
    """
    if re.search(r'可能|据说|推测|计划|没有|并未|未曾|不详|未知|[？?]',original):
        return None
    memories=re.findall(r'作为([^，,。；;！？!?\n]{2,30}?)的纪念',original)
    reminders=re.findall(r'提醒(?:着)?(?:我们|大家|你们)?([^，,。；;！？!?\n]{2,45})',original)
    if len(memories)!=1 or len(reminders)!=1:
        return None
    memory=memories[0].strip()
    reminder=reminders[0].strip()
    if len(memory)+len(reminder)>65:
        return None
    return '它纪念的是'+memory+'，也让人记得'+reminder+'。'


def display_sources(sources, cited, plan):
    """Presentation only, after context resolution and current ACL validation.

    Keep every actual citation (including both scopes), with IDs/references
    unchanged. Absence may show one resolved object, never a random near match.
    The complete internal retrieval still drives answering and topic state.
    """
    wanted=set(cited)
    if wanted:
        return [r for r in sources if r['id'] in wanted]
    ids=plan.get('facet_ids',[]) or (plan.get('focus') or {}).get('primary_ids',[])
    return [r for r in sources if len(ids)==1 and r['id']==ids[0]][:1]
