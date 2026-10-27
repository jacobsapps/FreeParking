"""Full composition tests: real recovery + real shim + real observer + fake OS.

These intentionally are NOT labelled live iTerm/Claude/Codex verification.
"""
import base64
import copy
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).parents[1]))
sys.path.insert(0,str(Path(__file__).parents[2]/'Resources'))
from test_safety import Scope, ScopedTerminal, SafetyError
from parking.lab import Lab
from parking.model import ParkingError
from parking.service import compose
from parking.testing import FixtureTerminal


class OSBoundary:
    def __init__(self, root):
        self.root=root
        self.raw=[dict(id='work',tabs=[dict(sessions=[dict(id='work-tab',tty='/dev/work',title='Work',receipt='')])]),dict(id='agent',tabs=[dict(sessions=[dict(id='agent-tab',tty='/dev/agent',title='Agent',receipt='')])])]
        self.processes={os.getpid():dict(pid=os.getpid(),ppid=1,tty='/dev/agent',started='runner',executable='/bin/codex'), 50:dict(pid=50,ppid=1,tty='/dev/work',started='work',executable='/bin/codex')}
        self.folders={}
        self.count=0
        self.calls=[]
        self.cancel=False

    def windows(self):return copy.deepcopy(self.raw)
    def all(self):return copy.deepcopy(self.processes)
    def cwd(self,pid):return self.folders[pid]
    def call(self,action,**kwargs):
        if kwargs.get('windowId') in ('work','agent'):raise AssertionError('Protected target reached OS boundary')
        self.calls.append((action,kwargs))
        if action=='create':
            self.count+=1
            wid=kwargs['windowId'] or 'fixture-'+str(self.count)
            sid='session-'+str(self.count)
            tty='/dev/fixture'+str(self.count)
            pid=100000+self.count
            body=shlex.split(kwargs['command'])[2]
            folder=shlex.split(body)[1]
            token=base64.b64decode(re.search(r'freeParkingReceipt=([A-Za-z0-9+/=]+)',body).group(1)).decode()
            self.processes[pid]=dict(pid=pid,ppid=1,tty=tty,started='fixture-birth'+str(self.count),executable='/bin/zsh')
            self.folders[pid]=folder
            tab=dict(sessions=[dict(id=sid,tty=tty,title='Test',receipt=token)])
            window=next((w for w in self.raw if w['id']==wid),None)
            if window:
                assert kwargs['sessionIds']==[s['id'] for t in window['tabs'] for s in t['sessions']]
                window['tabs'].append(tab)
            else:self.raw.append(dict(id=wid,tabs=[tab]))
            return dict(windowId=wid,sessionId=sid)
        if action=='close':
            window=next(w for w in self.raw if w['id']==kwargs['windowId'])
            assert kwargs['sessionIds']==[s['id'] for t in window['tabs'] for s in t['sessions']]
            if not self.cancel:
                ttys={s['tty'] for t in window['tabs'] for s in t['sessions']}
                self.raw.remove(window)
                self.processes={pid:p for pid,p in self.processes.items() if p['tty'] not in ttys}
            return dict(acknowledged=True)
        raise AssertionError('Unsupported boundary action '+action)


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.os=OSBoundary(self.root)
        self.baseline=self.os.windows()
        self.scope=Scope.capture(self.root/'scope.json',self.os,self.os,['fixture-init-only'])
        self.a=patch('parking.service.ITerm',return_value=self.os);self.a.start();self.addCleanup(self.a.stop)
        self.b=patch('parking.service.Processes',return_value=self.os);self.b.start();self.addCleanup(self.b.stop)
        self.lab=Lab(self.scope,ScopedTerminal(self.scope,self.os),lambda:compose(scope_path=self.scope.path))

    @staticmethod
    def wait(predicate):
        result=predicate()
        if not result:raise AssertionError('Expected immediate fake OS observation')
        return result

    def test_select_one_of_two_fixtures_preserves_work_agent_and_unselected_fixture(self):
        first=self.lab.create(tabs=3)
        second=self.lab.create(tabs=2)
        other=next(w for w in self.os.windows() if w['id']==second['window_id'])
        result=self.lab.round_trip(first['window_id'],self.wait)
        self.assertNotEqual(result['window_id'],first['window_id'])
        self.assertEqual(result['state'],'open')
        self.assertEqual(self.os.windows()[:2],self.baseline)
        self.assertIn(other,self.os.windows())
        self.assertEqual(len(result['session_ids']),3)
        self.assertEqual(len(set(first['folders']+second['folders'])),5)
        self.assertTrue(all(Path(p).is_dir() for p in first['folders']+second['folders']))
        self.assertEqual(len([c for c in self.os.calls if c[0]=='close']),1)

    def test_lab_does_not_add_reconciliation_after_product_returns_pending(self):
        fixture=self.lab.create(tabs=2)
        from parking.operations import Operations, Outcome
        checks=[]
        def pending_operation(runner, record_id):
            checks.append(record_id)
            record=runner.workflow.store.load(record_id)
            return Outcome(record, 'checking', 'Still unconfirmed')
        with patch('parking.lab.Operations.reopen', new=pending_operation), \
             patch('parking.workflow.Workflow.reconcile', side_effect=AssertionError('Test-only completion loop')):
            with self.assertRaisesRegex(ParkingError, 'Still unconfirmed'):
                self.lab.round_trip(fixture['window_id'],self.wait)
        self.assertEqual(len(checks),1)
        self.assertEqual([c[0] for c in self.os.calls],['create','create','close'])

    def test_batched_fixture_setup_settles_once_after_all_exact_create_receipts(self):
        composer=self.lab.composer
        checks=[]
        def composed():
            store,observer,flow=composer()
            original=observer.observe_until
            def settle(ready):
                checks.append(len([c for c in self.os.calls if c[0]=='create']))
                return original(ready)
            observer.observe_until=settle
            return store,observer,flow
        self.lab.composer=composed
        fixture=self.lab.create(tabs=5,settle_each=False)
        self.assertEqual(checks,[5])
        self.assertEqual(fixture['state'],'open')
        self.assertEqual(len(fixture['session_ids']),5)
        self.assertEqual(self.os.windows()[:2],self.baseline)

    def test_batched_setup_never_marks_mismatched_final_receipts_open(self):
        composer=self.lab.composer
        def composed():
            store,observer,flow=composer()
            # Return an incomplete final read without waiting; it must not make
            # the existing raw create acknowledgements a successful fixture.
            observer.observe_until=lambda ready: type('Missing',(),{'window':lambda self,wid:None})()
            return store,observer,flow
        self.lab.composer=composed
        with self.assertRaisesRegex(ParkingError,'No parking attempted'):
            self.lab.create(tabs=5,settle_each=False)
        fixture=next(iter(self.scope.data['fixtures'].values()))
        self.assertEqual(fixture['state'],'creating')
        self.assertEqual(len(fixture['session_ids']),5)
        self.assertEqual([c[0] for c in self.os.calls],['create']*5)
        self.assertEqual(self.os.windows()[:2],self.baseline)

    def test_batched_mixed_setup_validates_each_exact_agent_identity(self):
        from dataclasses import replace
        import uuid
        from parking.agents import command
        self.lab.seeder=Mock()
        self.lab.seeder.seed.side_effect=lambda provider,folder,busy: dict(provider=provider,cwd=folder,session_id=str(uuid.uuid4()))
        composer=self.lab.composer
        expected=[]
        def composed():
            store,observer,flow=composer()
            original=observer.observe
            def settle(ready):
                snapshot=original()
                window=snapshot.windows[-1]
                agents=self.scope.data['fixture_agents']
                tabs=tuple(replace(t,provider=a['provider'],session_id=a['session_id']) for t,a in zip(window.tabs,agents))
                observed=replace(snapshot,windows=(replace(window,tabs=tabs),))
                expected.append(ready(observed))
                # Same valid terminal receipts/folders but wrong exact agent ID
                # must fail readiness even though the window resolves completely.
                wrong=replace(tabs[-1],session_id=str(uuid.uuid4()))
                bad=replace(observed,windows=(replace(window,tabs=tabs[:-1]+(wrong,)),))
                self.assertFalse(ready(bad))
                return observed
            observer.observe_until=settle
            return store,observer,flow
        self.lab.composer=composed
        def launch(adapter,tab,receipt,prompt=None):
            return command(tab,receipt,[tab.provider,'resume',tab.session_id],prompt)
        with patch('parking.agents.Claude.launch',new=launch),patch('parking.agents.Codex.launch',new=launch):
            fixture=self.lab.create(tabs=5,provider='mixed',settle_each=False)
        self.assertEqual(expected,[True])
        self.assertEqual(fixture['state'],'open')
        self.assertEqual([a['provider'] for a in fixture['agents']],['claude','codex','claude','codex','claude'])
        self.assertEqual(len([c for c in self.os.calls if c[0]=='create']),5)

    def test_batched_setup_still_refuses_protected_window_changes_between_creates(self):
        call=self.os.call
        def changing(action,**kwargs):
            result=call(action,**kwargs)
            if action=='create':
                self.os.raw[0]['tabs'].append(dict(sessions=[dict(id='user-added',tty='/dev/new',title='Work')]))
            return result
        self.os.call=changing
        with self.assertRaises(SafetyError):self.lab.create(tabs=5,settle_each=False)
        self.assertEqual(len([c for c in self.os.calls if c[0]=='create']),1)
        self.assertFalse(any(c[0]=='close' for c in self.os.calls))

    def test_repeated_complete_cycles_use_updated_selected_window(self):
        fixture=self.lab.create(tabs=2)
        initial=fixture['window_id']
        for _ in range(10):
            fixture=self.lab.round_trip(fixture['window_id'],self.wait)
            self.assertEqual(self.os.windows()[:2],self.baseline)
        self.assertEqual(len([c for c in self.os.calls if c[0]=='close']),10)
        with self.assertRaises(ParkingError):self.lab.selected(initial)

    def test_work_agent_and_arbitrary_ids_never_reach_os_mutation(self):
        self.lab.create()
        calls=len(self.os.calls)
        for wid in ('work','agent','unknown'):
            with self.assertRaises(ParkingError):self.lab.round_trip(wid,self.wait)
        self.assertEqual(len(self.os.calls),calls)

    def test_scope_cannot_import_existing_agent_sessions(self):
        for provider in ('codex','claude'):
            with self.assertRaises(ParkingError):self.lab.create(provider=provider)
        self.assertEqual(self.os.calls,[])
        self.assertFalse((self.root/'fixtures').exists())

    def test_fixture_folder_symlink_outside_scope_is_rejected(self):
        self.lab.create()
        escape=self.root/'escape';escape.symlink_to(self.root.parent,target_is_directory=True)
        self.scope.data['fixture_folders']=[str(escape)]
        calls=len(self.os.calls)
        terminal=FixtureTerminal(self.scope,ScopedTerminal(self.scope,self.os))
        with self.assertRaises(ParkingError):terminal.call('create',windowId=None,command=self.os.calls[0][1]['command'])
        self.assertEqual(len(self.os.calls),calls)

    def test_added_unregistered_tab_blocks_selected_fixture(self):
        fixture=self.lab.create()
        self.os.raw[-1]['tabs'].append(dict(sessions=[dict(id='unknown',tty='/dev/unknown',title='unexpected')]))
        calls=len(self.os.calls)
        with self.assertRaises(SafetyError):self.lab.round_trip(fixture['window_id'],self.wait)
        self.assertEqual(len(self.os.calls),calls)

    def test_cancel_stops_cycle_and_does_not_open_duplicates(self):
        fixture=self.lab.create()
        self.os.cancel=True
        with self.assertRaises(ParkingError):self.lab.round_trip(fixture['window_id'],self.wait)
        self.assertEqual([c[0] for c in self.os.calls],['create','close'])
        self.assertEqual(self.lab.selected(fixture['window_id'])['state'],'needs_check')
        with self.assertRaises(ParkingError):self.lab.round_trip(fixture['window_id'],self.wait)
        self.assertEqual(len(self.os.calls),2)

    def test_read_only_listing_contains_no_working_windows(self):
        self.lab.create();self.lab.create()
        self.assertEqual(len(self.lab.list()),2)
        self.assertFalse({'work','agent'} & {f['window_id'] for f in self.lab.list()})

    def test_acknowledged_creation_can_be_checked_without_creating_again(self):
        fixture=self.lab.create(tabs=2)
        self.scope.data['fixtures'][fixture['id']]['state']='creating'
        self.scope.save();calls=list(self.os.calls)
        checked=self.lab.check(fixture['window_id'])
        self.assertEqual(checked['state'],'open')
        self.assertEqual(self.os.calls,calls)
        self.assertEqual(self.os.windows()[:2],self.baseline)

    def test_check_rejects_changed_creation_receipts(self):
        fixture=self.lab.create(tabs=2)
        stored=self.scope.data['fixtures'][fixture['id']]
        stored['state']='creating';stored['session_ids']=list(reversed(stored['session_ids']))
        self.scope.save();calls=list(self.os.calls)
        with self.assertRaises(ParkingError):self.lab.check(fixture['window_id'])
        self.assertEqual(self.os.calls,calls)
        self.assertEqual(self.scope.data['fixtures'][fixture['id']]['state'],'creating')

    def test_native_mutation_requires_registered_owned_conversation(self):
        from dataclasses import replace
        import uuid
        from parking.model import Window
        fixture=self.lab.create()
        _,observer,_=compose(scope_path=self.scope.path)
        window=observer.observe().window(fixture['window_id'])
        tab=replace(window.tabs[0],provider='codex',session_id=str(uuid.uuid4()))
        source=Window(window.window_id,(tab,))
        terminal=FixtureTerminal(self.scope,ScopedTerminal(self.scope,self.os))
        with self.assertRaises(ParkingError):terminal.authorize_provider(source)
        self.scope.data['fixture_agents']=[dict(provider='codex',session_id=tab.session_id,cwd=tab.cwd)]
        self.scope.save();terminal.authorize_provider(source)
        with self.assertRaises(ParkingError):terminal.authorize_provider(Window('work',(tab,)))
