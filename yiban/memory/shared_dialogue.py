"""Short-lived, session-bound topic context. Never reuse old answers as evidence."""
import hashlib
import re
import secrets
import threading
import time


def broad_question(query):
    return bool(re.search(r'分别|对比|比较|区别|差别|所有|全部|各个|有哪些|哪些活动|汇总|总结|回顾', query))


def followup_question(query):
    text=re.sub(r'^(请问|请|那|那么|然后|还|再|我们|大家|这次|那次|这个活动|那个活动|它)+', '', query.strip())
    return bool(re.match(r'谁|几点|在哪|哪里|什么时候|多久|多长|多少|要带|带什么|需要|要准备|有什么|有没有|是否|能不能|费用|预算|负责人|集合|怎么去|结束|开始|日期|时间|地点',text)) or bool(re.match(r'^(它|这次|那次|这个|那个)',query))


def dates(text):
    return [f'{int(y):04d}-{int(m):02d}-{int(d):02d}' for y,m,d in re.findall(r'(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?',text)]


def catalog(items):
    """Group explicitly named records, keeping same-name events on different dates apart."""
    rows=[];unmatched=[]
    for item in items:
        content=item['content']
        titles=re.findall(r'[“「《"]([^”」》"\n]{4,80})[”」》"]',content)
        if not titles:
            titles=re.findall(r'(?:一次|举行|举办|组织|参加)([\u4e00-\u9fff]{2,18}(?:活动|读书会|分享会|研讨会|会议))',content)
        if not titles:
            # A heading-like event name need not be surrounded by quotation marks.
            titles=re.findall(r'^(?:参加|关于)?([\u4e00-\u9fff]{2,18}?(?:读书会|绘本会|分享会|研讨会|工作坊|工坊|展览|活动|会议))(?=定于|将于|在|于|旧版|的|需要|时[，,]|[，,:：。])',content)
        if not titles:
            unmatched.append(item);continue
        when=item.get('event_date') or next(iter(dates(content)), '')
        rows.append((titles[0],when,item))
    # Attach supplemental records only when one known title is explicitly present.
    names={row[0] for row in rows}
    for item in unmatched:
        matches=[name for name in names if name in item['content']]
        if len(matches)==1:rows.append((matches[0],item.get('event_date') or next(iter(dates(item['content'])),''),item))
    known={}
    for title,when,_ in rows:
        if when:known.setdefault(title,set()).add(when)
    topics={}
    for title,when,item in rows:
        if not when and len(known.get(title,set()))==1:when=next(iter(known[title]))
        key=hashlib.sha256((title+'\0'+when).encode()).hexdigest()[:24]
        topic=topics.setdefault(key,{'key':key,'title':title,'date':when,'ids':[],'states':set()})
        topic['ids'].append(item['id']);topic['states'].add(item['status'])
    for topic in topics.values():
        topic['label']=(topic['date']+' · ' if topic['date'] else '')+topic['title']
        topic['states']=sorted(topic['states'])
    return dict(sorted(topics.items(),key=lambda pair:(pair[1]["date"],pair[1]["title"])))


def matching(query,topics):
    query_dates=dates(query)
    # A month/day is also an explicit disambiguator, but never silently guesses the year.
    short_dates=re.findall(r'(\d{1,2})月(\d{1,2})[日号]?',query)
    words=set(re.findall(r'[\u4e00-\u9fff]{2,}',query))
    matches=[]
    for topic in topics.values():
        title=topic['title']
        lexical=title in query or any(len(w)>=3 and w in title for w in words)
        # Chinese questions have no spaces: accept distinctive shared bigrams, not just 活动.
        core=re.sub(r'活动|亲子|周末|会议','',title)
        pairs={core[i:i+2] for i in range(len(core)-1)}
        distinctive=pairs-{'秋日','冬季','春季','夏季','亲子','周末','计划','安排','体验','家庭','团队'}
        lexical=lexical or any(p in query for p in distinctive)
        when=topic['date']
        dated=when in query_dates if query_dates else any(when.endswith(f'-{int(m):02d}-{int(d):02d}') for m,d in short_dates)
        if (query_dates or short_dates) and not dated:continue
        if lexical or dated:matches.append(topic)
    return matches


