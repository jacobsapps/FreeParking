"""Permission restoration is per conversation and never inherits blanket YOLO."""
from dataclasses import replace
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.agents import Claude, Codex
from parking.model import Process, Tab, Window
from parking.permissions import launch_flags, label


class PermissionRestoration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tab=Tab('fixture','/dev/test','Not policy','codex',str(uuid.uuid4()),self.temp.name,
                     Process(10,'birth','/dev/test','codex'))

    def test_unknown_codex_permissions_override_a_global_yolo_default(self):
        rpc=Mock();rpc.read.return_value=dict(id=self.tab.session_id,cwd=self.tab.cwd)
        with patch.dict('os.environ', {'CODEX_SANDBOX_MODE':'danger-full-access'}):
            launch=Codex(Mock(),rpc).launch(self.tab,str(uuid.uuid4()))
        argv=shlex.split(shlex.split(launch)[2].split(' && exec ',1)[1])
        self.assertEqual(argv,['codex','--sandbox','read-only','--ask-for-approval','on-request','resume',self.tab.session_id])
        self.assertNotIn('--yolo',argv)

    def test_saved_policies_survive_serialization_without_leaking_between_tabs(self):
        first=replace(self.tab,permissions=dict(sandbox='read-only',approval='on-request'))
        second=replace(self.tab,terminal_id='second',session_id=str(uuid.uuid4()),
                       permissions=dict(sandbox='danger-full-access',approval='never'))
        window=Window.decode(json.loads(json.dumps(Window('window',(first,second)).encode())))
        self.assertEqual(launch_flags(window.tabs[0]),['--sandbox','read-only','--ask-for-approval','on-request'])
        self.assertEqual(launch_flags(window.tabs[1]),['--sandbox','danger-full-access','--ask-for-approval','never'])
        self.assertEqual(launch_flags(self.tab),['--sandbox','read-only','--ask-for-approval','on-request'])

    def test_unknown_claude_permissions_resume_in_plan_mode(self):
        tab=replace(self.tab,provider='claude')
        provider=Claude(Mock());provider.saved=Mock()
        launch=provider.launch(tab,str(uuid.uuid4()))
        argv=shlex.split(shlex.split(launch)[2].split(' && exec ',1)[1])
        self.assertEqual(argv,['claude','--permission-mode','plan','--resume',tab.session_id])
        self.assertNotIn('--dangerously-skip-permissions',argv)

    def test_only_that_claude_tabs_saved_mode_is_used(self):
        tab=replace(self.tab,provider='claude',permissions=dict(mode='bypassPermissions'))
        self.assertEqual(launch_flags(tab),['--permission-mode','bypassPermissions'])
        self.assertEqual(launch_flags(replace(tab,permissions={})),['--permission-mode','plan'])

    def test_unknown_or_malformed_policy_never_grants_full_access(self):
        for value in ({'sandbox':{}},{'sandbox':'unknown','approval':'never'},{'mode':{}},{'mode':'future'}):
            self.assertEqual(launch_flags(replace(self.tab,permissions=value)),['--sandbox','read-only','--ask-for-approval','on-request'])
            self.assertEqual(launch_flags(replace(self.tab,provider='claude',permissions=value)),['--permission-mode','plan'])

    def test_ui_distinguishes_unknown_permissions_and_saved_full_access(self):
        self.assertIn('Restricted on reopen',label(self.tab))
        self.assertIn('Full access',label(replace(self.tab,permissions=dict(sandbox='danger-full-access',approval='never'))))
