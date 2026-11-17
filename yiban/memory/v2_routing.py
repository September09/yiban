"""Conservative coverage hints for routing; never an answer or an authority check."""
import re
from yiban.memory import v2_relevance

# Vocabulary only. These normalizations do not supply names, dates or quantities.
_ALIASES = (('庆生','生日'),('课程','课'),('作品','投稿'),('人数','人'),('哪一天','日期'),('哪天','日期'),('什么时候','日期'),('何时','日期'),('哪两天','日期'))
_FILLER = ('日期','来当','我们班','我们','同学','最后','一共','正式','请问','告诉我','分别','各自','叫什么','是什么','是多少','哪几个','哪两个','哪几个月','几个月','多少','的','是','有','给','在','了','办','谁','哪','吗','呢','啊','呀')
_DATE = r'日期|什么时间|几月几日'
_NUMBER = r'(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百]+)\s*'
_UNITS = r'人|位|名|幅|本|门|公里|米|首|个'

def normalized(text):
    for old,new in _ALIASES:text=text.replace(old,new)
    return text

def anchors(text):
    text=text.replace('哪几个月',' ').replace('几个月',' ')
    text=re.sub(r'(?:多少|几)\s*(?:人|位|名|幅|本|门|公里|米|首|个)', ' ', text)
    for word in sorted(_FILLER,key=len,reverse=True):text=text.replace(word,' ')
    text=re.sub(r'(?:\d+|[零一二两三四五六七八九十百]+)(?:门|幅|位|名|人)', ' ', text)
    return v2_relevance.terms(text)

def covering_record(plan, query, sources, quotes, items=None):
    """One reviewed row must cover every clause in both its text and its evidence.

    This only avoids a second retrieval planner. The existing answer generator,
    reference validation and permission/version checks still run unchanged.
    """
    query=normalized(query)
    clauses=[p.strip() for p in re.split(r'[，,；;？?。！!]+',query) if p.strip()]
    if not clauses or re.search(r'和|以及|及|与|、',query):return False
    focus=plan.get('focus') or {}
    complete=[]
    selected={r['id'] for r in sources}
    # Other readable rows can block a shortcut, but cannot become proof without
    # an authorized exact-evidence read and inclusion in the answer payload.
    for row in items if items is not None else sources:
        if row.get('verification') not in ('confirmed','pending'):continue
        if focus and row['id'] not in focus.get('ids',[]):continue
        text=normalized(row['title']+' '+row['content'])
        has_evidence=row['id'] in selected and bool(quotes.get(row['id'],'').strip())
        quote=normalized(quotes.get(row['id'],row['content']))
        if not quote.strip():continue
        if re.search(r'未记载|没有记载|不详|有争议|无法确认',row['content']):continue
        text_terms=anchors(text);quote_terms=anchors(quote);matched=0;covered=True
        for clause in clauses:
            terms=anchors(clause)
            for field in ('奖','课','评委'):
                if field in clause and not all(field in s for s in (text,quote)):
                    covered=False;break
            if not covered:break
            date=bool(re.search(_DATE,clause))
            month=bool(re.search(r'哪几个月|几月|月份',clause))
            count=not month and bool(re.search(r'多少|几(?:位|名|人|幅|本|门|公里|米|首|个)',clause))
            if month and not all(re.search(r'\d{1,2}月',s) for s in (text,quote)):
                covered=False;break
            if date and not all(re.search(r'\d{1,2}月\d{1,2}日|20\d{2}-\d{2}-\d{2}',s) for s in (text,quote)):
                covered=False;break
            # Costs/identifiers and unrecognized units must keep the existing route.
            if re.search(r'钱|价格|费用|金额|号码|编号|推算|证明',clause):covered=False;break
            if count:
                unit=re.search(r'(?:多少|几)\s*('+_UNITS+r')',clause)
                if not unit:covered=False;break
                units=r'(?:人|位|名)' if unit[1] in ('人','位','名') else unit[1]
                if not all(re.search(_NUMBER+units,s) for s in (text,quote)):covered=False;break
            # Require question-specific anchors in current text AND actual evidence;
            # matching an event title or merely seeing a date is not sufficient.
            if terms:
                if min(len(terms & text_terms),len(terms & quote_terms))/len(terms)<.8:
                    covered=False;break
                matched+=len(terms & text_terms & quote_terms)
            elif not (date or count):covered=False;break
        if covered and (matched>=2 or focus):complete.append((row['id'],has_evidence and row['verification']=='confirmed'))
    return complete[0][0] if len(complete)==1 and complete[0][1] else None


def covered_record(plan, query, sources, quotes, items=None):
    return bool(covering_record(plan,query,sources,quotes,items))
