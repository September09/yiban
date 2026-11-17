"""Ephemeral, permission-bound topics for structured memories; no answer history."""
import hashlib
import json
import re
from yiban.memory.retrieval import query_terms, bigrams, normalize
from yiban.memory.v2_facts import request, mentioned_opponents
from yiban.memory import v2_relevance


def catalog(items):
    topics={}
    for row in items:
        event=row.get('event')
        if not event:continue
        key=hashlib.sha256((row['scope_key']+'\0'+event['key']).encode()).hexdigest()[:24]
        topic=topics.setdefault(key,{'key':key,'title':event['title'],'label':row['scope_name']+' · '+event['title'],
                                     'ids':[],'scope_key':row['scope_key'],'kind':event['kind'],'date':event['time'].get('start')})
        topic['ids'].append(row['id'])
    return topics


def anchor_score(query,title):
    return v2_relevance.event_scores(query, {'one': {'title': title, 'ids': []}})['one']


def begin(service,token,data,scopes,items,multi=False):
    guard=hashlib.sha256(json.dumps([(s['id'],s['version'],s['member_version']) for s in scopes],sort_keys=True).encode()).hexdigest()
    from yiban.backends.model_settings import REMOTE_REQUEST
    binding=json.dumps([data.get('scope_kind'),data.get('space_id'),data.get('space_choice'),bool(REMOTE_REQUEST.get())])
    plan=service.dialogue.begin(token,guard,binding,data.get('conversation_id'))
    topics=catalog(items);plan['topics']=topics
    previous=plan.get('previous') or {};selected=None;query=data['query'].strip()
    plan['query']=query
    prior=(previous.get('focus') or {}).get('request')
    selectors=request(query,items,prior)
    if selectors and not data.get('choice_id') and not multi:
        # Structured selectors operate on the whole authorized snapshot. Generic
        # title fragments cannot narrow a role, quantity, or multi-game request.
        plan['request']=selectors
        fact_query=query
        if selectors['kind']=='roles' and selectors.get('roles') and not request(query,items):
            fact_query+=' '+'、'.join(selectors['roles'])
        elif selectors['kind']=='scores' and not request(query,items):
            fact_query+=' 比分胜负'
        elif selectors['kind']=='quantities' and not request(query,items):
            fact_query+=' 捐书数量'
        # A bare "that game" may reuse the event, but an explicit opponent/date
        # must be evaluated afresh and must never inherit the previous opponent.
        if selectors['kind']=='scores' and prior and prior.get('kind')=='scores' and not re.search(r'\d+[A-Za-z]|\d+月|20\d{2}-|分别|对比|比较|所有|全部',query) and not mentioned_opponents(query,[r for r in items if (r.get('fact') or {}).get('predicate')=='score']):
            focus=previous.get('focus')
            if focus and focus['key'] in topics:
                plan['focus']=dict(topics[focus['key']]);items=[r for r in items if r['id'] in focus['ids']]
        plan['fact_query']=fact_query
        return plan,items,fact_query,None
    general=v2_relevance.document_facets(query,items)
    if general and not multi and not data.get('choice_id'):
        plan['focus']=None
        if len(general)==1:plan['facet_ids']=[general[0]['id']]
        return plan,general,'本轮问题：'+query,None
    if data.get('choice_id'):
        choices=(previous.get('pending') or {}).get('choices',[])
        choice=next((x for x in choices if x['id']==data['choice_id']),None)
        if not choice or choice['topic']['key'] not in topics:
            from yiban.memory.shared_memory import SharedError
            raise SharedError('活动选项已失效，请重新提问。',409)
        selected=topics[choice['topic']['key']]
    focus=previous.get('focus')
    if not focus or focus['key'] not in topics:
        focus=None
    else:
        # Rehydrate the current event. Previous labels/IDs are not factual evidence.
        old=focus
        focus={**topics[old['key']], 'primary_ids':old.get('primary_ids',[]), 'subject':old.get('subject','')}
    comparison=bool(re.search(r'对比|比较|相比|比起|哪个.{0,8}更|谁更',query))
    broad=multi or comparison or bool(re.search(r'所有|全部|各个',query))
    # Elliptical questions can omit both the subject and a leading pronoun.
    # These are intent words, not stored facts; explicit event anchors below
    # still switch topics, and the current ACL snapshot rehydrates every ID.
    follow=bool(re.match(r'^(?:那|那么|他|她|它|当时|这个|那个|这次|那次|刚才|后来|具体|还有|最后|最终|最高|最低|预告|赛后|所以)',query)
                or re.search(r'这个|那个|这件|那件|里面|其中|刚才',query)
                or re.fullmatch(r'(?:送给|赠予|赠给|赠送给|赠与)(?:了)?谁(?:了|的)?[呢吗啊呀？?]*',query))
    wanted=v2_relevance.event_kind(query)
    dated=False
    if focus and not comparison and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',focus.get('date') or ''):
        temporal=bool(re.search(r'那天|当天|同一天|当晚|赛后',query) and re.search(r'后|晚上|当晚|其他|又',query))
        shift=follow and wanted and not v2_relevance.compatible_kind(focus.get('kind'),wanted)
        if temporal or shift:
            day=focus['date']
            items=[r for r in items if r['scope_key']==focus['scope_key'] and r.get('event')
                   and r['event']['time'].get('start')==day]
            plan['date_context']=day;dated=True
    available={k:t for k,t in topics.items() if any(r['id'] in t['ids'] for r in items)}
    if focus and focus.get('kind')=='meeting' and follow and re.search(r'会上|会中',query):
        available={k:t for k,t in available.items() if t.get('kind')=='meeting'}
    scores=v2_relevance.event_scores(query,available,items)
    maximum=max(scores.values(),default=0)
    explicit=[available[k] for k,v in scores.items() if abs(v-maximum)<1e-8 and v>0]
    if comparison and focus:
        related={focus['key']:focus, **{t['key']:t for t in explicit}}
        if len(related)>1:
            plan['comparison_topics']=list(related.values())
            ids={identity for t in related.values() for identity in t['ids']}
            items=[r for r in items if r['id'] in ids]
            return plan,items,'比较'+ '、'.join(t['title'] for t in related.values())+'；本轮问题：'+query,None
    if not selected and len(explicit)==1 and not broad:selected=explicit[0]
    if len(explicit)>1 and follow and focus and not re.search(r'\d+月|20\d{2}-|第[一二三四五六七八九十\d]+届',query):
        prior_match=next((t for t in explicit if t['key']==focus['key']),None)
        if prior_match:explicit=[prior_match]
    if not selected and len(explicit)==1 and not broad:selected=explicit[0]
    if not selected and len(explicit)>1 and not broad:
        clarification=service.dialogue.clarify(plan,query,explicit)
        return plan,items,query,clarification['clarification']
    if not selected and follow and not broad and not dated:
        if focus:
            selected=focus
        elif not previous.get('pending'):
            return plan,[],query,'请先说明你指的是哪个事件或物品，再继续追问；我不会从其他文章猜测。'
    if selected:
        if follow and focus and selected['key']==focus['key']:
            selected=focus
        plan['object_followup']=bool(follow and re.search(r'里面|(?:这|那)(?:个|件)(?:纪念品|礼物|物品)',query))
        plan['focus']=selected
        items=[r for r in items if r['id'] in selected['ids']]
        subject=selected.get('subject','')
        effective='活动背景：'+selected['title']+('。上轮条目（仅用于解析代词，不是本轮提问）：'+subject if subject else '')+'。本轮问题：'+query
        return plan,items,effective,None
    effective=('关于'+plan['date_context']+'当天的活动；' if dated else ('上轮活动背景：'+focus['title']+'；' if multi and follow and focus else ''))+'本轮问题：'+query
    return plan,items,effective,None


