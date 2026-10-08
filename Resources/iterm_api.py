#!/usr/bin/env python3
"""Saved tab titles using iTerm's Python runtime.

Never enables the API, changes confirmation settings, types input, or approves
permission dialogs. The caller must persist recovery before invoking this file.
"""
import json
import sys
import asyncio


def main(request):
    if request.get("action") not in ("set-title", "set-titles"):
        print(json.dumps({"state": "error", "error": "Unknown iTerm operation."}))
        return
    result = {"state": "unavailable"}
    try:
        import iterm2
    except ImportError:
        print(json.dumps(result))
        return

    async def operate(connection):
        nonlocal result
        # Title method invocations must run outside a transaction.
        app = await iterm2.async_get_app(connection)
        result = {"state": "error", "error": "Could not verify the saved window."}
        if request.get("action") in ("set-title", "set-titles"):
            titles = [request] if request["action"] == "set-title" else request.get("titles")
            if not isinstance(titles, list) or not titles:
                raise ValueError("No saved titles were provided.")
            updates, seen = [], set()
            for item in titles:
                tabs = [tab for window in app.terminal_windows for tab in window.tabs
                        if ("windowId" not in item or str(window.window_id) == str(item["windowId"]))
                        and len(tab.all_sessions) == 1 and tab.all_sessions[0].session_id == item.get("sessionId")]
                if len(tabs) != 1 or not isinstance(item.get("title"), str) or item["sessionId"] in seen:
                    raise ValueError("A restored tab changed. No titles were altered.")
                seen.add(item["sessionId"])
                updates.append((tabs[0], item["title"]))
            # Method invocations cannot run inside a transaction. This uses the
            # matched tab's immutable ID, never the selected tab or typed input.
            # Escape literal backslashes: saved text must not become expressions.
            async def update_title(tab, title):
                await tab.async_set_title(title.replace("\\", "\\\\"))
                # Setting a title can acknowledge before its displayed variable
                # settles. Read the same immutable tab again; never rewrite it or
                # infer success from the setter acknowledgement alone.
                for attempt in range(6):
                    if await tab.async_get_variable("title") == title:
                        return
                    if attempt < 5:
                        await asyncio.sleep(0.2)
                raise ValueError("The saved tab name could not be confirmed yet. Recovery is kept; check again after the tabs finish opening.")
            # The connection multiplexes independent tab RPCs. Wait for every
            # result, even on a partial failure, before returning to the journal.
            outcomes = await asyncio.gather(*(update_title(tab, title) for tab, title in updates), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            result = {"state": "updated"}
            return

    async def finish(connection):
        try:
            await operate(connection)
        finally:
            # Only after all RPC acknowledgements/readbacks (or an error), bound
            # this helper's WebSocket disconnect handshake. iTerm's installed
            # SDK can otherwise wait ten seconds after the operation is done.
            # This is not a terminal-close setting or an operation timeout.
            websocket = getattr(connection, "websocket", None)
            timeout = getattr(websocket, "close_timeout", None)
            if type(timeout) in (int, float) and timeout > 1:
                try:
                    websocket.close_timeout = 1
                except AttributeError:
                    pass  # An SDK with a read-only transport keeps its default.

    try:
        iterm2.run_until_complete(finish, retry=False)
    except (Exception, SystemExit) as error:
        if result["state"] != "unavailable":
            result = {"state": "error", "error": str(error) or result.get("error", "Title update did not finish.")}
    print(json.dumps(result))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
