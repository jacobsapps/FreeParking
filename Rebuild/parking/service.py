"""Composition root and JSON transport. The app starts disk-only, not by scanning."""
from pathlib import Path
import json
import sys
import uuid
from .adapters import ITerm, Processes
from .model import ParkingError, Phase
from .providers import Providers, Shell
from .agents import Claude, Codex
from .codex_rpc import CodexRPC
from .settings import Settings
from .safety import Gate
from .store import Store
from .verifier import Observer
from .workflow import Workflow
from .operations import Operations

DEFAULT_ROOT = Path.home() / 'Library/Application Support/Free Parking/Recovery-v2'


def compose(root=DEFAULT_ROOT, scope_path=None, test_close_response=None, live=False):
    terminal, processes = ITerm(), Processes()
    if scope_path:
        # Safety package is bundled separately and cannot import legacy workflow.
        from test_safety import Scope, ScopedTerminal, ScopedProcesses
        from .testing import FixtureTerminal
        scope = Scope(scope_path)
        root = scope.path.parent / 'rebuild-garage'
        old = Path.home() / 'Library/Application Support/Car Park'
        if root.resolve() == old.resolve() or old.resolve() in root.resolve().parents:
            raise ParkingError('The rebuild cannot use the production garage.')
        if test_close_response:
            from .confirmation_testing import ConfirmingTerminal
            helper = Path(__file__).resolve().parents[2] / ".build/rebuild-close-confirmation"
            terminal = ConfirmingTerminal(scope, terminal, processes, test_close_response, helper)
        outer = ScopedTerminal(scope, terminal)
        processes = ScopedProcesses(scope, outer, processes)
        terminal = FixtureTerminal(scope, outer)
    store = Store(root)
    providers = Providers(Shell(processes), Claude(processes), Codex(processes, CodexRPC()))
    observer = Observer(terminal, processes, providers)
    gate = Gate(store, observer, terminal, providers, mutations_enabled=bool(scope_path) or live, settle=observer.observe_until)
    return store, observer, Workflow(store, observer, gate)


def dispatch(arguments, root=DEFAULT_ROOT):
    scope_path = None
    live = arguments[:1] == ['--live']
    if live: arguments = arguments[1:]
    if arguments[:1] == ['--scope']:
        scope_path = Path(arguments[1]).resolve()
        arguments = arguments[2:]
    if not arguments:
        arguments = ['list']
    store, observer, workflow = compose(root, scope_path, live=live)
    reply = dict(ok=True, windows=[], records=[], errors=[], test_mode=bool(scope_path), can_mutate=bool(scope_path) or live,
                 capabilities=dict(shell='live-tested', codex='native endpoint identity and archive/resume live-tested', claude='native process identity and interrupted resume live-tested'), settings=Settings(store.root).read())
    try:
        action = arguments[0]
        if action == 'settings':
            if len(arguments) == 2:
                reply['settings'] = Settings(store.root).save(json.loads(arguments[1]))
        elif action == 'list':
            pass
        elif action == 'scan':
            observation = observer.observe()
            reply['windows'] = [dict(**w.encode(), fingerprint=w.fingerprint) for w in observation.windows]
            reply['errors'] = [{'window_id':wid, 'message':message} for wid, message in observation.issues.items()]
            for record in store.all():
                if record['phase'] != Phase.RETURNED.value:
                    try:
                        workflow.reconcile(record['id'],read_only=True)
                    except Exception as error:
                        reply['errors'].append(dict(record_id=record['id'], message=str(error)))
        elif action in ('park', 'reopen'):
            if not scope_path and not live:
                raise ParkingError('Mutations are gated off without an explicit live-app or disposable-scope capability.')
            if action == 'park' and len(arguments) == 3:
                outcome=Operations(workflow).park(arguments[1], arguments[2])
            elif action == 'reopen' and len(arguments) == 2:
                outcome=Operations(workflow).reopen(arguments[1])
            else:
                raise ParkingError('Invalid operation arguments.')
            reply['operation_status']=outcome.status
            if outcome.status=='needs_action':reply.update(ok=False,error=outcome.detail)
            elif outcome.status=='checking':reply['message']=outcome.detail
        else:
            raise ParkingError('Unknown rebuild operation.')
    except Exception as error:
        reply.update(ok=False, error=str(error))
    reply['records'] = store.all()
    return reply


def main():
    try:
        reply = dispatch(sys.argv[1:])
    except Exception as error:
        reply = dict(ok=False, windows=[], records=[], errors=[], error=str(error), test_mode=False)
    print(json.dumps(reply))


if __name__ == '__main__':
    main()
