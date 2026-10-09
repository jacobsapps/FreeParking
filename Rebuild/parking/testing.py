"""An outer, independently tested boundary for the disposable proving slice.

Reuses the safety shim, NOT the September/October application logic. Its
baseline protects today's working windows and the supervising agent.
"""
import base64
from pathlib import Path
import re
import uuid
from .model import ParkingError, Process, Tab
from .providers import Shell
from .agents import Claude, Codex
from .codex_rpc import CodexRPC


def fixture_tab(scope, folder=None):
    folder = folder or str(scope.path.parent / 'fixture')
    return Tab('fixture', '/dev/fixture', 'Disposable folder', 'shell', '', folder,
               Process(1, 'fixture', '/dev/fixture', '/bin/zsh'))


class FixtureTerminal:
    def __init__(self, scope, scoped_terminal):
        self.scope, self.scoped = scope, scoped_terminal

    def windows(self):
        return self.scoped.windows()

    def authorize_provider(self, source):
        from test_safety import Scope
        fresh = Scope(self.scope.path)
        fresh.audit(self.scoped.terminal.windows())
        if (self.scope.journal_failed or fresh.data.get('pending_create')
                or fresh.data['owned'].get(source.window_id) != [t.terminal_id for t in source.tabs]):
            raise ParkingError('Native provider mutation does not belong to the exact disposable fixture.')
        registered = {(a['provider'],a['session_id'],a['cwd']) for a in fresh.data.get('fixture_agents',[])}
        if any(t.provider != 'shell' and (t.provider,t.session_id,t.cwd) not in registered for t in source.tabs):
            raise ParkingError('Provider mutation targets an unregistered conversation.')

    def call(self, action, **kwargs):
        if action == 'create':
            command = kwargs.get('command', '')
            matches = re.findall(r'freeParkingReceipt=([A-Za-z0-9+/=]+)', command)
            if len(matches) != 1:
                raise ParkingError('Fixture launches require one machine receipt.')
            token = base64.b64decode(matches[0], validate=True).decode()
            uuid.UUID(token)
            folders = self.scope.data.get('fixture_folders', [] if self.scope.data.get('fixture_agents') else [str(self.scope.path.parent / 'fixture')])
            root = self.scope.path.parent.resolve()
            if (not folders and not self.scope.data.get('fixture_agents')) or any(root not in Path(folder).resolve().parents for folder in folders):
                raise ParkingError('Fixture folders must remain inside the isolated test directory.')
            expected = [Shell(None).launch(fixture_tab(self.scope, folder), token) for folder in folders]
            for agent in self.scope.data.get('fixture_agents', []):
                if root not in Path(agent['cwd']).resolve().parents:
                    raise ParkingError('Agent fixture folder escaped its scope.')
                tab = Tab('fixture','/dev/fixture','Disposable agent',agent['provider'],agent['session_id'],agent['cwd'],
                          Process(1,'fixture','/dev/fixture','/bin/'+agent['provider']))
                provider = Claude(None) if agent['provider']=='claude' else Codex(None,CodexRPC())
                expected.append(provider.launch(tab, token))
                if agent.get('initial_prompt'):
                    expected.append(provider.launch(tab, token, prompt=agent['initial_prompt']))
                from .settings import Settings
                expected.append(provider.launch(tab, token, prompt=Settings(root/'rebuild-garage').read()['continue_message']))
            if command not in expected:
                raise ParkingError('Only the dedicated disposable folder command is permitted.')
            if command not in self.scope.data['launch_commands']:
                self.scope.data['launch_commands'].append(command)
                self.scope.save()
        return self.scoped.call(action, **kwargs)
