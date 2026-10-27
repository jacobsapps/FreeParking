"""Read-only fake process/terminal boundaries; no real sessions are inspected."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.model import Observation, ParkingError, Process, Tab, Uncertain, Window
from parking.providers import Providers, Shell
from parking.safety import Gate
from parking.verifier import Observer, Verifier


class ObservationBoundaries(unittest.TestCase):
    def setUp(self):
        self.saved = Tab('source-tab', '/dev/source', 'Fictional', 'codex',
            '11111111-1111-4111-8111-111111111111', '/tmp',
            Process(100, 'source birth', '/dev/source', '/bin/codex'), activity='idle')
        self.live = replace(self.saved, terminal_id='live-tab', tty='/dev/live',
            process=Process(101, 'live birth', '/dev/live', '/bin/codex'))
        self.other = replace(self.live, terminal_id='other-tab', tty='/dev/other',
            session_id='22222222-2222-4222-8222-222222222222',
            process=Process(102, 'other birth', '/dev/other', '/bin/codex'))
        self.tabs = {'live-tab': self.live, 'other-tab': self.other}
        self.rows = {t.process.pid: dict(pid=t.process.pid, started=t.process.started,
            tty=t.tty, executable=t.process.executable, ppid=0) for t in self.tabs.values()}
        self.rows[103] = dict(pid=103, started='job birth', tty='/dev/job', executable='/bin/unknown-job', ppid=0)
        self.binding_failures = {}
        owner = self

        class Agent:
            name = 'codex'
            def identify(self, session, processes): return owner.tabs[session['id']]
            def end_observation(self): return owner.binding_failures

        self.provider = Agent()
        self.providers = Providers(self.provider)
        self.processes = Mock()
        self.processes.all.return_value = self.rows
        self.terminal = Mock()
        self.terminal.windows.return_value = [dict(id='partial', tabs=[
            dict(sessions=[dict(id='live-tab', tty='/dev/live', receipt='launch-token')]),
            dict(sessions=[dict(id='job-tab', tty='/dev/job')])]),
            dict(id='stable', tabs=[dict(sessions=[dict(id='other-tab', tty='/dev/other')])])]
        self.observer = Observer(self.terminal, self.processes, self.providers)

    def test_known_duplicate_survives_unrelated_unsupported_tab(self):
        snapshot = self.observer.observe()
        self.assertIsNone(snapshot.window('partial'))
        self.assertEqual(Verifier.other_conversation_tabs(self.saved, snapshot), [self.live])
        self.assertEqual(snapshot.unresolved_providers['job-tab'], ())
        self.terminal.call.assert_not_called()

    def test_known_target_verification_is_not_a_whole_window_parkability_check(self):
        snapshot = self.observer.observe()
        target = dict(window_id='partial', terminal_id='live-tab', receipt='launch-token')
        self.assertTrue(Verifier.matching_tab(self.saved, target, snapshot))
        self.assertFalse(Verifier.matching_tab(self.saved, {**target, 'window_id':'stable'}, snapshot))
        self.assertFalse(Verifier.matching_tab(self.saved, {**target, 'receipt':'other-token'}, snapshot))

    def test_split_pane_identity_still_blocks_duplicate_resume(self):
        raw = self.terminal.windows.return_value[0]
        raw['tabs'] = [dict(sessions=[raw['tabs'][0]['sessions'][0], raw['tabs'][1]['sessions'][0]])]
        snapshot = self.observer.observe()
        self.assertIsNone(snapshot.window('partial'))
        self.assertIn('Split panes', snapshot.issues['partial'])
        self.assertEqual(Verifier.other_conversation_tabs(self.saved, snapshot), [self.live])
        self.assertFalse(Verifier.matching_tab(self.saved,
            dict(window_id='partial',terminal_id='live-tab',receipt='launch-token'),snapshot))

    def test_unresolved_same_provider_is_not_proof_of_absence(self):
        self.provider.identify = Mock(side_effect=ParkingError('Endpoint still starting'))
        snapshot = self.observer.observe()
        self.assertEqual(snapshot.unresolved_providers['live-tab'], ('codex',))
        with self.assertRaises(Uncertain): Verifier.other_conversation_tabs(self.saved, snapshot)

    def test_unrelated_unsupported_non_agent_does_not_block_stable_agent(self):
        snapshot = self.observer.observe()
        self.assertEqual(Verifier.other_conversation_tabs(self.saved, snapshot, {'live-tab'}), [])
        claude = replace(self.saved, provider='claude')
        self.assertEqual(Verifier.other_conversation_tabs(claude, snapshot), [])

    def test_missing_uncertainty_metadata_is_conservatively_unknown(self):
        snapshot = Observation(tuple(self.terminal.windows.return_value), (), {})
        with self.assertRaises(Uncertain): Verifier.other_conversation_tabs(self.saved, snapshot)

    def test_changed_binding_invalidates_only_its_tab_and_window(self):
        self.binding_failures[101] = 'Target endpoint changed'
        snapshot = self.observer.observe()
        self.assertNotIn('live-tab', snapshot.resolved_tabs)
        self.assertEqual(snapshot.unresolved_providers['live-tab'], ('codex',))
        self.assertIsNotNone(snapshot.window('stable'))
        self.assertEqual(snapshot.tab_issues['live-tab'], 'Target endpoint changed')

    def test_process_change_in_partially_supported_window_is_not_accepted(self):
        changed = dict(self.rows)
        changed[101] = {**changed[101], 'started':'different birth'}
        self.processes.all.side_effect = [self.rows, changed]
        snapshot = self.observer.observe()
        self.assertNotIn('live-tab', snapshot.resolved_tabs)
        self.assertIsNotNone(snapshot.window('stable'))
        with self.assertRaises(Uncertain): Verifier.other_conversation_tabs(self.saved, snapshot)

    def test_agent_starting_under_same_live_shell_invalidates_only_that_tab(self):
        before = {**self.rows, 110: dict(pid=110, started='shell birth', tty='/dev/shell',
            executable='/bin/zsh', ppid=1)}
        after = {**before, 111: dict(pid=111, started='new agent', tty='/dev/shell',
            executable='/bin/codex', ppid=110)}
        self.providers.items['shell'] = Shell(self.processes)
        self.processes.cwd.return_value = '/tmp'
        self.processes.all.side_effect = [before, after]
        self.terminal.windows.return_value.append(dict(id='shell-window', tabs=[
            dict(sessions=[dict(id='shell-tab', tty='/dev/shell')])]))
        snapshot = self.observer.observe()
        self.assertIsNone(snapshot.window('shell-window'))
        self.assertNotIn('shell-tab', snapshot.resolved_tabs)
        self.assertIn('codex', snapshot.unresolved_providers['shell-tab'])
        self.assertIsNotNone(snapshot.window('stable'))
        self.terminal.call.assert_not_called()

    def test_new_same_provider_process_on_target_tty_invalidates_binding(self):
        after = {**self.rows, 111: dict(pid=111, started='new agent', tty='/dev/live',
            executable='/bin/codex', ppid=101)}
        self.processes.all.side_effect = [self.rows, after]
        snapshot = self.observer.observe()
        self.assertNotIn('live-tab', snapshot.resolved_tabs)
        self.assertEqual(snapshot.unresolved_providers['live-tab'], ('codex',))
        self.assertIsNotNone(snapshot.window('stable'))

    def test_same_provider_candidate_birth_change_invalidates_binding(self):
        before = {**self.rows, 111: dict(pid=111, started='old candidate', tty='/dev/live',
            executable='/bin/codex', ppid=101)}
        after = {**before, 111: {**before[111], 'started':'reused candidate'}}
        self.processes.all.side_effect = [before, after]
        snapshot = self.observer.observe()
        self.assertNotIn('live-tab', snapshot.resolved_tabs)
        self.assertIsNotNone(snapshot.window('stable'))

    def test_ordinary_agent_tool_process_does_not_invalidate_owner(self):
        after = {**self.rows, 111: dict(pid=111, started='new tool', tty='/dev/live',
            executable='/usr/bin/python3', ppid=101)}
        self.processes.all.side_effect = [self.rows, after]
        snapshot = self.observer.observe()
        self.assertEqual(snapshot.resolved_tabs['live-tab'], self.live)
        self.assertIsNotNone(snapshot.window('stable'))

    def test_non_agent_job_starting_under_idle_shell_also_invalidates_shell(self):
        before = {**self.rows, 110: dict(pid=110, started='shell birth', tty='/dev/shell',
            executable='/bin/zsh', ppid=1)}
        after = {**before, 111: dict(pid=111, started='new job', tty='/dev/shell',
            executable='/usr/bin/python3', ppid=110)}
        self.providers.items['shell'] = Shell(self.processes)
        self.processes.cwd.return_value = '/tmp'
        self.processes.all.side_effect = [before, after]
        self.terminal.windows.return_value.append(dict(id='shell-window', tabs=[
            dict(sessions=[dict(id='shell-tab', tty='/dev/shell')])]))
        snapshot = self.observer.observe()
        self.assertIsNone(snapshot.window('shell-window'))
        self.assertNotIn('shell-tab', snapshot.resolved_tabs)
        self.assertIsNotNone(snapshot.window('stable'))

    def test_native_archive_gate_does_not_hide_known_duplicate(self):
        snapshot = self.observer.observe()
        record = dict(source=Window('source', (self.saved,)).encode(), phase='checking',
            intent=dict(kind='native_archive', index=0, archived=False))
        store = Mock(); store.verify.return_value = record
        observer = Mock(); observer.observe.return_value = snapshot
        provider = Mock(); providers = Mock(); providers.get.return_value = provider
        gate = Gate(store, observer, Mock(), providers, mutations_enabled=True)
        with self.assertRaises(ParkingError): gate.native_archive('durable receipt')
        provider.set_archived.assert_not_called()

    def test_unresolved_tab_is_still_present_for_source_absence(self):
        self.binding_failures[101] = 'Target endpoint changed'
        snapshot = self.observer.observe()
        record = dict(source=Window('partial', (self.live,)).encode())
        self.assertFalse(Verifier.source_absent(record, snapshot))

    def test_settle_retries_uncertain_target_readiness_but_never_replays_mutation(self):
        now = [0.0]
        self.observer.observe = Mock(return_value='snapshot')
        ready = Mock(side_effect=[Uncertain('Unresolved competitor'), True])
        with patch('parking.verifier.time.monotonic', side_effect=lambda:now[0]), \
             patch('parking.verifier.time.sleep', side_effect=lambda seconds:now.__setitem__(0,now[0]+seconds)):
            self.assertEqual(self.observer.observe_until(ready, seconds=1), 'snapshot')
        self.assertEqual(ready.call_count, 2)
        self.terminal.call.assert_not_called()

    def test_settle_readiness_uncertainty_is_bounded_and_not_absence(self):
        now = [0.0]
        self.observer.observe = Mock(return_value='snapshot')
        ready = Mock(side_effect=Uncertain('Unresolved competitor'))
        with patch('parking.verifier.time.monotonic', side_effect=lambda:now[0]), \
             patch('parking.verifier.time.sleep', side_effect=lambda seconds:now.__setitem__(0,now[0]+seconds)):
            with self.assertRaises(Uncertain): self.observer.observe_until(ready, seconds=.5)
        self.assertLessEqual(ready.call_count, 4)
        self.terminal.call.assert_not_called()


if __name__ == '__main__': unittest.main()
