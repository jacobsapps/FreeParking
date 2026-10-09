"""Provider contracts are independent from terminal display labels.

Shell, Claude and Codex adapters share exact identity and launch contracts.
There is deliberately no title-matching fallback or pretend-success implementation.
"""
import base64
from pathlib import Path
import shlex
import uuid
from .model import ParkingError, Process, Tab


class Shell:
    name = 'shell'

    def __init__(self, processes):
        self.processes = processes

    def identify(self, session, processes):
        attached = [p for p in processes.values() if p['tty'] == session['tty']]
        shells = [p for p in attached if Path(p['executable']).name.lstrip('-') == 'zsh']
        helpers = [p for p in attached if Path(p['executable']).name == 'caffeinate']
        unexpected = [p for p in attached if Path(p['executable']).name.lstrip('-') not in ('zsh', 'login', 'caffeinate')]
        keep_awake = len(helpers) == 1 and len(shells) <= 1 and not unexpected
        if keep_awake:
            p = helpers[0]
            # Only the exact argument-free OS helper. No arbitrary shell program,
            # wrapped command, PID-wait target, or flags are inferred/replayed.
            if self.processes.executable(p['pid']) != '/usr/bin/caffeinate':
                raise ParkingError('The keep-awake process is not the system caffeinate executable.')
            if self.processes.command(p['pid']) not in ('caffeinate', '/usr/bin/caffeinate'):
                raise ParkingError('Only plain caffeinate can be saved; this helper has unsupported arguments.')
            if shells and p['ppid'] != shells[0]['pid']:
                raise ParkingError('The keep-awake helper is not this shell’s direct child.')
        else:
            if len(shells) != 1 or unexpected or helpers:
                raise ParkingError('This tab is neither an identified agent nor an idle zsh folder. Nothing will be guessed or closed.')
            p = shells[0]
        cwd = self.processes.cwd(p['pid'])
        if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ParkingError('Cannot confirm this shell’s working folder.')
        return Tab(session['id'], session['tty'], session.get('title', ''), 'shell', '', cwd,
                   Process(p['pid'], p['started'], p['tty'], p['executable'], p['ppid']),
                   shell_command='caffeinate' if keep_awake else '')

    def launch(self, tab, receipt):
        tab.validate()
        uuid.UUID(receipt)
        if tab.provider != self.name or not Path(tab.cwd).is_dir():
            raise ParkingError('The saved folder is missing or the provider changed.')
        encoded = base64.b64encode(receipt.encode()).decode()
        # This is the NEW process's command, never input typed into a terminal.
        # A machine receipt is separate from both titles and agent identity.
        body = ('cd ' + shlex.quote(tab.cwd) + " && printf '\\033]1337;SetUserVar=freeParkingReceipt="
                + encoded + "\\007' && exec " + ("/usr/bin/caffeinate" if tab.shell_command else "/bin/zsh -l"))
        return shlex.join(['/bin/zsh', '-lc', body])


class Providers:
    def __init__(self, *providers):
        self.items = {p.name: p for p in providers}
        if len(self.items) != len(providers):
            raise ParkingError('Duplicate provider registration.')

    def begin_observation(self):
        for provider in self.items.values():
            hook=getattr(provider,'begin_observation',None)
            if hook:hook()

    def end_observation(self):
        for provider in self.items.values():
            hook=getattr(provider,'end_observation',None)
            if hook:hook()

    def identify(self, session, processes):
        attached = [p for p in processes.values() if p['tty'] == session['tty']]
        agents = {('codex' if Path(p['executable']).name == 'codex' else 'claude')
                  for p in attached if Path(p['executable']).name in ('codex','claude') or '/claude/' in p['executable']}
        if len(agents) > 1: raise ParkingError('Multiple agent providers share this terminal.')
        name = next(iter(agents), 'shell')
        return self.get(name).identify(session, processes)

    def get(self, name):
        if name not in self.items:
            raise ParkingError('This provider has not passed its rebuild readiness gate: ' + name)
        return self.items[name]
