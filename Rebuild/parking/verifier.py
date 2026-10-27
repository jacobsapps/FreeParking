"""Read-only observation and outcome verification. No mutation ports here."""
from .diagnostics import timed, event

import time
from .model import Observation, ParkingError, Uncertain, Window


class Observer:
    def __init__(self, terminal, processes, providers):
        self.terminal, self.processes, self.providers = terminal, processes, providers

    @timed('observer.settle')
    def observe_until(self, ready, seconds=30, presentation=True):
        """Wait for a newly launched shell to settle, without changing anything."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                snapshot = self.observe() if presentation else self.verify()
                is_ready = ready(snapshot)
            except Uncertain:
                if time.monotonic() >= deadline: raise
                time.sleep(0.2)
                continue  # Startup changed mid-read; observe again, never launch again.
            if is_ready or time.monotonic() >= deadline:
                return snapshot
            time.sleep(0.2)

    def verify(self):
        return self.observe(presentation=False)

    def verify_until(self, ready, seconds=30):
        return self.observe_until(ready, seconds, presentation=False)

    @timed('observer.scan')
    def observe(self, presentation=True):
        # Neither a denied permission nor an unreadable process list is 'empty'.
        lean = getattr(type(self.terminal), 'identity_windows', None)
        raw = lean(self.terminal) if lean and not presentation else self.terminal.windows()
        processes = self.processes.all()
        windows, issues, tab_issues, resolved, candidates, unresolved = [], {}, {}, {}, {}, {}
        batch = getattr(type(self.processes), 'begin_inspection', None)
        if batch:
            batch(self.processes, processes, {s['tty'] for w in raw for t in w['tabs'] for s in t['sessions']})
        try:
            self.providers.begin_observation()
            for window in raw:
                for index, raw_tab in enumerate(window['tabs']):
                    for session in raw_tab['sessions']:
                        try:
                            # Split panes remain unparkable as a window, but a
                            # known conversation in a pane must remain visible
                            # to duplicate checks for other windows.
                            tab = self.providers.identify(session, processes)
                            resolved[tab.terminal_id] = tab
                        except ParkingError as error:
                            from .confirmation import ConfirmationRequired
                            if isinstance(error, ConfirmationRequired):
                                candidates[error.candidate['terminal_id']] = error.candidate
                            tab_issues[session['id']] = str(error)
                            unresolved[session['id']] = tuple(sorted(self.providers.candidates(session, processes)))
        finally:
            try: binding_failures = self.providers.end_observation() or {}
            finally:
                if batch: self.processes.end_inspection()
        # Prevent a read/process race from being represented as a good binding.
        # Invalidate the affected tab, not unrelated stable windows.
        after = self.processes.all()
        def attached(tab, inventory):
            session = {'tty': tab.tty}
            # An idle shell's contract excludes all additional jobs. An agent
            # can normally spawn tool processes: only new/changed agent
            # candidates invalidate its exact ownership binding here.
            return {(pid, p['started'], p['executable']) for pid, p in inventory.items()
                    if p['tty'] == tab.tty and (tab.provider == 'shell' or
                        self.providers.candidates(session, {pid: p}))}
        for tid, tab in list(resolved.items()):
            current = after.get(tab.process.pid, {})
            changed = (current.get('started') != tab.process.started or current.get('tty') != tab.tty
                       or attached(tab, processes) != attached(tab, after))
            error = binding_failures.get(tab.process.pid)
            if changed or error:
                del resolved[tid]
                tab_issues[tid] = error or 'This tab’s process changed during inspection. Observe again.'
                unresolved[tid] = (tab.provider,) if tab.provider != 'shell' else ('claude', 'codex')
        for window in raw:
            tabs, problems = [], []
            for index, raw_tab in enumerate(window['tabs']):
                if len(raw_tab['sessions']) != 1:
                    problems.append('Tab ' + str(index + 1) + ': Split panes cannot yet be parked together.')
                for session in raw_tab['sessions']:
                    tid = session['id']
                    if tid in resolved: tabs.append(resolved[tid])
                    else:
                        # A new provider seen in the second process read must
                        # not disappear from the uncertainty/duplicate guard.
                        unresolved[tid] = tuple(sorted(set(unresolved.get(tid, ())) |
                            self.providers.candidates(session, after)))
                    if tid in tab_issues:
                        problems.append('Tab ' + str(index + 1) + ': ' + tab_issues[tid])
            if not window['tabs']: problems.append('Empty window.')
            if problems: issues[window['id']] = '\n'.join(problems)
            else: windows.append(Window(window['id'], tuple(tabs), window.get('bounds', {})))
        event('observer.result', windows=len(raw), tabs=sum(len(w['tabs']) for w in raw), resolved=len(resolved), issues=len(issues))
        return Observation(tuple(raw), tuple(windows), issues, tab_issues, resolved, candidates, unresolved)


class Verifier:
    @staticmethod
    def resolved(observation):
        # Older fixtures/records may supply only complete windows. Individual
        # resolved tabs are authoritative even when their window is unparkable.
        tabs = {t.terminal_id: t for w in observation.windows for t in w.tabs}
        tabs.update(observation.resolved_tabs)
        return tabs

    @staticmethod
    def other_conversation_tabs(tab, observation, allowed_ids=()):
        if tab.provider == 'shell': return []
        resolved = Verifier.resolved(observation)
        matches = [live for live in resolved.values()
                if live.terminal_id not in allowed_ids and
                (live.provider, live.session_id) == (tab.provider, tab.session_id)]
        if matches: return matches
        unknown = observation.terminal_ids - resolved.keys() - set(allowed_ids)
        if any(tab.provider in observation.unresolved_providers.get(tid, ('claude', 'codex')) for tid in unknown):
            raise Uncertain('Another terminal has an unresolved '+tab.provider+' identity. Its conversation may already be open; nothing duplicated or changed.')
        return []

    @staticmethod
    def source_open(record, observation):
        source = Window.decode(record['source'])
        live = observation.window(source.window_id)
        return bool(live and live.fingerprint == source.fingerprint)

    @staticmethod
    def source_absent(record, observation):
        source = Window.decode(record['source'])
        return not ({t.terminal_id for t in source.tabs} & observation.terminal_ids)

    @staticmethod
    def receipt_target(token, observation):
        found = [(w['id'], s['id']) for w in observation.raw for t in w['tabs'] for s in t['sessions']
                 if s.get('receipt') == token]
        if len(found) > 1:
            raise Uncertain('A launch receipt is visible more than once. No duplicate launch is permitted.')
        return dict(window_id=found[0][0], terminal_id=found[0][1], receipt=token) if found else None

    @staticmethod
    def matching_tab(saved, target, observation):
        tab = Verifier.resolved(observation).get(target['terminal_id'])
        raw = observation.raw_window(target['window_id'])
        if tab is None or raw is None:
            return False
        containing = [t for t in raw['tabs'] if any(s['id'] == target['terminal_id'] for s in t['sessions'])]
        if len(containing) != 1 or len(containing[0]['sessions']) != 1:
            return False  # The target itself must still be a single saved tab.
        receipt = containing[0]['sessions'][0].get('receipt')
        return (receipt == target['receipt'] and tab.provider == saved.provider
                and tab.session_id == saved.session_id and tab.cwd == saved.cwd
                and tab.shell_command == saved.shell_command)

    @classmethod
    def returned(cls, record, observation):
        if cls.source_open(record, observation):
            return True
        source = Window.decode(record['source'])
        if set(record['targets']) != {str(i) for i in range(len(source.tabs))}:
            return False
        targets = [record['targets'][str(i)] for i in range(len(source.tabs))]
        if len({t['window_id'] for t in targets}) != 1:
            return False
        if not all(cls.matching_tab(saved, target, observation) for saved, target in zip(source.tabs, targets)):
            return False
        raw = observation.raw_window(targets[0]['window_id'])
        expected = [t['terminal_id'] for t in targets]
        actual = [s['id'] for t in raw['tabs'] for s in t['sessions'] if s['id'] in expected]
        return actual == expected
