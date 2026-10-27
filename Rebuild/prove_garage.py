#!/usr/bin/env python3
"""Scoped acceptance through the actual production garage backend subprocess.

Only --run creates disposable sessions/windows. A fresh protective baseline is
mandatory. No installed UI claim: this exercises the selected bundle's helper,
including Park All, durable recovery and the same bounded check calls as Swift.
Fixture setup defers identity settling until all exact owned create receipts
are saved; parking/restoration still use the unmodified production entrypoint.
Normal owned-window close sheets are approved by a separate test-only AX driver.
No test-only mutation API is added to the production helper.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Resources'))
from test_safety import Scope, ScopedTerminal, write_private
from parking.adapters import ITerm, Processes
from parking.agent_testing import AgentSeeder
from parking.confirmation_testing import ConfirmingTerminal
from parking.lab import Lab
from parking.model import ParkingError
from parking.service import compose
from parking.store import Store, atomic_json


def complete_request(invoke, arguments, checks=2):
    """Mirror OperationCompletion.call: observe exact record, never replay writes."""
    reply = invoke(arguments)
    if arguments[0] not in ('park', 'park-all', 'park-confirmed', 'restore', 'check', 'finish-parking'):
        return reply
    record_id = reply.get('operation_id')
    if not record_id: return reply
    for _ in range(checks):
        if reply.get('operation_status') != 'checking': break
        reply = invoke(['check', record_id])
    return reply


class BundleBackend:
    def __init__(self, backend, scope, helper, approve_confirmations=True, profile=False):
        self.backend, self.scope, self.helper = Path(backend), scope, Path(helper)
        self.count = 0
        self.responses = []
        self.approved = set()
        self.approve_confirmations = approve_confirmations
        self.profile = profile

    def authorize_pending(self, driver, terminal, request):
        try:
            return driver.authorize(request)
        except ParkingError:
            # Normal close may complete between the first inventory and this
            # authorization. Absence only cancels a test click; it never grants
            # permission to target another sheet or skip the production proof.
            raw = terminal.windows()
            Scope(self.scope.path).audit(raw)
            present = {s['id'] for w in raw for t in w['tabs'] for s in t['sessions']}
            if not (set(request['sessionIds']) & present): return None
            raise

    def approve_owned_sheet(self):
        """Approve a pending sheet only; never submit a close ourselves."""
        if not self.approve_confirmations:
            return  # Respect user-managed iTerm settings; no AX access needed.
        scope = Scope(self.scope.path)
        terminal, processes = ITerm(), Processes()
        raw = terminal.windows()
        scope.audit(raw)
        present = {w['id'] for w in scope.owned_windows(raw)}
        driver = ConfirmingTerminal(scope, terminal, processes, 'OK', self.helper)
        for record in Store(scope.path.parent / 'rebuild-garage').all():
            if record['phase'] != 'closing' or record['intent'] != {'kind': 'close'}: continue
            source = record['source']
            if source['window_id'] not in present: continue
            request = dict(windowId=source['window_id'], sessionIds=[t['terminal_id'] for t in source['tabs']])
            key = (record['id'], source['window_id'], tuple(request['sessionIds']))
            if key in self.approved: continue
            pid = self.authorize_pending(driver, terminal, request)
            if pid is None: continue
            inspected = subprocess.run([str(self.helper), str(pid), request['windowId'], 'inspect'],
                                       capture_output=True, text=True, timeout=5)
            if inspected.returncode:
                raise ParkingError('Owned confirmation inspection failed: ' + inspected.stderr.strip())
            if inspected.stdout.strip() != 'close-confirmation': continue
            # Recheck full scope immediately before button press.
            if self.authorize_pending(driver, terminal, request) is None: continue
            responded = subprocess.run([str(self.helper), str(pid), request['windowId'], 'OK'],
                                       capture_output=True, text=True, timeout=5)
            if responded.returncode or responded.stdout.strip() != 'responded-OK':
                raise ParkingError('Owned confirmation response is unconfirmed; no second click.')
            self.approved.add(key)
            write_private(scope.path.parent / 'confirmations' / (str(time.time_ns()) + '.json'),
                          dict(**request, response='OK', result=responded.stdout.strip()))

    def invoke(self, arguments):
        self.count += 1
        directory = self.scope.path.parent / 'production-calls'
        directory.mkdir(exist_ok=True, mode=0o700)
        prefix = directory / str(self.count)
        # Scope code is external to the release bundle. Inject it only into
        # this supervised helper process, never the installed app.
        launcher = self.scope.path.parent/'bundle-test-launcher.py'
        launcher.write_text('import sys, importlib.util\n'
            + 'sys.path.insert(0, '+repr(str(ROOT/'Resources'))+')\n'
            + 'sys.path.insert(0, '+repr(str(self.backend.parent))+')\n'
            + 'spec=importlib.util.spec_from_file_location("parking.testing", '+repr(str(ROOT/'Rebuild/parking/testing.py'))+')\n'
            + 'module=importlib.util.module_from_spec(spec); sys.modules["parking.testing"]=module; spec.loader.exec_module(module)\n'
            + 'from parking.garage import main\nmain()\n')
        command = ['/usr/bin/python3', str(launcher), '--test-scope', str(self.scope.path), *arguments]
        if self.profile:
            command[1:1] = ['-m', 'cProfile', '-o', str(prefix.with_suffix('.pstats'))]
        atomic_json(prefix.with_suffix('.request.json'), dict(arguments=arguments, backend=str(self.backend)))
        confirmation_error = None
        with prefix.with_suffix('.stdout').open('w') as stdout, prefix.with_suffix('.stderr').open('w') as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            # A twenty-tab Park All includes several bounded native shutdowns.
            # This is only the test supervisor budget, not a retry or extra completion loop.
            deadline = time.monotonic() + 1800
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    # Only the exact helper subprocess created above. Its durable
                    # intent remains uncertain; never kill iTerm or agent clients.
                    child.terminate(); child.wait(timeout=10)
                    raise ParkingError('Owned backend deadline expired. Recovery is retained; do not repeat the request.')
                if arguments[0] in ('park', 'park-all', 'park-confirmed') and confirmation_error is None:
                    try: self.approve_owned_sheet()
                    except Exception as error: confirmation_error = error
                time.sleep(0.2)
        Scope(self.scope.path).audit(ITerm().windows())
        if confirmation_error: raise confirmation_error
        if child.returncode: raise ParkingError('Production helper failed; inspect its private stderr artifact.')
        reply = json.loads(prefix.with_suffix('.stdout').read_text())
        self.responses.append(reply)
        return reply


def continuation_counts(agents):
    """Count each conversation separately; multiple agents of one type are distinct."""
    from parking.agents import Claude
    from parking.codex_rpc import CodexRPC
    counts = {}
    for agent in agents:
        sid = agent['session_id']
        if agent['provider'] == 'claude':
            entries = [json.loads(line) for line in Claude(None).saved(sid, agent['cwd']).read_text().splitlines()]
            counts[sid] = sum(e.get('type') == 'user' and e.get('message', {}).get('content') == 'please continue' for e in entries)
        else:
            page = CodexRPC().call('thread/items/list', dict(threadId=sid, limit=100, sortDirection='asc'))
            if page.get('nextCursor'): raise ParkingError('Fixture transcript exceeds bounded count; no partial proof.')
            counts[sid] = sum(e['item']['type'] == 'userMessage' and
                [c.get('text') for c in e['item']['content']] == ['please continue'] for e in page['data'])
    return counts


def run(directory, backend, windows=4, tabs=5, busy=False, allow_protected_additions=False, approve_confirmations=True):
    directory, backend = Path(directory).resolve(), Path(backend).resolve()
    if directory.exists(): raise ParkingError('Use a new directory; never replace a protective baseline.')
    if not backend.is_file(): raise ParkingError('Build the production bundle first; backend is missing.')
    helper = ROOT / '.build/rebuild-close-confirmation'
    if approve_confirmations and not helper.is_file(): raise ParkingError('Build the scoped confirmation helper first.')
    directory.mkdir(parents=True, mode=0o700)
    scope = Scope.capture(directory / 'scope.json', ITerm(), Processes(), ['fixture-init-only'],
                          allow_protected_additions=allow_protected_additions)
    runner = BundleBackend(backend, scope, helper, approve_confirmations)
    report = dict(status='incomplete', allow_protected_additions=allow_protected_additions, approve_confirmations=approve_confirmations, evidence='Production bundle helper, not installed UI acceptance',
                  backend=str(backend), backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(),
                  module_hashes={str(p.relative_to(backend.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (backend.parent/'parking').glob('*') if p.is_file()},
                  fixtures=[], operations=[], error=None)
    def save(): atomic_json(directory / 'acceptance.json', report)
    def request(args):
        result = complete_request(runner.invoke, args)
        report['operations'].append(dict(arguments=args, status=result.get('operation_status'), ok=result.get('ok')))
        save()
        if not result.get('ok') or result.get('operation_status') != 'completed':
            raise ParkingError(result.get('error') or result.get('message') or 'Production operation did not complete; no test-only reconciliation.')
        return result
    save()
    try:
        for _ in range(windows):
            fresh = Scope(scope.path)
            lab = Lab(fresh, ScopedTerminal(fresh, ITerm()), lambda: compose(scope_path=scope.path), AgentSeeder(fresh))
            fixture = lab.create(tabs, 'mixed', busy=busy, settle_each=False)
            report['fixtures'].append(fixture); save()
        agents = [a for f in report['fixtures'] for a in f['agents']]
        before = continuation_counts(agents)
        parked = request(['park-all'])
        if len(parked['cars']) != windows or any(c['status'] != 'parked' for c in parked['cars']):
            raise ParkingError('Production Park All did not park exactly the owned fixtures.')
        store = Store(directory / 'rebuild-garage')
        expected = dict(before)
        for record in store.all():
            for index, outcome in record.get('interruptions', {}).items():
                if outcome['outcome'] == 'interrupted':
                    expected[record['source']['tabs'][int(index)]['session_id']] += 1
        for car in parked['cars']: request(['restore', car['id']])
        records = store.all()
        if len(records) != windows or any(r['phase'] != 'returned' or len(r['targets']) != tabs for r in records):
            raise ParkingError('Exact per-window restored receipts are incomplete.')
        # Read-only transcript settling does not improve lifecycle status.
        deadline = time.monotonic() + 30
        while True:
            counts = continuation_counts(agents)
            if counts == expected or time.monotonic() >= deadline: break
            time.sleep(0.3)
        if counts != expected: raise ParkingError('Native continuation counts disagree with interruption receipts.')
        report.update(status='passed', records=records, continuation_counts=counts,
                      protected_windows=Scope(scope.path).audit(ITerm().windows()))
    except Exception as error:
        report['error'] = str(error)
        raise
    finally: save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--windows', type=int, default=4)
    parser.add_argument('--tabs', type=int, default=5)
    parser.add_argument('--busy', action='store_true')
    parser.add_argument('--allow-protected-additions', action='store_true',
        help='New scope only: permit additional user tabs while preserving every original protected tab.')
    parser.add_argument('--no-confirmation-helper', action='store_true',
        help='Do not drive close sheets. Use when the user has already disabled iTerm close prompts; never changes preferences.')
    args = parser.parse_args()
    if not 1 <= args.windows <= 4 or not 2 <= args.tabs <= 8: parser.error('Use 1–4 windows and 2–8 tabs.')
    run(args.directory, args.backend, args.windows, args.tabs, args.busy, args.allow_protected_additions, not args.no_confirmation_helper)
    print('Production helper acceptance passed. Owned fixture windows retained for scoped cleanup; installed UI not claimed.')