def search_items(items):
    # Search both stored labels and content; original records remain the model/citation source.
    return [dict(r,content=r['title']+'\n'+((r.get('event') or {}).get('title',''))+'\n'+r['content']) for r in items]


def expand_sources(plan,query,items,sources):
    if plan.get('comparison_topics'):
        balanced=[]
        for topic in plan['comparison_topics']:
            members=[r for r in items if r['id'] in topic['ids']]
            balanced+=v2_relevance.rank(query+' '+topic['title'],members)[:3]
        return list({r['id']:r for r in balanced+sources}.values())[:8]
    if not plan.get('focus'):return sources
    primary=set(plan['focus'].get('primary_ids',[]))
    if plan.get('object_followup') and len(primary)==1:
        # "Inside it" refers to the last cited object, not another note sharing
        # a common two-character fragment. IDs are re-read from this snapshot.
        object_rows=[r for r in items if r['id'] in primary]
        if object_rows:return object_rows
    event_terms=v2_relevance.terms(plan['focus']['title'])
    ranked=v2_relevance.rank(query,items,exclude=event_terms,rank_facet=True)
    if not ranked:
        primary=set(plan['focus'].get('primary_ids',[]))
        ranked=sorted(items,key=lambda r:(r['id'] not in primary,r['key']))
    # A one-record facet can use the existing reviewed extractive path; multi-part
    # requests retain related records rather than collapsing to the event overview.
    if len(ranked)==1 and not re.search(r'分别|各|名单|队长|两个|三支|哪些',query):
        plan['facet_ids']=[ranked[0]['id']]
    else:
        terms=v2_relevance.terms(query)-event_terms
        title_scores={r['id']:len(terms & v2_relevance.terms(r['title'])) for r in items}
        best=max(title_scores.values(),default=0)
        focused=[r for r in items if title_scores[r['id']]==best and best>0]
        if len(focused)==1 and not re.search(r'分别|各|名单|队长|两个|三支|哪些',query):
            plan['facet_ids']=[focused[0]['id']]
            return focused
    return list({r['id']:r for r in ranked+sources}.values())[:8]


