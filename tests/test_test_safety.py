"""Adversarial tests for the live-test boundary: no real iTerm/process calls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('safety', Path(__file__).parents[1] / 'Resources/test_safety.py')
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


def window(wid, sid, tty='/dev/test'):
    return {'id': wid, 'tabs': [{'sessions': [{'id': sid, 'tty': tty}]}]}


def process(pid, tty='/dev/test', parent=1, started='birth', executable='/bin/zsh'):
    return dict(pid=pid, ppid=parent, tty=tty, started=started, executable=executable)


class TestBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'scope.json'
        self.work = window('work', 'work-tab', '/dev/work')
        self.mine = window('agent', 'agent-tab', '/dev/agent')
        self.fixture = window('test', 'test-tab')
        self.processes = Mock()
        self.running = {os.getpid(): process(os.getpid(), '/dev/agent'), 50: process(50, '/dev/work')}
        self.processes.all.side_effect = lambda: self.running.copy()
        self.raw = [self.work, self.mine]
        self.terminal = Mock()
        self.terminal.windows.side_effect = lambda: copy.deepcopy(self.raw)
        self.scope = safety.Scope.capture(self.path, self.terminal, self.processes, ['fixture-command'])
        self.scoped = safety.ScopedTerminal(self.scope, self.terminal)
        self.proc = safety.ScopedProcesses(self.scope, self.scoped, self.processes)
        self.terminal.call.reset_mock()

    def enroll(self):
        def create(action, **kwargs):
            self.raw.append(copy.deepcopy(self.fixture))
            return {'windowId': 'test', 'sessionId': 'test-tab'}
        self.terminal.call.side_effect = create
        self.scoped.call('create', windowId=None, command='fixture-command')
        self.terminal.call.side_effect = None
        self.terminal.call.reset_mock()
        self.running[101] = process(101)
        self.proc.all()  # Capture exact test PID+birth while its owned TTY is live.

    def test_baseline_includes_work_and_our_own_terminal_before_any_create(self):
        self.assertEqual(self.scope.protected_windows, {'work', 'agent'})
        self.assertIn((os.getpid(), 'birth'), self.scope.protected_processes)
        self.terminal.call.assert_not_called()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_process_inspection_failure_prevents_any_creation_or_partial_baseline(self):
        path = self.path.parent / 'other.json'
        self.processes.all.side_effect = PermissionError('inspection denied')
        with self.assertRaises(PermissionError):
            safety.Scope.capture(path, self.terminal, self.processes, ['fixture-command'])
        self.assertFalse(path.exists())
        self.terminal.call.assert_not_called()

    def test_incomplete_self_snapshot_cannot_authorize_tests(self):
        self.processes.all.side_effect = lambda: {50: process(50)}
        with self.assertRaises(safety.SafetyError):
            safety.Scope.capture(self.path.parent / 'other.json', self.terminal, self.processes, ['fixture-command'])

    def test_old_manifest_rejected(self):
        self.path.write_text(json.dumps({'window_id': 'work', 'session_ids': ['work-tab']}))
        with self.assertRaises(safety.SafetyError): safety.Scope(self.path)

    def test_baseline_cannot_be_overwritten(self):
        before = self.path.read_bytes()
        with self.assertRaises(safety.SafetyError):
            safety.Scope.capture(self.path, self.terminal, self.processes, ['fixture-command'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_every_mutation_of_work_and_agent_window_is_blocked(self):
        self.enroll()
        for wid, sid in [('work', 'work-tab'), ('agent', 'agent-tab'), ('unknown', 'unknown-tab')]:
            for action in ['close', 'focus', 'check', 'set-bounds', 'set-title', 'create']:
                with self.subTest(wid=wid, action=action), self.assertRaises(safety.SafetyError):
                    self.scoped.call(action, windowId=wid, sessionIds=[sid], sessionId=sid, command='fixture-command')
        self.terminal.call.assert_not_called()

    def test_mixed_title_batch_is_rejected_before_first_write(self):
        self.enroll()
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('set-titles', titles=[{'windowId':'test','sessionId':'test-tab','title':'test'},
                                                 {'windowId':'work','sessionId':'work-tab','title':'wrong'}])
        self.terminal.call.assert_not_called()

    def test_scope_never_grants_ownership_from_recovery_files(self):
        (self.path.parent / 'garage').mkdir()
        (self.path.parent / 'garage' / 'poison.json').write_text(json.dumps({'restored_targets': {'0': {'window_id':'work', 'terminal_id':'work-tab'}}}))
        self.assertEqual(self.scoped.windows(), [])
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('close', windowId='work', sessionIds=['work-tab'])
        self.terminal.call.assert_not_called()

    def test_user_windows_created_after_baseline_are_also_unowned(self):
        self.raw.append(window('new-work', 'new-work-tab', '/dev/new-work'))
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('close', windowId='new-work', sessionIds=['new-work-tab'])
        self.terminal.call.assert_not_called()

    def test_receipt_cannot_claim_any_existing_window(self):
        self.raw.append(window('new-work', 'new-work-tab'))
        self.terminal.call.return_value = {'windowId':'new-work', 'sessionId':'new-session'}
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        self.assertIsNotNone(safety.Scope(self.path).data['pending_create'])
        self.assertEqual(self.scope.data['owned'], {})

    def test_unapproved_command_never_reaches_terminal(self):
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='resume a working session')
        self.terminal.call.assert_not_called()

    def test_create_failure_keeps_intent_and_blocks_all_further_mutations(self):
        self.terminal.call.side_effect = RuntimeError('Lost receipt')
        with self.assertRaises(RuntimeError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        self.assertIsNotNone(safety.Scope(self.path).data['pending_create'])
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        self.terminal.call.assert_called_once()

    def test_new_tab_in_test_window_stops_instead_of_looking_closed(self):
        self.enroll()
        self.raw[-1]['tabs'].append({'sessions':[{'id':'unregistered','tty':'/dev/new'}]})
        with self.assertRaises(safety.SafetyError): self.scoped.windows()
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('close', windowId='test', sessionIds=['test-tab'])
        self.terminal.call.assert_not_called()

    def test_test_tab_moved_to_work_window_stops(self):
        self.enroll()
        self.raw[0]['tabs'].extend(self.raw.pop()['tabs'])
        with self.assertRaises(safety.SafetyError): self.scoped.windows()
        self.terminal.call.assert_not_called()

    def test_exact_owned_close_allowed_and_nothing_else_selected(self):
        self.enroll()
        self.scoped.call('close', windowId='test', sessionIds=['test-tab'])
        self.terminal.call.assert_called_once_with('close', windowId='test', sessionIds=['test-tab'])

    def test_missing_or_reordered_target_is_not_closed(self):
        self.enroll()
        for ids in ([], ['work-tab'], ['test-tab','unknown']):
            with self.assertRaises(safety.SafetyError): self.scoped.call('close',windowId='test',sessionIds=ids)
        self.terminal.call.assert_not_called()

    def test_working_or_own_process_cannot_be_signalled_even_if_tty_looks_owned(self):
        self.enroll()
        for pid in (50, os.getpid()):
            self.running[pid]['tty'] = '/dev/test'
            with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[pid], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_only_captured_fixture_process_birth_can_be_signalled(self):
        self.enroll()
        self.proc.send_signal(self.running[101], signal.SIGINT)
        self.processes.send_signal.assert_called_once_with(self.running[101], signal.SIGINT)
        saved = self.running[101].copy()
        self.running[101]['started'] = 'reused pid'
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(saved, signal.SIGTERM)
        self.assertEqual(self.processes.send_signal.call_count, 1)

    def test_uncaptured_or_detached_process_is_blocked(self):
        self.enroll()
        for pid, tty in [(102, '/dev/test'), (103, '/dev/??'), (104, '/dev/work')]:
            self.running[pid] = process(pid, tty)
            with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[pid], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_captured_process_can_finish_after_its_fixture_window_closes(self):
        self.enroll()
        self.raw.pop()
        self.proc.send_signal(self.running[101], signal.SIGTERM)
        self.processes.send_signal.assert_called_once_with(self.running[101], signal.SIGTERM)

    def test_shared_server_and_child_are_blocked_even_on_test_tty(self):
        self.enroll()
        self.running[101]['executable'] = '/fixture/app-server-daemon/codex'
        self.running[102] = process(102, parent=101)
        self.proc.all()
        for pid in (101,102):
            with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[pid], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_current_runner_ancestors_protected_when_scope_reused(self):
        self.enroll()
        self.running[os.getpid()]['ppid'] = 101
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[101], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_no_process_group_or_sigkill_support(self):
        self.enroll()
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[101], signal.SIGKILL)
        self.processes.send_signal.assert_not_called()

    def test_protected_window_change_stops_test_without_repairing_it(self):
        self.enroll()
        self.raw.pop(0)
        with self.assertRaises(safety.SafetyError): self.scoped.call('close',windowId='test',sessionIds=['test-tab'])
        self.terminal.call.assert_not_called()

    def test_only_registered_windows_are_discoverable(self):
        self.enroll()
        self.assertEqual([w['id'] for w in self.scoped.windows()], ['test'])

    def test_pending_create_also_blocks_signals(self):
        self.enroll()
        self.scope.data['pending_create'] = {'command':'fixture-command'}
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[101], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_append_passes_exact_live_tab_ids_to_adapter_for_last_moment_check(self):
        self.enroll()
        self.terminal.call.return_value = {'windowId':'test','sessionId':'second-test-tab'}
        self.scoped.call('create', windowId='test', command='fixture-command')
        self.terminal.call.assert_called_once_with('create', windowId='test', command='fixture-command', sessionIds=['test-tab'])
        self.assertEqual(safety.Scope(self.path).data['owned']['test'], ['test-tab','second-test-tab'])

    def test_journal_failure_prevents_create(self):
        with patch.object(self.scope, 'save', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError): self.scoped.call('create',windowId=None,command='fixture-command')
        self.terminal.call.assert_not_called()

    def test_closed_subset_does_not_allow_an_unowned_survivor(self):
        self.enroll()
        self.scope.data['owned']['test'].append('second-test-tab')
        # Only the first original test tab remains. Closing it is permitted.
        self.scoped.call('close', windowId='test', sessionIds=['test-tab'])
        self.terminal.call.assert_called_once()
        self.terminal.call.reset_mock()
        self.raw[-1]['tabs'][0]['sessions'][0]['id'] = 'work-tab'
        with self.assertRaises(safety.SafetyError): self.scoped.windows()
        self.terminal.call.assert_not_called()

    def test_fifty_restart_create_interrupt_close_cleanup_cycles_preserve_working_windows(self):
        protected = copy.deepcopy(self.raw)
        for cycle in range(50):
            self.scope = safety.Scope(self.path)  # New helper invocation.
            self.scoped = safety.ScopedTerminal(self.scope, self.terminal)
            self.proc = safety.ScopedProcesses(self.scope, self.scoped, self.processes)
            wid, sid, tty = f'test-{cycle}', f'test-tab-{cycle}', f'/dev/test-{cycle}'
            pid = 10000 + cycle
            def call(action, **kwargs):
                if action == 'create':
                    self.raw.append(window(wid, sid, tty))
                    return {'windowId':wid, 'sessionId':sid}
                self.assertEqual(action, 'close')
                self.assertEqual(kwargs, {'windowId':wid, 'sessionIds':[sid]})
                self.raw[:] = [w for w in self.raw if w['id'] != wid]
                return {'ok':True}
            self.terminal.call.side_effect = call
            self.scoped.call('create',windowId=None,command='fixture-command')
            self.running[pid] = process(pid, tty)
            self.proc.all()
            self.proc.send_signal(self.running[pid], signal.SIGINT)
            self.scoped.call('close',windowId=wid,sessionIds=[sid])
            self.proc.send_signal(self.running[pid], signal.SIGTERM)
            del self.running[pid]
            self.assertEqual(self.raw, protected)
            self.assertEqual(self.scope.audit(self.raw), 2)
        signalled = [c.args[0]['pid'] for c in self.processes.send_signal.call_args_list]
        self.assertEqual(len(signalled), 100)
        self.assertTrue(all(10000 <= pid < 10050 for pid in signalled))

    def test_receipt_write_failure_poisoning_scope_prevents_any_followup_mutation(self):
        real_write = safety.write_private
        writes = []
        def write(path, data):
            writes.append(1)
            if len(writes) == 2:
                raise OSError('Receipt write failed')
            real_write(path, data)
        self.terminal.call.return_value = {'windowId':'test','sessionId':'test-tab'}
        with patch.object(safety, 'write_private', side_effect=write):
            with self.assertRaises(OSError):
                self.scoped.call('create', windowId=None, command='fixture-command')
        self.assertTrue(self.scope.journal_failed)
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        with self.assertRaises(safety.SafetyError):
            self.proc.send_signal(process(101), signal.SIGTERM)
        self.terminal.call.assert_called_once()
        self.processes.send_signal.assert_not_called()
        self.assertIsNotNone(safety.Scope(self.path).data['pending_create'])
