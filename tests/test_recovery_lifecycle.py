"""Interrupted park/restore recovery. Fakes only; no working terminals touched."""
import copy
import signal
import unittest
from unittest.mock import patch
import test_freeparking as fixtures

cp = fixtures.cp


class RecoveryLifecycleTests(unittest.TestCase):
    setUp = fixtures.CarParkTests.setUp
    park = fixtures.CarParkTests.park
    parked_car = fixtures.CarParkTests.parked_car
    awaiting_confirmation = fixtures.CarParkTests.awaiting_confirmation

    def test_close_ack_lost_after_window_closed_is_not_a_parking_failure(self):
        original = self.iterm.call
        def call(action, **kwargs):
            result = original(action, **kwargs)
            if action == 'close':
                raise cp.ParkError('Lost close acknowledgement')
            return result
        with patch.object(self.iterm, 'call', side_effect=call):
            self.park()
        car = self.store.cars()[0][0]
        self.assertEqual(car['status'], 'parked')
        self.assertEqual(self.iterm.calls.count('close'), 1)
        self.assertEqual(self.proc.running, {})

    def test_close_error_with_window_present_keeps_recovery_and_does_not_terminate(self):
        original = self.iterm.call
        def call(action, **kwargs):
            if action == 'close':
                raise cp.ParkError('Specific close failure')
            return original(action, **kwargs)
        with patch.object(self.iterm, 'call', side_effect=call):
            self.park()
        car = self.store.cars()[0][0]
        self.assertEqual(car['status'], 'attention')
        self.assertIn('Specific close failure', car['note'])
        self.assertTrue(all(number == signal.SIGINT for _, number in self.proc.signals))

    def test_retry_of_unchanged_original_reuses_durable_backup(self):
        self.iterm.cancel = True
        self.park()
        before = self.store.cars()[0][0]
        self.iterm.cancel = False
        self.park()
        cars = self.store.cars()[0]
        self.assertEqual(len(cars), 1)
        self.assertEqual(cars[0]['id'], before['id'])
        self.assertEqual(cars[0]['status'], 'parked')

    def test_changed_conversation_never_overwrites_original_backup(self):
        first = cp.prepare_car(self.store, self.window, self.proc)
        changed = copy.deepcopy(self.window)
        changed['tabs'][0]['session_id'] = '00000000-0000-4000-8000-000000000001'
        second = cp.prepare_car(self.store, changed, self.proc)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.load(first['id'])['tabs'][0]['session_id'], fixtures.SID)

    def test_interrupt_auto_closing_one_tab_closes_only_original_survivors(self):
        self.window['tabs'].append({**self.tab, 'index': 1, 'terminal_id': 'second'})
        self.window['fingerprint'] = cp.fingerprint(self.window)
        self.iterm.raw[0]['tabs'].append({'index': 1, 'sessions': [{'id': 'second'}]})
        original_signal = self.proc.send_signal
        original_call = self.iterm.call
        def send(process, number):
            original_signal(process, number)
            if number == signal.SIGINT:
                self.iterm.raw[0]['tabs'] = self.iterm.raw[0]['tabs'][:1]
        def call(action, **kwargs):
            if action == 'close':
                self.assertEqual(kwargs['sessionIds'], [fixtures.TERMINAL])
            return original_call(action, **kwargs)
        with patch.object(self.proc, 'send_signal', side_effect=send), patch.object(self.iterm, 'call', side_effect=call):
            self.park()
        self.assertEqual(self.store.cars()[0][0]['status'], 'parked')

    def test_new_tab_after_interrupt_prevents_close(self):
        original = self.proc.send_signal
        def send(process, number):
            original(process, number)
            if number == signal.SIGINT and len(self.iterm.raw[0]['tabs']) == 1:
                self.iterm.raw[0]['tabs'].append({'sessions': [{'id': 'unrelated'}]})
        with patch.object(self.proc, 'send_signal', side_effect=send):
            self.park()
        self.assertNotIn('close', self.iterm.calls)
        self.assertEqual(self.store.cars()[0][0]['status'], 'attention')
        self.assertTrue(all(number == signal.SIGINT for _, number in self.proc.signals))

    def test_original_open_window_reconciles_abandoned_saved_and_failed_parking(self):
        for status in ('saved_open', 'attention'):
            with self.subTest(status=status):
                car = cp.prepare_car(self.store, self.window, self.proc)
                car['status'] = status
                self.store.save(car)
                self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc), [car['id']])
                archived = self.store.load(car['id'])
                self.assertEqual(archived['removal_basis'], 'verified_live_return')
                self.assertEqual(archived['source_session_ids'], [fixtures.TERMINAL])
        self.assertEqual(self.iterm.calls, [])
        self.assertEqual(self.proc.signals, [])

    def test_manual_exact_resume_reconciles_even_a_parked_record_without_receipts(self):
        car, window = self.awaiting_confirmation()
        self.store.save(car)  # Simulate manual resume, no restore journal.
        self.iterm.calls.clear()
        with patch.object(cp, 'discover', return_value=[window]):
            self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc), [car['id']])
        self.assertEqual(self.iterm.calls, [])

    def test_unopened_parked_car_is_not_archived(self):
        car = self.parked_car()
        with patch.object(cp, 'discover', return_value=[]):
            self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc), [])
        self.assertEqual(self.store.load(car['id'])['status'], 'parked')

    def test_partial_failed_restore_never_archives_until_all_conversations_are_identified(self):
        car, window = self.awaiting_confirmation()
        car.update(status='attention', pending_tab=1)
        car['tabs'].append({**car['tabs'][0], 'index': 1,
                            'session_id': '00000000-0000-4000-8000-000000000001'})
        self.store.save(car)
        with patch.object(cp, 'discover', return_value=[window]):
            self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc), [])
        self.assertEqual(self.store.load(car['id'])['pending_tab'], 1)

    def test_presentation_failure_cannot_prevent_later_launches_or_verification(self):
        car = self.parked_car()
        car['window_bounds'] = dict(x=10, y=10, width=800, height=600)
        car['tabs'] = [{**car['tabs'][0], 'index': i} for i in range(4)]
        self.store.save(car)
        actions = []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'create':
                return {'windowId': 'new-window', 'sessionId': 'new-' + str(len(actions))}
            persisted = self.store.load(car['id'])
            self.assertEqual(len(persisted['restored_targets']), 4)
            self.assertEqual(persisted['status'], 'restored')
            self.assertIsNone(persisted['pending_tab'])
            raise cp.ParkError('Presentation unavailable')
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=call):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(actions, ['create'] * 4 + ['set-bounds', 'set-titles', 'focus'])
        saved = self.store.load(car['id'])
        self.assertTrue(saved['bounds_pending'])
        self.assertTrue(all(t['title_pending'] for t in saved['restored_targets'].values()))
        self.assertIn('Presentation unavailable', saved['note'])

    def test_lost_create_acknowledgement_is_reconciled_without_second_launch(self):
        car, window = self.awaiting_confirmation()
        saved = self.store.load(car['id'])
        saved.update(status='attention', pending_tab=0, restored_targets={})
        self.store.save(saved)
        self.iterm.calls.clear()
        with patch.object(cp, 'restore', side_effect=cp.ParkError('Lost create receipt')), patch.object(cp, 'discover', return_value=[window]):
            reply = cp.open_car(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(reply['verified_archived_ids'], [car['id']])
        self.assertEqual(self.iterm.calls, [])
        self.assertIsNone(self.store.load(car['id'])['pending_tab'])

    def test_unresolved_create_error_is_preserved_without_retrying_launch(self):
        car = self.parked_car()
        self.iterm.calls.clear()
        with patch.object(cp, 'restore', side_effect=cp.ParkError('Specific creation error')) as restore, patch.object(cp, 'discover', return_value=[]):
            with self.assertRaisesRegex(cp.ParkError, 'Specific creation error'):
                cp.open_car(self.store, self.iterm, self.proc, car['id'])
        restore.assert_called_once()
        self.assertEqual(self.iterm.calls, [])

    def test_discovery_cost_is_one_snapshot_per_reconciliation_pass(self):
        car = cp.prepare_car(self.store, self.window, self.proc)
        duplicate = copy.deepcopy(car)
        duplicate['id'] = '00000000-0000-4000-8000-000000000001'
        self.store.save(duplicate)  # Legacy duplicate recovery file, same window.
        with patch.object(cp, 'discover', return_value=[self.window]) as discover:
            archived = cp.archive_verified_returns(self.store, self.iterm, self.proc)
        self.assertEqual(set(archived), {car['id'], duplicate['id']})
        discover.assert_called_once()
