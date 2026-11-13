"""Content-free, bounded, volatile traces. An unguessable UUID is the read capability."""
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
import threading
import time
import uuid
from urllib.parse import urlsplit
import urllib.error

STAGES = {'asr', 'speaker', 'routing', 'memory', 'access', 'model_wait', 'model', 'tts', 'draft', 'commit', 'cancel'}


class TraceStore:
    def __init__(self, limit=128, ttl=1800, clock=time.monotonic):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.items, self.lock = {}, threading.RLock()

    @staticmethod
    def valid(identity):
        try:
            return isinstance(identity, str) and str(uuid.UUID(identity, version=4)) == identity
        except (ValueError, AttributeError):
            return False

    def prune(self):
        now = self.clock()
        self.items = {k: v for k, v in self.items.items() if now-v['updated'] < self.ttl}

    def begin(self, identity):
        if not self.valid(identity):
            return None
        with self.lock:
            self.prune()
            if identity not in self.items:
                if len(self.items) >= self.limit:
                    del self.items[min(self.items, key=lambda k: self.items[k]['updated'])]
                self.items[identity] = {'updated': self.clock(), 'steps': [], 'sequence': 0}
        return identity

    def start(self, identity, stage):
        if not identity or stage not in STAGES:
            return None
        with self.lock:
            item = self.items.get(identity)
            if item is None:
                return None
            item['sequence'] += 1
            step = {'sequence': item['sequence'], 'stage': stage, 'status': 'running', 'start': self.clock()}
            item['steps'].append(step)
            item['steps'] = item['steps'][-64:]
            item['updated'] = self.clock()
            return step['sequence']

    def end(self, identity, sequence, status='done'):
        with self.lock:
            item = self.items.get(identity)
            if not item:
                return
            for step in item['steps']:
                if step['sequence'] == sequence and step['status'] == 'running':
                    step.update(status=status, elapsed_ms=round((self.clock()-step['start'])*1000))
                    item['updated'] = self.clock()

    def retrieval(self, identity, data):
        if not isinstance(data,dict):return None
        clean={}
        if data.get('strategy') in {'lexical','hybrid'}:clean['strategy']=data['strategy']
        if data.get('reason_code') in {'relevant_candidates','missing_attribute','no_relevant_memory','embedding_unavailable'}:clean['reason_code']=data['reason_code']
        for key in ('candidate_count','hit_count','cache_hits','elapsed_ms'):
            value=data.get(key)
            if type(value) is int and 0<=value<=1_000_000:clean[key]=value
        clean['fallback']=data.get('fallback') is True
        with self.lock:
            if identity in self.items:self.items[identity]['retrieval']=clean
        return clean

    def model_call(self, identity):
        with self.lock:
            item=self.items.get(identity)
            if item is None:return
            usage=item.setdefault('usage',{'model_calls':0,'reported_calls':0,'input_tokens':0,'output_tokens':0})
            usage['model_calls']+=1

    def model_usage(self, identity, counts):
        if counts is None:return
        with self.lock:
            item=self.items.get(identity)
            if item is None or 'usage' not in item:return
            usage=item['usage'];usage['reported_calls']+=1
            usage['input_tokens']+=counts[0];usage['output_tokens']+=counts[1]

    def materials(self, identity, sources, kind='searched'):
        if kind not in ('searched', 'read'):return
        with self.lock:
            item = self.items.get(identity)
            if item is None:return
            refs = item.setdefault('material_refs', {'searched':[], 'read':[]})
            if kind == 'searched':item['search_performed'] = True
            for source in sources[:40]:
                key, version = source.get('id'), source.get('version')
                if isinstance(key,str) and 1<=len(key)<=128 and type(version) is int and version>=0:
                    ref = {'id':key,'version':version}
                    if ref not in refs[kind] and len(refs[kind])<40:refs[kind].append(ref)

    def model_identity(self, identity, config, vision=False):
        name = config.get('vision_model' if vision else 'model')
        if not isinstance(name,str) or not 1<=len(name)<=160 or any(ord(c)<32 for c in name):return
        backend = config.get('backend')
        if backend not in ('ollama','openai_compatible'):return
        route = 'local_service' if urlsplit(config.get('base_url','')).hostname in {'localhost','127.0.0.1','::1'} else 'remote_service'
        entry = {'model':name,'backend':backend,'route':route}
        with self.lock:
            item = self.items.get(identity)
            if item is not None:
                models = item.setdefault('models',[])
                if entry not in models and len(models)<8:models.append(entry)

    def failure(self, identity, error, stage=None):
        code, http = 'unexpected', None
        name = type(error).__name__
        if name in {'Cancelled','GeneratorExit'}:code='cancelled'
        elif name == 'SharedError':code={401:'permission',403:'permission',409:'stale'}.get(error.status,'invalid_request')
        elif isinstance(error,(TimeoutError,)):code='timeout'
        elif isinstance(error,urllib.error.HTTPError) or name=='ModelHTTPError':
            code='model_http';http=getattr(error,'code',None)
        elif isinstance(error,(urllib.error.URLError,ConnectionError)):code='model_connection'
        elif name == 'AgentProtocolError':code='tool_protocol'
        elif isinstance(error,(ValueError,RuntimeError)) and stage in {'routing','model_wait','model'}:code='model_output'
        elif isinstance(error,OSError):code='model_connection'
        else:code='invalid_request' if isinstance(error,ValueError) else 'unexpected'
        failure={'code':code, 'stage':stage if stage in STAGES else None}
        if type(http) is int and 100<=http<=599:failure['http_status']=http
        with self.lock:
            item=self.items.get(identity)
            if item is not None and 'failure' not in item:item['failure']=failure

    def absorb(self, identity, trace):
        # Mirror only public timing/model metadata into a caller's trace capability.
        with self.lock:
            item=self.items.get(identity)
            if item is None:return
            for step in trace.get('steps',[]):
                item['sequence']+=1
                item['steps'].append({**step,'sequence':item['sequence'],'start':self.clock()-step.get('elapsed_ms',0)/1000})
            item['steps']=item['steps'][-64:]
            for key in ('models','failure','retrieval','usage'):
                if key in trace:item[key]=deepcopy(trace[key])

    def read(self, identity, private=False):
        if not self.valid(identity):
            return {'steps': [], 'available': False}
        with self.lock:
            self.prune()
            item = self.items.get(identity)
            if not item:
                return {'steps': [], 'available': False}
            result = deepcopy(item['steps'])
            for step in result:
                step.setdefault('elapsed_ms', round((self.clock()-step['start'])*1000))
                del step['start']
            usage=deepcopy(item.get('usage',{'model_calls':0,'reported_calls':0,'input_tokens':0,'output_tokens':0}))
            usage['complete']=usage['model_calls']>0 and usage['reported_calls']==usage['model_calls']
            if not usage['reported_calls']:usage['input_tokens']=usage['output_tokens']=None
            return {'steps': result, 'available': True, 'usage':usage,
                    **({'models':deepcopy(item['models'])} if 'models' in item else {}),
                    **({'failure':deepcopy(item['failure'])} if 'failure' in item else {}),
                    **({'material_refs':deepcopy(item.get('material_refs',{})), 'search_performed':item.get('search_performed',False)} if private else {}), **({'retrieval':deepcopy(item['retrieval'])} if 'retrieval' in item else {})}


