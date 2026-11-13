"""Finite, local fact rules: suspicions, never a proof of truth or semantic completeness."""
from datetime import date
import re

STATES={'pending','valid','outdated'}
FIELDS=('称呼','居住城市','工作城市','回答语言','回答风格','最喜欢的饮料','最喜欢的颜色','集合时间','集合地点')


def state_fields(status='pending',reason='',event_date=''):
    if not isinstance(status,str) or status not in STATES:raise ValueError('请选择待核实、有效或过时状态。')
    if not isinstance(reason,str) or len(reason.strip())>500 or any(ord(c)<32 and c not in '\n\t' for c in reason):raise ValueError('状态说明需为不超过500字的文字。')
    if not isinstance(event_date,str) or (event_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',event_date)):raise ValueError('事件日期须为YYYY-MM-DD或留空。')
    if event_date:
        try:date.fromisoformat(event_date)
        except ValueError:raise ValueError('事件日期无效。') from None
    if status in ('valid','outdated') and not reason.strip():raise ValueError('标记有效或过时前请填写状态说明。')
    return {'status':status,'status_reason':reason.strip(),'event_date':event_date}


def fact_key(text):
    text=re.sub(r'\s+','',text).rstrip('。.!！?？')
    style=re.fullmatch(r'我喜欢(简短|详细)(?:的)?回答',text)
    if style:return ('回答风格',style[1])
    preference=re.fullmatch(r'我(不再喜欢|不喜欢|喜欢)([^，。；;！？!?]{1,80})',text)
    if preference:return ('喜好:'+preference[2],'不喜欢' if preference[1]=='不再喜欢' else preference[1])
    field=re.fullmatch(r'(?:我的)?('+'|'.join(FIELDS)+r')(?:[：:]|是)(.{1,80})',text)
    if field:return (field[1],field[2])
    city=re.fullmatch(r'我(?:现在)?(?:住在|居住在)([^，。；;！？!?]{1,40})',text)
    if city:return ('居住城市',city[1])
    return None


def possible_conflicts(content,rows,exclude=None):
    fact=fact_key(content)
    if not fact:return []
    result=[]
    for row in rows:
        other=fact_key(row['content'])
        if row['id']!=exclude and row.get('status','pending')!='outdated' and other and fact[0]==other[0] and fact[1]!=other[1]:
            result.append({k:row.get(k) for k in ('id','content','status','status_reason','event_date','revision')})
            result[-1].update(status=row.get('status','pending'),reason='同一事项存在不同或相反表述；不同时期可能合理，请核对日期与语境')
    return result


def conflict_pairs(rows,limit=50):
    groups={};pairs=[];total=0
    for row in rows:
        if row.get('status','pending')=='outdated':continue
        fact=fact_key(row['content'])
        if fact:groups.setdefault(fact[0],[]).append((row,fact[1]))
    for key,items in groups.items():
        for i,(left,a) in enumerate(items):
            for right,b in items[i+1:]:
                if a==b:continue
                total+=1
                if len(pairs)<limit:pairs.append({'left':left,'right':right,'field':key,'reason':'同一事项有不同说法，需要本人核对；不自动判断真伪。'})
    return {'pairs':pairs,'total':total,'limited':total>len(pairs)}