def finish(service,plan,sources,cited):
    current=[r for r in sources if r['id'] in set(cited)]
    if not current:
        prior=(plan.get('previous') or {}).get('focus')
        plan['focus']=dict(prior) if prior and prior['key'] in plan['topics'] else None
    if plan.get('focus') and current and not any(r['id'] in plan['focus']['ids'] for r in current):
        plan['focus']=None
    if not plan.get('focus') and current:
        topics=[t for t in plan['topics'].values() if any(r['id'] in t['ids'] for r in current)]
        if len(topics)==1:plan['focus']=dict(topics[0])
    if plan.get('focus') and current:
        focus=plan['focus'];matching=[r for r in current if r['id'] in focus['ids']]
        if matching:
            focus['primary_ids']=[r['id'] for r in matching]
            focus['subject']='、'.join(r['title'] for r in matching)[:160]
        if plan.get('request'):focus['request']=plan['request']
    if current and plan.get('request') and not plan.get('focus'):
        kind=plan['request']['kind']
        label={'roles':'职务查询','scores':'比赛查询','quantities':'数量查询'}[kind]
        plan['focus']={'key':'facts:'+kind,'title':label,'label':label,
                       'ids':[r['id'] for r in current],'request':plan['request']}
    return service.dialogue.finish(plan)


