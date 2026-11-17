"""Convert supplied narrative cards without treating inferred dates or public hints as facts/ACL."""
import hashlib
import json
import re
from yiban.memory.v2_schema import SCHEMA, validate
from yiban.memory.shared_memory import SharedError


def convert(document, original=None):
    if not isinstance(document,dict) or not isinstance(document.get('cards'),list) or not 1<=len(document['cards'])<=200:
        raise SharedError('请选择含1–200张cards的候选文件；新版包请直接校验。')
    if original is not None and (not isinstance(original,str) or not original.strip() or len(original)>200000):
        raise SharedError('原文需为1–200000字。')
    source_text=original if original else json.dumps(document,ensure_ascii=False,indent=2)
    sha=hashlib.sha256(source_text.encode()).hexdigest()
    source_key='source:'+sha[:24]
    source=document.get('source',{})
    if not isinstance(source,dict):raise SharedError('来源字段无效。')
    entities={};records=[];events=[];warnings=[]
    for n,c in enumerate(document['cards']):
        if not isinstance(c,dict):raise SharedError('候选卡片格式无效。')
        identity='card:'+sha[:12]+':'+str(n+1)
        title=c.get('title','');summary=c.get('what','');quote=c.get('quote') or summary
        if not isinstance(quote,str) or not quote:raise SharedError('候选缺少可核对的摘录。')
        excerpt=quote if original else json.dumps(quote,ensure_ascii=False)[1:-1]
        offset=source_text.find(excerpt)
        if offset<0:raise SharedError(f'第{n+1}条摘录不在提供的原文中，请先核对。')
        participants=[]
        if not isinstance(c.get('who_names',[]),list) or len(c.get('who_names',[]))>80:raise SharedError('人物名单格式无效。')
        for name in c.get('who_names',[]):
            if not isinstance(name,str):raise SharedError('人物姓名格式无效。')
            entity='person:'+sha[:12]+':'+hashlib.sha256(name.encode()).hexdigest()[:16]
            entities.setdefault(entity,{'key':entity,'kind':'person','name':name,'aliases':[]});participants.append(entity)
        kind=c.get('type','reflection')
        if kind not in {'event','quote'}:
            kind='reflection';warnings.append(title+'：叙述性事实暂作感受保留，尚未提取结构化事实。')
        if kind=='event' and (re.search(r'300\+|训练足迹|瘦了.*迎来|第一次越野与',title+summary)):
            kind='reflection';warnings.append(title+'：阶段总结或混合事件，需审阅拆分，不参与事件计数。')
        event_key=None
        if kind=='event':
            event_key='event:'+sha[:12]+':'+str(n+1)
            when=c.get('when','');precision=c.get('when_precision','unknown')
            if precision not in {'day','month','year'} or not when:when=None;precision='unknown'
            explicit=bool(original and when and (when in original or re.sub(r'-(\d\d)-',lambda m:'年'+str(int(m[1]))+'月',when) in original))
            events.append({'key':event_key,'kind':'narrative_event','title':title,'state':'unknown','location':'','parent_key':None,
                           'time':{'raw':c.get('when',''),'start':when,'end':None,'precision':precision,'basis':'explicit' if explicit else 'inferred' if when else 'unknown'}})
        records.append({'key':identity,'type':kind,'title':title,'summary':summary,'detail':c.get('detail',''),
                        'participants':list(dict.fromkeys(participants)),'event_key':event_key,'fact':None,
                        'emotions':c.get('emotion',[]),'tags':c.get('tags',[]),'confidence':c.get('confidence'),
                        'verification':'pending','verification_reason':'从候选文件转换，尚待核对原文及事件边界。',
                        'sensitivity':'personal' if c.get('visibility')=='private' else 'normal',
                        'evidence':[{'source_key':source_key,'start':offset,'end':offset+len(excerpt),'text':excerpt}]})
    packet={'schema':SCHEMA,'batch_name':source.get('source_title') or source.get('name') or '候选转换',
            'sources':[{'key':source_key,'name':source.get('name') or '候选文件.json','kind':'original' if original else 'candidate',
                        'text':source_text,'sha256':sha,'published_at':source.get('source_date',''),'url':source.get('source_url','')}],
            'entities':list(entities.values()),'events':events,'records':records}
    if not original:warnings.insert(0,'没有提供原始文章，当前依据仅为候选JSON；不读取文件里的本机路径，也不将候选引文冒充原文。')
    warnings.append('文件里的owner、public和needs_review只作为线索，不能决定账号归属、团队权限或核实状态。事件发生状态需人工核对。')
    return {'document':validate(packet),'warnings':warnings,'notice':'仅生成新版候选，尚未入库。'}
