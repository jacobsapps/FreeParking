"""Selectable, disposable test windows. No adoption of existing windows.

Every new window and launch receipt goes through the same outer safety shim.
The registry records the current window for each fixture after reopen; callers
must explicitly select it. A different disposable window is not selected by
accident just because it is visible or frontmost.
"""
from pathlib import Path
import uuid
from .model import ParkingError
from .operations import Operations
from .providers import Shell
from .testing import FixtureTerminal, fixture_tab


class Lab:
    def __init__(self, scope, terminal, composer, seeder=None):
        self.scope, self.terminal, self.composer = scope, terminal, composer
        self.seeder = seeder

    def create(self, tabs=1, provider='shell', busy=False, settle_each=True):
        if type(tabs) is not int or not 1 <= tabs <= 8:
            raise ParkingError('Use one to eight tabs per disposable window.')
        if provider not in ('shell','claude','codex','mixed') or (provider != 'shell' and self.seeder is None):
            raise ParkingError(provider + ' live fixtures are unavailable until exact identity and cancellation adapters are implemented. No session was launched.')
        # No operation proceeds after an ambiguous previous create.
        if self.scope.data.get('pending_create') or self.scope.journal_failed:
            raise ParkingError('An earlier test operation is unresolved. No fixture created.')
        self._reload()
        self.scope.audit(self.terminal.terminal.windows())
        fixture_id = str(uuid.uuid4())
        home = self.scope.path.parent / 'fixtures' / fixture_id
        home.mkdir(parents=True, mode=0o700)
        folders = []
        for i in range(tabs):
            # Spaces and quote exercise launch escaping without shell execution.
            folder = home / ("tab " + str(i + 1) + " ' " + uuid.uuid4().hex[:8])
            folder.mkdir(mode=0o700)
            folders.append(str(folder))
        agents = []
        if provider == 'shell':
            self.scope.data.setdefault('fixture_folders', []).extend(folders)
        else:
            if provider == 'mixed' and tabs < 2: raise ParkingError('Mixed fixtures need at least two tabs.')
            for i, folder in enumerate(folders):
                name = ('claude','codex')[i % 2] if provider == 'mixed' else provider
                agents.append(self.seeder.seed(name, folder, busy))
            self.scope.data.setdefault('fixture_agents', []).extend(agents)
        fixture = dict(id=fixture_id, window_id=None, session_ids=[], folders=folders,
                       provider=provider, agents=agents, state='creating', record_id=None)
        self.scope.data.setdefault('fixtures', {})[fixture_id] = fixture
        self.scope.save()
        terminal = FixtureTerminal(self.scope, self.terminal)
        for index, folder in enumerate(folders):
            if provider == 'shell':
                command = Shell(None).launch(fixture_tab(self.scope, folder), str(uuid.uuid4()))
            else:
                from .agents import Claude, Codex
                from .codex_rpc import CodexRPC
                from .model import Tab, Process
                agent = agents[index]
                adapter = Claude(None) if agent['provider'] == 'claude' else Codex(None,CodexRPC())
                tab = Tab('fixture','/dev/fixture','Disposable agent',agent['provider'],agent['session_id'],folder,
                          Process(2**30,'fixture','/dev/fixture',agent['provider']))
                command = adapter.launch(tab,str(uuid.uuid4()),agent.get('initial_prompt'))
            created = terminal.call('create', windowId=fixture['window_id'],
                                    sessionIds=fixture['session_ids'], command=command)
            fixture['window_id'] = created['windowId']
            fixture['session_ids'].append(created['sessionId'])
            self.scope.save()
            if agents and settle_each:
                _, observer, _ = self.composer()
                snapshot = observer.observe_until(lambda o: o.window(fixture['window_id']) is not None)
                if not snapshot.window(fixture['window_id']):
                    raise ParkingError(snapshot.issues.get(fixture['window_id'],'New agent identity is unconfirmed.'))
        if not settle_each:
            # Test setup only: raw creates still each pass the protective gate
            # and persist their receipt. Batch identity settling saves repeated
            # full inventories, never weakens the criterion for an open fixture.
            def matches(observed):
                window = observed.window(fixture['window_id'])
                if not window or [t.terminal_id for t in window.tabs] != fixture['session_ids']:
                    return False
                for i, tab in enumerate(window.tabs):
                    if tab.cwd != folders[i]: return False
                    if agents:
                        if (tab.provider, tab.session_id) != (agents[i]['provider'], agents[i]['session_id']): return False
                    elif tab.provider != 'shell': return False
                return True
            _, observer, _ = self.composer()
            snapshot = observer.observe_until(matches)
            if not matches(snapshot):
                raise ParkingError('New fixture identities do not match its exact saved conversations, folders and create receipts. No parking attempted.')
            self.scope.audit(self.terminal.terminal.windows())
        fixture['state'] = 'open'
        self.scope.save()
        return dict(fixture)

    def list(self):
        raw = self.terminal.windows()
        live = {w['id'] for w in raw}
        return [dict(f, present=f['window_id'] in live)
                for f in self.scope.data.get('fixtures', {}).values()]

    def selected(self, window_id):
        # Only a registry-created fixture can be selected. IDs from work, from
        # another scope, or from an old pre-reopen window are rejected.
        matches = [f for f in self.scope.data.get('fixtures', {}).values()
                   if f['window_id'] == window_id]
        if len(matches) != 1 or window_id not in self.scope.data['owned']:
            raise ParkingError('Select exactly one window created by this test lab. Existing working windows cannot be adopted.')
        return matches[0]

    def check(self, window_id):
        fixture = self.selected(window_id)
        self.scope.audit(self.terminal.terminal.windows())
        store, observer, workflow = self.composer()
        if fixture['state'] == 'creating' and not fixture.get('record_id'):
            snapshot = observer.observe_until(lambda o:o.window(window_id) is not None)
            window = snapshot.window(window_id)
            if not window or [t.terminal_id for t in window.tabs] != fixture['session_ids']:
                raise ParkingError('Created fixture does not match its acknowledged terminal receipts.')
            agents = fixture.get('agents', [])
            for i, tab in enumerate(window.tabs):
                if tab.cwd != fixture['folders'][i]:raise ParkingError('Created fixture folder changed.')
                if agents and (tab.provider,tab.session_id) != (agents[i]['provider'],agents[i]['session_id']):
                    raise ParkingError('Created fixture conversation changed.')
                if not agents and tab.provider != 'shell':raise ParkingError('Folder fixture is not a shell.')
            fixture['state'] = 'open'
            self.scope.save()
        elif fixture.get('record_id'):
            record = Operations(workflow, seconds=5).check(fixture['record_id']).record
            self._reload();fixture = self.scope.data['fixtures'][fixture['id']]
            if record['phase'] == 'returned':
                targets = [record['targets'].get(str(i)) for i in range(len(fixture['folders']))]
                if all(targets):
                    fixture.update(window_id=targets[0]['window_id'],session_ids=[t['terminal_id'] for t in targets],state='open')
            elif record['phase'] == 'parked':fixture['state'] = 'parked'
            self.scope.save()
        return dict(fixture)

    def round_trip(self, window_id, wait):
        fixture = self.selected(window_id)
        if fixture['state'] != 'open':
            raise ParkingError('This fixture has an unfinished operation; use inspect/check, not another round trip.')
        raw = self.terminal.terminal.windows()
        self.scope.audit(raw)
        # Snapshot every other window—including unselected fixtures. The proof
        # checks these after each phase, not just the original work baseline.
        protected = {w['id']: [s['id'] for t in w['tabs'] for s in t['sessions']]
                     for w in raw if w['id'] != window_id}
        def audit_others():
            current = self.terminal.terminal.windows()
            self.scope.audit(current)
            actual = {w['id']: [s['id'] for t in w['tabs'] for s in t['sessions']] for w in current}
            if any(actual.get(wid) != ids for wid, ids in protected.items()):
                raise ParkingError('An unselected window changed. Stop the test; do not repair it automatically.')
        fixture['state'] = 'parking'
        self.scope.save()
        store, observer, workflow = self.composer()
        window = wait(lambda: observer.observe().window(window_id))
        record = Operations(workflow).park(window_id, window.fingerprint).record
        self._reload()  # Independent helper composition can update ownership.
        fixture = self.scope.data['fixtures'][fixture['id']]
        fixture['record_id'] = record['id']
        fixture['state'] = 'parked' if record['phase'] == 'parked' else 'needs_check'
        self.scope.save()
        audit_others()
        if record['phase'] != 'parked':
            raise ParkingError(record.get('issue') or 'Parking did not complete.')
        fixture['state'] = 'opening'
        self.scope.save()
        _, _, workflow = self.composer()
        outcome = Operations(workflow).reopen(record['id'])
        record = outcome.record
        if outcome.status != 'completed':
            raise ParkingError(outcome.detail or 'The production operation did not complete. No test-only reconciliation was performed.')
        self._reload()
        fixture = self.scope.data['fixtures'][fixture['id']]
        targets = [record['targets'][str(i)] for i in range(len(fixture['folders']))]
        if len({t['window_id'] for t in targets}) != 1:
            raise ParkingError('Fixture did not return as one window.')
        fixture.update(window_id=targets[0]['window_id'], session_ids=[t['terminal_id'] for t in targets], state='open')
        self.scope.save()
        audit_others()
        return dict(fixture)

    def _reload(self):
        # Retain the object shared with the outer shim, update only its journal.
        from test_safety import Scope
        fresh = Scope(self.scope.path)
        self.scope.data = fresh.data
