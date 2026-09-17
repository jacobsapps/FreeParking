"""Offline coverage: saved appearance, automatic archival and optional close API."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import test_freeparking as fixtures
cp = fixtures.cp


class PresentationTests(unittest.TestCase):
    setUp = fixtures.CarParkTests.setUp
    park = fixtures.CarParkTests.park
    parked_car = fixtures.CarParkTests.parked_car
    awaiting_confirmation = fixtures.CarParkTests.awaiting_confirmation

    def test_saved_geometry_and_title_applied_only_after_created_identity_is_durable(self):
        bounds = dict(x=120, y=50, width=940, height=720)
        self.window['bounds'] = bounds
        self.tab['title'] = '🚙 A "quoted" title'
        car = self.parked_car()
        original = self.iterm.call
        seen = []
        def call(action, **kwargs):
            if action in ('set-title', 'set-bounds'):
                saved = self.store.load(car['id'])
                self.assertEqual(saved['restored_targets']['0']['terminal_id'], 'restored-fixture')
                self.assertEqual(saved['created_window_id'], '2')
                seen.append((action, kwargs))
            return original(action, **kwargs)
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=call):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(seen[0][1]['title'], self.tab['title'])
        self.assertEqual(seen[1][1]['bounds'], bounds)
        saved = self.store.load(car['id'])
        self.assertEqual(saved['window_bounds'], bounds)
        self.assertFalse(saved['bounds_pending'])
        self.assertFalse(saved['restored_targets']['0']['title_pending'])

    def test_old_car_without_geometry_still_restores_saved_title(self):
        car = self.parked_car()
        car.pop('window_bounds', None)
        self.store.save(car)
        with patch.object(cp, 'discover', return_value=[]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertIn('set-title', self.iterm.calls)
        self.assertNotIn('set-bounds', self.iterm.calls)

    def test_existing_matching_tabs_are_not_retitled_or_resized(self):
        car, window = self.awaiting_confirmation()
        self.iterm.calls.clear()
        with patch.object(cp, 'discover', return_value=[window]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(self.iterm.calls, ['focus'])

    def test_title_failure_is_journaled_then_retried_without_duplicate_launch(self):
        car = self.parked_car()
        original = self.iterm.call
        def fail_title(action, **kwargs):
            if action == 'set-title':
                raise cp.ParkError('Title not written')
            return original(action, **kwargs)
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=fail_title):
            with self.assertRaisesRegex(cp.ParkError, 'Title not written'):
                cp.restore(self.store, self.iterm, self.proc, car['id'])
        saved = self.store.load(car['id'])
        self.assertTrue(saved['restored_targets']['0']['title_pending'])
        self.assertIsNone(saved['pending_tab'])
        process = {**self.tab['process'], 'pid': 201, 'started': 'new process'}
        self.proc.running = {201: process}
        window = {**self.window, 'id': '2', 'tabs': [{**self.tab, 'terminal_id': 'restored-fixture', 'process': process}]}
        self.iterm.calls.clear()
        with patch.object(cp, 'discover', return_value=[window]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(self.iterm.calls, ['set-title', 'focus'])

    def test_verified_return_archives_automatically_and_keeps_all_recovery(self):
        car, window = self.awaiting_confirmation()
        with patch.object(cp, 'discover', return_value=[window]):
            reply = cp.open_car(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(reply['verified_archived_ids'], [car['id']])
        self.assertEqual(self.store.cars()[0], [])
        archived = self.store.cars(archived=True)[0][0]
        self.assertEqual(archived['tabs'], self.store.load(car['id'])['tabs'])
        cp.unarchive_car(self.store, car['id'])
        self.assertEqual(self.store.cars()[0][0]['id'], car['id'])
        self.assertEqual(self.store.cars(archived=True)[0], [])
        with patch.object(cp, 'discover', return_value=[window]):
            cp.open_car(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(self.store.load(car['id'])['status'], 'archived')

    def test_unverified_return_keeps_car_even_after_retry_limit(self):
        car, _ = self.awaiting_confirmation()
        with patch.object(cp, 'discover', return_value=[]):
            self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc, attempts=3), [])
        self.assertEqual(self.store.load(car['id'])['status'], 'restored')

    def test_duplicate_conversation_prevents_autoarchive(self):
        car, window = self.awaiting_confirmation()
        duplicate = {**window, 'id': '3', 'tabs': [{**window['tabs'][0], 'terminal_id': 'duplicate'}]}
        with patch.object(cp, 'discover', return_value=[window, duplicate]):
            with self.assertRaisesRegex(cp.ReturnNotVerified, 'more than once'):
                cp.verify_return(self.store.load(car['id']), self.iterm, self.proc)

    def test_extra_tabs_prevent_autoarchive(self):
        car, window = self.awaiting_confirmation()
        self.iterm.raw[0]['tabs'].append({'sessions': [{'id': 'extra'}]})
        with patch.object(cp, 'discover', return_value=[window]):
            self.assertEqual(cp.archive_verified_returns(self.store, self.iterm, self.proc), [])
        self.assertEqual(self.store.load(car['id'])['status'], 'restored')

    def test_permission_failure_is_not_a_verified_return(self):
        car, _ = self.awaiting_confirmation()
        with patch.object(cp, 'discover', side_effect=cp.AutomationPermissionError('Denied')):
            with self.assertRaises(cp.AutomationPermissionError):
                cp.archive_verified_returns(self.store, self.iterm, self.proc)
        self.assertEqual(self.store.load(car['id'])['status'], 'restored')

    def test_invalid_geometry_fails_closed(self):
        car = self.parked_car()
        for bounds in ({'x': 0, 'y': 0, 'width': True, 'height': 10},
                       {'x': float('inf'), 'y': 0, 'width': 100, 'height': 100}):
            car['window_bounds'] = bounds
            self.store.save(car)
            with self.assertRaisesRegex(cp.ParkError, 'window size'):
                self.store.load(car['id'])


class OptionalCloseTests(unittest.TestCase):
    def test_missing_runtime_uses_normal_iterm_confirmation(self):
        with patch.object(cp, 'iterm_python_runtime', return_value=None), patch.object(cp, 'run', return_value='{"ok":true}') as run:
            cp.ITerm().call('close', windowId='fixture-window', sessionIds=['fixture-session'])
        self.assertEqual(len(run.call_args_list), 2)  # check, normal close
        self.assertIn('"action": "close"', run.call_args_list[-1].args[0][-1])

    def test_api_success_does_not_send_a_second_close(self):
        with patch.object(cp, 'iterm_python_runtime', return_value=Path('/fixture/python')), patch.object(cp, 'run', side_effect=['{"ok":true}', '{"state":"closed"}']) as run:
            cp.ITerm().call('close', windowId='fixture-window', sessionIds=['fixture-session'])
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[-1].args[0][0], '/fixture/python')

    def test_ambiguous_api_failure_never_retries_via_applescript(self):
        with patch.object(cp, 'iterm_python_runtime', return_value=Path('/fixture/python')), patch.object(cp, 'run', side_effect=['{"ok":true}', '{"state":"error","error":"Uncertain"}']) as run:
            with self.assertRaisesRegex(cp.ParkError, 'Uncertain'):
                cp.ITerm().call('close', windowId='fixture-window', sessionIds=['fixture-session'])
        self.assertEqual(len(run.call_args_list), 2)

    def test_unavailable_api_falls_back_to_normal_close(self):
        with patch.object(cp, 'iterm_python_runtime', return_value=Path('/fixture/python')), patch.object(cp, 'run', side_effect=['{"ok":true}', '{"state":"unavailable"}', '{"ok":true}']) as run:
            cp.ITerm().call('close', windowId='fixture-window', sessionIds=['fixture-session'])
        self.assertEqual(len(run.call_args_list), 3)

    def test_api_target_requires_exact_order_no_splits_and_only_one_window(self):
        from types import SimpleNamespace as S
        spec = importlib.util.spec_from_file_location('close_helper', Path(cp.__file__).with_name('iterm_api.py'))
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        window = S(tabs=[S(all_sessions=[S(session_id='a')]), S(all_sessions=[S(session_id='b')])])
        app = S(terminal_windows=[window])
        self.assertIs(helper.matching_window(app, ['a', 'b']), window)
        for ids in ([], ['a'], ['b', 'a'], ['a', 'a']):
            with self.assertRaises(ValueError): helper.matching_window(app, ids)
        window.tabs[0].all_sessions.append(S(session_id='split'))
        with self.assertRaises(ValueError): helper.matching_window(app, ['a', 'b'])

class APIHelperTests(unittest.TestCase):
    def setUp(self):
        import asyncio
        from types import SimpleNamespace as S
        spec = importlib.util.spec_from_file_location('api_helper', Path(cp.__file__).with_name('iterm_api.py'))
        self.helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.helper)
        self.calls = []
        self.in_transaction = False
        self.title = 'Saved "title" \\(literal) 🅿️'
        async def set_title(title):
            self.assertFalse(self.in_transaction)
            self.calls.append(('title', title))
        async def get_variable(key): return self.title
        async def close(force=False):
            self.assertTrue(self.in_transaction)
            self.calls.append(('close', force))
        self.tab = S(all_sessions=[S(session_id='fixture')], async_set_title=set_title, async_get_variable=get_variable)
        window = S(tabs=[self.tab], async_close=close)
        async def refresh(): self.calls.append(('refresh', self.in_transaction))
        app = S(terminal_windows=[window], async_refresh=refresh)
        async def get_app(connection):
            self.assertFalse(self.in_transaction)
            return app
        owner = self
        class Transaction:
            def __init__(self, connection): pass
            async def __aenter__(self): owner.in_transaction = True
            async def __aexit__(self, *args): owner.in_transaction = False
        self.api = S(async_get_app=get_app, Transaction=Transaction,
                     run_until_complete=lambda callback, retry: asyncio.run(callback(None)))

    def invoke(self, request):
        import contextlib, io, json, sys
        output = io.StringIO()
        with patch.dict(sys.modules, {'iterm2': self.api}), contextlib.redirect_stdout(output):
            self.helper.main(request)
        return json.loads(output.getvalue())

    def test_saved_title_is_literal_and_read_back_without_transaction(self):
        result = self.invoke({'action': 'set-title', 'sessionId': 'fixture', 'title': self.title})
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(self.calls, [('title', self.title.replace('\\', '\\\\'))])

    def test_failed_title_readback_keeps_car(self):
        result = self.invoke({'action': 'set-title', 'sessionId': 'fixture', 'title': 'Different'})
        self.assertEqual(result['state'], 'error')

    def test_title_never_targets_an_unknown_session(self):
        result = self.invoke({'action': 'set-title', 'sessionId': 'other', 'title': self.title})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.calls, [])

    def test_close_refreshes_and_verifies_in_transaction(self):
        result = self.invoke({'action': 'close', 'sessionIds': ['fixture']})
        self.assertEqual(result['state'], 'closed')
        self.assertEqual(self.calls, [('refresh', True), ('close', True)])

    def test_changed_tabs_never_close(self):
        result = self.invoke({'action': 'close', 'sessionIds': ['other']})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.calls, [('refresh', True)])

    def test_unknown_action_never_connects_or_closes(self):
        result = self.invoke({'sessionIds': ['fixture']})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