TRACES = TraceStore()
CURRENT = ContextVar('conversation_trace', default=None)


@contextmanager
def phase(stage):
    identity = CURRENT.get()
    sequence = TRACES.start(identity, stage)
    try:
        yield
    except BaseException as error:
        # No exception text: upstream responses may contain private request data.
        status = 'cancelled' if type(error).__name__ in {'Cancelled', 'GeneratorExit'} else 'error'
        TRACES.failure(identity,error,stage)
        TRACES.end(identity, sequence, status)
        raise
    else:
        TRACES.end(identity, sequence)


def measured(stage, function, *args, **kwargs):
    with phase(stage):
        return function(*args, **kwargs)


def model_events(events):
    """Measure time to first answer text separately from the remaining generation."""
    identity=CURRENT.get();TRACES.model_call(identity);counts=None
    def observe(event):
        values=(event.get('prompt_eval_count'),event.get('eval_count'))
        if all(type(value) is int and 0<=value<=1_000_000_000 for value in values):return values
        return None
    try:
        with phase('model_wait'):
            while True:
                event = next(events)
                if event.get('error'):
                    raise ValueError('模型回答失败，请检查服务连接后重试。')
                counts=observe(event) or counts
                yield event
                if event.get('message', {}).get('content') or event.get('done'):
                    break
        with phase('model'):
            for event in events:
                if event.get('error'):
                    raise ValueError('模型回答中断，请重试。')
                counts=observe(event) or counts
                yield event
    except StopIteration:
        return
    finally:
        TRACES.model_usage(identity,counts)
        events.close()
