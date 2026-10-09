"""Own-fixture confirmation driver. Never installed as an app capability.

Runs only when the lab explicitly supplies a response. Approval requires the
independent scope, exact live tab order, and a durable close intent. It presses
the ordinary iTerm button; no forced-close API or preference changes.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import time
import uuid
from .model import ParkingError, Window
from .store import Store


class ConfirmingTerminal:
    def __init__(self, scope, terminal, processes, response, helper, runner=subprocess.run):
        if response not in ('OK', 'Cancel'):
            raise ParkingError('Unsupported test response.')
        self.scope, self.terminal, self.processes = scope, terminal, processes
        self.response, self.helper, self.runner = response, Path(helper), runner
        if not self.helper.is_file():
            raise ParkingError('Compile the disposable confirmation helper before testing.')

    def windows(self):
        return self.terminal.windows()

    def authorize(self, request):
        # Reload: never overwrite another helper's ownership journal.
        from test_safety import Scope, session_ids
        scope = Scope(self.scope.path)
        raw = self.terminal.windows()
        scope.audit(raw)
        wid, ids = request['windowId'], request['sessionIds']
        owned = {w['id']: w for w in scope.owned_windows(raw)}
        if wid not in owned or session_ids(owned[wid]) != ids or scope.data.get('pending_create'):
            raise ParkingError('Confirmation target is not the exact owned fixture.')
        records = Store(scope.path.parent/'rebuild-garage').all()
        matching = [r for r in records if r['phase'] == 'closing' and r['intent'] == {'kind':'close'}
                    and r['source']['window_id'] == wid
                    and [t['terminal_id'] for t in r['source']['tabs']] == ids]
        if len(matching) != 1:
            raise ParkingError('No unique durable close intent for this fixture.')
        current = self.processes.all()
        for tab in Window.decode(matching[0]['source']).tabs:
            p = current.get(tab.process.pid)
            if not p or p['started'] != tab.process.started or p['tty'] != tab.tty:
                raise ParkingError('Fixture process changed before confirmation.')
        applications = [p for p in current.values() if p['executable'].endswith('/iTerm.app/Contents/MacOS/iTerm2')]
        if len(applications) != 1:
            raise ParkingError('Cannot identify one iTerm application process.')
        return applications[0]['pid']

    def call(self, action, **request):
        if action != 'close':
            return self.terminal.call(action, **request)
        self.authorize(request)  # Refuse before even submitting a close request.
        responded = False
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(self.terminal.call, action, **request)
            while not closing.done():
                if not responded:
                    pid = self.authorize(request)
                    result = self.runner([str(self.helper), str(pid), request['windowId'], 'inspect'],
                                         capture_output=True, text=True, timeout=5)
                    if result.returncode:
                        raise ParkingError(result.stderr.strip() or 'Test AX inspection failed.')
                    if result.stdout.strip() == 'close-confirmation':
                        self.authorize(request)  # Recheck the complete scope after inspection.
                        result = self.runner([str(self.helper), str(pid), request['windowId'], self.response],
                                             capture_output=True, text=True, timeout=5)
                        if result.returncode or result.stdout.strip() != 'responded-' + self.response:
                            raise ParkingError('Own-fixture confirmation was not acknowledged.')
                        from test_safety import write_private
                        write_private(self.scope.path.parent / "confirmations" / (str(uuid.uuid4()) + ".json"),
                                      dict(window_id=request["windowId"], session_ids=request["sessionIds"],
                                           response=self.response, result=result.stdout.strip(), at=time.time()))
                        responded = True
                time.sleep(0.2)
            return closing.result()
