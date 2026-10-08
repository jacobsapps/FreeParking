"""Fail-closed boundaries for disposable Free Parking tests, never production.

The ownership journal is separate from recovery: a recovery file cannot grant
permission to target a window. Only receipts from this shim's creates can do so.
"""
import json
import os
from pathlib import Path
import signal
import tempfile


class SafetyError(RuntimeError):
    pass


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.scope-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if json.loads(path.read_text()) != data:
            raise SafetyError('Test ownership journal failed durable readback.')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def session_ids(window):
    return [s['id'] for t in window['tabs'] for s in t['sessions']]


class Scope:
    def __init__(self, path):
        self.path = Path(path)
        self.journal_failed = False
        self.data = json.loads(self.path.read_text())
        d = self.data
        if (d.get('schema_version') != 2 or not isinstance(d.get('protected_windows'), list)
                or not isinstance(d.get('protected_processes'), list) or not d['protected_processes']
                or not isinstance(d.get('owned'), dict) or not isinstance(d.get('launch_commands'), list)
                or not d['launch_commands'] or not all(isinstance(c, str) and c for c in d['launch_commands'])):
            raise SafetyError('Missing protective baseline. Legacy test scopes are not safe to run.')
        self.protected_windows = {w['id'] for w in d['protected_windows']}
        self.protected_sessions = {sid for w in d['protected_windows'] for sid in session_ids(w)}
        self.protected_processes = {(p['pid'], p['started']) for p in d['protected_processes']}
        for wid, ids in d['owned'].items():
            if (wid in self.protected_windows or not isinstance(ids, list) or not ids
                    or len(ids) != len(set(ids)) or self.protected_sessions.intersection(ids)):
                raise SafetyError('Test ownership overlaps protected windows or is invalid.')

    @classmethod
    def capture(cls, path, terminal, processes, commands):
        path = Path(path)
        if path.exists():
            raise SafetyError('Never overwrite a test baseline; use a new scope directory.')
        # Process inspection MUST succeed before any fixture is launched.
        baseline_processes = processes.all()
        if not baseline_processes or os.getpid() not in baseline_processes:
            raise SafetyError('Cannot protect this test runner: process inspection is incomplete.')
        baseline_windows = terminal.windows()
        write_private(path, dict(schema_version=2, protected_windows=baseline_windows,
                                protected_processes=list(baseline_processes.values()),
                                owned={}, owned_processes=[], launch_commands=commands, pending_create=None))
        return cls(path)

    def save(self):
        try:
            write_private(self.path, self.data)
        except Exception:
            self.journal_failed = True
            raise

    def owned_windows(self, raw):
        owned = self.data['owned']
        # A test tab moved to a working window must not become invisible and be
        # mistaken for a completed close. Contamination stops the whole test.
        owned_ids = {sid for ids in owned.values() for sid in ids}
        result = []
        for window in raw:
            wid, ids = window['id'], session_ids(window)
            if wid not in owned:
                if owned_ids.intersection(ids):
                    raise SafetyError('A test tab moved outside its owned window. Test stopped.')
                continue
            if (wid in self.protected_windows or not set(ids).issubset(owned[wid])
                    or any(len(t['sessions']) != 1 for t in window['tabs'])):
                raise SafetyError('An owned window acquired an unknown tab or split. Test stopped.')
            result.append(window)
        return result

    def audit(self, raw):
        """Read-only presence check. Never restores/mutates a changed work window."""
        current = {w['id']: session_ids(w) for w in raw}
        changed = [w['id'] for w in self.data['protected_windows']
                   if current.get(w['id']) != session_ids(w)]
        if changed:
            raise SafetyError('Protected window identities/order changed. Stop and inspect; do not repair automatically.')
        self.owned_windows(raw)
        return len(self.data['protected_windows'])