def missing_quantity(query,sources,quotes):
    """Do not count recipients/items from narrative prose when the requested quantity is absent."""
    units='个份本件只枚套台张'
    if re.search(r'目标|公约|愿景|口号|原则|含义|意思|象征',query) and not re.search(r'多少|数量|数目|几['+units+']',query):
        return None
    match=re.search(r'(?:几['+units+']|多少['+units+r']|[一二两三四五六七八九十\d]+['+units+r'])([\u4e00-\u9fffA-Za-z0-9]{1,12})',query)
    if not match:return None
    target=re.sub(r'(?:对吗|是吗|吗|呢|吧|了)$','',match[1])
    if target in {'人','同学','成员'} or not target:return None
    relevant=[r for r in sources if target in r['title']+' '+r['content']+' '+quotes.get(r['id'],'')]
    if not relevant:return None
    stated=r'[一二两三四五六七八九十百千万\d]+(?:余|多)?\s*['+units+r']\s*(?:[\u4e00-\u9fff]{0,4})?'+re.escape(target)
    for row in relevant:
        fact=row.get('fact') or {}
        if fact.get('predicate')=='quantity' or re.search(stated,row['content']+' '+quotes.get(row['id'],'')):return None
    return '记忆里提到了'+target+'，但没有记载具体数量，我暂时无法确认。不能根据提到的人数推算物品数量。',[r['id'] for r in relevant]


def extractive_overview(plan,query,sources):
    """Use reviewed text for a uniquely matched event/title overview; never infer details."""
    if not re.search(r'什么|哪些|有哪些|有什么',query) or re.search(r'什么时候|什么时间|什么日期|什么地点|为什么|什么原因|什么价格|谁|多少|几个',query):return None
    ids=plan.get('facet_ids',[])
    if len(ids)!=1:return None
    row=next((r for r in sources if r['id']==ids[0]),None)
    if not row:return None
    return json.dumps({'answer':'相关记忆记载：'+row['content'],'references':[row['reference']]},ensure_ascii=False)


def verified_followup(plan,query,items,sources,quotes):
    """Quote one reviewed statement for narrow who-made/who-received followups.

    Topic state supplies only an ID, never a remembered answer. Re-read content,
    verification and exact evidence on every turn; ambiguity stays with the
    existing model path. No entity inference, relation generation or answer cache.
    """
    clean=re.sub(r'[\s？?。！!]+$','',query.strip())
    prefix=r'(?:那|那么|他|她|它|这个|那个|当时|具体|这次|那次)*'
    creator=re.fullmatch(prefix+r'(?:是|由)?谁(?:制作|设计|做|打造)(?:的|了)?(?:呢|吗|啊|呀)?',clean)
    recipient=re.fullmatch(prefix+r'(?:是)?(?:送给|送了给|赠予|赠给|赠送给|赠与)(?:了)?谁(?:了|的)?(?:呢|吗|啊|呀)?',clean)
    if not creator and not recipient:return None
    focus=plan.get('focus') or {}
    primary=focus.get('primary_ids',[])
    if len(primary)!=1:return None
    verbs=r'制作|做成|设计|打造' if creator else r'送给|赠予|赠给|赠送给|赠与'
    # All currently readable event records participate, including alternatives
    # that did not rank in the retriever's top six. Never silently pick one.
    related=[r for r in items if re.search(verbs,r['content'])]
    if len(related)!=1 or related[0]['id']!=primary[0]:return None
    row=next((r for r in sources if r['id']==primary[0]),None)
    if not row or row['verification']!='confirmed' or row.get('fact') or len(row['content'])>240:return None
    excerpt=quotes.get(row['id'],'')
    if not excerpt.strip() or not re.search(verbs,excerpt):return None
    if re.search(r'(?:并未|没有|并非|不是|否认|未曾|未)(?:[^。；！？\n]{0,16})(?:'+verbs+r')',excerpt):return None
    # A record mentioning a relation is not necessarily asserting its answer.
    uncertain=r'未记载|没有记载|不详|未知|无法确认|待核实|可能|推测|据说|疑似|不是|并非|不确定|未确认|未制作|没有制作|未设计|没有设计|未送给|没有送给|未赠予|没有赠予|谁|？|\?'
    if re.search(uncertain,row['content']):return None
    body=re.sub(r'^(?:报道记载|资料记载|记录显示)[：:]?','',row['content'])
    return json.dumps({'answer':body,'references':[row['reference']]},ensure_ascii=False)
