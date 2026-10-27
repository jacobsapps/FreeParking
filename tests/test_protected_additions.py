"""Opt-in user tab additions never enlarge disposable-test mutation ownership."""
import copy
import json
from pathlib import Path
import signal
import unittest

import test_test_safety as fixtures

safety = fixtures.safety
window, process = fixtures.window, fixtures.process


class ProtectedAdditionsTests(unittest.TestCase):
    def setUp(self):
        fixtures.TestBoundaryTests.setUp(self)
        # Keep a multi-tab baseline with a pre-existing split, so preservation
        # proves both relative tab order and original session grouping.
        self.work['tabs'].extend([
            dict(sessions=[dict(id='second', tty='/dev/second')]),
            dict(sessions=[dict(id='split-a', tty='/dev/a'), dict(id='split-b', tty='/dev/b')]),
        ])
        self.path = self.path.parent/'additions.json'
        self.scope = safety.Scope.capture(self.path, self.terminal, self.processes,
                                         ['fixture-command'], allow_protected_additions=True)
        self.scoped = safety.ScopedTerminal(self.scope, self.terminal)
        self.proc = safety.ScopedProcesses(self.scope, self.scoped, self.processes)
        self.baseline = copy.deepcopy(self.raw)

    enroll = fixtures.TestBoundaryTests.enroll

    def add_user_tab(self, sid='new-work-tab', tty='/dev/new-work', position=None):
        tab = dict(sessions=[dict(id=sid, tty=tty)])
        if position is None: self.raw[0]['tabs'].append(tab)
        else: self.raw[0]['tabs'].insert(position, tab)

    def test_additions_before_between_after_original_tabs_are_read_only(self):
        before = self.path.read_bytes()
        self.add_user_tab('before', position=0)
        self.add_user_tab('between', position=2)
        self.add_user_tab('after')
        self.raw.append(window('new-window', 'new-window-tab'))
        self.assertEqual(self.scope.audit(self.raw), 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.scope.data['owned'], {})
        self.terminal.call.assert_not_called()

    def test_omitted_and_false_policy_keep_exact_legacy_audit(self):
        for policy in (None, False):
            with self.subTest(policy=policy):
                data = json.loads(self.path.read_text())
                if policy is None: data.pop('allow_protected_additions')
                else: data['allow_protected_additions'] = policy
                path = self.path.parent/('legacy-'+str(policy)+'.json')
                safety.write_private(path, data)
                scope = safety.Scope(path)
                scope.audit(self.baseline)
                raw = copy.deepcopy(self.baseline)
                raw[0]['tabs'].append(dict(sessions=[dict(id='extra', tty='/dev/extra')]))
                with self.assertRaises(safety.SafetyError): scope.audit(raw)

    def test_policy_must_be_boolean_on_capture_and_reload(self):
        for bad in (1, 0, 'true', None, [], {}):
            with self.subTest(value=bad):
                fresh = self.path.parent/'invalid-capture.json'
                with self.assertRaises(safety.SafetyError):
                    safety.Scope.capture(fresh, self.terminal, self.processes,
                                         ['fixture-command'], allow_protected_additions=bad)
                self.assertFalse(fresh.exists())
                data = json.loads(self.path.read_text()); data['allow_protected_additions'] = bad
                path = self.path.parent/'invalid-load.json'; safety.write_private(path, data)
                with self.assertRaises(safety.SafetyError): safety.Scope(path)

    def test_baseline_cannot_be_overwritten_to_enable_policy(self):
        before = self.path.read_bytes()
        with self.assertRaises(safety.SafetyError):
            safety.Scope.capture(self.path, self.terminal, self.processes,
                                 ['fixture-command'], allow_protected_additions=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_original_missing_reordered_or_moved_still_stops(self):
        variants = []
        raw = copy.deepcopy(self.baseline); raw[0]['tabs'].pop(1); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw[0]['tabs'].reverse(); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw[1]['tabs'].append(raw[0]['tabs'].pop(1)); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw.pop(0); variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(safety.SafetyError): self.scope.audit(raw)

    def test_original_tab_groups_cannot_split_merge_reorder_or_gain_panes(self):
        variants = []
        raw = copy.deepcopy(self.baseline)
        raw[0]['tabs'][0]['sessions'].append(dict(id='new-pane', tty='/dev/pane')); variants.append(raw)
        raw = copy.deepcopy(self.baseline)
        split = raw[0]['tabs'].pop()['sessions']; raw[0]['tabs'].extend(dict(sessions=[s]) for s in split); variants.append(raw)
        raw = copy.deepcopy(self.baseline)
        raw[0]['tabs'][0]['sessions'].extend(raw[0]['tabs'].pop(1)['sessions']); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw[0]['tabs'][-1]['sessions'].reverse(); variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(safety.SafetyError): self.scope.audit(raw)

    def test_duplicate_original_session_or_window_is_not_preservation(self):
        variants = []
        raw = copy.deepcopy(self.baseline); raw[0]['tabs'].append(copy.deepcopy(raw[0]['tabs'][0])); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw.append(window('duplicate-container', 'work-tab')); variants.append(raw)
        raw = copy.deepcopy(self.baseline); raw.append(copy.deepcopy(raw[0])); variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(safety.SafetyError): self.scope.audit(raw)

    def test_owned_tab_inserted_into_protected_window_still_stops(self):
        self.enroll()
        self.raw[0]['tabs'].extend(self.raw.pop()['tabs'])
        with self.assertRaises(safety.SafetyError): self.scope.audit(self.raw)
        with self.assertRaises(safety.SafetyError): self.scoped.call('close', windowId='test', sessionIds=['test-tab'])
        self.terminal.call.assert_not_called()

    def test_unknown_tab_or_split_in_owned_fixture_still_stops(self):
        self.enroll(); baseline = copy.deepcopy(self.raw)
        for split in (False, True):
            self.raw[:] = copy.deepcopy(baseline)
            if split: self.raw[-1]['tabs'][0]['sessions'].append(dict(id='intruder', tty='/dev/other'))
            else: self.raw[-1]['tabs'].append(dict(sessions=[dict(id='intruder', tty='/dev/other')]))
            with self.assertRaises(safety.SafetyError): self.scope.audit(self.raw)
        self.terminal.call.assert_not_called()

    def test_new_work_tab_never_becomes_mutable_or_discoverable(self):
        self.enroll(); self.add_user_tab()
        self.assertEqual([w['id'] for w in self.scoped.windows()], ['test'])
        for action in ('close', 'focus', 'check', 'set-bounds', 'set-title', 'create'):
            with self.subTest(action=action), self.assertRaises(safety.SafetyError):
                self.scoped.call(action, windowId='work', sessionIds=['new-work-tab'],
                                 sessionId='new-work-tab', command='fixture-command')
        self.terminal.call.assert_not_called()

    def test_forged_create_receipt_cannot_claim_added_work_tab(self):
        self.add_user_tab()
        self.terminal.call.return_value = dict(windowId='fake-new', sessionId='new-work-tab')
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        self.assertEqual(self.scope.data['owned'], {})
        self.assertIsNotNone(safety.Scope(self.path).data['pending_create'])

    def test_forged_create_receipt_cannot_claim_protected_window(self):
        self.add_user_tab()
        self.terminal.call.return_value = dict(windowId='work', sessionId='fake-new')
        with self.assertRaises(safety.SafetyError):
            self.scoped.call('create', windowId=None, command='fixture-command')
        self.assertEqual(self.scope.data['owned'], {})
        self.assertIsNotNone(safety.Scope(self.path).data['pending_create'])

    def test_added_work_process_is_never_captured_or_signalled(self):
        self.enroll(); self.add_user_tab()
        self.running[202] = process(202, tty='/dev/new-work')
        self.proc.all()
        self.assertNotIn(202, [p['pid'] for p in self.scope.data['owned_processes']])
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[202], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_user_tab_reusing_fixture_tty_protects_even_captured_old_process(self):
        self.enroll(); self.raw.pop()
        self.add_user_tab(tty='/dev/test')
        self.scope.audit(self.raw)
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[101], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_reused_fixture_pid_in_new_work_tab_is_not_signalled(self):
        self.enroll(); saved = self.running[101].copy(); self.raw.pop()
        self.add_user_tab(tty='/dev/test')
        self.running[101] = process(101, started='new birth')
        self.proc.all()
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(saved, signal.SIGTERM)
        with self.assertRaises(safety.SafetyError): self.proc.send_signal(self.running[101], signal.SIGTERM)
        self.processes.send_signal.assert_not_called()

    def test_owned_close_and_exact_signal_remain_allowed_after_user_addition(self):
        self.enroll(); self.add_user_tab()
        self.scoped.call('close', windowId='test', sessionIds=['test-tab'])
        self.proc.send_signal(self.running[101], signal.SIGINT)
        self.terminal.call.assert_called_once_with('close', windowId='test', sessionIds=['test-tab'])
        self.processes.send_signal.assert_called_once_with(self.running[101], signal.SIGINT)
        self.assertEqual(self.scope.audit(self.raw), 2)


if __name__ == '__main__': unittest.main()