class ScopedTerminal:
    ACTIONS = {'create', 'check', 'close', 'focus', 'set-title', 'set-titles', 'set-bounds'}

    def __init__(self, scope, terminal):
        self.scope, self.terminal = scope, terminal

    def windows(self):
        return self.scope.owned_windows(self.terminal.windows())

    def call(self, action, **kwargs):
        if action == 'list':
            return self.windows()
        if action not in self.ACTIONS:
            raise SafetyError('Unknown terminal operation refused by test shim.')
        if self.scope.journal_failed or self.scope.data.get('pending_create') is not None:
            raise SafetyError('An earlier test create has no receipt. No further mutations allowed.')
        raw = self.terminal.windows()
        self.scope.audit(raw)
        windows = {w['id']: w for w in self.scope.owned_windows(raw)}
        requests = kwargs.get('titles', [kwargs])
        if not isinstance(requests, list) or not requests:
            raise SafetyError('Empty test target list.')
        for request in requests:
            wid = request.get('windowId')
            if action == 'create' and wid is None:
                continue
            if wid not in windows:
                raise SafetyError('Test shim refuses an unowned window.')
            actual = session_ids(windows[wid])
            ids = request.get('sessionIds', [request['sessionId']] if 'sessionId' in request else [])
            if action != 'create' and (not ids or not set(ids).issubset(actual)):
                raise SafetyError('Test shim refuses an unowned/missing session.')
            if action in ('close', 'check', 'set-bounds') and ids != actual:
                raise SafetyError('Test target must match the exact remaining tab order.')
        if action != 'create':
            return self.terminal.call(action, **kwargs)
        if kwargs.get('command') not in self.scope.data['launch_commands']:
            raise SafetyError('Only pre-authorized fixture commands may be launched.')
        if kwargs.get('windowId') is not None:
            kwargs['sessionIds'] = session_ids(windows[kwargs['windowId']])
        self.scope.data['pending_create'] = dict(kwargs)
        self.scope.save()  # Intent precedes side effects; uncertainty stops tests.
        receipt = self.terminal.call(action, **kwargs)
        wid, sid = receipt['windowId'], receipt['sessionId']
        if (not isinstance(wid, str) or not wid or not isinstance(sid, str) or not sid
                or (kwargs.get('windowId') is None and wid in {w['id'] for w in raw})
                or sid in {sid for w in raw for sid in session_ids(w)}
                or wid in self.scope.protected_windows or sid in self.scope.protected_sessions
                or (kwargs.get('windowId') is not None and kwargs['windowId'] != wid)
                or any(sid in ids for ids in self.scope.data['owned'].values())):
            raise SafetyError('Create receipt overlaps a protected identity. Test stopped.')
        self.scope.data['owned'].setdefault(wid, []).append(sid)
        self.scope.data['pending_create'] = None
        self.scope.save()
        return receipt


class ScopedProcesses:
    def __init__(self, scope, terminal, processes):
        self.scope, self.terminal, self.processes = scope, terminal, processes

    def __getattr__(self, name):
        return getattr(self.processes, name)

    def all(self):
        current = self.processes.all()
        raw = self.terminal.terminal.windows()
        self.scope.audit(raw)
        windows = self.scope.owned_windows(raw)
        ttys = {s.get('tty') for w in windows for t in w['tabs'] for s in t['sessions']}
        ttys.difference_update({None, '', '??', '/dev/??'})
        known = {(p['pid'], p['started']): p for p in self.scope.data.get('owned_processes', [])}
        before = set(known)
        for p in current.values():
            key = (p['pid'], p['started'])
            if p.get('tty') in ttys and key not in self.scope.protected_processes:
                known[key] = p
        if set(known) != before:
            self.scope.data['owned_processes'] = list(known.values())
            self.scope.save()
        return current

    def send_signal(self, saved, number):
        if self.scope.journal_failed or self.scope.data.get('pending_create') is not None:
            raise SafetyError('Ambiguous test creation: no processes may be signalled.')
        if number not in (signal.SIGINT, signal.SIGTERM):
            raise SafetyError('Test shim permits no other signals or process-group operations.')
        current = self.processes.all()
        process = current.get(saved['pid'])
        if not process or process['started'] != saved['started']:
            raise SafetyError('Test process exited or PID was reused; no signal sent.')
        if (process['pid'], process['started']) in self.scope.protected_processes:
            raise SafetyError('Test shim refuses every process present before testing.')
        # Also protect the current helper and its ancestors if a scope is reused
        # by a later runner. Never depend on a guessed terminal title.
        ancestor, seen = current.get(os.getpid()), set()
        while ancestor and ancestor['pid'] not in seen:
            seen.add(ancestor['pid'])
            ancestor = current.get(ancestor['ppid'])
        if process['pid'] in seen:
            raise SafetyError('Test shim refuses the runner or its parent agent/shell.')
        raw = self.terminal.terminal.windows()
        self.scope.audit(raw)
        self.scope.owned_windows(raw)
        captured = any((p['pid'], p['started'], p.get('tty')) ==
                       (process['pid'], process['started'], process.get('tty'))
                       for p in self.scope.data.get('owned_processes', []))
        if not captured or process.get('tty') in (None, '', '/dev/??', '??'):
            raise SafetyError('No captured disposable-process identity; no signal sent.')
        # A close can remove the TTY from live windows before SIGTERM. Only
        # previously captured process births are eligible after that point.
        unowned_ttys = {s.get('tty') for w in raw if w['id'] not in self.scope.data['owned']
                        for t in w['tabs'] for s in t['sessions']}
        if process.get('tty') in unowned_ttys:
            raise SafetyError('The process TTY now belongs to an unowned window.')
        # Shared-server descendants must never be interrupted, even if their
        # controlling TTY was inherited from a disposable client.
        ancestor, seen = process, set()
        while ancestor and ancestor['pid'] not in seen:
            if '/app-server-daemon/' in ancestor.get('executable', ''):
                raise SafetyError('Shared Codex infrastructure is protected.')
            seen.add(ancestor['pid'])
            ancestor = current.get(ancestor['ppid'])
        self.processes.send_signal(saved, number)
