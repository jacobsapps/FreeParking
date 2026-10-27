# SPDX-License-Identifier: GPL-2.0-or-later
"""Standalone iTerm API companion: reorder an exact existing tab set only.

No creates, closes, terminal input, profile changes or agent operations.
Uses iTerm's transaction to bind inspection and reordering atomically.
"""
import json
from pathlib import Path
import sys

vendor = Path(__file__).resolve().parents[1]/'vendor'
if not vendor.is_dir(): vendor = Path(__file__).resolve().parents[2]/'.build/iterm-api'


def reorder_plan(groups, expected, desired):
    if (any(len(group) != 1 for group in groups)
            or [group[0] for group in groups] != expected
            or len(set(expected)) != len(expected)
            or sorted(expected) != sorted(desired)):
        raise ValueError('The exact tab layout changed. Nothing reordered.')
    return [expected.index(sid) for sid in desired]


def main():
    sys.path.insert(0, str(vendor))
    import iterm2
    request = json.loads(sys.argv[1])
    async def operation(connection):
        submitted = False
        try:
            async with iterm2.Transaction(connection):
                app = await iterm2.async_get_app(connection)
                expected = request['sessionIds']; desired = request['desiredIds']
                matches = [w for w in app.terminal_windows
                    if [s.session_id for t in w.tabs for s in t.sessions] == expected]
                if len(matches) != 1: raise ValueError('The exact window is no longer present. Nothing reordered.')
                window = matches[0]
                order = reorder_plan([[s.session_id for s in t.sessions] for t in window.tabs], expected, desired)
                submitted = True
                await window.async_set_tabs([window.tabs[i] for i in order])
            result = dict(acknowledged=True)
        except Exception as error:
            result = {'uncertain' if submitted else 'notSubmitted': str(error)}
        print(json.dumps(result), flush=True)
    iterm2.run_until_complete(operation)


if __name__ == '__main__': main()
