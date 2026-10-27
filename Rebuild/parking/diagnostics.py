"""Bounded, private timing traces. Never record commands, titles or transcripts."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import datetime
import json
import os
import time
import uuid

_active = ContextVar('parking_trace', default=None)
_parent = ContextVar('parking_span', default=None)
MAX_BYTES = 8 * 1024 * 1024

class Trace:
    def __init__(self, directory, action):
        self.path = None
        self.stream = None
        self.size = 0
        self.started = time.perf_counter()
        try:
            directory = Path(directory)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            files = sorted(directory.glob('timing-*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
            total = 0
            for index, old in enumerate(files):
                total += old.stat().st_size
                if index >= 29 or total > 32 * 1024 * 1024: old.unlink()
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
            self.path = directory / ('timing-' + stamp + '-' + uuid.uuid4().hex[:8] + '.jsonl')
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.stream = os.fdopen(fd, 'w', buffering=1)
        except OSError:
            pass  # Diagnostics must not prevent saving or reopening work.
        self.action = action

    def write(self, event_name, **fields):
        if self.stream is None or self.size >= MAX_BYTES: return
        try:
            line = json.dumps(dict(event=event_name, elapsed_ms=round((time.perf_counter()-self.started)*1000, 3), **fields))+'\n'
            self.stream.write(line)
            self.size += len(line)
        except (OSError, ValueError): pass

    def __enter__(self):
        self.token = _active.set(self)
        self.write('operation.begin', action=self.action, pid=os.getpid())
        return self

    def __exit__(self, kind, value, traceback):
        self.write('operation.end', action=self.action, outcome='error' if kind else 'ok', error_type=kind.__name__ if kind else None)
        _active.reset(self.token)
        if self.stream:
            try: self.stream.close()
            except OSError: pass

def event(name, **fields):
    trace = _active.get()
    if trace: trace.write(name, parent=_parent.get(), **fields)

@contextmanager
def span(name, **fields):
    trace = _active.get()
    if trace is None:
        yield
        return
    ident = uuid.uuid4().hex[:10]
    parent = _parent.get()
    token = _parent.set(ident)
    started = time.perf_counter()
    trace.write('span.begin', span=ident, parent=parent, name=name, **fields)
    outcome = 'ok'
    try:
        yield
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        trace.write('span.end', span=ident, parent=parent, name=name, duration_ms=round((time.perf_counter()-started)*1000,3), outcome=outcome)
        _parent.reset(token)

def timed(name):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with span(name): return function(*args, **kwargs)
        return measured
    return decorate
