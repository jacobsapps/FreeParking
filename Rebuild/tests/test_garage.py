"""Original UI contract over the real v2 recovery/workflow; OS boundary is fake."""
from dataclasses import replace
from unittest.mock import Mock,patch
import unittest
import test_core
from parking.garage import live_windows,park_all,dispatch
from parking.model import Observation,ParkingError,Window

class GarageTests(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        self.first=self.desktop.source
        second=Window('second',tuple(replace(t,terminal_id='second-'+t.terminal_id) for t in self.first.tabs))
        self.desktop.live=[self.first,second]

    def test_park_all_durably_saves_every_window_before_first_close(self):
        def check(action,kwargs):
            self.assertEqual(len(self.store.all()),2)
            for record in self.store.all():self.store.verify(self.store.receipt(record))
        self.desktop.on_call=check
        results=park_all(self.store,self.desktop,self.flow)
        self.assertEqual([r['phase'] for r in results],['parked','parked'])
        self.assertEqual(len(self.desktop.calls),2)

    def test_unresolved_window_is_visible_and_blocks_all_mutations(self):
        self.desktop.issue_windows={'second'}
        rows=live_windows(self.desktop.observe())
        self.assertEqual(len(rows),2);self.assertFalse(rows[1]['can_park'])
        self.assertEqual(len(rows[1]['tabs']),3)
        self.assertTrue(all(t['issue'] for t in rows[1]['tabs']))
        with self.assertRaises(ParkingError):park_all(self.store,self.desktop,self.flow)
        self.assertEqual(self.desktop.calls,[]);self.assertEqual(self.store.all(),[])

    def test_cancelled_first_close_does_not_close_second_window(self):
        self.desktop.cancel=True
        result=park_all(self.store,self.desktop,self.flow)
        self.assertEqual(result[0]['phase'],'saved');self.assertEqual(len(self.desktop.calls),1)
        self.assertEqual(len(self.desktop.live),2)

    def test_refresh_keeps_saved_open_batch_windows_available_to_finish_parking(self):
        self.desktop.cancel=True
        park_all(self.store,self.desktop,self.flow)
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)):
            scanned=dispatch(['--live','scan'])
            self.assertEqual(len(scanned['cars']),2)
            self.assertEqual(scanned['archives'],[])
            self.desktop.cancel=False
            for car in scanned['cars']:
                reply=dispatch(['--live','finish-parking',car['id']])
                self.assertTrue(reply['ok'],reply.get('error'))
        self.assertEqual(self.desktop.live,[])

    def test_a_failed_second_save_prevents_first_close(self):
        original=self.store.begin
        def begin(window):
            if window.window_id=='second':raise OSError('Disk full')
            return original(window)
        with patch.object(self.store,'begin',side_effect=begin),self.assertRaises(OSError):
            park_all(self.store,self.desktop,self.flow)
        self.assertEqual(self.desktop.calls,[])

    def test_tab_problem_retains_good_tab_names_and_exact_bad_tab_issue(self):
        snap=self.desktop.observe();raw=snap.raw[0];good=self.first.tabs[0]
        bad=Observation((raw,),(),{'fixture':'Tab 2: unknown'},
            {self.first.tabs[1].terminal_id:'Specific native failure'}, {good.terminal_id:good})
        row=live_windows(bad)[0]
        self.assertEqual(row['tabs'][0]['issue'],'');self.assertEqual(row['tabs'][0]['title'],good.title)
        self.assertEqual(row['tabs'][1]['issue'],'Specific native failure')
        self.assertFalse(row['can_park'])

    def test_original_ui_dispatch_uses_new_workflow_and_retains_backups(self):
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)):
            scanned=dispatch(['--live','scan']);self.assertEqual(len(scanned['windows']),2)
            parked=dispatch(['--live','park',self.first.window_id,self.first.fingerprint])
            self.assertTrue(parked['ok']);self.assertEqual(parked['cars'][0]['status'],'parked')
            cid=parked['cars'][0]['id']
            returned=dispatch(['--live','restore',cid]);self.assertTrue(returned['ok'])
            self.assertEqual(returned['cars'],[]);self.assertEqual(returned['archives'][0]['id'],cid)
            self.assertIn(cid,returned['verified_archived_ids'])
            self.assertTrue(self.store.path.exists())

    def test_bare_presentation_helper_cannot_park(self):
        with patch('parking.garage.compose',return_value=(self.store,self.desktop,self.flow)):
            result=dispatch(['park-all'])
        self.assertFalse(result['ok']);self.assertEqual(self.desktop.calls,[])

    def test_read_only_refresh_never_finishes_a_native_close(self):
        record,_=self.store.begin(self.first)
        from parking.model import Phase
        self.store.transition(record,Phase.CLOSING,dict(kind='close'))
        self.desktop.live=[]
        with patch.object(self.flow,'confirm_closed',side_effect=AssertionError('Must not mutate an agent')):
            result=self.flow.reconcile(record['id'],read_only=True)
        self.assertEqual(result['phase'],'closing');self.assertEqual(self.desktop.calls,[])
