"""Explicit, single-use user confirmation for a stale Codex resume endpoint.

A launch UUID is only a suggestion, never automatic proof of the displayed
conversation. Confirmation is scoped to one topology/process snapshot and is
not remembered across operations. No daemon configuration is changed.
"""
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import time
import uuid
from .model import ParkingError
from .store import atomic_json

class MissingEndpoint(ParkingError):
    pass

class ConfirmationRequired(ParkingError):
    def __init__(self, candidate):
        super().__init__("Codex reports an old terminal endpoint after resume. Confirm the displayed session when parking.")
        self.candidate = candidate

def resume_candidate(process, session, processes, rpc):
    command = processes.command(process.pid)
    try: argv = shlex.split(command)
    except ValueError: return None
    # Deliberately narrow: no picker, --last, title, fork or arbitrary prompt.
    if len(argv) != 3 or argv[1] != 'resume' or argv[0] != process.executable:
        return None
    try: sid = str(uuid.UUID(argv[2]))
    except ValueError: return None
    thread = rpc.call('thread/read', dict(threadId=sid, includeTurns=False))['thread']
    cwd = processes.cwd(process.pid)
    if (thread.get('id') != sid or thread.get('cwd') != cwd
            or thread.get('source') not in ('cli','vscode','exec','appServer')
            or thread.get('status',{}).get('type') == 'notLoaded'):
        return None
    return dict(terminal_id=session['id'], process=asdict(process), command=command,
                session_id=sid, cwd=cwd, name=thread.get('name') or 'Saved Codex session')

def binding(candidate):
    return {k:v for k,v in candidate.items() if k != 'name'}

def topology(snapshot):
    return [[w['id'], [[s['id'] for s in t['sessions']] for t in w['tabs']]] for w in snapshot.raw]

def prepare(root, snapshot, window_id=None):
    candidates = snapshot.confirmation_candidates
    selected = [w for w in snapshot.raw if window_id is None or w['id'] == window_id]
    affected = {s['id'] for w in selected for t in w['tabs'] for s in t['sessions']}
    if not selected or not affected.intersection(candidates): return None
    # No confirmation may paper over a different failure (split panes, unknown jobs).
    if any(sid not in candidates for sid in snapshot.tab_issues if sid in affected):
        return None
    entries = []
    for wi,w in enumerate(snapshot.raw,1):
        if window_id is not None and w['id'] != window_id: continue
        for ti,tab in enumerate(w['tabs'],1):
            for s in tab['sessions']:
                if s['id'] in candidates:
                    entries.append(dict(window_number=wi, tab_number=ti, title=s.get('title',''),
                                        **candidates[s['id']]))
    ident = str(uuid.uuid4())
    payload = dict(id=ident, expires=time.time()+120, window_id=window_id,
                   topology=topology(snapshot), entries=entries,
                   fingerprints={w.window_id:w.fingerprint for w in snapshot.windows})
    atomic_json(root/'confirmations'/(ident+'.json'), payload)
    return dict(id=ident, entries=entries)

def accept(root, ident, observer):
    # A backend command without the exact recent challenge cannot authorize this.
    if str(uuid.UUID(ident)) != ident: raise ParkingError('Invalid session confirmation.')
    path = root/'confirmations'/(ident+'.json')
    try: payload = json.loads(path.read_text())
    except (OSError,ValueError) as e: raise ParkingError('Session confirmation is missing or was already used. Park again to review.') from e
    path.unlink()  # Single use, including validation failures.
    if payload['expires'] < time.time(): raise ParkingError('Session confirmation expired. Park again to review.')
    snapshot = observer.observe()
    if json.loads(json.dumps(topology(snapshot))) != payload['topology']:
        raise ParkingError('Your windows changed. Nothing closed; park again to review.')
    if {w.window_id:w.fingerprint for w in snapshot.windows} != payload['fingerprints']:
        raise ParkingError('A session changed. Nothing closed; park again to review.')
    for entry in payload['entries']:
        current = snapshot.confirmation_candidates.get(entry['terminal_id'])
        if current is None or binding(current) != binding({k:v for k,v in entry.items() if k not in ('window_number','tab_number','title')}):
            raise ParkingError('The resumed tab changed. Nothing closed; park again to review.')
    # Operation-local authorization only; never write this into settings or hooks.
    provider = observer.providers.get('codex')
    provider.confirmed_resumes = {
        entry['terminal_id']: binding({k:v for k,v in entry.items() if k not in ('window_number','tab_number','title')})
        for entry in payload['entries']
    }
    return payload['window_id']
