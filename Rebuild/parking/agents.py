"""Exact agent identity and resume specifications, independent of tab titles."""
import base64
import json
from pathlib import Path
import shlex
import time
import uuid
from .model import ParkingError, Process, Tab, Uncertain, NotSubmitted
from .permissions import launch_flags, CLAUDE_MODES


def preflight_executable(executable):
    from .adapters import run
    # The same login-shell PATH used by the actual new terminal command.
    path = run(['/bin/zsh', '-lc', 'command -v -- '+shlex.quote(executable)]).strip().splitlines()
    if not path or not Path(path[-1]).is_file():
        raise ParkingError('Cannot find '+executable+' in your login shell. Nothing closed.')


def command(tab, receipt, argv, prompt=None):
    tab.validate(); uuid.UUID(receipt)
    if not Path(tab.cwd).is_dir(): raise ParkingError('The saved working folder is missing.')
    token = base64.b64encode(receipt.encode()).decode()
    args = list(argv)
    if prompt is not None:
        if not prompt.strip() or '\0' in prompt: raise ParkingError('Invalid continuation message.')
        args.append(prompt)
    body = ('cd ' + shlex.quote(tab.cwd) + " && printf '\\033]1337;SetUserVar=freeParkingReceipt="
            + token + "\\007' && exec " + shlex.join(args))
    return shlex.join(['/bin/zsh', '-lc', body])


def exact_process(session, processes, name):
    candidates = [p for p in processes.values() if p['tty'] == session['tty']
                  and ((name == 'codex' and Path(p['executable']).name == 'codex'
                        and '/app-server-daemon/' not in p['executable'])
                       or (name == 'claude' and ('/claude/' in p['executable'] or Path(p['executable']).name == 'claude')))]
    if len(candidates) != 1: raise ParkingError('Cannot identify one '+name+' process for this tab.')
    p = candidates[0]
    return p, Process(p['pid'],p['started'],p['tty'],p['executable'],p['ppid'])


def client_closed(tab, processes):
    live = processes.all().get(tab.process.pid)
    return not live or live['started'] != tab.process.started


def finish_client_close(tab, processes, authorize):
    import signal
    try:
        live = processes.all().get(tab.process.pid)
        if not live or live['started'] != tab.process.started: return
        if live['tty'] != tab.tty or live['executable'] != tab.process.executable or processes.cwd(tab.process.pid) != tab.cwd:
            raise ParkingError('Closed terminal process changed identity. No signal sent.')
        authorize()
    except Exception as error:
        raise NotSubmitted(str(error)) from error
    processes.send_signal(dict(pid=tab.process.pid,started=tab.process.started,tty=tab.tty),
                          signal.SIGINT if tab.provider == 'claude' else signal.SIGTERM)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        if client_closed(tab,processes):return
        time.sleep(.2)
    raise Uncertain('The closed terminal client has not exited. No force-kill or automatic replay.')


