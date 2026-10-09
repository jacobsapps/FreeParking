#!/usr/bin/env python3
"""Explicit live proof for ONE disposable folder window. No production Park All.

Requires process inspection and iTerm Automation in the execution environment.
Never use another transport to bypass a denied permission. Close confirmations
are left for the user; a cancellation stops the run and preserves all recovery.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources'))
from test_safety import Scope, ScopedProcesses, ScopedTerminal, write_private
from parking.adapters import ITerm, Processes
from parking.model import ParkingError
from parking.providers import Shell
from parking.service import compose
from parking.testing import FixtureTerminal, fixture_tab


def wait_for(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.3)
    raise ParkingError('Live proof deadline expired. Recovery is retained; no broader cleanup will be attempted.')


def run(directory, cycles):
    directory = Path(directory).resolve()
    if directory.exists():
        raise ParkingError('Use a new proof directory; never overwrite a protective baseline.')
    directory.mkdir(mode=0o700, parents=True)
    terminal, processes = ITerm(), Processes()
    scope = Scope.capture(directory/'scope.json', terminal, processes, ['fixture-init-only'])
    # Process inspection and baseline persistence succeeded before the first UI action.
    (directory/'fixture').mkdir()
    fixture = FixtureTerminal(scope, ScopedTerminal(scope, terminal))
    result = dict(status='incomplete', completed_cycles=0, protected_window_count=len(scope.protected_windows),
                  scope=str(scope.path), error=None)
    write_private(directory/'result.json', result)
    try:
        command = Shell(None).launch(fixture_tab(scope), str(uuid.uuid4()))
        receipt = fixture.call('create', windowId=None, sessionIds=[], command=command)
        for cycle in range(cycles):
            # Fresh composition simulates relaunch between operations. The same
            # production workflow runs behind a strictly scoped outer boundary.
            store, observer, workflow = compose(scope_path=scope.path)
            def identified():
                observation = observer.observe()
                return observation.window(receipt['windowId'])
            window = wait_for(identified)
            record = workflow.park(window.window_id, window.fingerprint)
            if record['phase'] != 'parked':
                raise ParkingError(record.get('issue') or 'The disposable window did not park.')
            scope = Scope(scope.path)
            scope.audit(terminal.windows())
            # New process-equivalent composition must find durable recovery.
            store, observer, workflow = compose(scope_path=scope.path)
            record = workflow.reopen(record['id'])
            def returned():
                checked = workflow.reconcile(record['id'])
                return checked if checked['phase'] == 'returned' else None
            record = wait_for(returned)
            target = record['targets']['0']
            receipt = dict(windowId=target['window_id'], sessionId=target['terminal_id'])
            scope = Scope(scope.path)
            scope.audit(terminal.windows())
            result['completed_cycles'] = cycle + 1
            write_private(directory/'result.json', result)
        # Leave the one registered fixture open for visual inspection. Never
        # perform broad cleanup or remove the user's existing windows.
        result['status'] = 'metadata_round_trips_passed_visual_review_pending'
        result['remaining_fixture'] = receipt
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        write_private(directory/'result.json', result)
    print('Scoped metadata round trips passed. Review the single remaining disposable fixture; working windows were not targeted.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True, help='Explicitly authorize creating the disposable fixture.')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--cycles', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 50:
        parser.error('Use 1–50 cycles.')
    run(args.directory, args.cycles)
