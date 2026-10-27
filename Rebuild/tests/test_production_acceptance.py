"""Acceptance driver protocol only; never launch fixtures or native services."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from prove_garage import complete_request, BundleBackend
from unittest.mock import Mock, patch
from parking.model import ParkingError


class AcceptanceProtocol(unittest.TestCase):
    def test_pending_launch_is_checked_without_replaying_restore(self):
        calls=[]
        def invoke(args):
            calls.append(args)
            return dict(ok=True,operation_id='owned',operation_status='completed' if len(calls)==3 else 'checking')
        result=complete_request(invoke,['restore','owned'])
        self.assertEqual(result['operation_status'],'completed')
        self.assertEqual(calls,[['restore','owned'],['check','owned'],['check','owned']])

    def test_unconfirmed_bound_stays_pending_not_test_only_success(self):
        calls=[]
        def invoke(args):
            calls.append(args)
            return dict(ok=True,operation_id='owned',operation_status='checking')
        result=complete_request(invoke,['park-all'])
        self.assertEqual(result['operation_status'],'checking')
        self.assertEqual(calls,[['park-all'],['check','owned'],['check','owned']])

    def test_definite_failure_is_never_polled_or_retried(self):
        calls=[]
        def invoke(args):
            calls.append(args)
            return dict(ok=False,operation_id='owned',operation_status='needs_action')
        self.assertFalse(complete_request(invoke,['restore','owned'])['ok'])
        self.assertEqual(calls,[['restore','owned']])


class ConfirmationRace(unittest.TestCase):
    def test_completed_close_cancels_test_click_but_does_not_authorize_another_target(self):
        scope=Mock();scope.path=Path('/fixture-only/scope.json')
        runner=BundleBackend('/fixture-only/backend.py',scope,'/fixture-only/helper')
        driver=Mock();driver.authorize.side_effect=ParkingError('Original window disappeared')
        terminal=Mock();terminal.windows.return_value=[dict(id='protected',tabs=[dict(sessions=[dict(id='protected-tab')])])]
        with patch('prove_garage.Scope') as fresh:
            self.assertIsNone(runner.authorize_pending(driver,terminal,dict(windowId='owned',sessionIds=['owned-tab'])))
            fresh.return_value.audit.assert_called_once_with(terminal.windows.return_value)
        driver.call.assert_not_called()

    def test_partial_source_or_failed_protection_audit_is_never_ignored(self):
        scope=Mock();scope.path=Path('/fixture-only/scope.json')
        runner=BundleBackend('/fixture-only/backend.py',scope,'/fixture-only/helper')
        driver=Mock();driver.authorize.side_effect=ParkingError('Source changed')
        terminal=Mock();terminal.windows.return_value=[dict(id='owned',tabs=[dict(sessions=[dict(id='owned-tab')])])]
        with patch('prove_garage.Scope'):
            with self.assertRaisesRegex(ParkingError,'Source changed'):
                runner.authorize_pending(driver,terminal,dict(windowId='owned',sessionIds=['owned-tab','other-owned-tab']))
        terminal.windows.return_value=[]
        with patch('prove_garage.Scope') as fresh:
            fresh.return_value.audit.side_effect=ParkingError('Protected baseline changed')
            with self.assertRaisesRegex(ParkingError,'Protected baseline changed'):
                runner.authorize_pending(driver,terminal,dict(windowId='owned',sessionIds=['owned-tab']))