class Claude:
    name = 'claude'
    def __init__(self, processes, root=None, executable=None):
        self.processes = processes
        self.root = Path(root) if root else Path.home()/'.claude'
        # Resolve in the user's login shell, as Codex does. Claude supports
        # native, Homebrew and npm installs; ~/.local/bin is not universal.
        self.executable = str(executable or 'claude')

    def registry(self, process):
        try: record = json.loads((self.root/'sessions'/(str(process.pid)+'.json')).read_text())
        except (OSError, ValueError) as error:
            raise ParkingError('Claude has not published its session identity yet. Finish any startup, sign-in or folder-access prompt in this tab, then refresh.') from error
        if (record.get('pid') != process.pid or record.get('pidDomain') != 'darwin'
                or record.get('kind') != 'interactive' or record.get('entrypoint') != 'cli'
                or ' '.join(record.get('procStart','').split()) != self.processes.started_utc(process.pid)):
            raise ParkingError('Claude process registry is stale; no title or latest-session fallback is used.')
        uuid.UUID(record['sessionId'])
        if record['cwd'] != self.processes.cwd(process.pid): raise ParkingError('Claude’s process and saved folder disagree.')
        return record

    def saved(self, sid, cwd):
        uuid.UUID(sid)
        paths = list((self.root/'projects').glob('*/'+sid+'.jsonl'))
        if len(paths) != 1 or paths[0].stat().st_size == 0:
            raise ParkingError('Claude has not persisted this exact conversation yet.')
        # Validate transcript identity and recorded folder, without using its title.
        with paths[0].open() as stream:
            for line in stream:
                entry = json.loads(line)
                if entry.get('sessionId') == sid and entry.get('cwd') == cwd: return paths[0]
        raise ParkingError('Claude transcript does not confirm this conversation and folder.')

    def identify(self, session, processes):
        _, process = exact_process(session, processes, self.name)
        record = self.registry(process)
        self.saved(record['sessionId'],record['cwd'])
        state = {'busy':'running','idle':'idle','waiting':'waiting'}.get(record.get('status'),'unknown')
        mode = record.get('permissionMode')
        permissions = dict(mode=mode) if isinstance(mode, str) and mode in CLAUDE_MODES else {}
        return Tab(session['id'],session['tty'],session.get('title',''),self.name,record['sessionId'],record['cwd'],process,state,
                   permissions=permissions)

    def client_closed(self, tab): return client_closed(tab, self.processes)

    def finish_close(self, tab, authorize): finish_client_close(tab, self.processes, authorize)

    def launch(self, tab, receipt, prompt=None):
        if tab.provider != self.name: raise ParkingError('Provider changed.')
        self.saved(tab.session_id,tab.cwd)
        return command(tab,receipt,[self.executable,*launch_flags(tab),'--resume',tab.session_id],prompt)

    def preflight(self, tab): preflight_executable(self.executable)

    def activity(self, tab):
        record = self.registry(tab.process)
        if record['sessionId'] != tab.session_id or record['cwd'] != tab.cwd:
            raise ParkingError('Claude switched conversations. Nothing interrupted.')
        return {'busy':'running','idle':'idle','waiting':'waiting'}.get(record.get('status'),'unknown')

    def interruption_marker(self, tab, path, offset):
        if path.stat().st_size < offset: return None
        with path.open() as stream:
            stream.seek(offset)
            for line in stream:
                try: entry = json.loads(line)
                except ValueError: continue
                if entry.get('type') != 'user' or entry.get('sessionId') != tab.session_id: continue
                content = entry.get('message', {}).get('content', [])
                texts = [content] if isinstance(content, str) else [c.get('text') for c in content if isinstance(c, dict) and c.get('type') == 'text']
                if texts in (['[Request interrupted by user]'], ['[Request interrupted by user for tool use]']):
                    return entry.get('uuid')
        return None

    def request_state(self, tab, path):
        """Latest persisted user request, retaining identity after natural completion.

        Claude writes thinking and text as separate assistant rows. A thinking
        row can carry end_turn while the response is still being produced.
        """
        request_id, completed = None, False
        with path.open() as stream:
            for line in stream:
                try: entry = json.loads(line)
                except ValueError as error:
                    raise ParkingError('Claude request history is incomplete. Nothing closed.') from error
                if not isinstance(entry, dict):
                    raise ParkingError('Claude request history is unreadable. Nothing closed.')
                if entry.get('sessionId') != tab.session_id or entry.get('isSidechain'): continue
                message = entry.get('message', {})
                if not isinstance(message, dict):
                    raise ParkingError('Claude request history is unreadable. Nothing closed.')
                content = message.get('content', [])
                if not isinstance(content, (str, list)):
                    raise ParkingError('Claude request history is unreadable. Nothing closed.')
                text = bool(content.strip()) if isinstance(content, str) else any(
                    isinstance(c, dict) and c.get('type') == 'text'
                    and isinstance(c.get('text'), str) and c['text'].strip() for c in content)
                if entry.get('type') == 'user':
                    # Image/document-only requests are new work too; only pure
                    # tool-result messages belong to the existing user request.
                    if isinstance(content, list):
                        kinds = [c.get('type') if isinstance(c, dict) else None for c in content]
                        if not kinds or any(k not in ('text', 'image', 'document', 'tool_result') for k in kinds):
                            raise ParkingError('Claude user request is unreadable. Nothing closed.')
                        if all(k == 'tool_result' for k in kinds): continue
                        user_request = text or any(k in ('image', 'document') for k in kinds)
                    else: user_request = text
                    if not user_request:
                        raise ParkingError('Claude user request is empty. Nothing closed.')
                    request_id, completed = entry.get('uuid'), False
                    if not isinstance(request_id, str) or not request_id:
                        raise ParkingError('Claude request identity is unavailable. Nothing closed.')
                elif entry.get('type') == 'assistant' and request_id:
                    reason = message.get('stop_reason')
                    if reason in ('stop_sequence', 'max_tokens', 'refusal') or (reason == 'end_turn' and text):
                        completed = True
        return dict(request_id=request_id, completed=completed)

    def pending_request(self, tab, path):
        state = self.request_state(tab, path)
        return None if state['completed'] else state['request_id']

    def continuation_request(self, tab):
        """Best-effort evidence for the OPTIONAL follow-up message, not identity.

        Identity and resumability are checked by identify()/launch() separately.
        A missing, partial or unfamiliar request record grants no auto-send credit;
        it must never block parking an otherwise verified exact conversation.
        """
        try:
            state = self.request_state(tab, self.saved(tab.session_id, tab.cwd))
            request_id = state['request_id']
            if state['completed'] or not request_id: return None
            uuid.UUID(request_id)
            return request_id
        except (ParkingError, OSError, ValueError, TypeError, KeyError):
            return None

    def prepare_close_interruption(self, tab):
        request_id = self.continuation_request(tab)
        if request_id is None: return None
        return dict(provider=self.name, session_id=tab.session_id, request_id=request_id,
                    method='terminal-close')

    def validate_close_interruption(self, tab, checkpoint):
        """Whether continuation evidence is still valid; never a close permit."""
        return bool(isinstance(checkpoint, dict)
                    and checkpoint.get('session_id') == tab.session_id
                    and checkpoint.get('provider') == self.name
                    and checkpoint.get('method') == 'terminal-close'
                    and checkpoint.get('request_id')
                    and self.continuation_request(tab) == checkpoint['request_id'])

    def confirm_close_interruption(self, tab, checkpoint):
        # Process shutdown is core lifecycle evidence. Keep inspection failures
        # and a still-live captured process OUTSIDE the optional-metadata catch.
        current = self.processes.all().get(tab.process.pid)
        if current and current['started'] == tab.process.started:
            raise Uncertain('Claude is still shutting down. Recovery is saved; check again.')
        interrupted = self.validate_close_interruption(tab, checkpoint)
        return dict(provider=self.name, session_id=tab.session_id,
                    request_id=checkpoint.get('request_id') if isinstance(checkpoint, dict) else None,
                    confirmation='terminal_closed_process_exited',
                    outcome='interrupted' if interrupted else 'unconfirmed')

    def interruption_current(self, tab, outcome):
        return bool(outcome and outcome.get('provider') == self.name
            and outcome.get('session_id') == tab.session_id and outcome.get('request_id')
            and self.continuation_request(tab) == outcome['request_id'])

    def interrupt(self, tab, authorize=None):
        import signal
        try:
            if self.activity(tab) != 'running': return dict(outcome='not_interrupted',session_id=tab.session_id,provider=self.name)
            path = self.saved(tab.session_id,tab.cwd)
            offset = path.stat().st_size
            request_id = self.pending_request(tab, path)
            if authorize is None: raise ParkingError('A durable interruption permit is required.')
            authorize()
        except Exception as error:
            raise NotSubmitted(str(error)) from error
        self.processes.send_signal(dict(pid=tab.process.pid,started=tab.process.started,tty=tab.tty),signal.SIGINT)
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            current = self.processes.all().get(tab.process.pid)
            if not current or current['started'] != tab.process.started:
                pending = self.pending_request(tab, path)
                if request_id and pending == request_id:
                    return dict(outcome='interrupted',session_id=tab.session_id,provider=self.name,
                                request_id=request_id,confirmation='process_exited_with_unfinished_request')
                if pending is None:
                    return dict(outcome='not_interrupted',session_id=tab.session_id,provider=self.name)
                raise Uncertain('Claude exited but the saved request changed. No automatic continuation is authorized.')
            try:
                state = self.activity(tab)
            except ParkingError:
                # Registry removal can precede process exit during graceful shutdown.
                state = 'unknown'
            if state == 'idle':
                marker = self.interruption_marker(tab,path,offset)
                if marker:
                    return dict(outcome='interrupted',session_id=tab.session_id,provider=self.name,event_id=marker)
                # It might have completed naturally while the signal was in flight.
                # No synthetic continuation credit without native interruption evidence.
                return dict(outcome='not_interrupted',session_id=tab.session_id,provider=self.name)
            time.sleep(0.2)
        raise Uncertain('Claude interruption is unconfirmed. Window stays open; no automatic continuation is authorized.')


