"""Read-only observation and outcome verification. No mutation ports here."""
import time
from .model import Observation, ParkingError, Uncertain, Window


class Observer:
    def __init__(self, terminal, processes, providers):
        self.terminal, self.processes, self.providers = terminal, processes, providers

    def observe_until(self, ready, seconds=30):
        """Wait for a newly launched shell to settle, without changing anything."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                snapshot = self.observe()
            except Uncertain:
                if time.monotonic() >= deadline: raise
                time.sleep(0.2)
                continue  # Startup changed mid-read; observe again, never launch again.
            if ready(snapshot) or time.monotonic() >= deadline:
                return snapshot
            time.sleep(0.2)

    def observe(self):
        # Neither a denied permission nor an unreadable process list is 'empty'.
        raw = self.terminal.windows()
        processes = self.processes.all()
        windows, issues, tab_issues, resolved = [], {}, {}, {}
        self.providers.begin_observation()
        try:
            for window in raw:
                tabs, problems = [], []
                for index, raw_tab in enumerate(window['tabs']):
                    try:
                        if len(raw_tab['sessions']) != 1:
                            raise ParkingError('Split panes cannot yet be parked together.')
                        tab = self.providers.identify(raw_tab['sessions'][0], processes)
                        tabs.append(tab); resolved[tab.terminal_id] = tab
                    except ParkingError as error:
                        problems.append('Tab ' + str(index + 1) + ': ' + str(error))
                        for session in raw_tab['sessions']: tab_issues[session['id']] = str(error)
                if not window['tabs']: problems.append('Empty window.')
                if problems: issues[window['id']] = '\n'.join(problems)
                else: windows.append(Window(window['id'], tuple(tabs)))
        finally:
            self.providers.end_observation()
        # Prevent a read/process race from being represented as a good binding.
        after = self.processes.all()
        for w in windows:
            if any(after.get(t.process.pid, {}).get('started') != t.process.started for t in w.tabs):
                raise Uncertain('A process changed during inspection. Observe again; no mutation authorized.')
        return Observation(tuple(raw), tuple(windows), issues, tab_issues, resolved)


class Verifier:
    @staticmethod
    def other_conversation_tabs(tab, observation, allowed_ids=()):
        if tab.provider == 'shell': return []
        return [live for window in observation.windows for live in window.tabs
                if live.terminal_id not in allowed_ids and
                (live.provider, live.session_id) == (tab.provider, tab.session_id)]

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
        window = observation.window(target['window_id'])
        tab = next((t for t in window.tabs if t.terminal_id == target['terminal_id']), None) if window else None
        if tab is None:
            return False
        raw = observation.raw_window(target['window_id'])
        receipt = next((s.get('receipt') for t in raw['tabs'] for s in t['sessions']
                        if s['id'] == target['terminal_id']), None)
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
