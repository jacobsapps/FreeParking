"""Join native conversation MCP endpoints to kernel-owned TUI listening sockets.

No display text, command-line resume IDs, timestamps, or 'latest' heuristics.
Explicit native subagent ancestry distinguishes one TUI owner from its workers.
Several unrelated root conversations remain ambiguous and are not parked.
"""
from .diagnostics import timed, event

from urllib.parse import urlsplit
import uuid
from .model import ParkingError, Uncertain
from .confirmation import MissingEndpoint


def loopback_origin(value):
    if not isinstance(value, str): return None
    try:
        url = urlsplit(value)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.username or url.password or url.path or url.query or url.fragment):
            return None
        return 'http://127.0.0.1:' + str(url.port)
    except ValueError:
        return None


class CodexIdentity:
    def __init__(self, processes, rpc):
        self.processes, self.rpc = processes, rpc
        self._observing = False
        self._snapshot = None
        self._bindings = {}

    def origin(self, sid):
        result = self.rpc.call('mcpServerStatus/list', dict(
            threadId=sid, serverName='codex_tui', detail='toolsAndAuthOnly', limit=100))
        if result.get('nextCursor'):
            raise Uncertain('Incomplete native Codex endpoint inventory.')
        rows = [r for r in result['data'] if r['name'] == 'codex_tui']
        if len(rows) > 1: raise Uncertain('More than one native Codex endpoint was returned.')
        if not rows or rows[0].get('runtimeStatus') != 'connected': return None
        return loopback_origin(rows[0].get('httpOrigin'))

    def begin_observation(self):
        self._observing = True
        self._snapshot = None
        self._bindings = {}

    def end_observation(self):
        try:
            if not self._bindings: return {}
            latest = self.inventory()
            failures = {}
            fresh = getattr(type(self.processes), 'fresh_listeners', None)
            endpoints = fresh(self.processes, list(self._bindings)) if fresh else None
            processes = self.processes.all()
            for process, listeners, sid in self._bindings.values():
                try:
                    self.validate_binding(process, listeners, sid, latest, processes, endpoints.get(process.pid, set()) if endpoints is not None else None)
                except ParkingError as error:
                    failures[process.pid] = str(error)
            return failures
        finally:
            self._observing = False
            self._snapshot = None
            self._bindings = {}

    def loaded(self):
        ids = self.rpc.call('thread/loaded/list', {})['data']
        if len(ids) > 512 or len(ids) != len(set(ids)):
            raise Uncertain('Invalid or oversized native Codex thread inventory.')
        for sid in ids: uuid.UUID(sid)
        return set(ids)

    @timed('codex.inventory')
    def inventory(self):
        # Cover newcomers, rather than requiring the entire native service to
        # stay unchanged. A disappeared unrelated worker is not a target race.
        ids = self.loaded()
        result = {}
        for _ in range(4):
            for sid in sorted(ids - result.keys()):
                try:
                    result[sid] = self.origin(sid)
                except ParkingError:
                    # Suppress a failed read only when native inventory proves
                    # that particular thread disappeared, never on a guess.
                    if sid in self.loaded(): raise
            latest = self.loaded()
            result = {sid: origin for sid, origin in result.items() if sid in latest}
            if latest <= result.keys(): return result
            ids = latest
        raise Uncertain('Native Codex inventory is still adding uninspected conversations. Observe again.')

    @timed('codex.binding')
    def validate_binding(self, process, listeners, sid, inventory, processes=None, endpoints=None):
        matches = [(thread, origin) for thread, origin in inventory.items() if origin in listeners]
        if (sid is None and matches) or (sid is not None and self.owning_thread(matches) != sid):
            raise Uncertain('This Codex terminal changed its owning conversation during inspection.')
        if (self.processes.loopback_listeners(process.pid) if endpoints is None else endpoints) != listeners:
            raise Uncertain('This Codex terminal changed its endpoint during inspection.')
        live = (self.processes.all() if processes is None else processes).get(process.pid)
        if not live or live['started'] != process.started or live['tty'] != process.tty:
            raise Uncertain('Codex process changed during endpoint inspection.')

    def owning_thread(self, matches):
        """Collapse only explicit native subagent relationships, never titles/forks."""
        if len(matches) == 1: return matches[0][0]
        if not matches: return None
        parents = {}
        for sid, _ in matches:
            thread = self.rpc.call('thread/read', dict(threadId=sid, includeTurns=False))['thread']
            if thread.get('id') != sid: raise Uncertain('Native Codex ancestry identity changed.')
            source = thread.get('source')
            if isinstance(source, str) and source in ('cli', 'vscode', 'exec', 'appServer'):
                parents[sid] = None
            elif isinstance(source, dict):
                subagent = source.get('subAgent')
                if not isinstance(subagent, dict): return None
                spawn = subagent.get('thread_spawn')
                if not isinstance(spawn, dict): return None
                parent = spawn.get('parent_thread_id')
                if not isinstance(parent, str): return None
                uuid.UUID(parent)
                parents[sid] = parent
            else: return None
        roots = [sid for sid, parent in parents.items() if parent is None]
        if len(roots) != 1: return None
        root = roots[0]
        for sid in parents:
            seen = set()
            while sid != root:
                if sid in seen or sid not in parents: return None
                seen.add(sid); sid = parents[sid]
        return root

    @timed('codex.resolve')
    def resolve(self, process):
        listeners = self.processes.loopback_listeners(process.pid)
        if not listeners:
            raise ParkingError('This Codex process has no native TUI endpoint to identify.')
        inventory = self._snapshot if self._snapshot is not None else self.inventory()
        if self._observing: self._snapshot = inventory
        matches = [(sid, origin) for sid, origin in inventory.items() if origin in listeners]
        if not matches:
            if self._observing:
                # A user-confirmed warm resume may use its exact CLI binding
                # after MissingEndpoint. A newly linked native owner must still
                # invalidate that fallback before this observation is used.
                self._bindings[process.pid] = (process, listeners, None)
            raise MissingEndpoint("Codex has not linked this terminal to its loaded conversation. No window was changed.")
        sid = self.owning_thread(matches)
        if sid is None:
            raise ParkingError('Codex endpoint does not identify exactly one loaded conversation. Nothing guessed or closed.')
        if not self._observing:
            self.validate_binding(process, listeners, sid, self.inventory())
        # During an observation all bindings are freshly checked together in
        # end_observation before any resolved tab is returned to a gate.
        if self._observing:
            self._bindings[process.pid] = (process, listeners, sid)
        return sid
