"""Product completion semantics; no terminals, native services or wall-clock waits."""
import copy
from unittest.mock import Mock, patch
import unittest
import test_core
from parking.operations import Operations, pending
from parking.model import Uncertain, Phase
from parking.garage import dispatch, car_view


class CompletionTests(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        self.parked = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.now = 0

    def sleep(self, seconds): self.now += seconds

    def runner(self, workflow=None, seconds=1):
        return Operations(workflow or self.flow, seconds=seconds,
                          clock=lambda: self.now, sleep=self.sleep)

    def record(self, *, count=3, intent=None, issue=None):
        record = copy.deepcopy(self.parked)
        record.update(phase='checking', intent=intent, issue=issue)
        record['targets'] = {str(i): dict(window_id='new', terminal_id='tab'+str(i), receipt='receipt'+str(i)) for i in range(count)}
        return record

    def test_late_confirmation_is_completed_by_product_not_test_harness(self):
        waiting = self.record()
        returned = dict(waiting, phase='returned')
        flow = Mock()
        flow.reopen.return_value = waiting
        flow.reconcile.side_effect = [waiting, Uncertain('startup changed'), returned]
        result = self.runner(flow).reopen(waiting['id'])
        self.assertEqual(result.status, 'completed')
        flow.reopen.assert_called_once_with(waiting['id'])
        self.assertEqual(flow.reconcile.call_count, 3)
        for call in flow.reconcile.call_args_list:
            self.assertEqual(call.args, (waiting['id'],))
            self.assertEqual(call.kwargs, {'read_only': True})

    def test_bound_preserves_uncertain_write_and_original_error_without_replay(self):
        intent = dict(kind='create', index=2, receipt='pending', window_id='new')
        waiting = self.record(count=2, intent=intent, issue='Original lost create acknowledgement')
        flow = Mock();flow.reopen.return_value=waiting;flow.reconcile.return_value=waiting
        result = self.runner(flow).reopen(waiting['id'])
        self.assertEqual(result.status, 'checking')
        self.assertEqual(result.record['intent'], intent)
        self.assertEqual(result.detail, waiting['issue'])
        flow.reopen.assert_called_once()
        self.assertGreaterEqual(self.now, 1)
        self.assertLessEqual(flow.reconcile.call_count, 6)

    def test_observed_archive_success_with_missing_tabs_requires_explicit_retry(self):
        waiting = self.record(count=2, intent=dict(kind='native_archive', index=2, archived=False))
        confirmed = dict(waiting, intent=None)
        flow=Mock();flow.reopen.return_value=waiting;flow.reconcile.return_value=confirmed
        result=self.runner(flow).reopen(waiting['id'])
        self.assertEqual(result.status, 'needs_action')
        flow.reopen.assert_called_once();flow.reconcile.assert_called_once()

    def test_definite_pre_submission_failure_does_not_poll(self):
        waiting=self.record(count=2, issue='Unarchive request was not sent')
        waiting['issue_kind']='not_submitted'
        flow=Mock();flow.reopen.return_value=waiting
        result=self.runner(flow).reopen(waiting['id'])
        self.assertEqual(result.status,'needs_action');flow.reconcile.assert_not_called()

    def test_check_only_never_finishes_close_or_calls_reopen(self):
        waiting=self.record(count=0,intent=dict(kind='close'))
        waiting['phase']='closing'
        flow=Mock();flow.store.load.return_value=waiting;flow.reconcile.return_value=waiting
        self.assertEqual(self.runner(flow).check(waiting['id']).status,'checking')
        flow.reopen.assert_not_called();flow.park.assert_not_called()
        self.assertTrue(all(c.kwargs == {'read_only':True} for c in flow.reconcile.call_args_list))

    def test_original_park_request_can_finish_its_authorized_close(self):
        waiting=self.record(count=0,intent=dict(kind='close'));waiting['phase']='closing'
        flow=Mock();flow.park.return_value=waiting;flow.reconcile.return_value=self.parked
        result=self.runner(flow).park('fixture','fingerprint')
        self.assertEqual(result.status,'completed')
        flow.reconcile.assert_called_once_with(waiting['id'],read_only=False)

    def test_late_close_requires_explicit_finish_parking_not_hidden_mutation(self):
        waiting=self.record(count=0,intent=dict(kind='close'));waiting['phase']='closing'
        confirmed=dict(waiting,close_confirmed=True)
        flow=Mock();flow.store.load.return_value=waiting;flow.reconcile.return_value=confirmed
        result=self.runner(flow).check(waiting['id'])
        self.assertEqual(result.status,'needs_action')
        self.assertIn('Finish parking',result.detail)
        flow.finish_parking.assert_not_called();flow.reopen.assert_not_called()
        self.assertEqual(car_view(confirmed,self.store.root)['status'],'finish_parking')
        flow.finish_parking.return_value=self.parked
        self.assertEqual(self.runner(flow).finish_parking(waiting['id']).status,'completed')
        flow.finish_parking.assert_called_once_with(waiting['id'])
        flow.reopen.assert_not_called()

    def test_real_journal_late_close_explicit_finish_never_reopens_or_recloses(self):
        record=self.store.load(self.parked['id'])
        self.store.transition(record,Phase.CLOSING,{'kind':'close'})
        before=list(self.desktop.calls)
        checked=self.runner().check(record['id'])
        self.assertEqual(checked.status,'needs_action')
        self.assertTrue(checked.record.get('close_confirmed'))
        self.assertEqual(self.desktop.calls,before)
        finished=self.runner().finish_parking(record['id'])
        self.assertEqual(finished.status,'completed')
        self.assertEqual(finished.record['phase'],'parked')
        self.assertEqual(self.desktop.calls,before)

    def test_post_creation_exception_is_retained_across_runner_restart(self):
        def reopen(rid):
            result=self.flow.reopen_original(rid)
            self.store.transition(result, Phase.CHECKING)
            raise Uncertain('Final read interrupted after successful launches')
        self.flow.reopen_original=self.flow.reopen
        with patch.object(self.flow,'reopen',side_effect=reopen):
            result=self.runner().reopen(self.parked['id'])
        self.assertEqual(result.status,'completed')
        loaded=self.store.load(self.parked['id'])
        self.assertTrue(any('Final read interrupted' in (e.get('issue') or '') for e in loaded['events']))
        calls=list(self.desktop.calls)
        self.assertEqual(self.runner().check(loaded['id']).status,'completed')
        self.assertEqual(calls,self.desktop.calls)

    def test_dispatch_pending_is_not_false_failure_and_has_exact_check_target(self):
        record=self.store.load(self.parked['id'])
        record.update(phase='checking',intent=dict(kind='create',index=0,receipt='11111111-1111-4111-8111-111111111111',window_id=None),issue='Lost response')
        self.store.save(record)
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)), \
             patch('parking.garage.Operations',side_effect=lambda flow,**kwargs:self.runner(flow,seconds=0)):
            result=dispatch(['--live','check',record['id']])
        self.assertTrue(result['ok']);self.assertIsNone(result['error'])
        self.assertEqual(result['operation_status'],'checking')
        self.assertEqual(result['operation_id'],record['id'])
        self.assertEqual(result['cars'][0]['status'],'checking')
        self.assertEqual(result['cars'][0]['note'],'Lost response')
        self.assertFalse(result['cars'][0]['return_verified'])
        self.assertEqual(len(self.desktop.calls),1)  # Initial owned park only.

    def test_check_after_reload_retains_original_error_without_mutations(self):
        record=self.store.load(self.parked['id'])
        record.update(phase='checking',intent=None,issue='Known preflight failure',issue_kind='not_submitted')
        self.store.save(record)
        from parking.workflow import Workflow
        fresh=Workflow(self.store,self.desktop,self.gate)
        result=self.runner(fresh).check(record['id'])
        self.assertEqual(result.status,'needs_action')
        self.assertEqual(result.detail,'Known preflight failure')
        self.assertEqual(len(self.desktop.calls),1)
