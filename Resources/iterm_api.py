#!/usr/bin/env python3
"""Saved titles and optional per-window close using iTerm's Python runtime.

Never enables the API, changes confirmation settings, types input, or approves
permission dialogs. The caller must persist recovery before invoking this file.
"""
import json
import sys


def matching_window(app, session_ids):
    if not session_ids or len(set(session_ids)) != len(session_ids):
        raise ValueError("Invalid saved window identity.")
    matches = []
    for window in app.terminal_windows:
        if any(len(tab.all_sessions) != 1 for tab in window.tabs):
            continue
        actual = [tab.all_sessions[0].session_id for tab in window.tabs]
        if actual == session_ids:
            matches.append(window)
    if len(matches) != 1:
        raise ValueError("The saved window's tabs changed. No window was closed.")
    return matches[0]


def main(request):
    if request.get("action") not in ("close", "set-title"):
        print(json.dumps({"state": "error", "error": "Unknown iTerm operation."}))
        return
    result = {"state": "unavailable"}
    try:
        import iterm2
    except ImportError:
        print(json.dumps(result))
        return

    async def close(connection):
        nonlocal result
        # Initial subscription setup must happen outside a transaction. Refresh
        # the hierarchy inside it before matching the exact saved identities.
        app = await iterm2.async_get_app(connection)
        result = {"state": "error", "error": "Could not verify the saved window."}
        if request.get("action") == "set-title":
            tabs = [tab for window in app.terminal_windows for tab in window.tabs
                    if len(tab.all_sessions) == 1 and tab.all_sessions[0].session_id == request["sessionId"]]
            if len(tabs) != 1 or not isinstance(request.get("title"), str):
                raise ValueError("The restored tab changed. Its title was not altered.")
            # Method invocations cannot run inside a transaction. This uses the
            # matched tab's immutable ID, never the selected tab or typed input.
            # Escape literal backslashes: saved text must not become expressions.
            await tabs[0].async_set_title(request["title"].replace("\\", "\\\\"))
            if await tabs[0].async_get_variable("title") != request["title"]:
                raise ValueError("iTerm did not retain the saved title. The car is kept; retry after the tab finishes opening.")
            result = {"state": "updated"}
            return
        # No user-input or other UI changes may race the identity check. A forced
        # close is synchronous in iTerm; no confirmation can deadlock this block.
        async with iterm2.Transaction(connection):
            await app.async_refresh()
            window = matching_window(app, request["sessionIds"])
            if request.get("checkOnly"):
                result = {"state": "ready"}
                return
            # Any error after this point is ambiguous: the parent MUST NOT retry
            # through AppleScript or click a dialog. Keep recovery and stop.
            result = {"state": "error", "error": "The close result is uncertain. Recovery is kept; check iTerm."}
            await window.async_close(force=True)
            result = {"state": "closed"}

    try:
        iterm2.run_until_complete(close, retry=False)
    except (Exception, SystemExit) as error:
        if result["state"] != "unavailable":
            result = {"state": "error", "error": str(error) or result.get("error", "Close did not finish.")}
    print(json.dumps(result))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
