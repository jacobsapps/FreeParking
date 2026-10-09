"""Independent authorization contracts for the disposable-only AX responder."""
from pathlib import Path
import unittest
from unittest.mock import Mock
import test_lab
from parking.confirmation_testing import ConfirmingTerminal
from parking.model import ParkingError, Phase
from parking.service import compose


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        test_lab.LabTests.setUp(self)
        self.fixture = self.lab.create()
        store, observer, _ = compose(scope_path=self.scope.path)
        window = observer.observe().window(self.fixture['window_id'])
        self.record, _ = store.begin(window)
        store.transition(self.record, Phase.CLOSING, {'kind':'close'})
        self.store = store
        self.helper = self.root/'helper'; self.helper.touch()
        self.runner = Mock()
        self.os.processes[71] = dict(pid=71,ppid=1,tty='/dev/??',started='iTerm birth',executable='/Applications/iTerm.app/Contents/MacOS/iTerm2')
        self.driver = ConfirmingTerminal(self.scope,self.os,self.os,'OK',self.helper,self.runner)
        self.request = dict(windowId=window.window_id,sessionIds=[t.terminal_id for t in window.tabs])

    def test_authorizes_only_owned_exact_recoverable_window(self):
        self.assertEqual(self.driver.authorize(self.request),71)
        self.runner.assert_not_called()

    def test_rejects_protected_before_submitting_close(self):
        count = len(self.os.calls)
        for wid in ('work','agent'):
            with self.assertRaises(ParkingError):
                self.driver.call('close',windowId=wid,sessionIds=[wid+'-tab'])
        self.assertEqual(len(self.os.calls),count)
        self.runner.assert_not_called()

    def test_requires_durable_pending_close_not_just_ownership(self):
        self.store.transition(self.record, Phase.SAVED)
        with self.assertRaises(ParkingError):self.driver.authorize(self.request)
        self.runner.assert_not_called()

    def test_refuses_changed_process_birth(self):
        pid=self.record['source']['tabs'][0]['process']['pid']
        self.os.processes[pid]['started']='reused'
        with self.assertRaises(ParkingError):self.driver.authorize(self.request)

    def test_refuses_changed_tab_ids(self):
        with self.assertRaises(ParkingError):
            self.driver.authorize(dict(windowId=self.request['windowId'],sessionIds=['unknown']))

    def test_refuses_absent_iterm_process(self):
        del self.os.processes[71]
        with self.assertRaises(ParkingError):self.driver.authorize(self.request)

    def test_rejects_unknown_button_and_missing_helper(self):
        with self.assertRaises(ParkingError):ConfirmingTerminal(self.scope,self.os,self.os,'Quit',self.helper)
        with self.assertRaises(ParkingError):ConfirmingTerminal(self.scope,self.os,self.os,'OK',self.root/'missing')
