"""Per-turn cancellation with tombstones for cancel-before-start races."""
import re
import threading
import time
import uuid


class Cancelled(Exception):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.callbacks = set()

    def check(self):
        if self.event.is_set():
            raise Cancelled('本轮已打断。')

    def attach(self, callback):
        with self.lock:
            if self.event.is_set():
                callback()
                self.check()
            self.callbacks.add(callback)

    def detach(self, callback):
        with self.lock:
            self.callbacks.discard(callback)

    def cancel(self):
        with self.lock:
            self.event.set()
            callbacks = list(self.callbacks)
        for callback in callbacks:
            try:
                callback()
            except OSError:
                pass


class TurnRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def get(self, identity=None):
        identity = identity or uuid.uuid4().hex
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', identity):
            raise ValueError('无效的对话编号。')
        with self.lock:
            now = time.monotonic()
            self.items = {key: value for key, value in self.items.items() if now-value[0] < 900}
            if identity not in self.items:
                if len(self.items) >= 512:
                    raise ValueError('请求过多，请稍后重试。')
                self.items[identity] = (now, Cancellation())
            return self.items[identity][1]

    def cancel(self, identity):
        self.get(identity).cancel()
