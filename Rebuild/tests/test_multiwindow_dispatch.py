"""SIMULATED production dispatch acceptance, not native or installed-UI proof.

Real Store, Workflow, Gate, Operations and garage dispatch. Only terminal and
provider boundaries are deterministic fakes. No native agents or APIs are used.
"""
import base64
from collections import Counter
from dataclasses import replace
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1]))
import test_core
from parking.agents import command
from parking.garage import dispatch
from parking.model import ParkingError, Process, Tab, Window
from parking.providers import Providers
from parking.safety import Gate
from parking.store import Store
from parking.workflow import Workflow


class MultiwindowDispatchTests(unittest.TestCase):
    def test_four_mixed_windows_survive_optional_failures_and_dispatch_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); garage = root/'garage'
            owner = self
            selected = []
            for wi in range(4):
                tabs = []
                for ti in range(5):
                    cwd = root/('window '+str(wi))/('tab '+str(ti)+" ' folder")
                    cwd.mkdir(parents=True)
                    provider = 'claude' if ti % 2 == 0 else 'codex'
                    pid = 100 + wi*5 + ti
                    tty = '/dev/fixture-'+str(pid)
                    tabs.append(Tab('source-'+str(pid), tty, 'Fixture '+str(pid), provider,
                        str(uuid.uuid4()), str(cwd), Process(pid, 'birth', tty, '/fixture/'+provider),
                        'running', 'turn-'+str(pid) if provider == 'codex' else ''))
                selected.append(Window('selected-'+str(wi), tuple(tabs)))
            by_sid = {tab.session_id: tab for w in selected for tab in w.tabs}
            missing = selected[3].tabs[0].session_id
            completed_during_close = selected[3].tabs[2].session_id
            completed_before_capture = selected[3].tabs[4].session_id
            no_prompt = {missing, completed_during_close, completed_before_capture}
            mutation_log, launches, prompts, metadata_failures = [], Counter(), Counter(), []
            archive_states = {sid: False for sid, tab in by_sid.items() if tab.provider == 'codex'}
            request_ids = {sid: str(uuid.uuid4()) for sid in by_sid}

            def before_mutation(kind):
                records = Store(garage).all()  # Independent durable readback.
                owner.assertEqual(len(records), 4)
                owner.assertEqual({r['source']['window_id'] for r in records}, {w.window_id for w in selected})
                owner.assertTrue(all(len(r['source']['tabs']) == 5 for r in records))
                if not mutation_log:
                    owner.assertTrue(all(not r['targets'] for r in records))
                    owner.assertTrue(all(r['phase'] in ('saved', 'interrupting') for r in records))
                mutation_log.append(kind)

            class ScopedDesktop(test_core.Desktop):
                def __init__(self):
                    super().__init__(root)
                    self.live = [self.work] + selected
                    self.serial = 0

                def observe(self):
                    snapshot = super().observe()
                    # Simulate the test ownership boundary. The protected window
                    # exists on the backing desktop but is never Park All input.
                    return replace(snapshot,
                        raw=tuple(w for w in snapshot.raw if w['id'] != self.work.window_id),
                        windows=tuple(w for w in snapshot.windows if w.window_id != self.work.window_id))

                def call(self, action, **kwargs):
                    owner.assertNotEqual(kwargs.get('windowId'), self.work.window_id)
                    before_mutation(action)
                    if action == 'close':
                        live = next(w for w in self.live if w.window_id == kwargs['windowId'])
                        owner.assertEqual(kwargs['sessionIds'], [t.terminal_id for t in live.tabs])
                        return super().call(action, **kwargs)
                    owner.assertEqual(action, 'create')
                    body = shlex.split(kwargs['command'])[2]
                    argv = shlex.split(body.split(' && exec ', 1)[1])
                    sid = argv[2]; saved = by_sid[sid]
                    owner.assertEqual(argv[:2], ['/fixture/'+saved.provider,
                                                '--resume' if saved.provider == 'claude' else 'resume'])
                    owner.assertEqual(shlex.split(body.split(' && ', 1)[0]), ['cd', saved.cwd])
                    owner.assertNotIn(sid, {t.session_id for w in self.live for t in w.tabs})
                    token = base64.b64decode(re.search(r'freeParkingReceipt=([A-Za-z0-9+/=]+)', body).group(1)).decode()
                    uuid.UUID(token)
                    launches[sid] += 1
                    if argv[3:]:
                        owner.assertEqual(argv[3:], ['please continue']); prompts[sid] += 1
                    self.serial += 1
                    wid = kwargs['windowId'] or 'returned-'+str(self.serial)
                    tid, tty = 'returned-tab-'+str(self.serial), '/dev/returned-'+str(self.serial)
                    tab = replace(saved, terminal_id=tid, tty=tty, activity='idle',
                                  process=Process(500+self.serial, 'returned birth', tty, '/fixture/'+saved.provider))
                    existing = next((w for w in self.live if w.window_id == wid), None)
                    owner.assertEqual(kwargs['sessionIds'], [t.terminal_id for t in existing.tabs] if existing else [])
                    if existing: self.live[self.live.index(existing)] = Window(wid, existing.tabs+(tab,))
                    else: self.live.append(Window(wid, (tab,)))
                    self.tokens[tid] = token
                    self.calls.append((action, kwargs))
                    return dict(windowId=wid, sessionId=tid)

            desktop = ScopedDesktop()

            class Agent:
                def launch(self, tab, receipt, prompt=None):
                    owner.assertEqual((tab.provider, tab.cwd), (by_sid[tab.session_id].provider, by_sid[tab.session_id].cwd))
                    return command(tab, receipt, ['/fixture/'+tab.provider,
                        '--resume' if tab.provider == 'claude' else 'resume', tab.session_id], prompt)

                def interruption_current(self, tab, outcome):
                    return tab.session_id not in no_prompt

            class Claude(Agent):
                name = 'claude'
                def prepare_close_interruption(self, tab):
                    if tab.session_id == missing:
                        metadata_failures.append(tab.session_id)
                        raise ParkingError('Simulated optional request metadata unavailable')
                    if tab.session_id == completed_before_capture: return None
                    return dict(provider=self.name, session_id=tab.session_id,
                                request_id=request_ids[tab.session_id], method='terminal-close')

                def confirm_close_interruption(self, tab, checkpoint):
                    owner.assertNotIn(tab.terminal_id, {t.terminal_id for w in desktop.live for t in w.tabs})
                    return dict(provider=self.name, session_id=tab.session_id,
                                request_id=checkpoint['request_id'],
                                outcome='not_interrupted' if tab.session_id == completed_during_close else 'interrupted')

            class Codex(Agent):
                name = 'codex'
                def interrupt(self, tab, authorize):
                    authorize(tab.session_id, tab.turn_id)
                    before_mutation('interrupt')
                    for i, window in enumerate(desktop.live):
                        desktop.live[i] = Window(window.window_id, tuple(
                            replace(t, activity='idle') if t.terminal_id == tab.terminal_id else t for t in window.tabs))
                    return dict(provider=self.name, session_id=tab.session_id,
                                turn_id=tab.turn_id, outcome='interrupted')

                def is_archived(self, tab): return archive_states[tab.session_id]

                def set_archived(self, tab, desired, authorize):
                    authorize(tab.session_id)
                    before_mutation('archive' if desired else 'unarchive')
                    owner.assertNotIn(tab.terminal_id, {t.terminal_id for w in desktop.live for t in w.tabs})
                    archive_states[tab.session_id] = desired

            providers = Providers(Claude(), Codex())
            compositions = []
            def compose(root_arg, scope_path=None, live=False):
                owner.assertEqual(Path(root_arg), garage)
                owner.assertIsNotNone(scope_path)
                store = Store(garage)
                gate = Gate(store, desktop, desktop, providers, mutations_enabled=True)
                workflow = Workflow(store, desktop, gate)
                compositions.append(workflow)
                return store, desktop, workflow

            def invoke(*args):
                # Dispatch reloads SQLite and recreates the real lifecycle objects
                # on every command, as separate production helper invocations do.
                return dispatch(['--test-scope', str(root/'simulated-scope.json'), *args], root=garage)

            with patch('parking.garage.compose', side_effect=compose):
                parked = invoke('park-all')
                self.assertTrue(parked['ok'], parked['error'])
                self.assertEqual(parked['operation_status'], 'completed')
                self.assertEqual(len(parked['cars']), 4)
                self.assertTrue(all(car['status'] == 'parked' for car in parked['cars']))
                self.assertEqual(parked['archives'], [])
                self.assertEqual(desktop.live, [desktop.work])
                self.assertEqual(metadata_failures, [missing])
                self.assertTrue(all(archive_states.values()))
                records = Store(garage).all()
                record_ids = [record['id'] for record in records]
                for record_id in record_ids:
                    restored = invoke('restore', record_id)
                    self.assertTrue(restored['ok'], restored['error'])
                    self.assertEqual(restored['operation_status'], 'completed')
                final = invoke('list')
                self.assertEqual(final['cars'], [])
                self.assertEqual(len(final['archives']), 4)
                self.assertEqual(set(final['verified_archived_ids']), set(record_ids))
                self.assertTrue(all(car['return_verified'] for car in final['archives']))
                self.assertEqual(launches, Counter({sid: 1 for sid in by_sid}))
                self.assertEqual(prompts, Counter({sid: 1 for sid in by_sid if sid not in no_prompt}))
                self.assertTrue(all(not state for state in archive_states.values()))
                self.assertEqual(desktop.live[0], desktop.work)
                self.assertEqual(len(desktop.live), 5)
                for record in Store(garage).all():
                    wid = record['targets']['0']['window_id']
                    live = next(w for w in desktop.live if w.window_id == wid)
                    self.assertEqual([(t.provider, t.session_id, t.cwd) for t in live.tabs],
                        [(t['provider'], t['session_id'], t['cwd']) for t in record['source']['tabs']])
                    self.assertEqual([record['targets'][str(i)]['terminal_id'] for i in range(5)],
                                     [t.terminal_id for t in live.tabs])
                before = (list(mutation_log), launches.copy(), prompts.copy(), list(desktop.live))
                for record_id in record_ids:
                    self.assertEqual(invoke('check', record_id)['operation_status'], 'completed')
                    # Accidental duplicate Restore after helper reload is a no-op.
                    self.assertEqual(invoke('restore', record_id)['operation_status'], 'completed')
                self.assertEqual(invoke('list')['cars'], [])
                self.assertEqual((mutation_log, launches, prompts, desktop.live), before)
                self.assertEqual(len({id(flow) for flow in compositions}), len(compositions))
                self.assertEqual(mutation_log.count('close'), 4)
                self.assertEqual(mutation_log.count('create'), 20)
                self.assertEqual(mutation_log.count('interrupt'), 8)
                self.assertEqual(mutation_log.count('archive'), 8)
                self.assertEqual(mutation_log.count('unarchive'), 8)


if __name__ == '__main__': unittest.main()
