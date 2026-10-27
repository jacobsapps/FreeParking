![Free Parking](Artwork/FreeParkingBanner.png)

# Free Parking

A local macOS app for putting your **iTerm windows** away and
resuming its Claude, Codex, idle folder and plain `caffeinate` tabs in their original order.

The original parking-lot/car interface in `Sources/FreeParking/` runs on the fresh
subsystem implementation in `Rebuild/`. The legacy backend is kept for comparison
only and is not bundled in the app.

## Build and install

Requires macOS 14+, iTerm2, Xcode Command Line Tools, `/usr/bin/python3`, and
signed-in Claude/Codex CLIs in the login shell. Current live evidence is from
macOS 27.0.1, iTerm2 3.7.3, Claude Code 2.1.282 and Codex CLI/shared daemon 0.164.0.
Other versions may expose different native identity data;
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

The build installs pinned dependencies for a separate tab-layout companion.
Recovering a missing middle tab uses iTerm's Python API to put surviving and
replacement tabs back in their saved order. Enable that API in iTerm Settings
if you need layout repair. Ordinary parking and reopening use Automation.
Dependency licenses and the companion source are included in the bundle.

## Use

1. Click **Park my windows**. Every window's recovery is saved and verified before
   the first interruption or close. The app respects your iTerm close settings.
   An unresolved tab blocks the batch before anything closes.
2. **Review windows** lists every window and its tab names, including exact
   identification problems. You can also park one identified window there.
3. Click a parked car to reopen it. Exact conversation IDs, folders, grouping and
   tab order are verified. Confirmed returns leave the parking lot; recovery stays
   in the archive. Up to eight saved titles are visible on each car; **Tab details
   and permissions** shows the complete list and each tab's restore state.
   Partial returns show the exact opened count and **Open remaining tabs**.
4. The **settings gear** controls whether a task that Free Parking actually
   interrupted receives `please continue` on reopening; the message is editable.
   Idle tasks receive no message. Verified per-tab permission settings are kept.
   When the original mode is unavailable, Codex explicitly opens read-only with
   approval on request; Claude opens in plan mode. Global YOLO defaults are not inherited.

The release app always operates on the complete iTerm desktop. It has no agent
window exclusion and cannot launch with a disposable test scope. A durable batch
journal and per-window progress distinguish completed, pending and untouched saves.

Titles are display-only, never identity. Codex subagents sharing a parent's
terminal endpoint are associated through explicit native parent relationships;
unrelated conversations remain ambiguous. A warm resume can retain a dead TUI
endpoint in Codex. For an exact `codex resume UUID` launch, parking offers that
saved session for **explicit confirmation**, never silently treats the launch
command as current identity. Check the listed name/folder/UUID; cancel if you
switched conversations inside the tab. Confirmation is single-use, expires after
two minutes, and is bound to the terminal, process birth and window layout.
Other unknown jobs cannot be confirmed away. Confirmation appears only when
parking, not as a standing home-screen notice. The app scans once on launch;
refocusing it or reattaching its view does not scan again. Use Refresh for an
updated inventory. Parking still performs a fresh safety check. Refresh is
read-only with respect to terminals and agents.

No terminal typing, shared-daemon kills, background hooks, or iTerm preference
changes. Split panes, unrelated foreground programs, and ambiguous
agent identities are deliberately not supported. A manually reopened conversation
is not automatically adopted or duplicated. Lost acknowledgements remain pending
rather than risking another launch/message. This is at-most-once launch behavior,
not guaranteed message delivery after every crash.

### Recovery and previous versions

The new journal is private local SQLite under
`~/Library/Application Support/Free Parking/Recovery-v2/`.
Legacy recovery under `~/Library/Application Support/Car Park/` is **unchanged**.
Complete parked v1 saves with exact session IDs are copied into the new journal
once; saved shell commands and old permission-bypass flags are never executed.
An unfinished legacy launch is reported for review and retained, never replayed
by guessing. **Garage → Show Previous Recovery Files** opens the originals.
One unreadable record is reported separately and does not hide intact recoveries.

## Verification

```sh
python3 -m unittest discover -s Rebuild/tests -v
python3 Rebuild/verify.py --suite all --build
```

The component suites cover the real recovery store, exact identity contracts,
reconciliation, continuation, replay rejection and the independent test guard.
Fake OS tests are not desktop acceptance evidence.

The October 27 repair includes native seven-tab mixed-agent restoration, recovery
after a middle returned tab closes, seven historical conversations selected from
Backtrace's index, and busy Claude/Codex interruption with exactly one continuation
per interrupted conversation. See `Rebuild/REPAIR-2026-10-27.txt` for the evidence
and the whole-desktop acceptance result.

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
