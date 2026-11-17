"""Read bounded, reviewed source packets; ranges are checked against supplied text."""
import hashlib
from yiban.memory.shared_memory import SharedError

SCHEMA = 'yiban.reviewed-materials.v1'

def unpack(document, text):
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise SharedError('请选择正式审阅导入包，原始候选审阅JSON不能直接导入。')
    raw_sources, items = document.get('sources'), document.get('items')
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 10:
        raise SharedError('每个审阅包需有1–10份文字来源。')
    if not isinstance(items, list) or not 1 <= len(items) <= 40:
        raise SharedError('每个审阅包需有1–40条候选。')
    sources=[]
    for source in raw_sources:
        if not isinstance(source, dict):raise SharedError('来源格式无效。')
        name=text(source.get('name'),'来源名称',160)
        original=text(source.get('text'),'原文',30000)
        if source.get('sha256') != hashlib.sha256(original.encode()).hexdigest():
            raise SharedError('来源摘要与文字不一致，请重新生成审阅包。')
        sources.append({'name':name,'text':original})
    if sum(len(s['text']) for s in sources)>30000:raise SharedError('每包原文合计最多30000字，请分批。')
    result=[];keys=set()
    for item in items:
        if not isinstance(item,dict):raise SharedError('候选格式无效。')
        key=text(item.get('review_key'),'审阅编号',64)
        if key in keys:raise SharedError('审阅编号重复。')
        keys.add(key)
        index=item.get('source_index');ranges=item.get('ranges')
        if type(index) is not int or not 0<=index<len(sources):raise SharedError('原文编号无效。')
        if not isinstance(ranges,list) or not 1<=len(ranges)<=12:raise SharedError('每条候选需有1–12段原文依据。')
        original=sources[index]['text'];positions=[];quotes=[]
        for span in ranges:
            if not isinstance(span,dict):raise SharedError('原文摘录格式无效。')
            start,end=span.get('start'),span.get('end')
            if type(start) is not int or type(end) is not int or not 0<=start<end<=len(original) or end-start>2000:
                raise SharedError('原文位置或摘录长度无效。')
            if original[start:end]!=span.get('text'):raise SharedError('原文摘录不匹配，请重新生成审阅包。')
            positions.append((start,end));quotes.append(original[start:end])
        result.append({'review_key':key,'content':text(item.get('content'),'候选内容',1200),
                       'event_date':item.get('event_date',''),'source_note':text(item.get('source_note',''),'来源说明',200,0),
                       'status':item.get('status','pending'),'status_reason':text(item.get('status_reason',''),'状态说明',200,0),
                       'evidence':'\n\n'.join(quotes),'source_name':sources[index]['name'],'_source':index,'_ranges':positions})
    return sources,result,text(document.get('batch_name'),'批次名称',160)
