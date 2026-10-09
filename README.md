![Free Parking](Artwork/FreeParkingBanner.png)

# Free Parking

A local macOS app for putting an **explicitly selected iTerm window** away and
resuming its Claude, Codex and idle folder tabs in their original order.

The current app is a fresh subsystem implementation in `Rebuild/`. The old car UI
and backend remain in source history and the legacy source directories for
comparison; the default build now uses the new core.

## Build and install

Requires macOS 14+, iTerm2, Xcode Command Line Tools, `/usr/bin/python3`, and
signed-in Claude/Codex CLIs in the login shell. Current live evidence is from
macOS 27.0.1, iTerm2 3.7.3, Claude Code 2.1.296, Codex CLI 0.160.1 and its native
shared daemon 0.162.0. Other versions may expose different native identity data;
unsupported or ambiguous identities are refused, not guessed.

```sh
bash scripts/build-app.sh            # build only
bash scripts/build-app.sh --install  # quit Free Parking first; leave iTerm open
open "/Applications/Free Parking.app"
```

Installation preserves the stable app path and bundle identity for existing Dock
shortcuts. It backs up the previous app under
`~/Library/Application Support/Free Parking/Application Backups/` before replacing
it. Builds are locally signed, not notarized downloads. Automation access to
iTerm is required. Process inspection failures are reported, not treated as an
empty desktop. No accessibility or screen-capture permission is required by the
application.

## Use

1. Select the window, then **Park selected window**. Recovery is persisted before
   any task interruption or close. Normal iTerm close confirmations remain on.
2. Select its parked entry and **Reopen saved window**. Exact conversation IDs,
   folders, provider, grouping and tab order are verified. Confirmed returns leave
   the parked list; recovery remains under **Show retained backups**.
3. Hover over a row for all saved tab names. Titles are display-only and never
   used to decide which conversation to close or resume.
4. **Settings…** controls whether a task that Free Parking actually interrupted
   receives `please continue` on reopening; the message is editable. Idle tasks
   receive no message. Native agent permissions are not escalated.

No Park All, terminal typing, shared-daemon kills, background hooks, or iTerm
preference changes. Split panes, unrelated foreground programs, and ambiguous
agent identities are deliberately not supported. A manually reopened conversation
is not automatically adopted or duplicated. Lost acknowledgements remain pending
rather than risking another launch/message. This is at-most-once launch behavior,
not guaranteed message delivery after every crash.

### Recovery and previous versions

The new journal is private local SQLite under
`~/Library/Application Support/Free Parking/Recovery-v2/`.
Legacy recovery under `~/Library/Application Support/Car Park/` is **unchanged**;
**Previous recovery…** opens that folder. Legacy cars are not silently imported or
resolved by title. The backed-up previous app can still access them. Keep these
backups if old parked sessions remain.

## Verification

```sh
python3 -m unittest discover -s Rebuild/tests -v
python3 Rebuild/verify.py --suite all --build
```

The component suites cover the real recovery store, exact identity contracts,
reconciliation, continuation, replay rejection and the independent test guard.
Fake OS tests are not desktop acceptance evidence.

For explicitly authorized live verification, the test-only lab creates **new**
Claude/Codex conversations in random folders and independently protects every
pre-existing window and the runner. Use a fresh directory beneath a trusted
parent; do not bypass a trust screen or denied inspection.

```sh
bash Rebuild/build-test-tools.sh
python3 Rebuild/prove_agents.py --run --directory NEW_PRIVATE_DIRECTORY --cycles 3
```

This checks busy mixed-agent parking, repeated idle cycles, native continuation
counts, cancelled close, settings off and protected window identities. Its
exact-window AX helper handles only the owned fixture's ordinary close sheet;
that helper is **not shipped in the app**. Tests never change iTerm preferences.
The selectable lab is `Rebuild/lab.py`; receipt-proven cleanup is
`Rebuild/cleanup.py`. Private transcripts, screenshots, recovery and live evidence
must never be committed or published.

See [`Rebuild/STATUS.txt`](Rebuild/STATUS.txt) for the component findings and
remaining limitations. Nothing here claims perfection across every future CLI
version, desktop change or crash timing.
