"""Native-shaped Claude transcript contracts; no agent or terminal is launched."""
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock
import uuid
sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.agents import Claude
from parking.model import ParkingError, Process, Tab, Uncertain


class ClaudeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.sid = str(uuid.uuid4())
        self.request = str(uuid.uuid4())
        self.tab = Tab('tab', '/dev/test', '', 'claude', self.sid, str(self.root),
                       Process(31, 'birth', '/dev/test', '/bin/claude'), 'running')
        (self.root/'projects/project').mkdir(parents=True)
        (self.root/'sessions').mkdir()
        self.path = self.root/'projects/project'/(self.sid+'.jsonl')
        self.processes = Mock()
        self.processes.all.return_value = {31: dict(started='birth')}
        self.processes.started_utc.return_value = 'UTC birth'
        self.processes.cwd.return_value = str(self.root)
        self.provider = Claude(self.processes, self.root)
        self.registry('busy')
        self.user(self.request)

    def registry(self, status):
        (self.root/'sessions/31.json').write_text(json.dumps(dict(pid=31,
            pidDomain='darwin', kind='interactive', entrypoint='cli', procStart='UTC birth',
            sessionId=self.sid, cwd=str(self.root), status=status)))

    def append(self, kind, **fields):
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type=kind, sessionId=self.sid, cwd=str(self.root), **fields))+'\n')

    def user(self, request):
        self.append('user', uuid=request, message=dict(content='fixture request'))

    def final(self, kind='text', reason='end_turn'):
        self.append('assistant', message=dict(content=[dict(type=kind, **{kind:'fixture response'})], stop_reason=reason))

    def test_thinking_end_turn_does_not_end_split_response(self):
        self.final('thinking')
        self.assertEqual(self.provider.pending_request(self.tab, self.path), self.request)
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.assertEqual(checkpoint['request_id'], self.request)
        self.final(); self.registry('idle')
        self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))
        self.assertEqual(self.provider.request_state(self.tab, self.path),
                         dict(request_id=self.request, completed=True))
        self.processes.all.return_value = {}
        self.assertEqual(self.provider.confirm_close_interruption(self.tab, checkpoint)['outcome'], 'unconfirmed')

    def test_stale_running_snapshot_can_prepare_after_natural_completion(self):
        self.final(); self.registry('idle')
        self.assertIsNone(self.provider.prepare_close_interruption(self.tab))

    def test_busy_registry_old_complete_request_is_not_evidence_of_new_request(self):
        self.final()
        self.assertIsNone(self.provider.prepare_close_interruption(self.tab))

    def test_new_completed_request_rejects_existing_checkpoint_even_when_idle(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.final(); self.user(str(uuid.uuid4())); self.final(); self.registry('idle')
        self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))
        self.processes.all.return_value = {}
        self.assertEqual(self.provider.confirm_close_interruption(self.tab, checkpoint)['outcome'], 'unconfirmed')

    def test_new_pending_request_rejects_existing_checkpoint(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.user(str(uuid.uuid4()))
        self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))

    def test_partial_append_is_not_silently_ignored(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        with self.path.open('a') as stream: stream.write('{"type":"user"')
        self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))

    def test_exit_with_unfinished_same_request_credits_only_that_request(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.final('thinking'); self.processes.all.return_value = {}
        outcome = self.provider.confirm_close_interruption(self.tab, checkpoint)
        self.assertEqual((outcome['outcome'], outcome['request_id']), ('interrupted', self.request))

    def test_other_explicit_terminal_reasons_are_not_ignored(self):
        for reason in ('max_tokens', 'stop_sequence', 'refusal'):
            with self.subTest(reason=reason):
                self.user(self.request); self.final('thinking', reason)
                self.assertIsNone(self.provider.pending_request(self.tab, self.path))

    def test_empty_end_turn_is_not_proof_of_finished_request(self):
        self.append('assistant', message=dict(content=[], stop_reason='end_turn'))
        self.assertEqual(self.provider.pending_request(self.tab, self.path), self.request)

    def test_image_only_request_is_new_work_not_a_tool_result(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.append('user', uuid=str(uuid.uuid4()), message=dict(content=[dict(type='image', source={})]))
        self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))

    def test_unknown_or_empty_user_content_cannot_hide_new_request(self):
        for content in ([], '', [dict(type='future_request_type')]):
            with self.subTest(content=content):
                self.path.write_text(''); self.user(self.request)
                checkpoint = self.provider.prepare_close_interruption(self.tab)
                self.append('user', uuid=str(uuid.uuid4()), message=dict(content=content))
                self.assertFalse(self.provider.validate_close_interruption(self.tab, checkpoint))

    def test_missing_request_metadata_does_not_invalidate_exact_resume_identity(self):
        self.path.write_text(json.dumps(dict(type='system', sessionId=self.sid, cwd=self.tab.cwd))+'\n')
        self.assertIsNone(self.provider.prepare_close_interruption(self.tab))
        launch = self.provider.launch(self.tab, str(uuid.uuid4()))
        self.assertIn(self.sid, launch)
        self.assertNotIn('please continue', launch)

    def test_malformed_request_metadata_revokes_credit_not_exact_resume(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        with self.path.open('a') as stream: stream.write('{incomplete')
        self.assertIsNone(self.provider.prepare_close_interruption(self.tab))
        self.assertFalse(self.provider.interruption_current(self.tab, checkpoint))
        self.processes.all.return_value = {}
        self.assertEqual(self.provider.confirm_close_interruption(self.tab, checkpoint)['outcome'], 'unconfirmed')
        self.assertIn(self.sid, self.provider.launch(self.tab, str(uuid.uuid4())))

    def test_process_inspection_failure_is_still_critical(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.processes.all.side_effect = ParkingError('process inspection denied')
        with self.assertRaises(ParkingError): self.provider.confirm_close_interruption(self.tab, checkpoint)

    def test_wrong_session_identity_still_blocks_launch(self):
        self.path.write_text(json.dumps(dict(type='system', sessionId=str(uuid.uuid4()), cwd=self.tab.cwd))+'\n')
        self.assertIsNone(self.provider.prepare_close_interruption(self.tab))
        with self.assertRaises(ParkingError): self.provider.launch(self.tab, str(uuid.uuid4()))

    def test_same_pending_request_keeps_optional_continuation_credit(self):
        checkpoint = self.provider.prepare_close_interruption(self.tab)
        self.assertTrue(self.provider.validate_close_interruption(self.tab, checkpoint))
        self.assertTrue(self.provider.interruption_current(self.tab, checkpoint))
        self.processes.all.return_value = {}
        self.assertEqual(self.provider.confirm_close_interruption(self.tab, checkpoint)['outcome'], 'interrupted')
