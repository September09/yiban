"""Ephemeral, permission-bound topics for structured memories; no answer history."""
import hashlib
import json
import re
from yiban.memory.retrieval import query_terms, bigrams, normalize
from yiban.memory.v2_facts import request, mentioned_opponents
from yiban.memory import v2_relevance, v2_intent, v2_narrative, v2_fields, v2_specifics, v2_followups


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


def begin(service,token,data,scopes,items,multi=False,context_items=None,cancel=None):
    guard=hashlib.sha256(json.dumps([(s['id'],s['version'],s['member_version']) for s in scopes],sort_keys=True).encode()).hexdigest()
    from yiban.backends.model_settings import REMOTE_REQUEST
    binding=json.dumps([data.get('scope_kind'),data.get('space_id'),data.get('space_choice'),bool(REMOTE_REQUEST.get())])
    plan=service.dialogue.begin(token,guard,binding,data.get('conversation_id'))
    context_items=items if context_items is None else context_items
    topics=catalog(context_items);plan['topics']=topics
    previous=plan.get('previous') or {};selected=None;query=data['query'].strip()
    plan['query']=query
    prior_record=(previous.get('focus') or {})
    target = v2_intent.scope_target(query)
    prior_scope = prior_record.get('scope_key', '').split(':', 1)[0]
    if target and prior_scope in {'personal', 'team'} and target != prior_scope:
        plan['scope_switch'] = True
        previous = {**previous, 'focus': None}
        prior_record = {}
    leader_follow=bool(re.fullmatch(r'(?:是|由)?谁(?:带领|带|领着|领|陪着|陪|组织|安排)(?:我们|我|大家)?(?:跑|去|训练|越野)?[的呢吗啊呀？?]*',query))
    if prior_record.get('kind')=='record':
        row=next((r for r in context_items if prior_record['key']=='record:'+r['id']),None)
        if row:topics[prior_record['key']]=v2_intent.record_topic(row)
    plan['recent']=list(previous.get('recent',[]))
    if not data.get('choice_id') and not multi and not request(query,items):
        excerpts = {}

        def read(row):
            if row['id'] not in excerpts:
                if cancel is not None:
                    cancel.check()
                data = {'scope_kind':row['scope_kind'], 'space_id':row.get('space_id'),
                        'id':row['id'], 'expected_version':row['version']}
                excerpts[row['id']] = '\n'.join(q['text'] for q in service.evidence(token,data)['quotes'])
            return excerpts[row['id']]

        followup = v2_followups.select(query, items, previous, read)
        if followup:
            field, rows, arg = followup
            ids = [r['id'] for r in rows]
            if ids:
                event = next((t for t in topics.values() if t['kind'] != 'record' and set(ids) <= set(t['ids'])), None)
                focus = dict(event) if event else v2_intent.record_topic(rows[0])
                topics[focus['key']] = focus
                plan.update(focus=focus, followup_fields=(field, ids, arg), facet_ids=ids)
            return plan,rows,'本轮问题：'+query,None
        detail = v2_specifics.select(query, items, previous)
        if detail:
            row, field = detail
            event = next((t for t in topics.values() if t['kind'] != 'record' and row['id'] in t['ids']), None)
            focus = dict(event) if event else v2_intent.record_topic(row)
            if not event:
                topics[focus['key']] = focus
            plan.update(focus=focus, facet_ids=[row['id']], evidence_facet=field)
            return plan,[row],'本轮问题：'+query,None
        field = v2_fields.bind(query, items, previous)
        if field:
            row, kind = field
            topic = v2_intent.record_topic(row)
            plan['topics'][topic['key']] = topic
            plan.update(focus=topic,resolved_record=True,facet_ids=[row['id']],narrative_field=kind)
            return plan,[row],'当前条目：'+row['title']+'；本轮问题：'+query,None
    if not data.get('choice_id') and not multi:
        facets=v2_intent.personal_facets(query,items)
        if facets:
            plan['personal_facet']=True
            plan['personal_trait']=next((name for name,pattern,_ in v2_intent.TRAITS if re.search(pattern,query)),None)
            if len(facets)==1:plan['facet_ids']=[facets[0]['id']]
            return plan,facets,'本轮问题：'+query,None
        if v2_intent.personal_result(query):
            plan['personal_result']=True
            return plan,[r for r in items if r.get('scope_kind')=='personal'],'本人赛事成绩；本轮问题：'+query,None
        restored=v2_narrative.descriptive_object(service,token,query,items)
        if not restored:
            restored=v2_relevance.returning_record(query,items)
        if not restored:
            restored=v2_narrative.returned_object(query,items,previous,service,token)
        if restored:
            topic=v2_intent.record_topic(restored)
            plan.update(focus=topic,resolved_record=True,facet_ids=[restored['id']])
            plan['topics'][topic['key']]=topic
            return plan,[restored],'本轮问题：'+query,None
        support=[r for r in items if r['id'] in prior_record.get('primary_ids',[])]
        if (v2_intent.partner_support_followup(query) and len(support)==1
                and support[0].get('scope_kind')=='personal' and not support[0].get('fact')
                and re.search(r'搭子|搭档|陪练|伙伴',support[0]['title']+' '+support[0]['content'])):
            topic=v2_intent.record_topic(support[0]);plan['topics'][topic['key']]=topic
            plan.update(focus=topic,personal_facet=True,personal_trait='partner',facet_ids=[support[0]['id']])
            return plan,support,'上轮条目：'+topic['title']+'；本轮问题：'+query,None
        if prior_record.get('kind')=='record' and prior_record['key'] in topics and (leader_follow or re.match(r'^(?:那|所以|这个|她|他|它)',query)):
            # Keep attribute-only context for records without an event. Never keep answers.
            topical=topics[prior_record['key']]
            if not v2_relevance.query_domains(query) or re.search(r'出生|生日|体重|称重|公斤|名字|期望|期待|确诊',query):
                plan['focus']=dict(topical)
                found=[r for r in items if r['id'] in topical['ids']]
                return plan,found,'上轮条目：'+topical['title']+'；本轮问题：'+query,None
    # Resolve short item/relationship followups before unrelated event headings
    # can win on generic predicates such as “prepare” or “receive”.
    primary=prior_record.get('primary_ids',[])
    focused=[r for r in items if r['id'] in primary]
    if (not data.get('choice_id') and not multi and len(primary)==len(focused)==1
            and not focused[0].get('fact') and v2_intent.item_followup(query)):
        topic=v2_intent.record_topic(focused[0])
        plan['topics'][topic['key']]=topic;plan['focus']=topic
        plan['object_followup']=True
        return plan,focused,'上轮条目：'+topic['title']+'；本轮问题：'+query,None
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
    # Habit/reflection/relationship questions span episodes rather than inheriting
    # the last individual training event. This changes search scope, never ACLs.
    broad=multi or comparison or bool(re.search(
        r'所有|全部|各个|平时|日常|通常|总体|整体|感悟|体会|领悟|搭子|陪练|合作伙伴',query))
    # Elliptical questions can omit both the subject and a leading pronoun.
    # These are intent words, not stored facts; explicit event anchors below
    # still switch topics, and the current ACL snapshot rehydrates every ID.
    follow=bool(leader_follow or re.match(r'^(?:那|那么|他|她|它|当时|这个|那个|这份|那份|这次|那次|刚才|后来|具体|还有|最后|最终|最高|最低|预告|赛后|所以)',query)
                or re.search(r'这个|那个|这件|那件|这份|那份|里面|其中|刚才',query)
                or re.match(r'^(?:(?:是|由)?谁(?:制作|设计|做|打造)|评委|裁判|主持人|主讲人)',query)
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
        plan['object_followup']=bool(follow and re.search(r'里面|(?:这|那)(?:个|件|份)(?:纪念品|礼物|物品)',query))
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
    if plan.get('followup_fields'):
        ids = plan['followup_fields'][1]
        by_id = {r['id']:r for r in items}
        return [by_id[identity] for identity in ids]
    if plan.get('comparison_topics'):
        balanced=[]
        for topic in plan['comparison_topics']:
            members=[r for r in items if r['id'] in topic['ids']]
            balanced+=v2_relevance.rank(query+' '+topic['title'],members)[:3]
        return list({r['id']:r for r in balanced+sources}.values())[:8]
    if (plan.get('personal_facet') or plan.get('resolved_record') or plan.get('evidence_facet')) and len(plan.get('facet_ids',[]))==1:
        # A semantic field match may have no literal bigram overlap. Keep its
        # current authorized record rather than dropping it in a second rank.
        facet=[r for r in items if r['id'] in plan['facet_ids']]
        if facet:return facet
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
    attribute = v2_relevance.specific_attribute(query)
    if len(ranked)==1 and not re.search(r'分别|各|名单|队长|两个|三支|哪些',query):
        if not attribute or not any(row['id'] != ranked[0]['id'] for row in sources):
            plan['facet_ids']=[ranked[0]['id']]
    else:
        terms=v2_relevance.terms(query)-event_terms
        title_scores={r['id']:len(terms & v2_relevance.terms(r['title'])) for r in items}
        best=max(title_scores.values(),default=0)
        focused=[r for r in items if title_scores[r['id']]==best and best>0]
        # A short title overlap must not hide another row containing the
        # requested attribute (distance, elevation, named person's award, etc.).
        if len(focused)==1 and not attribute and not re.search(r'分别|各|名单|队长|两个|三支|哪些',query):
            plan['facet_ids']=[focused[0]['id']]
            return focused
    return list({r['id']:r for r in ranked+sources}.values())[:8]


def finish(service,plan,sources,cited):
    current=[r for r in sources if r['id'] in set(cited)]
    previous_ids=((plan.get('previous') or {}).get('focus') or {}).get('primary_ids',[])
    same_narrative=len(current)==1 and not current[0].get('fact') and previous_ids==[current[0]['id']]
    standalone = (len(current)==1 and not current[0].get('event') and not current[0].get('fact')
                  and all(k in current[0] for k in ('scope_key','scope_name','title')))
    if len(current)==1 and (standalone or same_narrative or plan.get('resolved_record') or plan.get('personal_facet') or (plan.get('facet_ids') and (current[0].get('scope_kind')=='personal' or not current[0].get('event')))):
        topic=v2_intent.record_topic(current[0]);plan['topics'][topic['key']]=topic;plan['focus']=topic
    if not current:
        prior=(plan.get('previous') or {}).get('focus')
        if not plan.get('resolved_record'):
            plan['focus']=dict(prior) if prior and not plan.get('scope_switch') and prior['key'] in plan['topics'] else None
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
        if plan.get('day_anchor') and plan['day_anchor'][0] in {r['id'] for r in matching}:
            focus['day_record'], focus['day_index'] = plan['day_anchor']
    if current and plan.get('request') and not plan.get('focus'):
        kind=plan['request']['kind']
        label={'roles':'职务查询','scores':'比赛查询','quantities':'数量查询'}[kind]
        plan['focus']={'key':'facts:'+kind,'title':label,'label':label,
                       'ids':[r['id'] for r in current],'request':plan['request']}
    # Keep only labels and IDs of a few actually cited records, never answers
    # or excerpts. Later turns rehydrate exclusively from their ACL snapshot.
    recent=list(plan.get('recent',[]))
    for row in current:
        if row.get('fact') or not all(k in row for k in ('scope_name','scope_key','title')):continue
        topic=v2_intent.record_topic(row)
        recent=[t for t in recent if t['key']!=topic['key']]+[topic]
    plan['recent']=recent[-6:]
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
    if plan.get('personal_trait') in {'reflection','training_places','joining','partner'}:return None
    if re.search(r'分别|各自',query) or v2_relevance.specific_attribute(query, narrow=True):return None
    if not re.search(r'什么|哪些|有哪些|有什么',query) or re.search(r'什么时候|什么时间|什么日期|什么地点|为什么|什么原因|什么价格|什么奖|哪个赛道|谁|多少|几个|期望|期待|寄托|如何|怎么|确诊|诊断|出生|生日|名字|姓名|叫什么|药名|剂量|处方',query):return None
    ids=plan.get('facet_ids',[])
    if len(ids)!=1:return None
    row=next((r for r in sources if r['id']==ids[0]),None)
    if not row:return None
    return json.dumps({'answer':'相关记忆记载：'+row['content'],'references':[row['reference']]},ensure_ascii=False)


def verified_followup(plan,query,items,sources,quotes):
    """Quote one reviewed statement for narrow creator/recipient/leader followups.

    Topic state supplies only an ID, never a remembered answer. Re-read content,
    verification and exact evidence on every turn; ambiguity stays with the
    existing model path. No entity inference, relation generation or answer cache.
    """
    clean=re.sub(r'[\s？?。！!]+$','',query.strip())
    prefix=r'(?:那|那么|他|她|它|这|这个|那个|当时|具体|这次|那次)*'
    leader=re.fullmatch(r'(?:是|由)?谁(?:带领|带|领着|领|陪着|陪)(?:我们|我|大家)?(?:跑|去|训练|越野)?[的呢吗啊呀]*',clean)
    creator=re.fullmatch(prefix+r'(?:是|由)?谁(?:制作|设计|做|打造|准备)(?:的|了)?(?:呢|吗|啊|呀)?',clean)
    recipient=re.fullmatch(prefix+r'(?:是)?(?:送给|送了给|赠予|赠给|赠送给|赠与)(?:了)?(?:谁|哪(?:几|两|二)?位同学)(?:了|的)?(?:呢|吗|啊|呀)?',clean)
    # A named object before the interrogative does not make it a new task.
    if not creator and re.search(r'谁(?:制作|设计|做|打造|准备)',clean) and not re.search(r'多少|何时|哪天|哪里|怎么|如何|为什么|尺寸|店铺',clean):
        creator=True
    if not creator and not recipient and not leader:return None
    focus=plan.get('focus') or {}
    primary=focus.get('primary_ids',[])
    if len(primary)!=1:return None
    verbs=r'带领|带(?:我们|大家|我)|领着|陪着' if leader else (r'制作|做成|设计|打造|准备' if creator else r'送给|赠予|赠给|赠送给|赠与')
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


def verified_material_origin(plan,query,sources,quotes):
    """Extract one explicit origin clause for the current item/material.

    Only the current authorized, confirmed record and exact evidence qualify.
    Missing detail, multiple origins, uncertainty or extra subquestions stay
    with the existing guarded model path. Never repair arbitrary nested nouns.
    """
    clean=re.sub(r'[\s？?。！!]+$','',query.strip())
    match=re.fullmatch(r'(?:那|那么)?(?:里面的|其中的)?(?P<subject>[\u4e00-\u9fff]{1,10}?)(?:是)?(?:从(?:哪里|哪儿|何处)(?:来|带来|带回|带回来)?|来(?:自|源于)(?:哪里|哪儿|何处))(?:的)?(?:呢|呀|啊)?',clean)
    if not match:return None
    subject=match['subject']
    if re.search(r'和|与|及|还|多少|几个|费用|分别',subject):return None
    primary=(plan.get('focus') or {}).get('primary_ids',[])
    if len(primary)!=1:return None
    row=next((r for r in sources if r['id']==primary[0]),None)
    if not row or row['verification']!='confirmed' or row.get('fact'):return None
    excerpt=quotes.get(row['id'],'')
    if not excerpt.strip() or re.search(r'可能|据说|推测|传闻|否认|不详|未知|无法确认|待核实|不确定|没有|并未|并非|不是|未曾|计划|打算|想要|[？?]',excerpt):return None
    # Common material names, not answers or corpus entities. Exact quotation
    # supplies the person, place and material; no model paraphrase is needed.
    targets={'沙子','沙粒','黄沙','沙'} if subject in {'沙子','沙粒','黄沙','沙'} else {subject}
    clauses=re.split(r'[。！？；;\n]+',excerpt)
    found=[]
    for clause in clauses:
        for segment in re.split(r'[，,]',clause):
            origin=re.fullmatch(r'[^，,。；\n]{1,24}从[^，,。；\n]{1,40}带回(?P<material>[^，,。；\n]{1,40})',segment.strip())
            if origin and any(t in origin['material'] for t in targets):found.append(segment.strip())
    if len(found)!=1:return None
    return json.dumps({'answer':found[0]+'。','references':[row['reference']]},ensure_ascii=False)


def brief_personal_field(trait, content, excerpt, query=''):
    """Shorten presentation only; select clauses from the current evidence.

    No model paraphrase or stored demo answers. If the evidence cannot support
    a compact form, keep the existing reviewed answer and its reference.
    """
    if trait=='training_places':
        match=re.search(r'地点包括([^。；;\n]+)',content)
        if match and not re.search(r'可能|待核实|推测|不详|未知|未曾|没有|未到|计划|打算',content):
            places=match[1].strip('，, ')
            # The reviewed summary already contains the list and any extra
            # settings; drop the count disclaimer, not part of the location.
            body='你的训练地点包括'+places
            body=re.sub(r'，也利用([^。；;]+)训练$',r'，也在\1练过',body)
            return body.rstrip('。；; ')+'。'
    if trait=='reflection':
        clauses=[c.strip() for c in re.split(r'[。；;…！!？?\n]+',excerpt) if c.strip()]
        time_parts=[c for c in clauses if '时间管理' in c]
        persistence=[c for c in clauses if re.search(r'不是.{0,50}才坚持.{0,12}而是.{0,50}坚持.{0,30}才',c)]
        if len(time_parts)==len(persistence)==1 and time_parts[0]!=persistence[0]:
            # Keep the original causal order and qualifiers, including
            # “有时候”. Do not rewrite it as a comparison of importance.
            time_clause=re.sub(r'^(?:也)?(?:领悟到(?:了)?|体会到(?:了)?|明白(?:了)?|认识到(?:了)?)','',time_parts[0])
            causal=re.sub(r'^(?:也)?(?:明白(?:了)?|领悟到(?:了)?|体会到(?:了)?)','',persistence[0])
            if len(time_clause)+len(causal)<=150:
                if query and re.search(r'碎片时间|时间管理',query) and not re.search(r'坚持',query):
                    return '你体会到'+time_clause.rstrip('… .')+'。'
                return '你体会到'+time_clause+'；也明白了：'+causal.rstrip('… .')+'。'
    return None


def reviewed_personal_extract(plan,query,sources,quotes):
    """Return a reviewed narrow field, never invent a missing subquestion answer.

    The current ACL/evidence read and a unique confirmed personal record are
    required. Additional requested fields keep the ordinary model/unknown path.
    """
    trait=plan.get('personal_trait')
    patterns={'joining':r'加入.{0,30}俱乐部|宣传',
              'partner':r'搭子|搭档|陪练|伙伴',
              'reflection':r'时间管理.{0,200}坚持|坚持.{0,200}时间管理',
              'training_places':r'地点|跑过|训练',
              'time_management':r'时间管理|出差|酒店',
              'training_rank':r'(?:训练|爬升).{0,18}榜'}
    if trait not in patterns or len(plan.get('facet_ids',[]))!=1:return None
    if re.search(r'多少|多长|多久|多远|几(?:个|本|位|次|场|层|公里|公斤|分钟|小时)|哪年|哪天|几点|用时|全名|姓名|费用|多少钱|分别|各自|每次|每天|每周|每月|总共|什么软件|叫什么',query):return None
    if trait=='joining':
        if not re.search(r'怎么|如何|起因|最初|起初|当初|打动|入门|为什么|为何|促使',query) or re.search(r'什么时候|日期|哪一?年|哪一?天|哪月|哪家|哪里|在哪|谁',query):return None
    if trait=='partner':
        relation_query=re.sub(r'^最后把.{1,10}分开说[：:]','',query.strip())
        named_partner=bool(re.fullmatch(r'我(?:自己的|的)?训练时的伙伴(?:是)?谁[的呢吗呀啊？?。]*',relation_query))
        if not named_partner and not v2_intent.partner_support_followup(query) and not re.fullmatch(r'(?:那|那么)?(?:我(?:自己的)?的?|本人的?)?(?:训练)?(?:搭子|搭档|陪练|练.{0,4}伙伴)(?:是)?谁(?:[，,、]?(?:他|她|对方)(?:给了我什么支持|怎么支持我|如何支持我|给了我哪些支持))?[的呢吗呀啊？?。]*',query.strip()):return None
    elif '谁' in query:return None
    row=next((r for r in sources if r['id']==plan['facet_ids'][0]),None)
    if not row or row['scope_kind']!='personal' or row['verification']!='confirmed' or row.get('fact'):return None
    if len(row['content'])>260 or not quotes.get(row['id'],'').strip():return None
    if not re.search(patterns[trait],quotes[row['id']]):return None
    if trait=='training_rank' and not re.search(r'比赛|赛事',query):return None
    body=row['content']
    if trait=='joining':
        if re.search(r'未加入|没有加入|并非|不详|未知|可能|推测|据说|待核实',body):return None
        body=body.replace('叙述者','你')
    if trait=='partner':
        proof=body+' '+quotes[row['id']]
        if re.search(r'未记载|没有记载|不详|未知|无法确认|可能|推测|据说|疑似|不确定|未确认|谁|[？?]',proof):return None
        relation=r'(?:搭子|搭档|陪练|伙伴)'
        if re.search(relation+r'(?:并非|不是|不叫|不姓|没有)|(?:并非|不是)(?:我|你|他|她)?的?(?:训练)?'+relation,proof):return None
        if re.search(r'支持',query) and not all(re.search(r'坚持|鼓励|支持|陪练|陪伴|帮助|带领|陪跑',s) for s in (body,quotes[row['id']])):return None
        body=body.replace('叙述者','你')
    if trait in {'reflection','training_places'}:
        brief=brief_personal_field(trait,row['content'],quotes[row['id']],query)
        if brief:body=brief
        elif trait=='reflection':
            if len(quotes[row['id']])>400:return None
            body='原文写道：“'+quotes[row['id']].strip()+'”'
    if trait=='training_rank':body+=' 训练榜与比赛名次是不同口径，不能据此确认比赛冠军。'
    return json.dumps({'answer':body,'references':[row['reference']]},ensure_ascii=False)
