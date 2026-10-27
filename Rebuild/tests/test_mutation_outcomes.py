"""All mutation boundaries distinguish rejection from unknown submission."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch
import test_core
import test_continuation_workflow
from parking.agents import Codex, finish_client_close
from parking.adapters import ITerm, Processes
from parking.model import NotSubmitted, ParkingError, Uncertain, Window, Phase


class MutationOutcomes(unittest.TestCase):
    def setUp(self): test_continuation_workflow.ContinuationWorkflow.setUp(self)

    def test_unsent_interrupt_allows_park_retry_without_closing_on_failure(self):
        provider = self.providers.items['claude']
        original = provider.interrupt
        provider.interrupt = Mock(side_effect=NotSubmitted('No connection; nothing sent'))
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.assertEqual(record['phase'], 'saved')
        self.assertIsNone(record['intent'])
        self.assertEqual(record['issue_kind'], 'not_submitted')
        self.assertEqual(self.desktop.calls, [])
        provider.interrupt = original
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.assertEqual(record['phase'], 'parked')
        self.assertEqual(len(self.stops), 1)

    def test_gate_interrupt_inventory_failure_is_not_submitted(self):
        record, _ = self.store.begin(self.desktop.source)
        receipt = self.store.transition(record, Phase.INTERRUPTING, dict(kind='interrupt', index=0))
        provider = self.providers.items['claude']
        provider.interrupt = Mock()
        with patch.object(self.desktop, 'observe', side_effect=Uncertain('Changing inventory')):
            with self.assertRaises(NotSubmitted): self.gate.interrupt(receipt)
        provider.interrupt.assert_not_called()

    def test_unknown_interrupt_never_repeats_when_no_native_receipt_exists(self):
        provider = self.providers.items['claude']
        provider.interrupt = Mock(side_effect=Uncertain('Response lost'))
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        for _ in range(2):
            self.flow.reconcile(record['id'])
            self.flow.reopen(record['id'])
        provider.interrupt.assert_called_once()
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.store.load(record['id'])['issue'], 'Response lost')

    def test_observed_interrupt_outcome_reconciles_without_repeating_or_closing(self):
        provider = self.providers.items['claude']
        provider.interrupt = Mock(side_effect=Uncertain('Response lost'))
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        provider.observe_interruption = Mock(return_value=dict(provider='claude',
            session_id=self.sid, outcome='interrupted'))
        record = self.flow.reconcile(record['id'], read_only=True)
        self.assertEqual(record['phase'], 'saved')
        self.assertIsNone(record['intent'])
        self.assertEqual(record['interruptions']['0']['outcome'], 'interrupted')
        provider.interrupt.assert_called_once()
        self.assertEqual(self.desktop.calls, [])

    def test_generic_close_failure_is_unknown_not_permission_to_retry(self):
        # Simulate a submission with a lost non-ParkingError acknowledgement.
        def unknown(*args, **kwargs): raise OSError('Lost response')
        self.desktop.call = unknown
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.assertEqual(record['intent'], {'kind': 'close'})
        self.assertEqual(record['phase'], 'closing')

    def test_terminal_exact_target_rejection_is_explicit_not_submitted(self):
        terminal = ITerm(runner=lambda *args, **kwargs: '{"notSubmitted":"Target changed"}')
        with self.assertRaises(NotSubmitted): terminal.call('close', windowId='owned', sessionIds=['owned'])

    def test_terminal_transport_failure_still_preserves_uncertainty(self):
        def lost(*args, **kwargs): raise OSError('Lost helper')
        with self.assertRaises(Uncertain): ITerm(runner=lost).call('create', windowId=None, command='fixture')

    def test_shutdown_preflight_failure_cannot_be_recorded_as_submitted_signal(self):
        tab = self.desktop.source.tabs[0]
        processes = Mock()
        processes.all.side_effect = OSError('Process list unavailable')
        with self.assertRaises(NotSubmitted): finish_client_close(tab, processes, Mock())
        processes.send_signal.assert_not_called()

    def test_codex_preflight_read_failure_does_not_submit_interrupt(self):
        rpc = Mock()
        rpc.read.side_effect = OSError('Read unavailable')
        with self.assertRaises(NotSubmitted): Codex(Mock(), rpc).interrupt(self.desktop.source.tabs[0], Mock())
        rpc.interrupt.assert_not_called()

    def test_signal_validation_refusal_is_not_submitted(self):
        processes = Processes()
        with patch.object(processes, 'all', return_value={}), patch('parking.adapters.os.kill') as kill:
            with self.assertRaises(NotSubmitted): processes.send_signal(dict(pid=999, started='birth', tty='/dev/test'), 2)
            kill.assert_not_called()

    def test_codex_interruption_recovery_requires_exact_latest_turn(self):
        rpc = Mock(); provider = Codex(Mock(), rpc)
        tab = replace(self.desktop.source.tabs[0], provider='codex', turn_id='saved-turn')
        rpc.call.return_value = {'data': []}
        rpc.read.return_value = dict(id=tab.session_id, cwd=tab.cwd,
            turns=[dict(id='new-turn', status='completed')])
        self.assertIsNone(provider.observe_interruption(tab))
        self.assertFalse(provider.interruption_current(tab, dict(provider='codex', session_id=tab.session_id, turn_id='saved-turn')))
        rpc.read.return_value['turns'] = [dict(id='saved-turn', status='interrupted')]
        self.assertEqual(provider.observe_interruption(tab)['outcome'], 'interrupted')
        rpc.interrupt.assert_not_called()

    def test_historical_interruption_is_not_current_continuation_credit(self):
        rpc = Mock(); provider = Codex(Mock(), rpc)
        tab = replace(self.desktop.source.tabs[0], provider='codex', turn_id='saved-turn')
        outcome = dict(provider='codex', session_id=tab.session_id, turn_id='saved-turn')
        rpc.read.return_value = dict(id=tab.session_id, cwd=tab.cwd, turns=[
            dict(id='new-turn', status='completed'), dict(id='saved-turn', status='interrupted')])
        self.assertFalse(provider.interruption_current(tab, outcome))

    def test_old_interrupt_can_reconcile_without_authorizing_stale_continuation(self):
        rpc = Mock(); provider = Codex(Mock(), rpc)
        tab = replace(self.desktop.source.tabs[0], provider='codex', turn_id='saved-turn')
        rpc.read.return_value = dict(id=tab.session_id, cwd=tab.cwd,
            turns=[dict(id='new-turn', status='completed')])
        rpc.call.return_value = dict(data=[dict(id='saved-turn', status='interrupted')])
        outcome = provider.observe_interruption(tab)
        self.assertEqual(outcome['outcome'], 'interrupted')
        self.assertFalse(provider.interruption_current(tab, outcome))
        rpc.interrupt.assert_not_called()
        self.assertEqual(rpc.call.call_args.args[0], 'thread/turns/list')

    def test_reopen_does_not_continue_a_request_that_has_since_completed(self):
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.providers.items['claude'].interruption_current = Mock(return_value=False)
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual(self.prompts, [[]])

    def test_continuation_change_between_plan_and_launch_opens_without_message(self):
        record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.providers.items['claude'].interruption_current = Mock(side_effect=[True, False])
        result = self.flow.reopen(record['id'])
        self.assertIsNone(result['intent'])
        self.assertEqual(result['phase'], 'returned')
        self.assertIsNone(result['issue'])
        self.assertEqual(self.prompts, [[]])
