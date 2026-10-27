"""Partial five-tab restores, with real journal/gates and an isolated fake desktop.

No native session, terminal, or production recovery data is accessed.
"""
from dataclasses import replace
import unittest
import uuid
from unittest.mock import Mock
import test_core
from parking.agents import command
from parking.model import NotSubmitted, Uncertain, Window
from parking.workflow import Workflow


class RestoreFailures(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        template = self.desktop.source.tabs[0]
        tabs = tuple(replace(template, terminal_id='fixture-' + str(i),
            title='Saved tab ' + str(i + 1), provider='codex',
            session_id=str(uuid.uuid4()), activity='idle') for i in range(5))
        self.desktop.source = Window('fixture', tabs)
        self.desktop.live[1] = self.desktop.source
        self.archived = {tab.session_id: False for tab in tabs}
        self.archive_calls = []
        self.injected_failure = None
        owner = self

        class Agent:
            def launch(self, tab, receipt, prompt=None):
                return command(tab, receipt, ['codex', 'resume', tab.session_id], prompt)

            def is_archived(self, tab):
                return owner.archived[tab.session_id]

            def set_archived(self, tab, desired, authorize):
                authorize(tab.session_id)
                if not desired and tab == tabs[3] and owner.injected_failure:
                    raise owner.injected_failure
                owner.archive_calls.append((tab.session_id, desired))
                owner.archived[tab.session_id] = desired

        self.providers.items['codex'] = Agent()
        original = self.desktop.call

        def call(action, **kwargs):
            result = original(action, **kwargs)
            if action == 'create':
                window = self.desktop.live[-1]
                returned = tuple(replace(tab, provider='codex',
                    session_id=tabs[i].session_id, activity='idle')
                    for i, tab in enumerate(window.tabs))
                self.desktop.live[-1] = Window(window.window_id, returned)
            return result

        self.desktop.call = call
        self.record = self.flow.park('fixture', self.desktop.source.fingerprint)
        self.assertEqual(self.record['phase'], 'parked')
        # Previously interrupted turns: retries must not send continuation twice.
        self.record['interruptions'] = {str(i): dict(provider='codex',
            session_id=tabs[i].session_id, outcome='interrupted') for i in (1, 2)}
        self.store.save(self.record)

    def reopen(self):
        return self.flow.reopen(self.record['id'])

    def assert_three_returned(self, record):
        self.assertEqual(set(record['targets']), {'0', '1', '2'})
        self.assertEqual(len([a for a, _ in self.desktop.calls if a == 'create']), 3)
        self.assertEqual(self.desktop.live[0], self.desktop.work)

    def test_pre_submission_failure_allows_retry_without_duplicate_tabs_or_prompts(self):
        self.injected_failure = NotSubmitted('Preflight thread read failed')
        record = self.reopen()
        self.assert_three_returned(record)
        self.assertIsNone(record['intent'])
        self.assertIn('Tab 4', record['issue'])
        self.assertIn('Preflight thread read failed', record['issue'])
        self.injected_failure = None
        # Recreate the coordinator, as after quitting/reopening the app.
        self.flow = Workflow(self.store, self.desktop, self.gate)
        returned = self.reopen()
        self.assertEqual(returned['phase'], 'returned')
        self.assertEqual(len(returned['targets']), 5)
        launches = [args['command'] for action, args in self.desktop.calls if action == 'create']
        self.assertEqual(len(launches), 5)
        self.assertEqual(sum('please continue' in cmd for cmd in launches), 2)
        self.assertEqual(len([c for c in self.archive_calls if c[1] is False]), 5)
        self.assertTrue(any('Preflight thread read failed' in (e.get('issue') or '') for e in returned['events']))
        self.assertEqual(self.desktop.live[0], self.desktop.work)

    def test_observation_failure_before_native_request_is_retryable(self):
        observe = self.desktop.observe
        def observation():
            intent = self.store.load(self.record['id'])['intent']
            if intent == dict(kind='native_archive', index=3, archived=False):
                raise Uncertain('Process inventory changed while reading')
            return observe()
        self.desktop.observe = observation
        record = self.reopen()
        self.assert_three_returned(record)
        self.assertIsNone(record['intent'])
        self.assertEqual(len([c for c in self.archive_calls if c[1] is False]), 3)
        self.desktop.observe = observe
        self.assertEqual(self.reopen()['phase'], 'returned')

    def test_uncertain_submitted_request_stays_pending_and_keeps_original_error(self):
        self.injected_failure = Uncertain('Unarchive acknowledgement lost')
        record = self.reopen()
        self.assert_three_returned(record)
        self.assertEqual(record['intent'], dict(kind='native_archive', index=3, archived=False))
        events = record['events']
        self.injected_failure = None
        # Merely returning from an uncertain call cannot authorize replay.
        for _ in range(2):
            record = self.reopen()
            self.assert_three_returned(record)
            self.assertEqual(record['issue'], 'Unarchive acknowledgement lost')
            self.assertEqual(record['events'], events)
        # Once the native DB confirms success, proceed without a second unarchive.
        tab = self.desktop.source.tabs[3]
        self.archived[tab.session_id] = False
        self.assertEqual(self.reopen()['phase'], 'returned')
        self.assertNotIn((tab.session_id, False), self.archive_calls)

    def test_post_submission_database_failure_is_not_misclassified(self):
        from parking.agents import Codex
        tab = self.desktop.source.tabs[3]
        rpc = Mock()
        rpc.read.return_value = dict(id=tab.session_id, cwd=tab.cwd, turns=[])
        provider = Codex(Mock(), rpc)
        provider.is_archived = Mock(side_effect=[True, OSError('DB readback failed')])
        with self.assertRaises(OSError):
            provider.set_archived(tab, False, Mock())
        rpc.call.assert_called_once()

    def test_provider_preflight_failure_never_submits(self):
        from parking.agents import Codex
        tab = self.desktop.source.tabs[3]
        rpc = Mock()
        rpc.read.side_effect = Uncertain('Read timed out')
        provider = Codex(Mock(), rpc)
        provider.is_archived = Mock(return_value=True)
        with self.assertRaises(NotSubmitted):
            provider.set_archived(tab, False, Mock())
        rpc.call.assert_not_called()
