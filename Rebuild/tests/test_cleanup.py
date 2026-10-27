import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from cleanup import Cleanup
from parking.model import ParkingError

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.receipt=self.root/'receipt';self.receipt.write_text('original')
        self.scope=Mock(path=self.receipt,data=dict(owned={'test':['test-tab']}))
        self.terminal=Mock();self.terminal.windows.return_value=[dict(id=w,tabs=[dict(sessions=[dict(id=w+'-tab',tty='/dev/'+w)])]) for w in ('work','test')]
        self.processes=Mock();self.processes.all.return_value={2:dict(pid=2,ppid=0,tty='/dev/test',started='birth',executable='/bin/zsh'),3:dict(pid=3,ppid=0,tty='/dev/??',started='app',executable='/Applications/iTerm.app/Contents/MacOS/iTerm2')}
        with patch('cleanup.Scope',return_value=self.scope):self.cleanup=Cleanup([self.receipt],self.root/'cleanup',self.terminal,self.processes)

    def test_only_original_owned_receipts_authorize_cleanup(self):
        self.assertEqual(set(self.cleanup.plan['targets']),{'test'})
        self.assertEqual(self.cleanup.authorize('test'),3)
        with self.assertRaises(ParkingError):self.cleanup.authorize('work')
        self.terminal.call.assert_not_called()

    def test_changed_work_window_stops_cleanup(self):
        self.terminal.windows.return_value[0]['tabs']=[]
        with self.assertRaises(ParkingError):self.cleanup.authorize('test')

    def test_reordered_surviving_owned_tabs_keep_original_receipts(self):
        self.scope.data['owned']['test']=['test-tab','closed-tab','third-tab']
        tabs=self.terminal.windows.return_value[1]['tabs']
        tabs.insert(0,dict(sessions=[dict(id='third-tab',tty='/dev/test')]))
        original=self.receipt.read_bytes()
        with patch('cleanup.Scope',return_value=self.scope):
            cleanup=Cleanup([self.receipt],self.root/'reordered',self.terminal,self.processes)
        self.assertEqual(cleanup.plan['targets']['test']['ids'],['third-tab','test-tab'])
        self.assertEqual(cleanup.authorize('test'),3)
        self.assertEqual(self.receipt.read_bytes(),original)

    def test_unknown_current_tab_never_becomes_owned_by_cleanup(self):
        self.terminal.windows.return_value[1]['tabs'].append(dict(sessions=[dict(id='unknown',tty='/dev/new')]))
        with patch('cleanup.Scope',return_value=self.scope):
            cleanup=Cleanup([self.receipt],self.root/'unknown',self.terminal,self.processes)
        self.assertEqual(cleanup.plan['targets'],{})

    def test_new_test_process_and_changed_receipt_stop_cleanup(self):
        self.processes.all.return_value[2]['started']='reused'
        with self.assertRaises(ParkingError):self.cleanup.authorize('test')
        self.receipt.write_text('changed')
        with self.assertRaises(ParkingError):self.cleanup.authorize('test')
