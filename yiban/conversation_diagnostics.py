"""Content-free, bounded, volatile traces. An unguessable UUID is the read capability."""
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
import threading
import time
import uuid

STAGES = {'asr', 'speaker', 'routing', 'memory', 'access', 'model_wait', 'model', 'tts'}


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

    def read(self, identity):
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
            return {'steps': result, 'available': True}


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
        TRACES.end(identity, sequence, status)
        raise
    else:
        TRACES.end(identity, sequence)


def measured(stage, function, *args, **kwargs):
    with phase(stage):
        return function(*args, **kwargs)


def model_events(events):
    """Measure time to first answer text separately from the remaining generation."""
    try:
        with phase('model_wait'):
            while True:
                event = next(events)
                if event.get('error'):
                    raise ValueError('模型回答失败，请检查服务连接后重试。')
                yield event
                if event.get('message', {}).get('content') or event.get('done'):
                    break
        with phase('model'):
            for event in events:
                if event.get('error'):
                    raise ValueError('模型回答中断，请重试。')
                yield event
    except StopIteration:
        return
    finally:
        events.close()
