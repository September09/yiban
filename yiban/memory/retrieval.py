"""Permission-scoped retrieval. SQLite memories remain authoritative; vectors are disposable."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from yiban.turns import Cancellation, Cancelled

DEFAULT_CONFIG = {
    'enabled': False, 'strategy': 'hybrid', 'base_url': 'http://127.0.0.1:11434',
    'model': 'qwen3-embedding:0.6b', 'top_k': 5, 'min_similarity': 0.45,
    'min_lexical': 0.18, 'relative_score_floor': 0.85, 'timeout': 25, 'batch_size': 16,
}
QUERY_INSTRUCTION = 'Instruct: Given a question, retrieve relevant personal or team memory passages that contain information to answer it.\nQuery: '
PREPROCESS_VERSION = 'memory-v1-ctx4096'


class RetrievalUnavailable(Exception):
    pass


def validate_config(config):
    if not isinstance(config, dict) or set(config) - set(DEFAULT_CONFIG):
        raise ValueError('检索配置包含未知字段。')
    c = dict(DEFAULT_CONFIG, **config)
    if type(c['enabled']) is not bool or c['strategy'] not in {'lexical', 'hybrid'}:
        raise ValueError('检索开关或策略无效。')
    u = urlsplit(c['base_url']) if isinstance(c['base_url'], str) else None
    if not u or u.scheme != 'http' or u.hostname not in {'127.0.0.1','localhost','::1'} or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
        raise ValueError('记忆向量服务目前只允许本机 HTTP 地址。')
    try:
        if u.port is not None and not 1 <= u.port <= 65535: raise ValueError()
    except ValueError:
        raise ValueError('向量服务端口无效。') from None
    for key, low, high in [('top_k',1,8),('timeout',1,120),('batch_size',1,32)]:
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError(f'retrieval.{key} 超出允许范围。')
    for key in ('min_similarity','min_lexical','relative_score_floor'):
        if type(c[key]) not in (float,int) or not math.isfinite(c[key]) or not 0 < c[key] <= 1:
            raise ValueError(f'retrieval.{key} 必须在 0–1 之间且大于 0。')
    if not isinstance(c['model'],str) or not c['model'].strip() or len(c['model']) > 120:
        raise ValueError('向量模型名称无效。')
    c['base_url'] = c['base_url'].rstrip('/')
    return c


def normalize(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text.lower())


def bigrams(text):
    text=normalize(text)
    return {text[i:i+2] for i in range(len(text)-1)}


def query_terms(query):
    text=re.sub(r'请问|请|帮我|一下|有没有|是否|怎么|如何|什么|哪儿|哪里|哪位|多少|是谁|谁|我们|大家|你们|这件事|这次|那次|安排|举行|活动|记录|记忆|查询|查看|回顾', '', query)
    return bigrams(text)


def lexical_score(query, content):
    terms=query_terms(query)
    overlap=len(terms & bigrams(content))
    return overlap/max(len(terms),1),overlap


def facet_supported(query, item):
    """Conservative checks for explicit missing attributes; not general answer entailment."""
    content=item['content']
    if re.search(r'预算|多少钱|花了多少|费用|经费|收费|价钱|价格',query):
        if not re.search(r'预算|经费|费用|收费|价钱|价格|免费|\d+(?:\.\d+)?\s*元',content):return False
    if re.search(r'电话|手机号码|联系方式',query):
        if not re.search(r'电话|手机|联系方式|\b1[3-9]\d{9}\b|\d{3,4}-\d{7,8}',content):return False
    return True


def focused_results(query, ranked, scores, floor):
    """Keep a score band for focused questions; broad/comparison queries need wider coverage."""
    if not ranked or re.search(r'所有|全部|各个|有哪些|哪些活动|分别|对比|比较|区别|差别|回顾|总结|汇总',query):
        return ranked
    cutoff=scores[ranked[0]['id']][0]*floor
    return [item for item in ranked if scores[item['id']][0]>=cutoff]


def unit_vector(values):
    if not isinstance(values,list) or not 16 <= len(values) <= 4096:
        raise RetrievalUnavailable('vector_shape')
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in values):
        raise RetrievalUnavailable('vector_value')
    norm=math.hypot(*values)
    if not math.isfinite(norm) or norm < 1e-12:raise RetrievalUnavailable('empty_vector')
    return [x/norm for x in values]


class OllamaEmbedding:
    def __init__(self, config):
        self.config=validate_config(config)

    def _request(self, path, payload, cancel, deadline):
        cancel.check();remaining=deadline-time.monotonic()
        if remaining<=0:raise RetrievalUnavailable('timeout')
        u=urlsplit(self.config['base_url'])
        conn=http.client.HTTPConnection(u.hostname,u.port or 80,timeout=remaining)
        def abort():
            try:
                if conn.sock:conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            conn.close()
        try:
            cancel.attach(abort)
            raw=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
            conn.request('GET' if payload is None else 'POST',path,body=raw,headers={'Content-Type':'application/json'})
            response=conn.getresponse()
            data=response.read(16_000_001);cancel.check()
            if response.status!=200 or len(data)>16_000_000:raise RetrievalUnavailable('embedding_service')
            return json.loads(data)
        except Cancelled:raise
        except Exception:
            cancel.check();raise RetrievalUnavailable('embedding_service') from None
        finally:cancel.detach(abort);conn.close()

    def fingerprint(self, cancel, deadline):
        data=self._request('/api/tags',None,cancel,deadline)
        if not isinstance(data,dict) or not isinstance(data.get('models'),list):raise RetrievalUnavailable('model_list')
        for model in data['models']:
            if not isinstance(model,dict):continue
            if model.get('name')==self.config['model'] and isinstance(model.get('digest'),str):
                return self.config['model']+':'+model['digest']+':'+PREPROCESS_VERSION
        raise RetrievalUnavailable('model_missing')

    def embed(self, texts, cancel, deadline):
        data=self._request('/api/embed',{'model':self.config['model'],'input':texts,'truncate':False,'keep_alive':'5m','options':{'num_ctx':4096}},cancel,deadline)
        if not isinstance(data,dict):raise RetrievalUnavailable('embedding_shape')
        values=data.get('embeddings')
        if not isinstance(values,list) or len(values)!=len(texts):raise RetrievalUnavailable('embedding_shape')
        vectors=[unit_vector(v) for v in values]
        if len({len(v) for v in vectors})!=1:raise RetrievalUnavailable('embedding_shape')
        return vectors


class VectorCache:
    """No plaintext or query history is stored. Access is by trusted candidate ID/hash."""
    def __init__(self,path):self.path=Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=1)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT, id TEXT, fingerprint TEXT, version TEXT, hash TEXT, vector TEXT, PRIMARY KEY(namespace,id,fingerprint))')
                yield db
        finally:db.close()

    @staticmethod
    def digest(item):
        return hashlib.sha256(item['content'].encode()).hexdigest()

    def load(self,namespace,items,fingerprint):
        result={}
        with self.connect() as db:
            for item in items:
                row=db.execute('SELECT vector FROM vectors WHERE namespace=? AND id=? AND fingerprint=? AND version=? AND hash=?',
                    (namespace,item['id'],fingerprint,str(item['version']),self.digest(item))).fetchone()
                if row:
                    try:result[item['id']]=unit_vector(json.loads(row[0]))
                    except (ValueError,TypeError,RetrievalUnavailable):pass
        return result

    def save(self,namespace,items,fingerprint,vectors):
        with self.connect() as db:
            for item,vector in zip(items,vectors):
                db.execute('DELETE FROM vectors WHERE namespace=? AND id=?',(namespace,item['id']))
                db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?)',(namespace,item['id'],fingerprint,str(item['version']),self.digest(item),json.dumps(vector,separators=(',',':'))))

    def invalidate(self,namespace,identity=None):
        if not self.path.exists():return
        with self.connect() as db:
            if identity is None:db.execute('DELETE FROM vectors WHERE namespace=?',(namespace,))
            else:db.execute('DELETE FROM vectors WHERE namespace=? AND id=?',(namespace,identity))


class MemoryRetriever:
    def __init__(self, config=None, cache_path=None, provider=None):
        self.config=validate_config(config or {})
        self.provider=provider or OllamaEmbedding(self.config)
        self.cache=VectorCache(cache_path) if cache_path else None
        self._lock=threading.Lock()

    def invalidate(self,namespace,identity=None):
        if self.cache:
            try:self.cache.invalidate(namespace,identity)
            except (sqlite3.Error,OSError):pass  # Query-time hashes and ACLs still reject stale entries.

    def retrieve(self, query, candidates, *, namespace, top_k=None, cancel=None):
        cancel=cancel or Cancellation();cancel.check();started=time.monotonic()
        deadline=started+self.config['timeout'];top_k=self.config['top_k'] if top_k is None else top_k
        if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('检索问题为空或过长。')
        if not isinstance(namespace,str) or not namespace:raise ValueError('检索范围无效。')
        if type(top_k) is not int or not 1<=top_k<=8:raise ValueError('检索条数无效。')
        # Copy trusted candidates so external mutations cannot change an in-flight lookup.
        items=[dict(x) for x in candidates]
        if len({x['id'] for x in items})!=len(items):raise ValueError('重复的候选编号。')
        eligible=[x for x in items if facet_supported(query,x)]
        lexical={x['id']:lexical_score(query,x['content']) for x in eligible}
        strategy=self.config['strategy'];fallback=False;reason='relevant_candidates';cache_hits=0;scores={}
        if eligible and strategy=='hybrid':
            acquired=False
            try:
                while not acquired:
                    cancel.check()
                    if time.monotonic()>=deadline:raise RetrievalUnavailable('timeout')
                    acquired=self._lock.acquire(timeout=.05)
                fingerprint=self.provider.fingerprint(cancel,deadline)
                vectors=self.cache.load(namespace,eligible,fingerprint) if self.cache else {}
                cache_hits=len(vectors);missing=[x for x in eligible if x['id'] not in vectors]
                batch_size=self.config['batch_size']
                for offset in range(0,len(missing),batch_size):
                    cancel.check()
                    if time.monotonic()>=deadline:raise RetrievalUnavailable('timeout')
                    batch=missing[offset:offset+batch_size]
                    encoded=self.provider.embed([x['content'] for x in batch],cancel,deadline)
                    if self.cache:self.cache.save(namespace,batch,fingerprint,encoded)
                    vectors.update({x['id']:v for x,v in zip(batch,encoded)})
                qv=self.provider.embed([QUERY_INSTRUCTION+query],cancel,deadline)[0]
                for item in eligible:
                    v=vectors[item['id']]
                    if len(v)!=len(qv):raise RetrievalUnavailable('embedding_shape')
                    sim=sum(a*b for a,b in zip(v,qv));lex,overlap=lexical[item['id']]
                    if sim>=self.config['min_similarity'] or (lex>=self.config['min_lexical'] and overlap>=2):
                        scores[item['id']]=(.85*sim+.15*lex,sim)
            except Cancelled:raise
            except (RetrievalUnavailable,sqlite3.Error,OSError,ValueError):
                cancel.check();fallback=True;strategy='lexical';reason='embedding_unavailable';scores={}
            finally:
                if acquired:self._lock.release()
        if strategy=='lexical':
            for item in eligible:
                lex,overlap=lexical[item['id']]
                if lex>=self.config['min_lexical'] and overlap>=2:scores[item['id']]=(lex,None)
        cancel.check()
        priority={'valid':0,'pending':1,'outdated':2}
        ranked=sorted((x for x in eligible if x['id'] in scores),key=lambda x:(-scores[x['id']][0],priority.get(x.get('status'),1),x['id']))
        ranked=focused_results(query,ranked,scores,self.config['relative_score_floor'])[:top_k]
        hits=[{'id':x['id'],'version':x['version'],'score':round(scores[x['id']][0],6)} for x in ranked]
        if not fallback:reason='relevant_candidates' if hits else 'missing_attribute' if items and not eligible else 'no_relevant_memory'
        meta={'strategy':strategy,'reason_code':reason,'candidate_count':len(items),'hit_count':len(hits),'cache_hits':cache_hits,'fallback':fallback,'elapsed_ms':round((time.monotonic()-started)*1000)}
        return {'decision':'found' if hits else 'no_match','hits':hits,**meta}
