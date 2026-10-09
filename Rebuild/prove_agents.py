#!/usr/bin/env python3
"""Live mixed-agent acceptance using only new conversations and owned windows.

Keeps a fresh immutable protective baseline, normal iTerm confirmations, and
private evidence. Requires the test-only AX helper, local signed-in CLIs, and a
trusted parent directory. Stops on uncertainty; never repairs the baseline,
replays a create, changes trust settings, or cleans up working windows.
"""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources'))
from test_safety import Scope, ScopedTerminal
from parking.adapters import ITerm, Processes
from parking.agent_testing import AgentSeeder
from parking.agents import Claude
from parking.codex_rpc import CodexRPC
from parking.lab import Lab
from parking.model import ParkingError
from parking.service import compose
from parking.settings import Settings
from parking.store import atomic_json
from prove import wait_for


def continuation_counts(agents):
    """Read native persisted user messages, not launch commands or screen text."""
    counts = {}
    for agent in agents:
        sid = agent['session_id']
        if agent['provider'] == 'claude':
            entries = [json.loads(line) for line in Claude(None).saved(sid, agent['cwd']).read_text().splitlines()]
            counts['claude'] = sum(e.get('type') == 'user' and
                e.get('message', {}).get('content') == 'please continue' for e in entries)
        else:
            page = CodexRPC().call('thread/items/list', dict(threadId=sid, limit=100, sortDirection='asc'))
            if page.get('nextCursor'):
                raise ParkingError('Test transcript exceeds its bounded inspection. No partial count accepted.')
            counts['codex'] = sum(e['item']['type'] == 'userMessage' and
                [c.get('text') for c in e['item']['content']] == ['please continue'] for e in page['data'])
    return counts


def require(condition, message):
    if not condition: raise ParkingError(message)


def run(directory, cycles):
    directory = directory.resolve()
    require(not directory.exists(), 'Use a fresh directory; never overwrite a protective baseline.')
    directory.mkdir(parents=True, mode=0o700)
    scope = Scope.capture(directory / 'scope.json', ITerm(), Processes(), ['fixture-init-only'])
    def composer(response='OK'):
        return compose(scope_path=scope.path, test_close_response=response)
    lab = Lab(scope, ScopedTerminal(scope, ITerm()), composer, AgentSeeder(scope))
    settings = Settings(directory / 'rebuild-garage')
    before_settings = settings.read()
    report = dict(status='incomplete', cycles=[], cancel=None, settings_off=None,
                  visual_review='not performed', error=None)
    def save(): atomic_json(directory / 'acceptance.json', report)
    def audited(): return Scope(scope.path).audit(ITerm().windows())
    def wait(predicate): return wait_for(predicate, seconds=60)
    def wait_idle(fixture):
        def idle():
            _, observer, _ = composer()
            window = observer.observe().window(fixture['window_id'])
            return window and all(t.activity == 'idle' for t in window.tabs)
        wait(idle)
    def require_busy(fixture):
        _, observer, _ = composer()
        window = observer.observe().window(fixture['window_id'])
        require(window and [t.activity for t in window.tabs] == ['running', 'running'],
                'Both disposable tasks must still be running to prove interruption.')
    save()
    try:
        settings.save(dict(before_settings, auto_continue_interrupted=True, continue_message='please continue'))
        fixture = lab.create(2, 'mixed', busy=True)
        require_busy(fixture)
        for cycle in range(cycles):
            if cycle: wait_idle(fixture)
            started = time.monotonic()
            fixture = lab.round_trip(fixture['window_id'], wait)
            store, _, workflow = composer()
            record = store.load(fixture['record_id'])
            require(record['phase'] == 'returned', 'Returned identities are not verified.')
            if not cycle:
                require([record['interruptions'][str(i)]['outcome'] for i in range(2)] == ['interrupted'] * 2,
                        'Both interruptions need native confirmation.')
            else:
                require(not record.get('interruptions'), 'An idle cycle must not authorize continuation.')
            wait(lambda: continuation_counts(fixture['agents']) == dict(claude=1, codex=1))
            workflow.reopen(record['id'])
            require(continuation_counts(fixture['agents']) == dict(claude=1, codex=1), 'Retry duplicated continuation.')
            report['cycles'].append(dict(cycle=cycle + 1, record=record,
                continue_counts=continuation_counts(fixture['agents']),
                protected_windows=audited(), seconds=round(time.monotonic() - started, 2)))
            save(); print('Mixed cycle', cycle + 1, 'passed.', flush=True)
        wait_idle(fixture)
        _, observer, workflow = composer('Cancel')
        window = observer.observe().window(fixture['window_id'])
        before = [t.terminal_id for t in window.tabs]
        record = workflow.park(window.window_id, window.fingerprint)
        require(record['phase'] == 'saved', 'Cancelled close must leave the source open.')
        require([t.terminal_id for t in observer.observe().window(window.window_id).tabs] == before,
                'Cancelled close changed original tabs.')
        workflow.reopen(record['id'])
        require(continuation_counts(fixture['agents']) == dict(claude=1, codex=1), 'Cancellation sent a message.')
        report['cancel'] = dict(original_tabs_retained=True, no_extra_continuation=True, protected_windows=audited())
        save(); print('Mixed close cancellation passed.', flush=True)
        settings.save(dict(before_settings, auto_continue_interrupted=False, continue_message='please continue'))
        fixture_off = lab.create(2, 'mixed', busy=True)
        require_busy(fixture_off)
        fixture_off = lab.round_trip(fixture_off['window_id'], wait)
        store, _, _ = composer()
        record = store.load(fixture_off['record_id'])
        require(record['phase'] == 'returned' and
                [record['interruptions'][str(i)]['outcome'] for i in range(2)] == ['interrupted'] * 2,
                'Settings-off case must actually interrupt and restore both agents.')
        require(continuation_counts(fixture_off['agents']) == dict(claude=0, codex=0), 'Disabled setting still sent continuation.')
        report['settings_off'] = dict(record=record, continue_counts=continuation_counts(fixture_off['agents']))
        report.update(status='passed', protected_windows=audited())
        print('Mixed acceptance passed; owned fixtures left open for inspection.', flush=True)
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        settings.save(before_settings)
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--cycles', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 50: parser.error('Use 1–50 cycles.')
    run(args.directory, args.cycles)
