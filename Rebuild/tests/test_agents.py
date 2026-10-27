"""Provider/continuation contracts. No live agent or user terminal is touched."""
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
sys.path.insert(0,str(Path(__file__).parents[1]))
from parking.agents import Claude, Codex, command
from parking.model import ParkingError, Process, Tab, Uncertain
from parking.settings import Settings, DEFAULTS, continuation_message
from parking.service import dispatch


class AgentContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sid=str(uuid.uuid4())
        self.tab=Tab('tab','/dev/test','Not an identity','claude',self.sid,str(self.root),Process(31,'birth','/dev/test','/bin/claude'), 'running')
        self.receipt=dict(outcome='interrupted',session_id=self.sid,provider='claude')

    def test_settings_round_trip_and_private_permissions(self):
        settings=Settings(self.root);self.assertEqual(settings.read(),DEFAULTS)
        value=dict(auto_continue_interrupted=False,continue_message='Resume carefully')
        self.assertEqual(settings.save(value),value)
        self.assertEqual(settings.path.stat().st_mode&0o777,0o600)

    def test_malformed_settings_are_rejected_not_silently_enabled(self):
        for value in ({},dict(auto_continue_interrupted='yes',continue_message='x'),dict(auto_continue_interrupted=True,continue_message='')):
            with self.assertRaises(ParkingError):Settings(self.root).save(value)

    def test_only_confirmed_interruption_of_same_conversation_continues(self):
        self.assertEqual(continuation_message(self.tab,self.receipt,DEFAULTS),'please continue')
        for receipt in (None,{},dict(self.receipt,outcome='requested'),dict(self.receipt,outcome='not_interrupted'),dict(self.receipt,session_id=str(uuid.uuid4())),dict(self.receipt,provider='codex')):
            self.assertIsNone(continuation_message(self.tab,receipt,DEFAULTS))
        self.assertIsNone(continuation_message(self.tab,self.receipt,dict(DEFAULTS,auto_continue_interrupted=False)))

    def test_message_is_single_quoted_argv_not_terminal_typing_or_shell_code(self):
        prompt="please continue; $(touch /DO_NOT_CREATE) ' quoted"
        launch=command(self.tab,str(uuid.uuid4()),['claude','--resume',self.sid],prompt)
        shell=shlex.split(launch)[2];argv=shlex.split(shell.split(' && exec ',1)[1])
        self.assertEqual(argv,['claude','--resume',self.sid,prompt])
        self.assertNotIn('--dangerously',launch)

    def native_claude(self):
        root=self.root/'claude';(root/'sessions').mkdir(parents=True);(root/'projects/project').mkdir(parents=True)
        record=dict(pid=31,pidDomain='darwin',kind='interactive',entrypoint='cli',procStart='UTC birth',sessionId=self.sid,cwd=str(self.root),status='busy')
        (root/'sessions/31.json').write_text(json.dumps(record))
        (root/'projects/project'/(self.sid+'.jsonl')).write_text(json.dumps(dict(sessionId=self.sid,cwd=str(self.root),type='system'))+'\n')
        processes=Mock();processes.all.return_value={31:dict(started='birth')};processes.started_utc.return_value='UTC birth';processes.cwd.return_value=str(self.root)
        return Claude(processes,root),record

    def test_claude_native_registry_confirms_exact_process_and_transcript(self):
        provider,_=self.native_claude()
        tab=provider.identify(dict(id='t',tty='/dev/test',title='anything'),{31:dict(pid=31,ppid=1,tty='/dev/test',executable='/bin/claude',started='birth')})
        self.assertEqual((tab.session_id,tab.activity),(self.sid,'running'))
        self.assertIn('--resume',provider.launch(tab,str(uuid.uuid4()),'please continue'))

    def test_claude_resume_uses_login_shell_lookup_across_install_locations(self):
        provider,_=self.native_claude()
        launch=shlex.split(provider.launch(self.tab,str(uuid.uuid4())))
        self.assertEqual(launch[:2],['/bin/zsh','-lc'])
        self.assertEqual(shlex.split(launch[2].split(' && exec ',1)[1]),
                         ['claude','--permission-mode','plan','--resume',self.sid])
        provider.executable='/custom path/claude'
        launch=shlex.split(provider.launch(self.tab,str(uuid.uuid4())))
        self.assertEqual(shlex.split(launch[2].split(' && exec ',1)[1])[0],'/custom path/claude')

    def test_claude_stale_registry_never_falls_back_to_title(self):
        provider,record=self.native_claude();record['procStart']='wrong'
        (provider.root/'sessions/31.json').write_text(json.dumps(record))
        with self.assertRaises(ParkingError):provider.registry(self.tab.process)

    def test_claude_interruption_needs_permit_and_observed_idle(self):
        provider,_=self.native_claude();provider.activity=Mock(side_effect=['running','idle'])
        provider.interruption_marker=Mock(return_value='native-cancel-event')
        permit=Mock()
        result=provider.interrupt(self.tab,authorize=permit)
        self.assertEqual(result,dict(self.receipt,event_id='native-cancel-event'));permit.assert_called_once()
        provider.processes.send_signal.assert_called_once()

    def test_claude_natural_completion_during_signal_does_not_auto_continue(self):
        provider,_=self.native_claude();provider.activity=Mock(side_effect=['running','idle'])
        self.assertEqual(provider.interrupt(self.tab,authorize=Mock())['outcome'],'not_interrupted')

    def test_claude_idle_is_not_marked_interrupted(self):
        provider,_=self.native_claude();provider.activity=Mock(return_value='idle')
        self.assertEqual(provider.interrupt(self.tab)['outcome'],'not_interrupted')
        provider.processes.send_signal.assert_not_called()

    def test_codex_interrupts_only_exact_turn_without_signalling_server(self):
        rpc=Mock();processes=Mock();provider=Codex(processes,rpc)
        tab=Tab('t','/dev/test','title','codex',self.sid,str(self.root),self.tab.process,'running','turn-1')
        rpc.read.side_effect=[dict(turns=[dict(id='turn-1',status='inProgress')]),dict(turns=[dict(id='turn-1',status='interrupted')])]
        permit=Mock();outcome=provider.interrupt(tab,authorize=permit)
        self.assertEqual(outcome['outcome'],'interrupted')
        rpc.interrupt.assert_called_once_with(self.sid,'turn-1',authorize=permit)
        processes.send_signal.assert_not_called()

    def test_codex_finished_turn_never_gets_auto_continue_credit(self):
        rpc=Mock();provider=Codex(Mock(),rpc)
        tab=Tab('t','/dev/test','title','codex',self.sid,str(self.root),self.tab.process,'running','turn-1')
        rpc.read.side_effect=[dict(turns=[dict(id='turn-1',status='inProgress')]),dict(turns=[dict(id='turn-1',status='completed')])]
        self.assertEqual(provider.interrupt(tab,authorize=Mock())['outcome'],'not_interrupted')

    def test_codex_new_turn_is_not_interrupted_using_stale_snapshot(self):
        rpc=Mock();rpc.read.return_value=dict(turns=[dict(id='new-turn',status='inProgress')])
        provider=Codex(Mock(),rpc)
        with self.assertRaises(ParkingError):provider.interrupt(self.tab,authorize=Mock())
        rpc.interrupt.assert_not_called()

    def test_settings_transport_does_not_inspect_or_mutate_iterm(self):
        with patch('parking.adapters.ITerm.windows',side_effect=AssertionError('Do not inspect')):
            response=dispatch(['settings',json.dumps(dict(DEFAULTS,auto_continue_interrupted=False))],root=self.root)
        self.assertFalse(response['settings']['auto_continue_interrupted'])
        self.assertTrue(response['ok'])

    def test_claude_process_exit_with_same_unfinished_native_request_confirms_stop(self):
        provider,_=self.native_claude()
        provider.pending_request=Mock(return_value='request-exact')
        provider.processes.all.return_value={}
        outcome=provider.interrupt(self.tab,authorize=Mock())
        self.assertEqual(outcome['outcome'],'interrupted')
        self.assertEqual(outcome['request_id'],'request-exact')

    def test_claude_exit_after_natural_completion_does_not_continue(self):
        provider,_=self.native_claude()
        provider.pending_request=Mock(side_effect=['request-exact',None])
        provider.processes.all.return_value={}
        self.assertEqual(provider.interrupt(self.tab,authorize=Mock())['outcome'],'not_interrupted')

    def test_claude_exit_with_different_request_is_uncertain(self):
        provider,_=self.native_claude()
        provider.pending_request=Mock(side_effect=['request-exact','changed'])
        provider.processes.all.return_value={}
        with self.assertRaises(Uncertain):provider.interrupt(self.tab,authorize=Mock())

    def test_native_claude_pending_request_ignores_tool_result_but_ends_on_final(self):
        provider,_=self.native_claude()
        path=provider.saved(self.sid,self.tab.cwd)
        def append(kind,**fields):
            with path.open('a') as stream:
                stream.write(json.dumps(dict(type=kind,sessionId=self.sid,**fields))+'\n')
        append('user',uuid='request-id',message=dict(content='do work'))
        append('assistant',message=dict(content=[],stop_reason='tool_use'))
        append('user',uuid='tool-id',message=dict(content=[dict(type='tool_result')]))
        self.assertEqual(provider.pending_request(self.tab,path),'request-id')
        append('assistant',message=dict(content=[dict(type='text', text='done')],stop_reason='end_turn'))
        self.assertIsNone(provider.pending_request(self.tab,path))

    def test_claude_close_checkpoint_requires_a_persisted_request(self):
        provider,_=self.native_claude()
        self.assertIsNone(provider.prepare_close_interruption(self.tab))

    def test_claude_close_checkpoint_checks_current_request_and_process_exit(self):
        provider,_=self.native_claude();request_id=str(uuid.uuid4())
        provider.request_state=Mock(return_value=dict(request_id=request_id, completed=False))
        checkpoint=provider.prepare_close_interruption(self.tab)
        provider.validate_close_interruption(self.tab,checkpoint)
        with self.assertRaises(Uncertain):provider.confirm_close_interruption(self.tab,checkpoint)
        provider.processes.all.return_value={}
        self.assertEqual(provider.confirm_close_interruption(self.tab,checkpoint)['outcome'],'interrupted')
        provider.request_state.return_value=dict(request_id=request_id, completed=True)
        self.assertEqual(provider.confirm_close_interruption(self.tab,checkpoint)['outcome'],'unconfirmed')

    def test_codex_archive_never_changes_a_still_running_thread(self):
        rpc=Mock();provider=Codex(Mock(),rpc)
        from dataclasses import replace
        tab=replace(self.tab,provider='codex')
        provider.is_archived=Mock(return_value=False)
        rpc.read.return_value=dict(id=tab.session_id,cwd=tab.cwd,turns=[dict(id='turn',status='inProgress')])
        with self.assertRaises(ParkingError):provider.set_archived(tab,True,Mock())
        rpc.call.assert_not_called()

    def test_closed_client_shutdown_only_signals_the_saved_birth(self):
        from parking.agents import finish_client_close
        provider,_=self.native_claude();p=provider.processes
        p.all.return_value={31:dict(started='birth',tty='/dev/test',executable='/bin/claude')}
        def stopped(*args):p.all.return_value={}
        p.send_signal.side_effect=stopped
        permit=Mock();finish_client_close(self.tab,p,permit)
        permit.assert_called_once();p.send_signal.assert_called_once()

    def test_reused_client_pid_is_not_signalled(self):
        from parking.agents import finish_client_close
        provider,_=self.native_claude();p=provider.processes
        p.all.return_value={31:dict(started='different birth')}
        finish_client_close(self.tab,p,Mock());p.send_signal.assert_not_called()

    def test_changed_executable_cannot_receive_shutdown(self):
        from parking.agents import finish_client_close
        provider,_=self.native_claude();p=provider.processes
        p.all.return_value={31:dict(started='birth',tty='/dev/test',executable='/different/program')}
        with self.assertRaises(ParkingError):finish_client_close(self.tab,p,Mock())
        p.send_signal.assert_not_called()
