"""Real journal + coordinator contracts for interruption and resume-message delivery."""
from dataclasses import replace
import shlex
import unittest
import uuid
import test_core
from parking.agents import command
from parking.model import Window, ParkingError, Uncertain
from parking.settings import Settings, DEFAULTS


class ContinuationWorkflow(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        self.prompts=[];self.stops=[]
        original=self.desktop.source
        self.sid=str(uuid.uuid4())
        first=replace(original.tabs[0],provider='claude',session_id=self.sid,activity='running')
        self.desktop.source=Window(original.window_id,(first,)+original.tabs[1:])
        self.desktop.live[1]=self.desktop.source
        owner=self
        class Agent:
            def launch(self,tab,receipt,prompt=None):return command(tab,receipt,['claude','--resume',tab.session_id],prompt)
            def interrupt(self,tab,authorize=None):
                authorize()
                saved=owner.store.all()[0]
                owner.assertEqual(saved['phase'],'interrupting')
                owner.stops.append(tab.session_id)
                owner.desktop.live[1]=Window('fixture',(replace(tab,activity='idle'),)+owner.desktop.source.tabs[1:])
                return dict(outcome='interrupted',session_id=tab.session_id,provider='claude')
        self.providers.items['claude']=Agent()
        original_call=self.desktop.call
        def call(action,**kwargs):
            if action=='create' and 'claude' in kwargs['command']:
                argv=shlex.split(shlex.split(kwargs['command'])[2].split(' && exec ',1)[1])
                owner.prompts.append(argv[3:] if len(argv)>3 else [])
            result=original_call(action,**kwargs)
            if action=='create':
                w=self.desktop.live[-1]
                tabs=list(w.tabs)
                for i,t in enumerate(tabs):
                    source=self.desktop.source.tabs[i]
                    tabs[i]=replace(t,provider=source.provider,session_id=source.session_id,activity='idle')
                self.desktop.live[-1]=Window(w.window_id,tuple(tabs))
            return result
        self.desktop.call=call

    def park(self):return self.flow.park('fixture',self.desktop.source.fingerprint)

    def test_running_agent_is_stopped_then_gets_one_exact_resume_prompt(self):
        record=self.park();self.assertEqual(record['phase'],'parked')
        record=self.flow.reopen(record['id']);self.assertEqual(record['phase'],'returned')
        self.assertEqual(self.stops,[self.sid]);self.assertEqual(self.prompts,[['please continue']])
        self.flow.reopen(record['id']);self.assertEqual(len(self.prompts),1)
        self.assertEqual(self.desktop.live[0],self.desktop.work)

    def test_manual_resume_is_not_duplicated_or_sent_a_message(self):
        record=self.park()
        tab=replace(self.desktop.source.tabs[0],terminal_id='manually-resumed',activity='idle')
        self.desktop.live.append(Window('manual-window',(tab,)))
        record=self.flow.reopen(record['id'])
        self.assertEqual(self.prompts,[])
        self.assertEqual([a for a,_ in self.desktop.calls],['close'])
        self.assertIn('already open elsewhere',record['issue'])

    def test_shared_conversation_in_another_tab_cannot_be_parked(self):
        tab=replace(self.desktop.source.tabs[0],terminal_id='other-tab')
        self.desktop.live.append(Window('other-window',(tab,)))
        with self.assertRaises(ParkingError):self.park()
        self.assertEqual(self.stops,[]);self.assertEqual(self.desktop.calls,[])

    def test_disabled_setting_resumes_without_message(self):
        record=self.park();Settings(self.store.root).save(dict(DEFAULTS,auto_continue_interrupted=False))
        self.flow.reopen(record['id']);self.assertEqual(self.prompts,[[]])

    def test_idle_agent_is_not_interrupted_or_prompted(self):
        t=self.desktop.source.tabs[0]
        self.desktop.source=Window('fixture',(replace(t,activity='idle'),)+self.desktop.source.tabs[1:])
        self.desktop.live[1]=self.desktop.source
        self.flow.reopen(self.park()['id']);self.assertEqual(self.stops,[]);self.assertEqual(self.prompts,[[]])

    def test_uncertain_interruption_cannot_close_or_send(self):
        def interrupt(*a,**k):raise Uncertain('No interrupt receipt')
        self.providers.items['claude'].interrupt=interrupt
        record=self.park();self.assertEqual(record['phase'],'interrupting')
        self.flow.reopen(record['id']);self.assertEqual(self.desktop.calls,[]);self.assertEqual(self.prompts,[])

    def test_failed_reopen_preflight_does_not_send_message(self):
        record=self.park()
        def launch(*a,**k):raise ParkingError('Saved session is unavailable')
        self.providers.items['claude'].launch=launch
        with self.assertRaises(ParkingError):self.flow.reopen(record['id'])
        self.assertEqual(self.prompts,[])

    def test_cancelled_close_does_not_send_to_still_open_agent(self):
        self.desktop.cancel=True
        record=self.park();self.assertEqual(record['phase'],'saved')
        self.flow.reopen(record['id']);self.assertEqual(self.prompts,[])

    def test_ambiguous_resume_never_sends_continuation_twice(self):
        record=self.park();self.desktop.fail_create=True
        record=self.flow.reopen(record['id'])
        self.assertEqual(record['intent']['continue_message'],'please continue')
        self.flow.reopen(record['id'])
        self.assertEqual(self.prompts,[['please continue']])

class MixedClosureTests(unittest.TestCase):
    def setUp(self):
        ContinuationWorkflow.setUp(self)
        source=self.desktop.source
        self.codex_id=str(uuid.uuid4());self.request_id=str(uuid.uuid4());self.events=[]
        self.desktop.source=Window('fixture',(source.tabs[0],replace(source.tabs[1],provider='codex',session_id=self.codex_id,activity='running',turn_id='turn-exact')))
        self.desktop.live[1]=self.desktop.source
        owner=self
        class Claude:
            def prepare_close_interruption(self,tab):
                return dict(provider='claude',session_id=tab.session_id,request_id=owner.request_id,method='terminal-close')
            def validate_close_interruption(self,tab,checkpoint):
                owner.assertEqual(owner.store.all()[0]['phase'],'closing')
                owner.assertEqual(checkpoint['request_id'],owner.request_id)
                owner.events.append('claude-close-authorized')
            def confirm_close_interruption(self,tab,checkpoint):
                owner.assertNotIn('fixture',[w.window_id for w in owner.desktop.live])
                owner.events.append('claude-close-confirmed')
                return dict(provider='claude',session_id=tab.session_id,outcome='interrupted')
            def launch(self,tab,receipt,prompt=None):return command(tab,receipt,['claude','--resume',tab.session_id],prompt)
        class Codex:
            def interrupt(self,tab,authorize):
                authorize(tab.session_id,tab.turn_id)
                owner.events.append('codex-interrupted')
                live=owner.desktop.live[1]
                owner.desktop.live[1]=Window('fixture',(live.tabs[0],replace(live.tabs[1],activity='idle')))
                return dict(provider='codex',session_id=tab.session_id,outcome='interrupted',turn_id=tab.turn_id)
            def launch(self,tab,receipt,prompt=None):return command(tab,receipt,['codex','resume',tab.session_id],prompt)
        self.providers.items.update(claude=Claude(),codex=Codex())

    def park(self):return self.flow.park('fixture',self.desktop.source.fingerprint)

    def test_mixed_running_window_is_saved_closed_once_and_both_agents_continue(self):
        record=self.park();self.assertEqual(record['phase'],'parked')
        self.assertEqual(self.events,['codex-interrupted','claude-close-authorized','claude-close-confirmed'])
        record=self.flow.reopen(record['id']);self.assertEqual(record['phase'],'returned')
        self.assertEqual(record['interruptions']['0']['outcome'],'interrupted')
        self.assertEqual(record['interruptions']['1']['outcome'],'interrupted')
        self.assertEqual(self.prompts,[['please continue']])
        launches=[args['command'] for action,args in self.desktop.calls if action=='create']
        self.assertEqual(len(launches),2)
        for launch in launches:self.assertIn('please continue',launch)
        self.assertEqual(self.desktop.live[0],self.desktop.work)

    def test_cancelled_mixed_close_never_credits_or_continues_claude(self):
        self.desktop.cancel=True;record=self.park();self.assertEqual(record['phase'],'saved')
        self.assertNotIn('0',record['interruptions']);self.flow.reopen(record['id'])
        self.assertEqual(self.prompts,[])

    def test_pending_process_shutdown_reconciles_without_another_close(self):
        provider=self.providers.items['claude'];original=provider.confirm_close_interruption
        def pending(*args):raise Uncertain('still shutting down')
        provider.confirm_close_interruption=pending
        record=self.park();self.assertEqual(record['phase'],'closing')
        provider.confirm_close_interruption=original
        record=self.flow.reconcile(record['id']);self.assertEqual(record['phase'],'parked')
        self.assertEqual([a for a,_ in self.desktop.calls],['close'])

    def test_changed_claude_request_rejects_close(self):
        def changed(*args):raise ParkingError('different active request')
        self.providers.items['claude'].validate_close_interruption=changed
        record=self.park();self.assertEqual(record['phase'],'saved')
        self.assertEqual(self.desktop.calls,[])

class NativeArchiveTests(MixedClosureTests):
    def setUp(self):
        super().setUp();self.archive_state=False;owner=self
        provider=self.providers.items['codex']
        def is_archived(tab):return owner.archive_state
        def set_archived(tab,desired,authorize):
            authorize(tab.session_id)
            record=owner.store.all()[0]
            owner.assertEqual(record['intent'],dict(kind='native_archive',index=1,archived=desired))
            owner.assertNotIn('fixture',[w.window_id for w in owner.desktop.live])
            owner.archive_state=desired
        provider.is_archived=is_archived;provider.set_archived=set_archived

    def test_native_runtime_is_archived_after_close_and_unarchived_before_resume(self):
        record=self.park();self.assertEqual(record['phase'],'parked');self.assertTrue(self.archive_state)
        record=self.flow.reopen(record['id']);self.assertEqual(record['phase'],'returned');self.assertFalse(self.archive_state)
        self.assertEqual(record['native_archives'],{'1':False})

    def test_native_runtime_is_not_unarchived_if_manually_resumed_elsewhere(self):
        record=self.park()
        tab=replace(self.desktop.source.tabs[1],terminal_id='manual-codex',activity='idle')
        self.desktop.live.append(Window('manual-window',(tab,)))
        record=self.flow.reopen(record['id'])
        self.assertEqual(record['native_archives'],{'1':True})
        self.assertIn('already open elsewhere',record['issue'])

    def test_lost_archive_ack_reconciles_native_state_without_repeating_it(self):
        provider=self.providers.items['codex'];original=provider.set_archived;calls=[]
        def lost(tab,desired,authorize):
            calls.append(desired);original(tab,desired,authorize);raise Uncertain('lost acknowledgement')
        provider.set_archived=lost
        record=self.park();self.assertEqual(record['phase'],'closing')
        record=self.flow.reconcile(record['id']);self.assertEqual(record['phase'],'parked');self.assertEqual(calls,[True])
