"""Join native conversation MCP endpoints to kernel-owned TUI listening sockets.

No display text, command-line resume IDs, timestamps, or 'latest' heuristics.
Explicit native subagent ancestry distinguishes one TUI owner from its workers.
Several unrelated root conversations remain ambiguous and are not parked.
"""
from urllib.parse import urlsplit
import uuid
from .model import ParkingError, Uncertain


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

    def end_observation(self):
        try:
            if self._snapshot is not None and self._snapshot != self.inventory():
                raise Uncertain('Codex endpoint bindings changed during observation.')
        finally:
            self._observing = False
            self._snapshot = None

    def inventory(self):
        ids = self.rpc.call('thread/loaded/list', {})['data']
        if len(ids) > 512 or len(ids) != len(set(ids)):
            raise Uncertain('Invalid or oversized native Codex thread inventory.')
        result = {}
        for sid in ids:
            uuid.UUID(sid)
            result[sid] = self.origin(sid)
        if set(self.rpc.call('thread/loaded/list', {})['data']) != set(ids):
            raise Uncertain('Codex changed its loaded conversations during inspection.')
        return result

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

    def resolve(self, process):
        listeners = self.processes.loopback_listeners(process.pid)
        if not listeners:
            raise ParkingError('This Codex process has no native TUI endpoint to identify.')
        inventory = self._snapshot if self._snapshot is not None else self.inventory()
        if self._observing: self._snapshot = inventory
        matches = [(sid, origin) for sid, origin in inventory.items() if origin in listeners]
        sid = self.owning_thread(matches)
        if sid is None:
            raise ParkingError('Codex endpoint does not identify exactly one loaded conversation. Nothing guessed or closed.')
        if ((not self._observing and self.inventory() != inventory)
                or self.processes.loopback_listeners(process.pid) != listeners):
            raise Uncertain('Codex changed conversations or endpoint during inspection.')
        live = self.processes.all().get(process.pid)
        if not live or live['started'] != process.started or live['tty'] != process.tty:
            raise Uncertain('Codex process changed during endpoint inspection.')
        return sid
