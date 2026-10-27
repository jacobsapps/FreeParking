"""Exact-session lifecycle stays independent of optional continuation evidence.

Real journal, workflow, gate and Claude transcript parser; deterministic terminal
and process boundaries only. These tests never launch or inspect native agents.
"""
from dataclasses import replace
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1]))
import test_core
from parking.agents import Claude, command
from parking.model import NotSubmitted, ParkingError, Phase, Uncertain, Window
from parking.providers import Providers, Shell
from parking.safety import Gate
from parking.settings import DEFAULTS, Settings
from parking.store import Store
from parking.workflow import Workflow


class OptionalContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'garage')
        self.desktop = test_core.Desktop(self.root)
        self.sid, self.request = str(uuid.uuid4()), str(uuid.uuid4())
        first = replace(self.desktop.source.tabs[0], provider='claude', session_id=self.sid,
                        activity='running', process=replace(self.desktop.source.tabs[0].process,
                                                           executable='/fixture/claude'))
        self.desktop.source = Window('fixture', (first,))
        self.desktop.live[1] = self.desktop.source
        self.processes = Mock()
        self.processes.all.side_effect = lambda: ({first.process.pid: dict(started=first.process.started)}
            if any(w.window_id == 'fixture' for w in self.desktop.live) else {})
        self.provider = Claude(self.processes, self.root/'claude', executable='/fixture/claude')
        self.path = self.root/'claude/projects/project'/(self.sid+'.jsonl')
        self.path.parent.mkdir(parents=True)
        self.entry('system')  # Exact persisted identity exists even without a request.
        self.entry('user', uuid=self.request, message=dict(content='fixture work'))
        self.providers = Providers(Shell(None), self.provider)
        self.gate = Gate(self.store, self.desktop, self.desktop, self.providers, mutations_enabled=True)
        self.flow = Workflow(self.store, self.desktop, self.gate)
        self.prompts = []
        original = self.desktop.call
        def call(action, **kwargs):
            if action == 'create':
                argv = shlex.split(shlex.split(kwargs['command'])[2].split(' && exec ', 1)[1])
                self.assertEqual(argv[:3], ['/fixture/claude', '--resume', self.sid])
                self.prompts.append(argv[3:])
            result = original(action, **kwargs)
            if action == 'create':
                live = self.desktop.live[-1]
                self.desktop.live[-1] = Window(live.window_id, tuple(
                    replace(t, provider='claude', session_id=self.sid, activity='idle') for t in live.tabs))
            return result
        self.desktop.call = call

    def entry(self, kind, **fields):
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type=kind, sessionId=self.sid, cwd=str(self.root), **fields))+'\n')

    def park(self):
        return self.flow.park('fixture', self.desktop.source.fingerprint)

    def returned(self, record, prompt=False):
        self.assertEqual(record['phase'], 'parked')
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual(self.prompts, [['please continue']] if prompt else [[]])
        self.assertEqual([action for action, _ in self.desktop.calls], ['close', 'create'])
        self.assertEqual(self.desktop.live[0], self.desktop.work)
        self.assertEqual(result['source']['tabs'][0]['session_id'], self.sid)
        return result

    def test_unknown_claude_activity_preserves_exact_session_without_message(self):
        self.desktop.source = Window('fixture', (replace(self.desktop.source.tabs[0], activity='unknown'),))
        self.desktop.live[1] = self.desktop.source
        self.returned(self.park())

    def test_missing_request_metadata_does_not_block_busy_exact_session(self):
        self.path.write_text(''); self.entry('system')
        self.returned(self.park())

    def test_partial_request_append_does_not_block_busy_exact_session(self):
        with self.path.open('a') as stream: stream.write('{"type":"user"')
        self.returned(self.park())

    def test_changed_request_after_capture_closes_session_without_old_message(self):
        original = self.gate.prepare_close
        def changed(receipt):
            self.entry('user', uuid=str(uuid.uuid4()), message=dict(content='new work'))
            return original(receipt)
        self.gate.prepare_close = changed
        self.returned(self.park())

    def test_unreadable_metadata_after_close_does_not_prevent_parking(self):
        def during_close(action, args):
            if action == 'close':
                with self.path.open('a') as stream: stream.write('{"type":"assistant"')
        self.desktop.on_call = during_close
        self.returned(self.park())

    def test_optional_preparation_exception_does_not_block_core(self):
        with patch.object(self.provider, 'prepare_close_interruption', side_effect=ParkingError('optional evidence unreadable')):
            self.returned(self.park())

    def test_reopen_optional_evidence_exception_drops_message(self):
        record = self.park()
        with patch.object(self.provider, 'interruption_current', side_effect=OSError('optional read failed')):
            self.returned(record)

    def test_stale_prompt_at_final_gate_is_omitted_without_retry(self):
        record = self.park()
        with patch.object(self.provider, 'interruption_current', side_effect=[True, False]) as current:
            returned = self.returned(record)
            self.assertEqual(current.call_count, 2)
        # The persisted intent is permission, never a delivery receipt.
        creates = [event for event in returned['events'] if (event.get('intent') or {}).get('kind') == 'create']
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]['intent']['continue_message'], 'please continue')

    def test_final_gate_optional_read_exception_also_omits_without_retry(self):
        record = self.park()
        with patch.object(self.provider, 'interruption_current', side_effect=[True, OSError('late optional failure')]):
            self.returned(record)

    def test_corrupt_optional_settings_disable_message_not_restore(self):
        record = self.park()
        (self.store.root/'settings.json').write_text('{broken json')
        self.returned(record)
        self.assertEqual((self.store.root/'settings.json').read_text(), '{broken json')

    def test_disabled_setting_does_not_read_optional_reopen_evidence(self):
        record = self.park()
        Settings(self.store.root).save(dict(DEFAULTS, auto_continue_interrupted=False))
        with patch.object(self.provider, 'interruption_current', side_effect=AssertionError('must not read')) as current:
            self.returned(record)
            current.assert_not_called()

    def test_exact_unfinished_request_still_continues_once(self):
        returned = self.returned(self.park(), prompt=True)
        self.flow.reopen(returned['id'])
        self.assertEqual(self.prompts, [['please continue']])

    def test_exact_identity_guards_still_reject_changed_session_folder_or_process(self):
        record, _ = self.store.begin(self.desktop.source)
        receipt = self.store.transition(record, Phase.CLOSING, dict(kind='close'))
        tab = self.desktop.source.tabs[0]
        changed_tabs = (replace(tab, session_id=str(uuid.uuid4())),
                        replace(tab, cwd=str(self.root/'different')),
                        replace(tab, process=replace(tab.process, started='different birth')))
        for changed in changed_tabs:
            with self.subTest(identity=changed.identity):
                self.desktop.live[1] = Window('fixture', (changed,))
                with self.assertRaises(ParkingError): self.gate.close(receipt)
        self.assertEqual(self.desktop.calls, [])

    def test_duplicate_exact_session_still_blocks_close(self):
        other = replace(self.desktop.source.tabs[0], terminal_id='another-tab')
        self.desktop.live.append(Window('another-window', (other,)))
        with self.assertRaises(ParkingError): self.park()
        self.assertEqual(self.desktop.calls, [])

    def test_duplicate_appearing_after_save_blocks_final_close_gate(self):
        record, _ = self.store.begin(self.desktop.source)
        receipt = self.store.transition(record, Phase.CLOSING, dict(kind='close'))
        other = replace(self.desktop.source.tabs[0], terminal_id='late-manual-tab')
        self.desktop.live.append(Window('late-manual-window', (other,)))
        with self.assertRaisesRegex(NotSubmitted, 'opened elsewhere before close'):
            self.gate.close(receipt)
        self.assertEqual(self.desktop.calls, [])
        self.assertIn(self.desktop.source, self.desktop.live)

    def test_unresolved_same_provider_after_save_blocks_final_close_gate(self):
        record, _ = self.store.begin(self.desktop.source)
        receipt = self.store.transition(record, Phase.CLOSING, dict(kind='close'))
        other = replace(self.desktop.source.tabs[0], terminal_id='late-unresolved-tab')
        self.desktop.live.append(Window('late-unresolved-window', (other,)))
        self.desktop.issue_windows.add('late-unresolved-window')
        with self.assertRaisesRegex(NotSubmitted, 'unresolved claude identity'):
            self.gate.close(receipt)
        self.assertEqual(self.desktop.calls, [])

    def test_new_unrelated_session_after_save_does_not_block_final_close(self):
        record, _ = self.store.begin(self.desktop.source)
        receipt = self.store.transition(record, Phase.CLOSING, dict(kind='close'))
        other = replace(self.desktop.source.tabs[0], terminal_id='unrelated-tab',
                        session_id=str(uuid.uuid4()))
        unrelated = Window('unrelated-window', (other,))
        self.desktop.live.append(unrelated)
        self.gate.close(receipt)
        self.assertEqual([action for action, _ in self.desktop.calls], ['close'])
        self.assertIn(unrelated, self.desktop.live)
        self.assertIn(self.desktop.work, self.desktop.live)

    def test_checkpoint_journal_failure_is_not_swallowed_as_optional(self):
        original = self.store.save
        def failed(record):
            if record.get('close_interruptions'): raise OSError('journal write failed')
            return original(record)
        with patch.object(self.store, 'save', side_effect=failed):
            with self.assertRaises(OSError): self.park()
        self.assertEqual(self.desktop.calls, [])

    def test_unconfirmed_native_client_exit_still_blocks_completion(self):
        tab = self.desktop.source.tabs[0]
        self.processes.all.side_effect = lambda: {tab.process.pid: dict(started=tab.process.started)}
        with patch.object(self.provider, 'finish_close', side_effect=Uncertain('unknown shutdown result')):
            record = self.park()
        self.assertEqual(record['phase'], 'closing')
        self.assertEqual(record['intent']['kind'], 'stop_client')
        self.assertEqual(self.flow.reopen(record['id'])['phase'], 'closing')
        self.assertEqual([action for action, _ in self.desktop.calls], ['close'])

    def test_unknown_submitted_create_is_not_replayed_when_prompt_was_omitted(self):
        record = self.park()
        self.desktop.fail_create = True
        with patch.object(self.provider, 'interruption_current', return_value=False):
            record = self.flow.reopen(record['id'])
            self.assertEqual(record['intent']['kind'], 'create')
            self.flow.reopen(record['id'])
        self.assertEqual(self.prompts, [[]])
        self.assertEqual([action for action, _ in self.desktop.calls], ['close', 'create'])

    def test_codex_unknown_activity_and_unknown_interruption_still_block(self):
        tab = replace(self.desktop.source.tabs[0], provider='codex', activity='unknown', turn_id='exact-turn')
        self.desktop.source = Window('fixture', (tab,)); self.desktop.live[1] = self.desktop.source
        class Codex:
            def interrupt(self, tab, authorize):
                authorize(tab.session_id, tab.turn_id)
                raise Uncertain('interrupt may have been submitted')
            def launch(self, tab, receipt, prompt=None):
                return command(tab, receipt, ['codex', 'resume', tab.session_id], prompt)
        self.providers.items['codex'] = Codex()
        with self.assertRaises(ParkingError): self.park()
        self.assertEqual(self.desktop.calls, [])
        self.desktop.source = Window('fixture', (replace(tab, activity='running'),))
        self.desktop.live[1] = self.desktop.source
        record = self.park()
        self.assertEqual(record['phase'], 'interrupting')
        self.assertEqual(self.flow.reopen(record['id'])['phase'], 'interrupting')
        self.assertEqual(self.desktop.calls, [])


if __name__ == '__main__': unittest.main()