class Codex:
    name = 'codex'
    def __init__(self, processes, rpc, root=None, executable='codex'):
        self.processes,self.rpc=processes,rpc
        self.root=Path(root) if root else Path.home()/'.codex'
        self.executable=executable
        from .codex_identity import CodexIdentity
        self.identity = CodexIdentity(processes,rpc)

    def begin_observation(self): self.identity.begin_observation()

    def end_observation(self): return self.identity.end_observation()

    def identify(self, session, processes):
        _, process=exact_process(session,processes,self.name)
        # Exact open-rollout identity is available on embedded-server Codex.
        # Never replace this with fuzzy titles or a folder's most recent thread.
        matches={}
        for name in self.processes.files(process.pid):
            path=Path(name)
            if path.name.startswith('rollout-') and path.suffix=='.jsonl' and self.root in path.parents:
                with path.open() as stream: entry=json.loads(stream.readline())
                meta=entry.get('payload',{})
                if entry.get('type')=='session_meta':
                    matches[meta['id']]=meta
        if len(matches)>1:
            raise ParkingError('Multiple Codex rollouts are open. Nothing will be guessed.')
        if matches:
            sid=next(iter(matches));uuid.UUID(sid)
        else:
            from .confirmation import MissingEndpoint, ConfirmationRequired, resume_candidate, binding
            confirmed = getattr(self, 'confirmed_resumes', {}).get(session['id'])
            if confirmed:
                from dataclasses import asdict
                if (asdict(process) != confirmed['process']
                        or self.processes.command(process.pid) != confirmed['command']
                        or self.processes.cwd(process.pid) != confirmed['cwd']):
                    raise ParkingError('The confirmed Codex process changed. Nothing closed.')
            try:
                sid=self.identity.resolve(process)
                if confirmed and sid != confirmed['session_id']:
                    raise ParkingError('Codex switched conversations after confirmation. Nothing closed.')
            except MissingEndpoint:
                candidate = resume_candidate(process, session, self.processes, self.rpc)
                if candidate is None: raise
                if confirmed is None: raise ConfirmationRequired(candidate)
                if binding(candidate) != confirmed:
                    raise ParkingError('The confirmed Codex tab changed. Nothing closed.')
                sid = candidate['session_id']
        cwd=self.processes.cwd(process.pid)
        thread=self.rpc.read(sid)
        if thread['cwd']!=cwd:raise ParkingError('Codex process and thread folder disagree.')
        turn=self.active_turn(thread)
        state='running' if turn else ('idle' if thread['status']['type'] in ('idle','notLoaded') else 'waiting')
        return Tab(session['id'],session['tty'],session.get('title',''),self.name,sid,cwd,process,state,turn or '')

    @staticmethod
    def active_turn(thread):
        turns=[t['id'] for t in thread.get('turns',[]) if t['status']=='inProgress']
        if len(turns)>1:raise Uncertain('More than one active Codex turn was returned.')
        return turns[0] if turns else None

    def is_archived(self, tab):
        import sqlite3
        from contextlib import closing
        try:
            with closing(sqlite3.connect((self.root/'state_5.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
                row = db.execute('SELECT archived,cwd FROM threads WHERE id=?',(tab.session_id,)).fetchone()
        except sqlite3.Error as error:
            raise ParkingError('Cannot confirm Codex native archive state.') from error
        if not row or row[1] != tab.cwd or row[0] not in (0,1):
            raise ParkingError('Codex native archive identity/folder disagrees.')
        return bool(row[0])

    def set_archived(self, tab, archived, authorize):
        try:
            if self.is_archived(tab) == archived: return
            thread = self.rpc.read(tab.session_id)
            if thread['id'] != tab.session_id or thread['cwd'] != tab.cwd or self.active_turn(thread):
                raise ParkingError('Only this saved, stopped Codex conversation may change archive state.')
        except Exception as error:
            raise NotSubmitted(str(error)) from error
        self.rpc.call('thread/archive' if archived else 'thread/unarchive', dict(threadId=tab.session_id),
                      mutation=True, authorize=lambda sid, turn: authorize(sid))
        if self.is_archived(tab) != archived:
            raise Uncertain('Codex archive result is unconfirmed; do not repeat blindly.')

    def client_closed(self, tab): return client_closed(tab, self.processes)

    def finish_close(self, tab, authorize): finish_client_close(tab, self.processes, authorize)

    def launch(self, tab, receipt, prompt=None):
        thread=self.rpc.read(tab.session_id)
        if tab.provider!=self.name or thread['id']!=tab.session_id or thread['cwd']!=tab.cwd:
            raise ParkingError('The exact Codex thread and folder could not be verified.')
        return command(tab,receipt,[self.executable,*launch_flags(tab),'resume',tab.session_id],prompt)

    def preflight(self, tab): preflight_executable(self.executable)

    def interrupt(self,tab,authorize=None):
        try:
            thread=self.rpc.read(tab.session_id)
            turn=self.active_turn(thread)
            if not turn:return dict(outcome='not_interrupted',session_id=tab.session_id,provider=self.name)
            if turn!=tab.turn_id:raise ParkingError('Codex started a different turn. Refresh before parking.')
        except Exception as error:
            raise NotSubmitted(str(error)) from error
        self.rpc.interrupt(tab.session_id,turn,authorize=authorize)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            current=self.rpc.read(tab.session_id)
            stopped=next((t for t in current.get('turns',[]) if t['id']==turn),None)
            if stopped and stopped['status']=='interrupted':
                return dict(outcome='interrupted',session_id=tab.session_id,provider=self.name,turn_id=turn)
            if stopped and stopped['status']=='completed':
                return dict(outcome='not_interrupted',session_id=tab.session_id,provider=self.name,turn_id=turn)
            time.sleep(.2)
        raise Uncertain('Codex interruption is not confirmed. The shared server was not signalled.')

    def observe_interruption(self, tab):
        """Recover a lost interrupt acknowledgement without sending anything."""
        thread = self.rpc.read(tab.session_id)
        if thread.get('id') != tab.session_id or thread.get('cwd') != tab.cwd:
            raise Uncertain('Saved interruption conversation or folder changed.')
        turn = next((t for t in thread.get('turns', []) if t['id'] == tab.turn_id), None)
        if not turn:
            # Latest turn can have advanced after a lost acknowledgement. A
            # bounded exact-ID history read can recover the fact, not authorize
            # continuation of old work (interruption_current checks latest only).
            history = self.rpc.call('thread/turns/list', dict(threadId=tab.session_id,
                limit=100, sortDirection='desc', itemsView='notLoaded'))
            turns = history.get('data')
            if not isinstance(turns, list): raise Uncertain('Unreadable saved interruption history.')
            matches = [t for t in turns if isinstance(t, dict) and t.get('id') == tab.turn_id]
            if len(matches) > 1: raise Uncertain('Ambiguous saved interruption history.')
            turn = matches[0] if matches else None
        if not turn or turn.get('status') == 'inProgress': return None
        if turn['status'] not in ('interrupted', 'completed', 'failed'): return None
        return dict(outcome='interrupted' if turn['status'] == 'interrupted' else 'not_interrupted',
                    session_id=tab.session_id, provider=self.name, turn_id=tab.turn_id)

    def interruption_current(self, tab, outcome):
        if (outcome.get('provider') != self.name or outcome.get('session_id') != tab.session_id
                or not outcome.get('turn_id')): return False
        thread = self.rpc.read(tab.session_id)
        # CodexRPC.read explicitly requests only the latest persisted turn.
        # Do not search history: a later completed request revokes old credit.
        turns = thread.get('turns', [])
        return (thread.get('id') == tab.session_id and thread.get('cwd') == tab.cwd
                and len(turns) == 1 and turns[0]['id'] == outcome['turn_id']
                and turns[0]['status'] == 'interrupted')