class SharedDialogue:
    TTL=600
    LIMIT=128

    def __init__(self,error):
        self.error=error;self._entries={};self._lock=threading.Lock()

    def clear(self,session,identity=None):
        fingerprint=hashlib.sha256(session.encode()).hexdigest()
        with self._lock:
            for key in list(self._entries):
                if self._entries[key]['session']==fingerprint and (identity is None or identity==key):self._entries.pop(key,None)

    def begin(self,token,guard,scope,identity=None):
        now=time.monotonic();session=hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            self._entries={k:v for k,v in self._entries.items() if v['expires']>now}
            previous=self._entries.get(identity) if isinstance(identity,str) else None
            if identity and (not previous or previous['session']!=session or previous['guard']!=guard or previous['scope']!=scope):
                raise self.error('上轮话题已失效，或空间内容、权限发生变化。请重新说出活动名称。',409)
            # Rotate at each turn; parallel/replayed turns cannot overwrite each other.
            if identity:self._entries.pop(identity,None)
        return {'session':session,'guard':guard,'scope':scope,'previous':previous,'focus':None,'pending':None}

    def clarify(self,plan,query,topics,prefix='有多个可能的活动，请先选择你指的是哪一个：'):
        choices=[{'id':secrets.token_urlsafe(12),'topic':topic} for topic in topics[:6]]
        plan['pending']={'query':query,'choices':choices} if choices else None
        plan['focus']=None
        answer=prefix+'\n'+'\n'.join(f'{i}. {c["topic"]["label"]}' for i,c in enumerate(choices,1))
        if len(topics)>6:answer+='\n还有其他活动，请补充名称和日期缩小范围。'
        if not choices:answer='请先告诉我活动名称或日期，再问集合、带队或携带物品等安排。'
        return {'query':query,'items':[],'clarification':answer,'plan':plan}

    def route(self,plan,query,items,choice_id=None):
        topics=catalog(items);plan['topics']=topics
        previous=plan['previous'] or {};pending=previous.get('pending')
        explicit=matching(query,topics)
        selected=None;effective=query
        if choice_id:
            choices=(pending or {}).get('choices',[])
            choice=next((c for c in choices if c['id']==choice_id),None)
            if not choice or choice['topic']['key'] not in topics:raise self.error('活动选项已失效，请重新提问。',409)
            selected=topics[choice['topic']['key']];effective=pending['query']
        elif pending:
            number=re.fullmatch(r'(?:第)?([1-6一二三四五六])(?:个|项|条)?[。！!]?',query.strip())
            if number:
                value=number[1];index=int(value)-1 if value.isdigit() else '一二三四五六'.index(value)
                choices=pending['choices']
                if index<len(choices):selected=topics.get(choices[index]['topic']['key']);effective=pending['query']
            elif len(explicit)==1 and not re.search(r'谁|哪里|哪儿|几点|什么|多少|是否|吗|？|\?',query):
                selected=explicit[0];effective=pending['query']
            if not selected and not explicit:
                return self.clarify(plan,pending['query'],[topics[c['topic']['key']] for c in pending['choices'] if c['topic']['key'] in topics], '还不能确定你指的是哪次活动，请选择或补充日期：')
        if not selected:
            if broad_question(query):return {'query':query,'items':items,'plan':plan}
            if len(explicit)>1:return self.clarify(plan,query,explicit)
            if explicit:selected=explicit[0]
            elif followup_question(query):
                focus=previous.get('focus')
                if focus and focus['key'] in topics:selected=topics[focus['key']]
                elif len(topics)==1:selected=next(iter(topics.values()))
                else:return self.clarify(plan,query,list(topics.values()))
        if selected:
            plan['focus']=selected
            effective=f'关于{selected["label"]}：{effective}'
            items=[item for item in items if item['id'] in selected['ids']]
        return {'query':effective,'items':items,'plan':plan}

    def after_retrieval(self,plan,query,sources):
        if plan['focus'] or broad_question(query):return None
        ids={item['id'] for item in sources}
        found=[t for t in plan['topics'].values() if ids.intersection(t['ids'])]
        if len(found)>1:return self.clarify(plan,query,found)
        if len(found)==1:plan['focus']=found[0]
        return None

    def finish(self,plan,keep_focus=True):
        focus=plan['focus'] if keep_focus else None
        pending=plan['pending']
        if not focus and not pending:return {'id':None,'topic':None,'choices':[],'needs_clarification':False}
        identity=secrets.token_urlsafe(32)
        entry={k:plan[k] for k in ('session','guard','scope')}
        # Retain identifiers and labels, never prior model answers or record bodies.
        entry.update(focus=focus,pending=pending,expires=time.monotonic()+self.TTL)
        with self._lock:
            while len(self._entries)>=self.LIMIT:self._entries.pop(next(iter(self._entries)))
            self._entries[identity]=entry
        return {'id':identity,'topic':focus['label'] if focus else None,'needs_clarification':bool(pending),
                'choices':[{'id':c['id'],'label':c['topic']['label']} for c in (pending or {}).get('choices',[])]}
