"""Source snapshots are saved only with confirmed memories, never from model-written quotes."""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from yiban.memory.shared_memory import SharedError

class SharedEvidence:
    def __init__(self, shared):
        self.shared=shared
        self.lock=threading.Lock()
        self.drafts={}
        with shared.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS shared_evidence(
                memory TEXT PRIMARY KEY REFERENCES shared_memories(id) ON DELETE CASCADE,
                source_name TEXT NOT NULL, source_text TEXT NOT NULL, source_hash TEXT NOT NULL,
                quotes_json TEXT NOT NULL, captured_content TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @staticmethod
    def locate(text, start, end):
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):
            raise SharedError('原文位置无效，请重新整理资料。')
        pages=list(re.finditer(r'【第 (\d+) 页】',text[:start+1]))
        page=int(pages[-1].group(1)) if pages else None
        header=re.match(r'【第 (\d+) 页】',text[start:end])
        if header:page=int(header.group(1))
        first=text.count('\n',0,start)+1
        last=text.count('\n',0,end-1)+1
        return {'start':start,'end':end,'line_start':first,'line_end':last,'page':page,
                'location':(f'第 {page} 页 · ' if page else '')+f'原文第 {first}–{last} 行',
                'text':text[start:end]}

    def stage(self, token, space_id, sources, candidates):
        now=time.monotonic();key=secrets.token_urlsafe(32)
        # A candidate points to one immutable reviewed source and one or more exact ranges.
        records=[]
        for item in candidates:
            source=sources[item.pop('_source')]
            quotes=[self.locate(source['text'],start,end) for start,end in item.pop('_ranges')]
            records.append({'source_name':source['name'],'source_text':source['text'],
                'source_hash':hashlib.sha256(source['text'].encode()).hexdigest(),'quotes':quotes})
            item['evidence_ref']={'draft':key,'candidate':len(records)-1}
            item['locations']=[q['location'] for q in quotes]
        with self.lock:
            self.drafts={k:v for k,v in self.drafts.items() if v['expires']>now}
            if len(self.drafts)>=32:self.drafts.pop(next(iter(self.drafts)))
            self.drafts[key]={'session':self.shared._token_hash(token),'space':space_id,'records':records,'expires':now+3600}

    def resolve(self, token, space_id, ref, content):
        if not isinstance(ref,dict) or not isinstance(ref.get('draft'),str) or type(ref.get('candidate')) is not int:
            raise SharedError('缺少原文依据，请重新生成候选记忆。')
        with self.lock:
            draft=self.drafts.get(ref['draft'])
            if not draft or draft['expires']<=time.monotonic():
                self.drafts.pop(ref['draft'],None)
                raise SharedError('原文暂存已过期，请重新生成候选记忆。',409)
            if draft['session']!=self.shared._token_hash(token) or draft['space']!=space_id:
                raise SharedError('原文不属于当前会话或空间。',403)
            index=ref['candidate']
            if not 0<=index<len(draft['records']):raise SharedError('原文引用无效。')
            record=copy.deepcopy(draft['records'][index])
        record['captured_content']=content
        return record

    @staticmethod
    def save(db, memory, evidence):
        if not evidence:return
        db.execute('INSERT INTO shared_evidence(memory,source_name,source_text,source_hash,quotes_json,captured_content) VALUES (?,?,?,?,?,?)',
                   (memory,evidence['source_name'],evidence['source_text'],evidence['source_hash'],
                    json.dumps(evidence['quotes'],ensure_ascii=False),evidence['captured_content']))

    def read(self, token, data):
        with self.shared.store.connect() as db:
            db.execute('BEGIN');actor=self.shared._actor(db,token)
            space=self.shared._member(db,actor['id'],data.get('space_id'))
            memory=self.shared._record(db,data.get('id'))
            if memory['space']!=space['id'] or not self.shared._visible(actor,space,memory):
                raise SharedError('没有查看这条记忆依据的权限。',403)
            full=space['role']=='owner' or actor['id']==memory['author']
            if data.get('include_full') is True and not full:
                raise SharedError('完整原文仅仍在空间内的作者和管理员可查看。',403)
            row=db.execute('SELECT * FROM shared_evidence WHERE memory=?',(memory['id'],)).fetchone()
            if not row:return {'available':False,'message':'这条记忆没有保留原文依据，无法从来源说明还原。'}
            result={k:row[k] for k in ('source_name','source_hash','captured_content','created')}
            result.update(available=True,quotes=json.loads(row['quotes_json']),can_read_full=full,
                          content_changed=memory['content']!=row['captured_content'],memory_status=memory['status'])
            if data.get('include_full') is True:result['source_text']=row['source_text']
            return result

    def discard(self, token, space_id, key):
        if not isinstance(key,str):return
        with self.lock:
            draft=self.drafts.get(key)
            if draft and draft['session']==self.shared._token_hash(token) and draft['space']==space_id:
                self.drafts.pop(key,None)
