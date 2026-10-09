#!/usr/bin/env python3
"""Explicit cleanup of receipt-proven disposable windows, never a live-test reset.

Old baselines remain untouched. A fresh cleanup plan protects every current
window not named in original create receipts, and the invoking process ancestry.
No creation, terminal input, agent signals, settings, or broad cleanup capability.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources'))
from test_safety import Scope, session_ids
from parking.adapters import ITerm, Processes
from parking.model import ParkingError
from parking.store import atomic_json


class Cleanup:
    def __init__(self, scopes, directory, terminal, processes):
        self.terminal, self.processes = terminal, processes
        self.directory = Path(directory)
        if self.directory.exists(): raise ParkingError('Never overwrite a cleanup plan.')
        self.directory.mkdir(parents=True, mode=0o700)
        raw, ps = terminal.windows(), processes.all()
        ancestry, pid = set(), os.getpid()
        while pid in ps and pid not in ancestry:
            ancestry.add(pid); pid = ps[pid]['ppid']
        protected_ttys = {ps[pid]['tty'] for pid in ancestry}
        receipts = {}
        for path in scopes:
            scope = Scope(path)  # Validate historical ownership, not an edited baseline.
            for wid, ids in scope.data['owned'].items():
                receipts.setdefault(wid, []).append(dict(path=str(scope.path.resolve()), ids=ids,
                    sha256=hashlib.sha256(scope.path.read_bytes()).hexdigest()))
        targets, protected = {}, {}
        for w in raw:
            ids = session_ids(w)
            ttys = {s['tty'] for t in w['tabs'] for s in t['sessions']}
            matches = [r for r in receipts.get(w['id'], []) if r['ids'] == ids]
            if matches and not ttys.intersection(protected_ttys) and all(len(t['sessions']) == 1 for t in w['tabs']):
                targets[w['id']] = dict(ids=ids, receipts=matches,
                    processes=[dict(p) for p in ps.values() if p['tty'] in ttys], state='planned')
            else: protected[w['id']] = ids
        self.plan = dict(protected=protected, targets=targets, complete=False)
        self.save()

    def save(self): atomic_json(self.directory/'cleanup.json', self.plan)

    def audit(self):
        raw = self.terminal.windows()
        current = {w['id']:session_ids(w) for w in raw}
        if any(current.get(wid) != ids for wid,ids in self.plan['protected'].items()):
            raise ParkingError('A protected current window changed. Cleanup stopped.')
        return raw

    def authorize(self, wid):
        target = self.plan['targets'].get(wid)
        if not target: raise ParkingError('Cleanup cannot target an unowned window.')
        raw = self.audit(); window = next((w for w in raw if w['id'] == wid), None)
        if not window or session_ids(window) != target['ids']:
            raise ParkingError('Disposable window changed or disappeared. No repeated close.')
        for receipt in target['receipts']:
            if hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest() != receipt['sha256']:
                raise ParkingError('Original ownership receipt changed. Cleanup stopped.')
        ps = self.processes.all()
        ttys = {s['tty'] for t in window['tabs'] for s in t['sessions']}
        births = {(p['pid'],p['started']) for p in target['processes']}
        if any((p['pid'],p['started']) not in births for p in ps.values() if p['tty'] in ttys):
            raise ParkingError('A new process appeared in the disposable window. Cleanup stopped.')
        apps = [p['pid'] for p in ps.values() if p['executable'].endswith('/iTerm.app/Contents/MacOS/iTerm2')]
        if len(apps) != 1: raise ParkingError('Cannot identify exactly one iTerm process.')
        return apps[0]

    def run(self, helper):
        for wid,target in self.plan['targets'].items():
            self.authorize(wid)
            target['state'] = 'close-submitted'; self.save()
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.terminal.call,'close',windowId=wid,sessionIds=target['ids'])
                answered = False
                while not result.done():
                    if not answered and any(w['id'] == wid for w in self.audit()):
                        pid = self.authorize(wid)
                        inspected = subprocess.run([str(helper),str(pid),wid,'inspect'],capture_output=True,text=True,timeout=5)
                        if inspected.returncode: raise ParkingError(inspected.stderr)
                        if inspected.stdout.strip() == 'close-confirmation':
                            self.authorize(wid)
                            pressed = subprocess.run([str(helper),str(pid),wid,'OK'],capture_output=True,text=True,timeout=5)
                            if pressed.returncode or pressed.stdout.strip() != 'responded-OK':
                                raise ParkingError('Owned close confirmation not acknowledged.')
                            answered = True
                    time.sleep(.2)
                result.result()
            after = self.audit()
            if any(w['id'] == wid for w in after): raise ParkingError('Owned window remained open. No repeated close.')
            target['state'] = 'closed'; self.save()
            print('Closed one receipt-proven disposable window.', flush=True)
        self.audit(); self.plan['complete'] = True; self.save()
        print('Cleanup complete. Protected working windows unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--receipts',type=Path,nargs='+',required=True)
    args = parser.parse_args()
    Cleanup(args.receipts,args.directory,ITerm(),Processes()).run(Path(__file__).resolve().parents[1]/'.build/rebuild-close-confirmation')
