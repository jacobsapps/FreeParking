"""Component contracts against real SQLite and a deterministic terminal boundary."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.model import Observation, ParkingError, Phase, Process, Tab, Uncertain, Window
from parking.providers import Providers, Shell
from parking.safety import Gate
from parking.store import Store
from parking.verifier import Verifier
from parking.workflow import Workflow


class Desktop:
    """Intentionally adversarial fake; persistence is always the real Store."""
    def __init__(self, folder):
        self.folder = str(folder)
        self.work = Window('working', (Tab('work-tab','/dev/work','Do not touch','shell','',self.folder,Process(99,'work birth','/dev/work','/bin/zsh')),))
        self.source = Window('fixture', tuple(Tab('test-'+str(i), '/dev/test'+str(i), 'Fixture '+str(i), 'shell','', self.folder, Process(100+i,'birth','/dev/test'+str(i),'/bin/zsh')) for i in range(3)))
        self.live = [self.work, self.source]
        self.tokens = {}
        self.calls = []
        self.cancel = False
        self.lose_close = False
        self.lose_create = False
        self.fail_create = False
        self.issue_windows = set()
        self.on_call = None

    def observe(self):
        raw = tuple(dict(id=w.window_id, tabs=[dict(sessions=[dict(id=t.terminal_id,tty=t.tty,title=t.title,receipt=self.tokens.get(t.terminal_id,''))]) for t in w.tabs]) for w in self.live)
        return Observation(raw, tuple(w for w in self.live if w.window_id not in self.issue_windows), {w:'Unknown job' for w in self.issue_windows})

    def call(self, action, **kwargs):
        if self.on_call: self.on_call(action, kwargs)
        if kwargs.get('windowId') == 'working': raise AssertionError('Touched working window')
        self.calls.append((action, kwargs))
        if action == 'close':
            if not self.cancel: self.live = [w for w in self.live if w.window_id != kwargs['windowId']]
            if self.lose_close: raise Uncertain('Lost close receipt')
            return {'acknowledged':True}
        if self.fail_create: raise Uncertain('Create did not return a receipt')
        import re, base64
        encoded = re.search(r'freeParkingReceipt=([A-Za-z0-9+/=]+)',kwargs['command']).group(1)
        token = base64.b64decode(encoded).decode()
        sid = 'restored-'+str(len(self.tokens))
        wid = kwargs['windowId'] or 'returned'
        tab = Tab(sid,'/dev/'+sid,'Unrelated label','shell','',self.folder,Process(200+len(self.tokens),'new birth','/dev/'+sid,'/bin/zsh'))
        existing = next((w for w in self.live if w.window_id == wid),None)
        if existing: self.live[self.live.index(existing)] = Window(wid, existing.tabs+(tab,))
        else: self.live.append(Window(wid,(tab,)))
        self.tokens[sid] = token
        if self.lose_create: raise Uncertain('Lost create acknowledgement')
        return {'windowId':wid,'sessionId':sid}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'garage')
        self.desktop = Desktop(self.root)
        self.providers = Providers(Shell(None))
        self.gate = Gate(self.store,self.desktop,self.desktop,self.providers,mutations_enabled=True)
        self.flow = Workflow(self.store,self.desktop,self.gate)

    def park(self):
        return self.flow.park('fixture',self.desktop.source.fingerprint)

    def test_complete_round_trip_preserves_work_and_retains_backup(self):
        record = self.park()
        self.assertEqual(record['phase'],'parked')
        record = self.flow.reopen(record['id'])
        self.assertEqual(record['phase'],'returned')
        self.assertEqual(self.desktop.live[0],self.desktop.work)
        self.assertEqual(len(record['targets']),3)
        self.assertEqual(len(self.store.all()),1)
        self.assertEqual([t['title'] for t in record['source']['tabs']], ['Fixture 0','Fixture 1','Fixture 2'])

    def test_store_is_durable_in_an_independent_python_process(self):
        record = self.park()
        code = 'from parking.store import Store; import sys; print(Store(sys.argv[1]).load(sys.argv[2])["phase"])'
        output = subprocess.check_output([sys.executable,'-c',code,str(self.store.root),record['id']],
            env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])},text=True)
        self.assertEqual(output.strip(),'parked')
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)

    def test_display_titles_never_change_identity_or_gate_outcomes(self):
        original = self.desktop.source
        changed = Window(original.window_id, tuple(Tab(t.terminal_id,t.tty,'Working | Not a real identity','shell','',t.cwd,t.process) for t in original.tabs))
        self.assertEqual(changed.fingerprint, original.fingerprint)
        self.desktop.live[1] = changed
        self.assertEqual(self.park()['phase'],'parked')

    def test_failed_save_prevents_terminal_call(self):
        with patch.object(self.store,'save',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError): self.park()
        self.assertEqual(self.desktop.calls,[])

    def test_save_receipt_is_present_before_close_and_every_create(self):
        def check(action, args):
            record = self.store.all()[0]
            self.assertEqual(record['intent']['kind'],action)
            self.assertEqual(record['phase'], 'closing' if action=='close' else 'opening')
        self.desktop.on_call = check
        self.flow.reopen(self.park()['id'])

    def test_cancel_keeps_original_and_retry_reuses_record(self):
        self.desktop.cancel=True
        one=self.park();self.assertEqual(one['phase'],'saved')
        self.desktop.cancel=False
        two=self.park();self.assertEqual(one['id'],two['id'])
        self.assertEqual(two['phase'],'parked')
        self.assertEqual(len(self.store.all()),1)

    def test_lost_close_ack_observed_closed_is_parked(self):
        self.desktop.lose_close=True
        self.assertEqual(self.park()['phase'],'parked')
        self.assertEqual(len(self.desktop.calls),1)

    def test_unconfirmed_close_stays_pending_without_duplicate_or_false_return(self):
        self.desktop.cancel = True
        self.desktop.lose_close = True
        record = self.park()
        self.assertEqual(record['phase'], 'closing')
        self.assertEqual(record['intent'], {'kind':'close'})
        self.assertEqual(self.flow.reconcile(record['id'])['phase'], 'closing')
        self.assertEqual(self.flow.reopen(record['id'])['phase'], 'closing')
        with self.assertRaises(ParkingError): self.park()
        self.assertEqual(len(self.desktop.calls), 1)
        # Confirmation completed later, after the original helper timed out.
        self.desktop.live = [self.desktop.work]
        self.assertEqual(self.flow.reconcile(record['id'])['phase'], 'parked')
        self.desktop.cancel = self.desktop.lose_close = False
        self.assertEqual(self.flow.reopen(record['id'])['phase'], 'returned')

    def test_stale_durable_receipt_never_authorizes_close(self):
        record, old = self.store.begin(self.desktop.source)
        self.store.transition(record,Phase.CLOSING,{'kind':'close'})
        with self.assertRaises(ParkingError):self.gate.close(old)
        self.assertEqual(self.desktop.calls,[])

    def test_missing_close_intent_never_authorizes_close(self):
        _, receipt=self.store.begin(self.desktop.source)
        with self.assertRaises(ParkingError):self.gate.close(receipt)
        self.assertEqual(self.desktop.calls,[])

    def test_process_switch_between_save_and_close_is_rejected(self):
        record,_=self.store.begin(self.desktop.source)
        receipt=self.store.transition(record,Phase.CLOSING,{'kind':'close'})
        tab=self.desktop.source.tabs[0]
        changed=Tab(tab.terminal_id,tab.tty,tab.title,tab.provider,tab.session_id,tab.cwd,Process(tab.process.pid,'new birth',tab.tty,'/bin/zsh'))
        self.desktop.live[1]=Window('fixture',(changed,)+self.desktop.source.tabs[1:])
        with self.assertRaises(ParkingError):self.gate.close(receipt)
        self.assertEqual(self.desktop.calls,[])

    def test_default_gate_cannot_mutate_desktop(self):
        record,_=self.store.begin(self.desktop.source)
        receipt=self.store.transition(record,Phase.CLOSING,{'kind':'close'})
        gate=Gate(self.store,self.desktop,self.desktop,self.providers)
        with self.assertRaises(ParkingError):gate.close(receipt)
        self.assertEqual(self.desktop.calls,[])

    def test_lost_create_ack_reconciles_exact_token_and_retry_never_duplicates(self):
        record=self.park();self.desktop.lose_create=True
        record=self.flow.reopen(record['id'])
        self.assertEqual(len(record['targets']),1)
        self.assertIsNone(record['intent'])
        self.desktop.lose_create=False
        record=self.flow.reopen(record['id'])
        self.assertEqual(record['phase'],'returned')
        self.assertEqual(len([c for c in self.desktop.calls if c[0]=='create']),3)

    def test_reopen_waits_for_first_tab_startup_before_appending(self):
        original_call = self.desktop.call
        def call(action, **kwargs):
            result = original_call(action, **kwargs)
            if action == 'create': self.desktop.issue_windows.add('returned')
            return result
        self.desktop.call = call
        waits = []
        def settle(ready):
            snapshot = self.desktop.observe()
            if not ready(snapshot):
                waits.append(True)
                self.desktop.issue_windows.clear()
                snapshot = self.desktop.observe()
                self.assertTrue(ready(snapshot))
            return snapshot
        self.gate.settle = settle
        record = self.flow.reopen(self.park()['id'])
        self.assertEqual(len(waits), 3)  # Includes final returned-state settling.
        self.desktop.issue_windows.clear()
        self.assertEqual(self.flow.reconcile(record['id'])['phase'], 'returned')
        self.assertEqual(len([c for c in self.desktop.calls if c[0] == 'create']), 3)

    def test_preflight_rejection_clears_only_unsubmitted_intent_and_can_resume(self):
        original_call = self.desktop.call
        def call(action, **kwargs):
            result = original_call(action, **kwargs)
            if action == 'create': self.desktop.issue_windows.add('returned')
            return result
        self.desktop.call = call
        record = self.flow.reopen(self.park()['id'])
        self.assertEqual(record['phase'], 'checking')
        self.assertIsNone(record['intent'])
        self.assertEqual(len(record['targets']), 1)
        self.desktop.call = original_call
        self.desktop.issue_windows.clear()
        self.assertEqual(self.flow.reopen(record['id'])['phase'], 'returned')
        self.assertEqual(len([c for c in self.desktop.calls if c[0] == 'create']), 3)

    def test_missing_create_receipt_without_live_token_never_repeats(self):
        record=self.park();self.desktop.fail_create=True
        record=self.flow.reopen(record['id'])
        self.assertIsNotNone(record['intent'])
        count=len(self.desktop.calls)
        self.desktop.fail_create=False
        self.flow.reopen(record['id'])
        self.assertEqual(len(self.desktop.calls),count)
        self.assertEqual(self.desktop.live,[self.desktop.work])

    def test_restart_after_close_intent_uses_observation_not_error_text(self):
        record,_=self.store.begin(self.desktop.source)
        self.store.transition(record,Phase.CLOSING,{'kind':'close'},issue='misleading old message')
        self.desktop.live=[self.desktop.work]
        self.assertEqual(self.flow.reconcile(record['id'])['phase'],'parked')
        self.assertEqual(self.desktop.calls,[])

    def test_invalid_folder_preflight_opens_nothing(self):
        record=self.park()
        source=Window.decode(record['source'])
        tabs=list(source.tabs);t=tabs[-1]
        tabs[-1]=Tab(t.terminal_id,t.tty,t.title,t.provider,t.session_id,str(self.root/'missing'),t.process)
        window=Window(source.window_id,tuple(tabs))
        record.update(source=window.encode(),fingerprint=window.fingerprint)
        self.store.save(record)
        with self.assertRaises(ParkingError):self.flow.reopen(record['id'])
        self.assertEqual([c[0] for c in self.desktop.calls],['close'])

    def test_unknown_provider_opens_nothing(self):
        record=self.park()
        source=Window.decode(record['source']);tabs=list(source.tabs);t=tabs[-1]
        tabs[-1]=Tab(t.terminal_id,t.tty,'Not identity','codex',str(uuid.uuid4()),t.cwd,t.process)
        window=Window(source.window_id,tuple(tabs));record.update(source=window.encode(),fingerprint=window.fingerprint)
        self.store.save(record)
        with self.assertRaises(ParkingError):self.flow.reopen(record['id'])
        self.assertEqual([c[0] for c in self.desktop.calls],['close'])

    def test_unsupported_live_source_is_not_mistaken_for_absent(self):
        self.desktop.issue_windows.add('fixture')
        with self.assertRaises(ParkingError):self.park()
        record,_=self.store.begin(self.desktop.source)
        self.assertFalse(Verifier.source_absent(record,self.desktop.observe()))
        record=self.flow.reopen(record['id'])
        self.assertIsNotNone(record['issue'])
        self.assertEqual(self.desktop.calls,[])

    def test_wrong_folder_does_not_verify_return(self):
        record=self.flow.reopen(self.park()['id'])
        w=self.desktop.live[-1];t=w.tabs[0]
        changed=Tab(t.terminal_id,t.tty,t.title,t.provider,t.session_id,str(self.root/'different'),t.process)
        self.desktop.live[-1]=Window(w.window_id,(changed,)+w.tabs[1:])
        self.assertFalse(Verifier.returned(record,self.desktop.observe()))

    def test_reordered_tabs_do_not_verify_return(self):
        record=self.flow.reopen(self.park()['id'])
        w=self.desktop.live[-1];self.desktop.live[-1]=Window(w.window_id,tuple(reversed(w.tabs)))
        self.assertFalse(Verifier.returned(record,self.desktop.observe()))

    def test_corrupt_record_is_visible_not_silently_discarded(self):
        record=self.park()
        with self.store.connection() as db:db.execute('UPDATE records SET body=? WHERE id=?', ('{}',record['id']))
        with self.assertRaises(ParkingError):self.store.all()

    def test_compare_and_swap_rejects_stale_writer(self):
        record=self.park();stale=copy.deepcopy(record)
        self.store.save(record)
        with self.assertRaises(ParkingError):self.store.save(stale)

    def test_per_window_lock_rejects_overlapping_operation(self):
        with self.store.operation('fixture'):
            with self.assertRaises(ParkingError):self.park()
        self.assertEqual(self.desktop.calls,[])

    def test_unrelated_window_lock_does_not_block_fixture(self):
        with self.store.operation('working'):
            self.assertEqual(self.park()['phase'],'parked')

    def test_fifty_complete_cycles_preserve_the_working_window(self):
        for i in range(50):
            self.desktop.live=[self.desktop.work,self.desktop.source]
            # A new park gets a new journal; a completed backup of the same
            # source identities must be retained rather than overwritten.
            record=self.park()
            record=self.flow.reopen(record['id'])
            self.assertEqual(record['phase'],'returned')
            self.assertEqual(self.desktop.live[0],self.desktop.work)
        self.assertEqual(len(self.store.all()),50)


if __name__=='__main__':unittest.main()
