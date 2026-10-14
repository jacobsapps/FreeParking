"""No native calls: exact launch suggestions need explicit, scoped confirmation."""
from dataclasses import asdict,replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import uuid
import test_core
from parking.confirmation import resume_candidate,prepare,accept,binding,MissingEndpoint,ConfirmationRequired
from parking.model import Observation,Process,ParkingError
from parking.agents import Codex

class ResumeConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sid=str(uuid.uuid4())
        self.process=Process(50,'birth','/dev/test','/bin/codex',1)
        self.session=dict(id='owned',tty='/dev/test',title='Display only')
        self.processes=Mock()
        self.processes.command.return_value='/bin/codex resume '+self.sid
        self.processes.cwd.return_value=str(self.root)
        self.processes.files.return_value=[]
        self.thread=dict(id=self.sid,cwd=str(self.root),name='Exact saved session',source='vscode',status=dict(type='idle'),turns=[])
        self.rpc=Mock();self.rpc.call.return_value=dict(thread=self.thread);self.rpc.read.return_value=self.thread
        self.candidate=resume_candidate(self.process,self.session,self.processes,self.rpc)
        self.raw=({'id':'window','tabs':[dict(sessions=[self.session])]},)
        self.snapshot=Observation(self.raw,(),{'window':'confirm'},{'owned':'confirm'},{},{'owned':self.candidate})
        self.observer=Mock();self.observer.observe.return_value=self.snapshot
        self.provider=Codex(self.processes,self.rpc)
        self.provider.identity=Mock();self.provider.identity.resolve.side_effect=MissingEndpoint('stale')
        self.observer.providers.get.return_value=self.provider
        self.process_map={50:dict(pid=50,started='birth',tty='/dev/test',executable='/bin/codex',ppid=1)}

    def test_stale_endpoint_only_suggests_never_silently_identifies(self):
        with self.assertRaises(ConfirmationRequired) as caught:
            self.provider.identify(self.session,self.process_map)
        self.assertEqual(caught.exception.candidate['session_id'],self.sid)

    def test_picker_last_extra_arguments_and_wrong_executable_are_not_suggestions(self):
        for argv in ('/bin/codex resume --last','/bin/codex resume name',
                     '/bin/codex resume '+self.sid+' prompt','/other/codex resume '+self.sid):
            self.processes.command.return_value=argv
            self.assertIsNone(resume_candidate(self.process,self.session,self.processes,self.rpc))

    def test_native_folder_id_and_root_must_match(self):
        for changes in (dict(id=str(uuid.uuid4())),dict(cwd='/other'),dict(source={'subAgent':{}}),dict(status={'type':'notLoaded'})):
            self.rpc.call.return_value=dict(thread={**self.thread,**changes})
            self.assertIsNone(resume_candidate(self.process,self.session,self.processes,self.rpc))

    def test_confirmation_is_single_use_and_operation_scoped(self):
        challenge=prepare(self.root,self.snapshot)
        self.assertEqual(accept(self.root,challenge['id'],self.observer),None)
        tab=self.provider.identify(self.session,self.process_map)
        self.assertEqual(tab.session_id,self.sid)
        with self.assertRaises(ParkingError):accept(self.root,challenge['id'],self.observer)
        other=Codex(self.processes,self.rpc);other.identity=self.provider.identity
        with self.assertRaises(ConfirmationRequired):other.identify(self.session,self.process_map)

    def test_expired_confirmation_cannot_enable_provider(self):
        challenge=prepare(self.root,self.snapshot)
        with patch('parking.confirmation.time.time',return_value=time.time()+121):
            with self.assertRaises(ParkingError):accept(self.root,challenge['id'],self.observer)
        self.assertFalse(hasattr(self.provider,'confirmed_resumes'))

    def test_process_and_topology_changes_reject_confirmation(self):
        for snapshot in (
            replace(self.snapshot,raw=()),
            replace(self.snapshot,confirmation_candidates={'owned':{**self.candidate,'process':{**asdict(self.process),'started':'new'}}})
        ):
            challenge=prepare(self.root,self.snapshot);self.observer.observe.return_value=snapshot
            with self.assertRaises(ParkingError):accept(self.root,challenge['id'],self.observer)
            self.assertFalse(hasattr(self.provider,'confirmed_resumes'))

    def test_other_tab_problem_cannot_be_confirmed_away(self):
        snap=replace(self.snapshot,raw=({'id':'window','tabs':[dict(sessions=[self.session]),dict(sessions=[dict(id='unknown')])]},),
                     tab_issues={'owned':'confirm','unknown':'unsupported job'})
        self.assertIsNone(prepare(self.root,snap))

    def test_switch_after_confirmation_is_rejected(self):
        self.provider.confirmed_resumes={'owned':binding(self.candidate)}
        self.provider.identity.resolve.side_effect=None;self.provider.identity.resolve.return_value=str(uuid.uuid4())
        with self.assertRaisesRegex(ParkingError,'switched'):
            self.provider.identify(self.session,self.process_map)

    def test_process_reuse_after_confirmation_is_rejected_even_with_matching_endpoint(self):
        self.provider.confirmed_resumes={'owned':binding(self.candidate)}
        self.provider.identity.resolve.side_effect=None;self.provider.identity.resolve.return_value=self.sid
        self.process_map[50]['started']='new'
        with self.assertRaisesRegex(ParkingError,'process changed'):
            self.provider.identify(self.session,self.process_map)

    def test_no_preparation_for_ordinary_windows(self):
        self.assertIsNone(prepare(self.root,replace(self.snapshot,confirmation_candidates={})))

    def test_unknown_or_consumed_confirmation_is_rejected(self):
        with self.assertRaises(ParkingError):accept(self.root,str(uuid.uuid4()),self.observer)

