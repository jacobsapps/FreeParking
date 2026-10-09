#!/usr/bin/env python3
"""Select owned fixtures for live proofs; never operate on working windows."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Resources'))
from test_safety import Scope, ScopedTerminal
from parking.adapters import ITerm, Processes
from parking.lab import Lab
from parking.service import compose
from prove import wait_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',type=Path,required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('init',help='Snapshot protection before creating anything.')
    create=sub.add_parser('create',help='Create a registered window with random isolated folders.')
    create.add_argument('--tabs',type=int,default=1)
    create.add_argument('--provider',choices=['shell','codex','claude','mixed'],default='shell')
    create.add_argument('--busy',action='store_true',help='Start disposable agent tasks to verify interruption/continuation.')
    sub.add_parser('list',help='List ONLY registered fixture windows.')
    check=sub.add_parser('check',help='Reconcile known fixture receipts without repeating creation.')
    check.add_argument('--window',required=True)
    sub.add_parser('audit',help='Read-only protected-window identity check.')
    run=sub.add_parser('round-trip',help='Park/reopen one explicitly selected fixture.')
    run.add_argument('--window',required=True)
    run.add_argument('--cycles',type=int,default=1)
    run.add_argument('--confirm-own-close',choices=['OK','Cancel'],help='Respond only to this owned fixture’s normal iTerm close sheet.')
    args=parser.parse_args()
    if args.action=='round-trip' and not 1<=args.cycles<=50:
        parser.error('Use 1–50 cycles.')
    terminal=ITerm()
    if args.action=='init':
        Scope.capture(args.scope.resolve(),terminal,Processes(),['fixture-init-only'])
        print('Protective baseline saved. No window created.')
        return
    scope=Scope(args.scope.resolve())
    lab=Lab(scope,ScopedTerminal(scope,terminal),lambda:compose(scope_path=scope.path,test_close_response=getattr(args,"confirm_own_close",None)))
    if args.action=='create':
        if args.provider!='shell':
            from parking.agent_testing import AgentSeeder
            lab.seeder=AgentSeeder(scope)
        result=lab.create(args.tabs,args.provider,args.busy)
    elif args.action=='list': result=lab.list()
    elif args.action=='check': result=lab.check(args.window)
    elif args.action=='audit': result=dict(protected_windows=scope.audit(terminal.windows()))
    else:
        wid=args.window
        for _ in range(args.cycles):
            result=lab.round_trip(wid,wait_for)
            wid=result['window_id']
        result=dict(result,completed_cycles=args.cycles,visual_review='pending')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
