"""Boundary tests. Subprocesses/terminal connections are injected, never real."""
from pathlib import Path
import json
import os
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
sys.path.insert(0,str(Path(__file__).parents[1]))
sys.path.insert(0,str(Path(__file__).parents[2]/'Resources'))
from parking.adapters import ITerm, Processes
from parking.model import ParkingError, Process, Tab, Uncertain
from parking.providers import Providers, Shell
from parking.service import compose, dispatch
from parking.verifier import Observer


class BoundaryTests(unittest.TestCase):
    def test_transient_object_read_restarts_the_whole_inventory_only(self):
        runner=Mock(side_effect=[ParkingError("Can't get object. (-1728)"),
                                 ParkingError('FP_READ_RACE: changed'), '[{"id":"settled","tabs":[]}]'])
        with patch('parking.adapters.time.sleep'):
            self.assertEqual(ITerm(runner).windows(),[dict(id='settled',tabs=[])])
        self.assertEqual(runner.call_count,3)
        self.assertTrue(all(json.loads(c.args[0][-1])['action']=='list' for c in runner.call_args_list))

    def test_persistent_layout_churn_is_uncertain_never_empty(self):
        runner=Mock(side_effect=ParkingError("Can't get object. (-1728)"))
        with patch('parking.adapters.time.sleep'), self.assertRaises(Uncertain):
            ITerm(runner).windows()
        self.assertEqual(runner.call_count,4)

    def test_permission_failure_is_not_a_transient_layout_retry(self):
        runner=Mock(side_effect=ParkingError('Not authorized (-1743)'))
        with self.assertRaises(ParkingError): ITerm(runner).windows()
        self.assertEqual(runner.call_count,1)

    def test_object_read_error_after_create_submission_is_never_replayed(self):
        runner=Mock(side_effect=ParkingError("Can't get object. (-1728)"))
        with self.assertRaises(Uncertain): ITerm(runner).call('create',windowId=None,command='fixture')
        self.assertEqual(runner.call_count,1)

    def test_list_failure_is_not_empty_inventory(self):
        adapter=ITerm(runner=Mock(side_effect=PermissionError('Denied')))
        with self.assertRaises(ParkingError):adapter.windows()

    def test_mutation_failure_is_explicitly_ambiguous(self):
        adapter=ITerm(runner=Mock(side_effect=TimeoutError('No receipt')))
        with self.assertRaises(Uncertain):adapter.call('create',windowId=None,command='fixture')

    def test_close_preserves_normal_confirmation_without_timeout(self):
        runner=Mock(return_value='{"acknowledged":true}')
        ITerm(runner).call('close',windowId='fixture',sessionIds=['tab'])
        self.assertIsNone(runner.call_args.kwargs['timeout'])
        self.assertEqual(json.loads(runner.call_args.args[0][-1])['sessionIds'],['tab'])

    def test_unknown_command_does_not_execute(self):
        runner=Mock()
        with self.assertRaises(ParkingError):ITerm(runner).call('quit-all')
        runner.assert_not_called()

    def test_malformed_json_inventory_does_not_mean_empty_desktop(self):
        for raw in ('{}','not json','null'):
            with self.subTest(raw=raw),self.assertRaises(ParkingError):ITerm(Mock(return_value=raw)).windows()

    def test_ps_parser_preserves_birth_and_spaces_in_executable(self):
        rows=' 123 1 ttys004 Fri Oct  9 10:00:00 2026 /path with spaces/zsh\n'
        p=Processes(Mock(return_value=rows)).all()[123]
        self.assertEqual(p['started'],'Fri Oct 9 10:00:00 2026')
        self.assertEqual(p['executable'],'/path with spaces/zsh')
        self.assertEqual(p['tty'],'/dev/ttys004')

    def test_partial_empty_or_denied_process_inventory_fails_closed(self):
        for runner in (Mock(return_value=''),Mock(return_value='broken row'),Mock(side_effect=PermissionError('Denied'))):
            with self.assertRaises((ParkingError,PermissionError)):Processes(runner).all()

    def test_cwd_requires_exactly_one_value(self):
        for value in ('p123\n','n/one\nn/two\n'):
            with self.assertRaises(ParkingError):Processes(Mock(return_value=value)).cwd(123)

    def test_shell_launch_quotes_folder_and_never_uses_title_as_code(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"spaces ' $(do-not-execute)";p.mkdir()
            tab=Tab('t','/dev/test','$(irrelevant title)','shell','',str(p),Process(1,'birth','/dev/test','/bin/zsh'))
            token=str(uuid.uuid4())
            command=Shell(None).launch(tab,token)
            argv=shlex.split(command)
            self.assertEqual(argv[:2],['/bin/zsh','-lc'])
            self.assertIn(shlex.quote(str(p)),argv[2])
            self.assertNotIn('irrelevant',command)
            self.assertIn('SetUserVar=freeParkingReceipt=',command)

    def test_scan_resolves_only_idle_shells_and_preserves_unsupported_raw_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            terminal=Mock();terminal.windows.return_value=[dict(id='shell',tabs=[dict(sessions=[dict(id='s',tty='/dev/shell',title='Busy title does not matter')])]),dict(id='work',tabs=[dict(sessions=[dict(id='c',tty='/dev/agent',title='Ready')])])]
            p=Mock();p.all.return_value={1:dict(pid=1,ppid=0,tty='/dev/shell',started='one',executable='/bin/zsh'),2:dict(pid=2,ppid=0,tty='/dev/agent',started='two',executable='/bin/codex')};p.cwd.return_value=folder
            snapshot=Observer(terminal,p,Providers(Shell(p))).observe()
            self.assertEqual([w.window_id for w in snapshot.windows],['shell'])
            self.assertIn('work',snapshot.issues)
            self.assertEqual(snapshot.terminal_ids,{'s','c'})
            terminal.call.assert_not_called()

    def test_list_is_disk_only_even_without_terminal_permissions(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ITerm,'windows',side_effect=AssertionError('Must not inspect')),patch.object(Processes,'all',side_effect=AssertionError('Must not inspect')):
            result=dispatch(['list'],root=Path(folder))
        self.assertTrue(result['ok']);self.assertEqual(result['records'],[])

    def test_unscoped_mutations_rejected_before_any_inspection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ITerm,'windows',side_effect=AssertionError('Must not inspect')),patch.object(Processes,'all',side_effect=AssertionError('Must not inspect')):
            result=dispatch(['park','working','fingerprint'],root=Path(folder))
        self.assertFalse(result['ok']);self.assertIn('gated off',result['error'])

    def test_explicit_live_capability_only_dispatches_selected_window(self):
        with tempfile.TemporaryDirectory() as folder,patch('parking.service.compose') as composer:
            store,observer,flow=Mock(),Mock(),Mock()
            store.root=Path(folder);store.all.return_value=[]
            flow.park.return_value={'id':'owned-record','phase':'parked'}
            composer.return_value=store,observer,flow
            result=dispatch(['--live','park','chosen-window','captured-fingerprint'],root=Path(folder))
            self.assertTrue(result['ok']);self.assertTrue(result['can_mutate'])
            self.assertFalse(result['test_mode'])
            flow.park.assert_called_once_with('chosen-window','captured-fingerprint')
            observer.observe.assert_not_called()
            self.assertTrue(composer.call_args.kwargs['live'])

    def test_process_changes_during_observation_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            terminal=Mock();terminal.windows.return_value=[dict(id='w',tabs=[dict(sessions=[dict(id='t',tty='/dev/test',title='test')])])]
            p=Mock();p.cwd.return_value=folder;p.all.side_effect=[{1:dict(pid=1,ppid=0,tty='/dev/test',started='one',executable='/bin/zsh')},{}]
            snapshot=Observer(terminal,p,Providers(Shell(p))).observe()
            self.assertIsNone(snapshot.window('w'))
            self.assertNotIn('t',snapshot.resolved_tabs)
            self.assertIn('changed',snapshot.tab_issues['t'])
            self.assertIn('codex',snapshot.unresolved_providers['t'])

    def test_plain_keep_awake_tab_preserves_the_exact_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Mock();p.cwd.return_value=folder;p.command.return_value='caffeinate';p.executable.return_value='/usr/bin/caffeinate'
            shell=dict(pid=1,ppid=0,tty='/dev/test',started='shell',executable='-zsh')
            helper=dict(pid=2,ppid=1,tty='/dev/test',started='helper',executable='caffeinate')
            provider=Shell(p)
            tab=provider.identify(dict(id='t',tty='/dev/test'),{1:shell,2:helper})
            self.assertEqual(tab.shell_command,'caffeinate');self.assertEqual(tab.process.pid,2)
            launch=provider.launch(tab,str(uuid.uuid4()))
            self.assertIn('exec /usr/bin/caffeinate',launch)
            restored=provider.identify(dict(id='new',tty='/dev/test'),{2:helper})
            self.assertEqual(restored.shell_command,'caffeinate')
            p.command.return_value='caffeinate /bin/unsafe'
            with self.assertRaises(ParkingError):provider.identify(dict(id='t',tty='/dev/test'),{1:shell,2:helper})

    def test_keep_awake_helper_does_not_admit_unrelated_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Mock();p.cwd.return_value=folder;p.command.return_value='caffeinate';p.executable.return_value='/usr/bin/caffeinate'
            helper=dict(pid=2,ppid=1,tty='/dev/test',started='helper',executable='caffeinate')
            unknown=dict(pid=3,ppid=1,tty='/dev/test',started='other',executable='unknown')
            with self.assertRaises(ParkingError):Shell(p).identify(dict(id='t',tty='/dev/test'),{2:helper,3:unknown})