class ConfirmationDispatchTests(unittest.TestCase):
    setUp = test_core.Fixture.setUp
    def test_dispatch_does_not_close_until_exact_confirmation_then_rejects_replay(self):
        from types import SimpleNamespace
        from parking.garage import dispatch
        self.desktop.live=[self.desktop.source]
        base=self.desktop.observe()
        tab=self.desktop.source.tabs[0]
        candidate=dict(terminal_id=tab.terminal_id,process=asdict(tab.process),command='/bin/codex resume '+str(uuid.uuid4()),
                       session_id=str(uuid.uuid4()),cwd=tab.cwd,name='Saved session')
        pending=replace(base,windows=(),issues={'fixture':'Confirm'},
                        tab_issues={tab.terminal_id:'Confirm'},confirmation_candidates={tab.terminal_id:candidate},
                        resolved_tabs={t.terminal_id:t for t in self.desktop.source.tabs[1:]})
        provider=SimpleNamespace(confirmed_resumes={})
        self.desktop.providers=Mock();self.desktop.providers.get.return_value=provider
        original=self.desktop.observe
        self.desktop.observe=lambda: original() if provider.confirmed_resumes else pending
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)):
            scan=dispatch(['scan'])
            self.assertEqual(scan['message'],'')
            self.assertTrue(any(t['needs_confirmation'] for w in scan['windows'] for t in w['tabs']))
            reply=dispatch(['--live','park-all'])
            self.assertTrue(reply['ok']);self.assertIsNotNone(reply['confirmation'])
            self.assertEqual(self.desktop.calls,[]);self.assertEqual(self.store.all(),[])
            ident=reply['confirmation']['id']
            confirmed=dispatch(['--live','park-confirmed',ident])
            self.assertTrue(confirmed['ok'],confirmed.get('error'))
            self.assertEqual(confirmed['cars'][0]['status'],'parked')
            self.assertEqual(len(self.desktop.calls),1)
            replay=dispatch(['--live','park-confirmed',ident])
            self.assertFalse(replay['ok']);self.assertEqual(len(self.desktop.calls),1)

    def test_scan_names_the_problem_tab_instead_of_generic_warning(self):
        from parking.garage import dispatch
        base=self.desktop.observe();tab=self.desktop.source.tabs[0]
        snap=replace(base,windows=(self.desktop.work,),issues={'fixture':'Unknown'},
                     tab_issues={tab.terminal_id:'No saved session yet'})
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)),patch.object(self.desktop,'observe',return_value=snap):
            reply=dispatch(['scan'])
        self.assertIn(tab.title,reply['message'])
        self.assertIn('No saved session yet',reply['message'])
        self.assertNotIn('Some tabs',reply['message'])
