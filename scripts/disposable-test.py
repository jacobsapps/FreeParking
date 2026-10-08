#!/usr/bin/env python3
"""Explicitly scoped test operations. Never imports a working session to test it."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources'))
import freeparking as fp
from test_safety import Scope, ScopedTerminal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'create-folder', 'audit', 'scan', 'park-all', 'restore', 'list'])
    parser.add_argument('scope', type=Path)
    parser.add_argument('car_id', nargs='?')
    args = parser.parse_args()
    scope_path = args.scope.resolve()
    if fp.ROOT.resolve() == scope_path.parent or fp.ROOT.resolve() in scope_path.parents:
        raise RuntimeError('Test scopes cannot live in the production garage.')
    terminal, processes = fp.ITerm(), fp.Processes()
    if args.action == 'init':
        # No windows are created until BOTH process and terminal baselines exist.
        fixture = scope_path.parent / 'disposable-folder'
        command = fp.launch_command({'provider': 'shell', 'cwd': str(fixture)})
        Scope.capture(scope_path, terminal, processes, [command])
        fixture.mkdir(parents=True, exist_ok=True)
        print('Protected baseline saved. No windows opened or changed.')
        return
    scope = Scope(scope_path)
    scoped = ScopedTerminal(scope, terminal)
    if args.action == 'create-folder':
        with fp.Store(scope_path.parent / 'garage').lock():
            # Each create goes through the receipt journal, never raw iTerm.
            scoped.call('create', windowId=None, command=scope.data['launch_commands'][0])
        print('Disposable folder window created and registered.')
    elif args.action == 'audit':
        count = scope.audit(terminal.windows())
        print(f'{count} protected windows retain their original tab identities/order.')
    else:
        command = ['--test-scope', str(scope_path), args.action]
        if args.car_id:
            command.append(args.car_id)
        fp.main(command)


if __name__ == '__main__':
    main()
