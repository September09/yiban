"""Conservative event counts over the complete, already authorized snapshot."""
from collections import defaultdict
import re


def count_kind(query):
    if not re.search(r'几次|多少次|几场|多少场|几回|次数|场次|总共.{0,8}(聚餐|生日会|篮球|徒步)|累计.{0,8}(聚餐|生日会|篮球|徒步)',query):return None
    if re.search(r'聚餐|聚过餐|聚了餐|吃.{0,4}饭',query):return '聚餐',r'聚餐|共进晚餐|共进午餐'
    if re.search(r'生日会|庆生会',query):return '生日会',r'生日会|庆生会'
    if re.search(r'篮球',query):return '篮球比赛',r'篮球.{0,14}(?:赛|对阵)|(?:对阵|对战)\s*\d{2}[A-Za-z]'
    if re.search(r'徒步|登山|爬山',query):return '徒步',r'徒步|登山|爬山'
    return None


def event_title(item):
    parts=item.get('source_note','').split('｜')
    if len(parts)>=3:return parts[2].strip()
    titles=re.findall(r'[“《]([^”》]{3,50})[”》]',item['content'])
    return titles[0] if titles else ''


def count_events(query,items,today):
    kind=count_kind(query)
    if not kind:return None
    label,pattern=kind;scopes=defaultdict(list)
    query_years=set(re.findall(r'(20\d{2})年',query))
    query_months={int(x) for x in re.findall(r'(\d{1,2})月',query)}
    if re.search(r'今年|本年',query):query_years.add(str(today.year))
    if '本月' in query:query_years.add(str(today.year));query_months.add(today.month)
    attendance=bool(re.search(r'我(?!们).{0,8}(?:参加|参与|出席|去过|聚餐过|聚过餐)',query))
    query_dates={f'{int(y):04d}-{int(m):02d}-{int(d):02d}' for y,m,d in re.findall(r'(20\d{2})年(\d{1,2})月(\d{1,2})日',query)}
    for item in items:
        if attendance and item.get('origin')!='personal':continue
        if re.search(pattern,item['content']):scopes[item['scope_key']].append(item)
    if not scopes:
        return {'answer':('团队活动记录不能直接证明你本人出席；' if attendance else '')+'当前可见资料中没有找到可逐场统计的'+label+'记录，不能据此认定实际发生过0次。','sources':[],'answer_source_ids':[],'source_usage':'program_selected','calculation':{'kind':label,'complete':False,'groups':[]}}
    lines=['按当前可见资料逐条核对并去重，以下是已记录的活动下限，不能代表实际全部次数：'];sources=[];groups=[]
    for scope,rows in scopes.items():
        events={};excluded=[];undated=[]
        for item in rows:
            content=item['content'];when=item.get('event_date','');title=event_title(item)
            if re.search(r'将于|拟于|计划|预告|暂定|打算|取消|未举行|未举办|未参加|没有参加',content):excluded.append(item);continue
            if re.search(r'(?:累计|一共|总共|已经|已).{0,8}[一二三四五六七八九十\d]+次',content):excluded.append(item);continue
            if when and when>today.isoformat():excluded.append(item);continue
            if not when:
                dates=re.findall(r'(20\d{2})年(\d{1,2})月(\d{1,2})日',content)
                if len(set(dates))==1:when='-'.join(f'{int(x):0{4 if i==0 else 2}d}' for i,x in enumerate(dates[0]))
            if when and when>today.isoformat():excluded.append(item);continue
            ranged=len(query_dates)==2 and bool(re.search(r'至|到|之间',query))
            if when and query_years and not ranged and when[:4] not in query_years:continue
            if when and query_months and not (len(query_dates)==2 and re.search(r'至|到|之间',query)) and int(when[5:7]) not in query_months:continue
            if when and query_dates:
                ordered=sorted(query_dates)
                ranged=len(ordered)==2 and bool(re.search(r'至|到|之间',query))
                if ranged and not ordered[0]<=when<=ordered[-1]:continue
                if not ranged and when not in query_dates:continue
            # Different records about a same-day event are not new occurrences.
            # This conservative merge can undercount two same-day events; do not
            # present the result as an exact lifetime total.
            key=when
            if not key:
                if title:key='未记日期:'+title
                else:undated.append(item);continue
            event=events.setdefault(key,{'date':when,'title':title,'ids':[],'records':[]})
            event['ids'].append(item['id']);event['records'].append(item)
        for item in undated:
            if not events:
                events['日期待核实']={'date':'','title':'日期和活动身份待核实','ids':[item['id']],'records':[item]}
            else:excluded.append(item)
        # An undated supplemental row with a matching known title is the same event.
        for key,event in list(events.items()):
            if event['date'] or not event['title']:continue
            same=[e for e in events.values() if e['date'] and e['title']==event['title']]
            if len(same)==1:
                same[0]['ids']+=event['ids'];same[0]['records']+=event['records'];del events[key]
            elif len(same)>1:
                excluded+=event['records'];del events[key]
        scope_name=rows[0]['scope_name'];dated=sum(bool(e['date']) for e in events.values());undated_groups=len(events)-dated
        count=dated if dated else (1 if events and not query_years else 0)
        lines.append('\n'+scope_name+'：'+('已记录至少'+str(count)+'次'+label+'。' if count else '没有足够的已发生记录可计数，不能认定实际为0次。'))
        if undated_groups:lines.append('另有'+str(undated_groups)+'组具体日期未记载的活动记录，不能直接假定它们与其他记录互不重复。')
        lines.append('共整理出'+str(len(events))+'组相关活动记录；同一组的多条报道只算一组。')
        for number,(key,event) in enumerate(sorted(events.items()),1):
            contents=[]
            for item in event['records']:
                if item['content'] not in contents:contents.append(item['content'])
                if item['id'] not in {s['id'] for s in sources}:sources.append(dict(item,reference=len(sources)+1))
            lines.append(str(number)+'. '+(event['date'] or '具体日期未记载')+'：'+contents[0])
        if any(x['status']=='pending' for e in events.values() for x in e['records']):lines.append('以上包含待核实记录，次数与活动细节仍需核对。')
        if excluded:lines.append('另有'+str(len(excluded))+'条预告、汇总或身份不明确的记录未单独加次数。')
        groups.append({'scope':scope,'scope_name':scope_name,'count':count,'record_groups':len(events),'undated_groups':undated_groups,'events':[{k:v for k,v in e.items() if k!='records'} for e in events.values()],'excluded_count':len(excluded)})
    lines.append('\n统计口径：同一范围内同日同类记录合并，未记日期的记录按活动线索核对；个人和不同团队分别列出，不直接相加。不是按搜索命中条数计数。')
    return {'answer':'\n'.join(lines),'sources':sources,'answer_source_ids':[s['id'] for s in sources],'source_usage':'program_selected','calculation':{'kind':label,'complete':False,'groups':groups}}
