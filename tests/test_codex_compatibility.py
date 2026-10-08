"""No terminal access: saved-index, process-scope and test-mode fixtures."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('fp', Path(__file__).parents[1] / 'Resources/freeparking.py')
fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
SID = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class CodexCompatibilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / ('rollout-fixture-' + SID + '.jsonl')
        self.path.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': SID, 'cwd': str(self.root), 'source': 'vscode', 'originator': 'codex-tui'}}) + '\n')
        self.db = sqlite3.connect(self.root / 'state_5.sqlite'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT, name TEXT, cwd TEXT, rollout_path TEXT)')
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?)', (SID, 'Fixture task', str(self.root), str(self.path)))
        self.db.commit()
        self.process = dict(pid=101, ppid=10, started='fixture', tty='/dev/fixture', executable='/fixture/codex')
        self.api = Mock(); self.api.cwd.return_value = str(self.root)
        self.session = dict(native_title='Fixture task | Ready', job_pid='101', title='Decorated unrelated title')

    def resolve(self):
        return fp.codex_terminal_metadata(self.session, self.process, self.api, self.root)

    def test_shared_tui_metadata_is_supported(self):
        result = self.resolve()
        self.assertEqual(result['session_id'], SID)
        self.assertTrue(result['shared_session'])
        self.assertEqual(result['native_status'], 'Ready')

    def test_uuid_native_title_before_name_generation(self):
        self.session['native_title'] = SID + ' | Ready'
        self.assertEqual(self.resolve()['session_id'], SID)

    def test_spinner_and_status_are_not_part_of_name(self):
        for title in ['⠋ Fixture task | Working', 'Fixture task ⠋ | Ready', '[!] Action Required | Fixture task | Waiting']:
            self.session['native_title'] = title
            self.assertEqual(self.resolve()['session_id'], SID)

    def test_action_required_title_identifies_but_blocks_parking(self):
        for title in ['[ . ] Action Required | Fixture task', '[!] Action Required | Fixture task | Ready']:
            self.session['native_title'] = title
            tab = dict(self.resolve(), issue='')
            self.assertEqual(tab['session_id'], SID)
            self.assertIn('Finish or cancel', fp.parking_issue(tab))

    def test_decorated_or_missing_native_title_cannot_claim_session(self):
        for title in ['', 'Codex', 'Fixture task', 'Wrong task | Ready']:
            self.session['native_title'] = title
            with self.assertRaises(fp.ParkError): self.resolve()

    def test_stale_job_pid_blocks_identity(self):
        self.session['job_pid'] = '102'
        with self.assertRaisesRegex(fp.ParkError, 'foreground'): self.resolve()

    def test_duplicate_names_never_choose_latest(self):
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?)', (OTHER, 'Fixture task', str(self.root), str(self.path)))
        self.db.commit()
        with self.assertRaisesRegex(fp.ParkError, 'exactly one'): self.resolve()

    def test_same_name_in_another_folder_is_not_ambiguous(self):
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?)', (OTHER, 'Fixture task', '/different', str(self.path)))
        self.db.commit()
        self.assertEqual(self.resolve()['session_id'], SID)

    def test_missing_file_and_stale_index_fail_closed(self):
        self.path.unlink()
        with self.assertRaises(OSError): self.resolve()

    def test_transcript_identity_must_match_index(self):
        self.path.write_text(json.dumps({'type':'session_meta','payload':{
            'id':OTHER,'cwd':str(self.root),'source':'cli'}}))
        with self.assertRaisesRegex(fp.ParkError, 'disagree'): self.resolve()

    def test_editor_and_subagent_transcripts_are_not_tui_identity(self):
        for source, origin in [('vscode', 'codex_vscode'), ({'subagent':'fixture'}, 'codex-tui')]:
            self.path.write_text(json.dumps({'type':'session_meta','payload':{
                'id':SID,'cwd':str(self.root),'source':source,'originator':origin}}))
            with self.assertRaises(ValueError): self.resolve()

    def test_shared_busy_session_is_identified_but_cannot_park(self):
        tab = dict(self.resolve(), issue='')
        self.assertFalse(fp.parking_issue(tab))
        tab['native_status'] = 'Working'
        self.assertIn('Finish or cancel', fp.parking_issue(tab))

    def test_shared_server_and_all_its_children_are_excluded(self):
        server = dict(self.process, pid=201, ppid=101, executable='/fixture/app-server-daemon/releases/0.160.1/bin/codex')
        child = dict(self.process, pid=202, ppid=201, executable='/fixture/work')
        own = dict(self.process, pid=102, ppid=101, executable='/fixture/private-helper')
        running = {p['pid']:p for p in [self.process, server, child, own]}
        self.assertEqual({p['pid'] for p in fp.descendants([self.process],running)}, {101,102})
        self.assertEqual({p['pid'] for p in fp.safe_tracked_processes(list(running.values()),running)}, {101,102})
        with patch.object(fp.Processes, 'all', return_value=running), patch.object(fp.os, 'kill') as kill:
            fp.Processes().send_signal(server, 15)
            fp.Processes().send_signal(child, 15)
        kill.assert_not_called()



if __name__ == '__main__': unittest.main()
