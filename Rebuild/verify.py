#!/usr/bin/env python3
"""Choose offline components, composed workflow, or all checks. Never opens UI."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
GROUPS={
    'components':[[sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_adapters.py','-v'],
                  ['node','Rebuild/tests/test_terminal.js']],
    'workflow':[[sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_core.py','-v'],
                [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_continuation_workflow.py','-v']],
    'agents':[[sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_agents.py','-v'],
              [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_codex_identity.py','-v'],
              [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_codex_transport.py','-v']],
    'integration':[['bash','scripts/test-garage-contract.sh'],
                   [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_garage.py','-v'],
                   [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_lab.py','-v']],
    'safety':[[sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_cleanup.py','-v'],
              [sys.executable,'-m','unittest','discover','-s','tests','-p','test_test_safety.py','-v'],
              [sys.executable,'-m','unittest','discover','-s','Rebuild/tests','-p','test_confirmation.py','-v']],
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=['all',*GROUPS],default='all')
    parser.add_argument('--build',action='store_true',help='Also build the separate app and native test tools.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or ROOT/'.local/Verification'/('rebuild-check-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    commands=[(group,command) for group,commands in GROUPS.items() if args.suite in ('all',group) for command in commands]
    if args.build:
        commands.append(('native-test-tools',['bash','Rebuild/build-test-tools.sh']))
        commands.append(('native-build',['bash','Rebuild/build.sh']))
    report=dict(evidence='Offline checks only. Integration replaces OS calls with a deterministic fake; real SQLite and safety boundaries are exercised.',checks=[])
    for index,(group,command) in enumerate(commands):
        log=output/(str(index+1)+'-'+group+'.txt')
        with log.open('w') as stream:
            result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        report['checks'].append(dict(group=group,exit_code=result.returncode,log=str(log)))
        print(group+': '+('passed' if result.returncode==0 else 'FAILED')+' — '+str(log))
    (output/'result.json').write_text(json.dumps(report,indent=2))
    raise SystemExit(any(c['exit_code'] for c in report['checks']))


if __name__=='__main__':main()
