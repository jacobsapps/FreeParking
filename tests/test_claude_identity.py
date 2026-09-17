"""Claude process-registry checks: temporary fixtures only, no real terminals."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('freeparking', Path(__file__).parents[1] / 'Resources/freeparking.py')
cp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cp)
SID = '12345678-1234-4234-8234-123456789abc'
BIRTH = 'Thu Sep 17 10:00:00 2026'


class ClaudeIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.process = {'pid': 101, 'ppid': 10, 'tty': '/dev/fixture', 'started': BIRTH, 'executable': '/fixture/claude'}
        self.record = {'pid': 101, 'sessionId': SID, 'procStart': BIRTH,
                       'cwd': str(self.root), 'pidDomain': 'darwin', 'kind': 'interactive', 'entrypoint': 'cli'}
        self.path = self.root / 'sessions/101.json'
        self.path.parent.mkdir()
        self.transcript = self.root / ('projects/fixture/' + SID + '.jsonl')
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text('{}\n')
        self.write()
        root = self.root
        class Metadata:
            def cwd(self, pid): return str(root)
            def started_utc(self, pid): return BIRTH
            def files(self, pid): raise AssertionError('Native registry should not need an open transcript')
        self.api = Metadata()

    def write(self): self.path.write_text(json.dumps(self.record))
    def resolve(self): return cp.claude_native_metadata(self.process, self.api, self.root)

    def test_exact_live_record_identifies_without_custom_hooks(self):
        result = self.resolve()
        self.assertEqual(result['session_id'], SID)
        self.assertEqual(result['cwd'], str(self.root))
        self.assertEqual(result['transcript_path'], str(self.transcript))

    def test_missing_registry_allows_older_version_fallback(self):
        self.path.unlink()
        self.assertIsNone(self.resolve())

    def test_reused_pid_and_wrong_process_kind_are_rejected(self):
        for field, value in [('pid', 102), ('pid', True), ('procStart', 'Thu Sep 17 11:00:00 2026'),
                             ('pidDomain', 'linux'), ('kind', 'background'), ('entrypoint', 'sdk'),
                             ('cwd', '/different'), ('sessionId', 'not-a-uuid')]:
            with self.subTest(field=field, value=value):
                old = self.record[field]
                self.record[field] = value
                self.write()
                with self.assertRaises(cp.ParkError): self.resolve()
                self.record[field] = old

    def test_malformed_or_incomplete_registry_is_not_silently_ignored(self):
        for raw in ('{broken', '[]', '{}', '{"pid":101}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                with self.assertRaises(cp.ParkError): self.resolve()

    def test_transcript_must_exist_and_be_nonempty(self):
        self.transcript.write_text('')
        with self.assertRaises(cp.ParkError): self.resolve()
        self.transcript.unlink()
        with self.assertRaises(cp.ParkError): self.resolve()

    def test_duplicate_transcript_is_not_guessed(self):
        other = self.root / ('projects/another/' + SID + '.jsonl')
        other.parent.mkdir(); other.write_text('{}\n')
        with self.assertRaises(cp.ParkError): self.resolve()

    def test_native_identity_takes_precedence_over_open_files_or_old_hooks(self):
        raw = {'index': 0, 'sessions': [{'id': 'fixture-terminal', 'tty': '/dev/fixture', 'title': 'Fixture'}]}
        with patch.object(cp, 'claude_native_metadata', return_value=self.resolve()):
            result = cp.resolve_tab(raw, {101: self.process}, self.api, [])
        self.assertEqual(result['issue'], '')
        self.assertEqual(result['session_id'], SID)

    def test_stale_native_record_blocks_hook_fallback(self):
        raw = {'index': 0, 'sessions': [{'id': 'fixture-terminal', 'tty': '/dev/fixture', 'title': 'Fixture'}]}
        with patch.object(cp, 'claude_native_metadata', side_effect=cp.ParkError('stale registry')), \
             patch.object(cp, 'claude_hook_metadata', side_effect=AssertionError('Must not use stale fallback')):
            result = cp.resolve_tab(raw, {101: self.process}, self.api, [])
        self.assertEqual(result['issue'], 'stale registry')
        self.assertEqual(result['session_id'], '')


if __name__ == '__main__': unittest.main()
