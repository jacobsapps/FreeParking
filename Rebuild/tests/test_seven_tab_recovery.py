"""Seven-tab incident regressions, using the real journal and workflow."""
from dataclasses import replace
from unittest.mock import patch
import unittest
import test_core
from parking.model import NotSubmitted, Window, ParkingError
from parking.garage import dispatch, car_view
from parking.workflow import Workflow


class SevenTabRecovery(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        tab = self.desktop.source.tabs[0]
        self.desktop.source = Window('fixture', tuple(replace(tab, terminal_id='tab-'+str(i),
            tty='/dev/test'+str(i), process=replace(tab.process, tty='/dev/test'+str(i)),
            title='Saved title '+str(i+1)) for i in range(7)), dict(x=40,y=50,width=800,height=600))
        self.desktop.live[1] = self.desktop.source

    def parked(self): return self.flow.park('fixture', self.desktop.source.fingerprint)

    def test_transient_second_append_retries_only_definitely_unsubmitted_request(self):
        record = self.parked(); call = self.desktop.call; failures = []
        def native(action, **kwargs):
            if action == 'create' and len(self.desktop.tokens) == 1 and not failures:
                failures.append(True); raise NotSubmitted('Exact window read changed before submission')
            return call(action, **kwargs)
        self.desktop.call = native
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual(len(result['targets']), 7)
        self.assertEqual(len(self.desktop.tokens), 7)
        creates = [kw for action,kw in self.desktop.calls if action=='create']
        self.assertEqual([c['title'] for c in creates], ['Saved title '+str(i) for i in range(1,8)])
        self.assertEqual(creates[0]['bounds'], self.desktop.source.bounds)

    def test_restart_after_first_returned_tab_closes_recovers_all_seven(self):
        record = self.parked(); call = self.desktop.call
        def refuse(action, **kwargs):
            if action=='create' and self.desktop.tokens: raise NotSubmitted('Append unavailable')
            return call(action, **kwargs)
        self.desktop.call = refuse
        partial = self.flow.reopen(record['id'])
        self.assertEqual(len(partial['targets']), 1)
        self.desktop.live = [self.desktop.work]
        self.desktop.call = call
        self.flow = Workflow(self.store, self.desktop, self.gate)
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual(len(result['targets']), 7)
        self.assertEqual(len(result['closed_targets']), 1)

    def test_user_extra_tab_does_not_prevent_remaining_six_from_opening(self):
        record = self.parked(); call = self.desktop.call
        def extra(action, **kwargs):
            result = call(action, **kwargs)
            if action=='create' and len(self.desktop.tokens)==1:
                w = self.desktop.live[-1]
                self.desktop.live[-1] = Window(w.window_id, w.tabs+(replace(self.desktop.work.tabs[0], terminal_id='extra'),))
            return result
        self.desktop.call = extra
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual(len(self.desktop.live[-1].tabs), 8)
        self.assertEqual(len(result['targets']), 7)

    def test_preflight_failure_prevents_closing_any_source_tab(self):
        self.providers.items['shell'].preflight = lambda tab: (_ for _ in ()).throw(ParkingError('CLI missing'))
        with self.assertRaisesRegex(ParkingError, 'CLI missing'): self.parked()
        self.assertEqual(self.desktop.calls, [])

    def test_closed_middle_target_is_recreated_and_original_order_is_restored(self):
        record = self.parked(); call = self.desktop.call
        def refuse(action, **kwargs):
            if action=='create' and len(self.desktop.tokens)>=3: raise NotSubmitted('Fourth append refused')
            return call(action, **kwargs)
        self.desktop.call = refuse
        partial = self.flow.reopen(record['id'])
        self.assertEqual(len(partial['targets']), 3)
        w = self.desktop.live[-1]
        self.desktop.live[-1] = Window(w.window_id,(w.tabs[0],w.tabs[2]))
        self.desktop.call = call
        result = self.flow.reopen(record['id'])
        self.assertEqual(result['phase'], 'returned')
        self.assertEqual([t.terminal_id for t in self.desktop.live[-1].tabs],
                         [result['targets'][str(i)]['terminal_id'] for i in range(7)])
        self.assertEqual(sum(a=='create' for a,_ in self.desktop.calls),8)
        self.assertEqual(sum(a=='reorder' for a,_ in self.desktop.calls),1)

    def test_unreadable_record_does_not_hide_intact_recovery(self):
        record = self.parked()
        with self.store.connection() as db:
            db.execute('INSERT INTO records VALUES (?,1,?)', ('broken', '{bad json'))
        with patch('parking.garage.compose', return_value=(self.store,self.desktop,self.flow)):
            reply = dispatch(['list'])
        self.assertEqual([c['id'] for c in reply['cars']], [record['id']])
        self.assertIn('broken', reply['warnings'][0])

    def test_per_tab_status_keeps_all_seven_titles_visible_after_partial_restore(self):
        record = self.parked()
        record['targets']['0'] = dict(window_id='returned', terminal_id='one', receipt='receipt')
        record['phase'] = 'checking'; self.store.save(record)
        view = car_view(record,self.store.root)
        self.assertEqual(len(view['tabs']), 7)
        self.assertEqual([t['restore_state'] for t in view['tabs']], ['opened']+['saved']*6)
