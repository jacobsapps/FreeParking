"""One-click, multi-window parking: fakes only, no terminal access."""
import importlib.util
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

sp = importlib.util.spec_from_file_location('fp', Path(__file__).parents[1] / 'Resources/freeparking.py')
fp = importlib.util.module_from_spec(sp); sp.loader.exec_module(fp)


class ParkingAllTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.store = fp.Store(self.root / 'garage')
        self.transcript = self.root / 'transcript.jsonl'; self.transcript.write_text('{}\n')
        self.windows = []
        self.running = {}
        for i in range(2):
            process = {'pid': 101+i, 'ppid': 10, 'tty': '/dev/fixture'+str(i),
                       'started': 'Thu Sep 17 10:00:00 2026', 'executable': '/fixture/codex'}
            self.running[process['pid']] = process
            tab = {'index': 0, 'title': 'Fixture', 'terminal_id': 'terminal-'+str(i), 'tty': process['tty'],
                   'provider': 'codex', 'session_id': '00000000-0000-4000-8000-%012d' % (i+1),
                   'cwd': str(self.root), 'issue': '', 'process': process, 'has_agent': True,
                   'transcript_path': str(self.transcript)}
            window = {'id': str(i+1), 'title': 'Fixture', 'tabs': [tab], 'can_park': True}
            window['fingerprint'] = fp.fingerprint(window); self.windows.append(window)
        owner = self
        self.signals = []; self.calls = []; self.cancel = False
        class Processes:
            def all(self): return dict(owner.running)
            def cwd(self, pid): return str(owner.root)
            def send_signal(self, p, sig):
                assert len(owner.store.cars()[0]) == 2, 'All windows must be saved before any signal'
                owner.signals.append((p['pid'], sig))
                if sig == signal.SIGTERM: owner.running.pop(p['pid'], None)
        class ITerm:
            def __init__(self):
                self.raw = [{'id': w['id'], 'tabs': [{'sessions': [{'id': w['tabs'][0]['terminal_id']}]}]} for w in owner.windows]
            def windows(self): return self.raw
            def call(self, action, **args):
                assert len(owner.store.cars()[0]) == 2, 'All windows must be durable first'
                owner.calls.append((action, args))
                if action == 'close' and not owner.cancel:
                    self.raw = [w for w in self.raw if w['id'] != args['windowId']]
                return {'ok': True}
        self.proc = Processes(); self.iterm = ITerm()
        self.discovery = patch.object(fp, 'discover', return_value=self.windows)
        self.discovery.start(); self.addCleanup(self.discovery.stop)
        self.sleep = patch.object(fp.time, 'sleep'); self.sleep.start(); self.addCleanup(self.sleep.stop)

    def test_all_recovery_files_exist_before_either_window_is_interrupted(self):
        fp.park_all(self.store, self.iterm, self.proc)
        self.assertEqual([a['windowId'] for action,a in self.calls if action == 'close'], ['1','2'])
        self.assertTrue(all(c['status']=='parked' for c in self.store.cars()[0]))

    def test_second_save_failure_closes_and_signals_nothing(self):
        original = self.store.save
        def save(car):
            if car['source_window_id']=='2': raise OSError('Disk full')
            original(car)
        with patch.object(self.store, 'save', side_effect=save):
            with self.assertRaises(OSError): fp.park_all(self.store, self.iterm, self.proc)
        self.assertEqual(self.calls, []); self.assertEqual(self.signals, [])
        self.assertEqual(len(self.store.cars()[0]), 1)

    def test_one_blocked_window_leaves_every_window_untouched(self):
        self.windows[1]['can_park'] = False; self.windows[1]['tabs'][0]['issue'] = 'No saved conversation'
        with self.assertRaisesRegex(fp.ParkError, 'Nothing closed.*Window 2, tab 1'):
            fp.park_all(self.store, self.iterm, self.proc)
        self.assertEqual(self.calls, []); self.assertEqual(self.signals, []); self.assertEqual(self.store.cars()[0], [])

    def test_cancelling_first_close_does_not_interrupt_second_window(self):
        self.cancel = True
        fp.park_all(self.store, self.iterm, self.proc)
        self.assertEqual([a['windowId'] for action,a in self.calls if action=='close'], ['1'])
        self.assertTrue(all(pid==101 and sig==signal.SIGINT for pid,sig in self.signals))
        self.assertTrue(all(c['status']=='saved_open' for c in self.store.cars()[0]))

    def test_tabs_changing_after_save_abort_before_any_interrupt(self):
        with patch.object(fp, 'discover', side_effect=[self.windows, []]):
            with self.assertRaisesRegex(fp.ParkError, 'Nothing closed'):
                fp.park_all(self.store, self.iterm, self.proc)
        self.assertEqual(self.calls, []); self.assertEqual(self.signals, [])

    def test_no_windows_is_a_noop(self):
        with patch.object(fp, 'discover', return_value=[]):
            self.assertEqual(fp.park_all(self.store, self.iterm, self.proc), 'No iTerm windows are open.')
        self.assertEqual(self.calls, []); self.assertEqual(self.store.cars()[0], [])

    def test_one_reopen_action_restores_every_car(self):
        cars = [fp.prepare_car(self.store, w, self.proc) for w in self.windows]
        with patch.object(fp, 'restore') as restore:
            fp.restore_all(self.store, self.iterm, self.proc)
        self.assertEqual({c.args[-1] for c in restore.call_args_list}, {c['id'] for c in cars})
        self.assertEqual(restore.call_count, 2)

    def test_corrupt_car_blocks_reopen_before_any_window_is_created(self):
        with patch.object(self.store, 'cars', return_value=([], ['Unreadable recovery'])), patch.object(fp, 'restore') as restore:
            with self.assertRaisesRegex(fp.ParkError, 'Nothing was opened'):
                fp.restore_all(self.store, self.iterm, self.proc)
        restore.assert_not_called()

    def shell(self, extra=None):
        process = {**self.running[101], 'executable': '-zsh'}
        processes = {101:process}
        if extra: processes[102] = {**self.running[102], 'tty':process['tty'], 'executable': extra}
        raw = {'index':0,'sessions':[{'id':'fixture-shell','tty':process['tty'],'title':'Fixture shell'}]}
        return fp.resolve_tab(raw, processes, self.proc, [])

    def test_idle_shell_and_caffeinate_save_the_folder(self):
        for extra in (None, 'caffeinate'):
            with self.subTest(extra=extra):
                tab = self.shell(extra)
                self.assertEqual(tab['provider'],'shell'); self.assertEqual(tab['issue'],'')
                self.assertEqual(tab['cwd'],str(self.root))
                self.assertIn('exec /bin/zsh -l',fp.resume_command(tab))

    def test_other_running_shell_jobs_are_not_silently_closed(self):
        tab = self.shell('make')
        self.assertIn('running make',tab['issue']); self.assertEqual(tab['provider'],'')

    def test_shells_are_not_signalled_and_caffeinate_only_terminates_after_close(self):
        tab = self.shell('caffeinate'); tab['auxiliary_processes'][0]['pid'] = 103
        self.running[103] = tab['auxiliary_processes'][0]
        self.running[101] = tab['process']
        self.windows[0]['tabs'] = [tab]; self.windows[0]['fingerprint'] = fp.fingerprint(self.windows[0])
        self.iterm.raw[0]['tabs'][0]['sessions'][0]['id'] = tab['terminal_id']
        fp.park_all(self.store,self.iterm,self.proc)
        self.assertFalse(any(pid==101 for pid,sig in self.signals))
        self.assertEqual([sig for pid,sig in self.signals if pid==103],[signal.SIGTERM])

    def test_reopened_shell_matches_its_recorded_tab_not_another_same_folder(self):
        tab = self.shell(); self.windows[0]['tabs']=[tab]
        car = fp.prepare_car(self.store,self.windows[0],self.proc)
        car.update(status='restored',restored_targets={'0':{'window_id':'9','terminal_id':'returned-shell'}})
        self.store.save(car)
        returned = {**tab,'terminal_id':'returned-shell'}
        other = {**tab,'terminal_id':'other-shell'}
        live = [{'id':'9','tabs':[returned,other]}]
        self.iterm.raw=[{'id':'9','tabs':[{'sessions':[{'id':'returned-shell'},{'id':'other-shell'}]}]}]
        self.running[101] = tab['process']
        with patch.object(fp,'discover',return_value=live):
            fp.verify_return(car,self.iterm,self.proc)
            # No new tab should be requested on a repeat reopen.
            with patch.object(self.iterm,'call',return_value={'ok':True}) as call:
                fp.restore(self.store,self.iterm,self.proc,car['id'])
            self.assertEqual([c.args[0] for c in call.call_args_list],['focus'])

if __name__ == '__main__': unittest.main()
