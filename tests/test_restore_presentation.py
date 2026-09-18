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
            if action in ('set-titles', 'set-bounds'):
                saved = self.store.load(car['id'])
                self.assertEqual(saved['restored_targets']['0']['terminal_id'], 'restored-fixture')
                self.assertEqual(saved['created_window_id'], '2')
                seen.append((action, kwargs))
            return original(action, **kwargs)
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=call):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual([x[0] for x in seen], ['set-bounds', 'set-titles'])
        self.assertEqual(seen[0][1]['bounds'], bounds)
        self.assertEqual(seen[1][1]['titles'][0]['title'], self.tab['title'])
        saved = self.store.load(car['id'])
        self.assertEqual(saved['window_bounds'], bounds)
        self.assertFalse(saved['bounds_pending'])
        self.assertFalse(saved['restored_targets']['0']['title_pending'])

    def test_many_tabs_resize_first_and_use_one_title_batch(self):
        bounds = dict(x=100, y=70, width=900, height=650)
        self.window['bounds'] = bounds
        car = self.parked_car()
        car['tabs'] = [{**car['tabs'][0], 'index': i} for i in range(4)]
        self.store.save(car)
        actions, created = [], []
        def call(action, **kwargs):
            actions.append(action)
            if action == 'create':
                sid = 'restored-' + str(len(created))
                created.append(sid)
                return {'windowId': '2', 'sessionId': sid}
            if action == 'set-bounds':
                persisted = self.store.load(car['id'])
                self.assertEqual(len(persisted['restored_targets']), len(created))
                self.assertEqual(kwargs['sessionIds'], created)
                return {'ok': True, 'bounds': bounds}
            if action == 'set-titles':
                self.assertEqual([t['sessionId'] for t in kwargs['titles']], created)
            return {'ok': True}
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=call):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(actions, ['create', 'set-bounds', 'create', 'create', 'create',
                                   'set-bounds', 'set-titles', 'focus'])

    def test_failed_geometry_keeps_pending_and_does_not_set_titles(self):
        self.window['bounds'] = dict(x=100, y=70, width=900, height=650)
        car = self.parked_car()
        original = self.iterm.call
        def call(action, **kwargs):
            if action == 'set-bounds': return {'ok': True}  # Missing readback.
            return original(action, **kwargs)
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=call):
            with self.assertRaisesRegex(cp.ParkError, 'could not be checked'):
                cp.restore(self.store, self.iterm, self.proc, car['id'])
        saved = self.store.load(car['id'])
        self.assertTrue(saved['bounds_pending'])
        self.assertTrue(saved['restored_targets']['0']['title_pending'])
        self.assertNotIn('set-titles', self.iterm.calls)

    def test_old_car_without_geometry_still_restores_saved_title(self):
        car = self.parked_car()
        car.pop('window_bounds', None)
        self.store.save(car)
        with patch.object(cp, 'discover', return_value=[]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertIn('set-titles', self.iterm.calls)
        self.assertNotIn('set-bounds', self.iterm.calls)

    def test_existing_matching_tabs_are_not_retitled_or_resized(self):
        car, window = self.awaiting_confirmation()
        self.iterm.calls.clear()
        with patch.object(cp, 'discover', return_value=[window]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(self.iterm.calls, ['focus'])

    def test_geometry_retry_resizes_only_the_journaled_window_before_titles(self):
        bounds = dict(x=120, y=60, width=940, height=700)
        self.window['bounds'] = bounds
        car = self.parked_car()
        original = self.iterm.call
        def fail_bounds(action, **kwargs):
            if action == 'set-bounds': raise cp.ParkError('Frame not settled')
            return original(action, **kwargs)
        with patch.object(cp, 'discover', return_value=[]), patch.object(self.iterm, 'call', side_effect=fail_bounds):
            with self.assertRaisesRegex(cp.ParkError, 'Frame not settled'):
                cp.restore(self.store, self.iterm, self.proc, car['id'])
        process = {**self.tab['process'], 'pid': 201, 'started': 'new process'}
        self.proc.running = {201: process}
        window = {**self.window, 'id': '2', 'tabs': [
            {**self.tab, 'terminal_id': 'restored-fixture', 'process': process}]}
        self.iterm.calls.clear()
        with patch.object(cp, 'discover', return_value=[window]):
            cp.restore(self.store, self.iterm, self.proc, car['id'])
        self.assertEqual(self.iterm.calls, ['set-bounds', 'set-titles', 'focus'])
        self.assertFalse(self.store.load(car['id'])['bounds_pending'])

    def test_title_failure_is_journaled_then_retried_without_duplicate_launch(self):
        car = self.parked_car()
        original = self.iterm.call
        def fail_title(action, **kwargs):
            if action == 'set-titles':
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
        self.assertEqual(self.iterm.calls, ['set-titles', 'focus'])

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


class ConcurrentDiscoveryTests(unittest.TestCase):
    def test_tab_reads_overlap_but_window_and_tab_order_are_preserved(self):
        from threading import Barrier
        from types import SimpleNamespace as S
        barrier = Barrier(4, timeout=2)
        def resolve(tab, *args):
            barrier.wait()
            return dict(index=tab['index'], terminal_id=tab['id'], provider='shell',
                        session_id='', cwd='/fixture', issue='', process={})
        raw = [{'id': str(i), 'title': 'Fixture', 'tabs': [
            {'index': j, 'id': f'{i}-{j}'} for j in range(2)]} for i in range(2)]
        with patch.object(cp, 'resolve_tab', side_effect=resolve), patch.object(cp, 'read_hook_records', return_value=[]):
            result = cp.discover(S(windows=lambda: raw), S(all=lambda: {}))
        self.assertEqual([w['id'] for w in result], ['0', '1'])
        self.assertEqual([[t['terminal_id'] for t in w['tabs']] for w in result],
                         [['0-0', '0-1'], ['1-0', '1-1']])


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
        self.connection = S(websocket=S(close_timeout=10))
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
        self.window = window
        async def get_app(connection):
            self.assertFalse(self.in_transaction)
            return app
        owner = self
        class Transaction:
            def __init__(self, connection): pass
            async def __aenter__(self): owner.in_transaction = True
            async def __aexit__(self, *args): owner.in_transaction = False
        self.api = S(async_get_app=get_app, Transaction=Transaction,
                     run_until_complete=lambda callback, retry: asyncio.run(callback(self.connection)))

    def invoke(self, request):
        import contextlib, io, json, sys
        output = io.StringIO()
        with patch.dict(sys.modules, {'iterm2': self.api}), contextlib.redirect_stdout(output):
            self.helper.main(request)
        return json.loads(output.getvalue())

    def test_disconnect_timeout_changes_only_after_rpc_and_readback(self):
        async def set_title(title):
            self.assertEqual(self.connection.websocket.close_timeout, 10)
        async def get_variable(key):
            self.assertEqual(self.connection.websocket.close_timeout, 10)
            return self.title
        self.tab.async_set_title = set_title
        self.tab.async_get_variable = get_variable
        result = self.invoke({'action': 'set-title', 'sessionId': 'fixture', 'title': self.title})
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(self.connection.websocket.close_timeout, 1)

    def test_shorter_disconnect_timeout_is_not_extended(self):
        self.connection.websocket.close_timeout = 0.2
        self.invoke({'action': 'set-title', 'sessionId': 'fixture', 'title': self.title})
        self.assertEqual(self.connection.websocket.close_timeout, 0.2)

    def test_missing_transport_timeout_keeps_supported_operation(self):
        del self.connection.websocket.close_timeout
        result = self.invoke({'action': 'set-title', 'sessionId': 'fixture', 'title': self.title})
        self.assertEqual(result['state'], 'updated')
        self.assertFalse(hasattr(self.connection.websocket, 'close_timeout'))

    def test_failed_operation_is_not_success_even_with_faster_disconnect(self):
        result = self.invoke({'action': 'set-title', 'sessionId': 'missing', 'title': self.title})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.connection.websocket.close_timeout, 1)

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

    def test_batch_titles_are_concurrent_and_all_read_back(self):
        import asyncio
        from types import SimpleNamespace as S
        started = set()
        async def write(sid, title):
            started.add(sid)
            async def wait_for_other():
                while len(started) < 2:
                    await asyncio.sleep(0)
            await asyncio.wait_for(wait_for_other(), timeout=1)
            self.calls.append(('title', sid))
        async def read(key): return self.title
        self.window.tabs = [S(all_sessions=[S(session_id=sid)],
                              async_set_title=lambda title, sid=sid: write(sid, title),
                              async_get_variable=read) for sid in ('first', 'second')]
        result = self.invoke({'action': 'set-titles', 'titles': [
            {'sessionId': sid, 'title': self.title} for sid in ('first', 'second')]})
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(set(self.calls), {('title', 'first'), ('title', 'second')})

    def test_partial_title_failure_waits_for_other_updates_and_returns_error(self):
        import asyncio
        from types import SimpleNamespace as S
        async def fail(title): raise ValueError('Write failed')
        async def finish(title):
            await asyncio.sleep(0.01)
            self.calls.append(('finished', title))
        async def read(key): return self.title
        self.window.tabs = [S(all_sessions=[S(session_id=sid)], async_set_title=writer,
                              async_get_variable=read) for sid, writer in [('first', fail), ('second', finish)]]
        result = self.invoke({'action': 'set-titles', 'titles': [
            {'sessionId': sid, 'title': self.title} for sid in ('first', 'second')]})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.calls, [('finished', self.title.replace('\\', '\\\\'))])

    def test_entire_batch_validated_before_any_title_changes(self):
        for second in ('fixture', 'missing'):
            result = self.invoke({'action': 'set-titles', 'titles': [
                {'sessionId': sid, 'title': self.title} for sid in ('fixture', second)]})
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
