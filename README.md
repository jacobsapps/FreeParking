![Free Parking — Park your tabs. Call it a day.](Artwork/FreeParkingBanner.png)

# Free Parking

Put your iTerm2 agent sessions away for the evening. One window becomes one car.
Click a car tomorrow to bring back its conversations, folders and saved tab
titles, in the same tab order, window size and position.

**Early personal prototype.** Live tests passed for a two-tab Codex window and
for Claude Opus through the native app: park → restore → verify → remove.
Reopening focused the existing conversation instead of creating duplicates;
removing one car kept other cars and recovery files intact. These tests are
not a guarantee for every CLI version or interrupted task. Try a disposable
window first. Disposable folder-tab round trips also verified exact saved titles,
window geometry, automatic archival and per-window prompt-free closing.
The latest speed and quit-handling changes have offline coverage (plus a
read-only connection-timing check); final live verification is still pending.

> **Permissions are deliberately skipped when agents resume.** Claude uses
> `--dangerously-skip-permissions`; Codex uses `--sandbox danger-full-access`
> and `--ask-for-approval never`. Only use this personal workflow with code and
> conversations you trust. This is not a safe-default agent launcher.

## Set up

You need:

- **macOS 14 or later**, iTerm2, and Xcode Command Line Tools (`xcode-select --install`).
- Swift 5.9+ and the developer-tools `/usr/bin/python3`.
- Claude Code and/or Codex CLI, installed, signed in, and available in your
  interactive login `zsh`. Live-tested with Codex 0.153.2 / 0.160.1 and Claude Code 2.1.274.

```sh
git clone https://github.com/jacobsapps/FreeParking.git
cd FreeParking
bash scripts/build-app.sh --install
open "/Applications/Free Parking.app"
```

The build creates a locally signed app; it does not launch it or touch iTerm.
`--install` puts it at **/Applications/Free Parking.app** and replaces that same
copy on future installs, so your Dock shortcut stays current. Quit Free Parking
before updating; your iTerm windows can stay open. After pulling updates, rerun
`bash scripts/build-app.sh --install`.

To keep it in the Dock, open the installed app, then right-click its Dock icon
and choose **Options → Keep in Dock**. Without `--install`, the script only
builds into `dist`; if Applications is not writable, copy that build there using
Finder instead. This is a source build, not a notarized downloadable release.

When prompted, **allow Free Parking to control iTerm**. Reading windows uses
macOS **Automation**, not Accessibility or screen capture. It does not click
through your tabs or type into them to scan. If denied, enable access in
**System Settings → Privacy & Security → Automation**, then retry.

### Try the UI without touching any terminals

```sh
bash scripts/build-app.sh --preview
open "dist/Free Parking Preview.app"
```

Preview uses fictional sessions. Its separate app bundle contains no terminal
helpers or Automation entitlement, and cannot read or close your real tabs.

## Use it

1. Click the painted **P** or **Park my windows** beneath it. This saves
   **every open iTerm window**, then closes them. One window becomes one car.
   Recovery for **all** windows is written and verified before **any** agent
   is interrupted or window closes.
2. **Click a car** to reopen that window. Already-open, verified conversations
   are brought forward instead of duplicated. Saved tab titles and the window's
   size and position are restored on newly created windows. All tabs are launched and journaled before layout or title updates;
   appearance failures do not stop the remaining conversations from opening.
   Hover over a car or its caption to see all saved tab names, in tab order.
3. Once every saved session, folder and tab order is verified, the car **drives
   away into the archive automatically**. If anything is uncertain, it stays.
   Click the **archive icon → Bring back car** to recover it at any time.

No bottom toolbar or scrolling home screen. The painted P stays available beside
parked cars; larger collections use pages. **⋯ → Show tabs / Recovery** is there
only if needed. **⋯ → Archive car** moves a car into the archive immediately,
without confirmation or accessing iTerm. The archive icon briefly acknowledges
the move. You can bring the car back at any time; no terminals are closed.
Recent cars have weekday-and-day registration plates, such as **FRIDAY 18**;
older cars include the month (and year when needed). Animations respect macOS
Reduce Motion.

### Close warnings and saved titles

Tab launch and tab-name confirmation are separate steps. Completed launch
receipts show **Opened · awaiting check**, not a launch error. Refresh also checks
all saved states, including interrupted parking, interrupted launches, and
conversations reopened manually. Once all exact session identities, folders,
processes, and relative tab order are verified, the car is acknowledged and
archived even if decorative title/layout confirmation failed. Extra unrelated
tabs do not block this check. Pending presentation flags remain in the recovery
backup. Missing or changed saved conversations are never silently verified.
Title readback allows a brief settling period without repeating writes.


Window closure always uses iTerm's normal confirmation path. Free Parking does
not force-close windows or change preferences. If a close acknowledgement is
lost, it checks whether the saved tabs actually remain instead of blindly
retrying or assuming failure. A retry of an unchanged original window reuses
its existing recovery record rather than adding another car.

If iTerm's Python API and bundled runtime are already available, they are used
only for saved tab names; otherwise titles use an AppleScript fallback. No
extra service, hook, or package is installed. A title/layout failure retains its
pending flags but does not turn successful launches into a failed restore.
Recovery records are archived only after live identity checks pass—not merely
because a close or launch command returned successfully.

Idle zsh tabs are saved as folders and reopen as shells. A standalone
`caffeinate` helper does not prevent parking; it is stopped after its window
closes, not resumed. Shell tabs do not resurrect previously exited agents or
preserve shell history/scrollback. Other running shell jobs and unidentified
agents block the operation with the affected window/tab named. Nothing closes
when this initial check fails.

Closing Free Parking itself does **not** park or close your terminals. You can
quit during a refresh. If a save, park or restore is already in progress, the app
finishes that operation and then quits, without a blocking confirmation dialog.
Cancelling an iTerm close confirmation stops the remaining parking sequence;
all recovery files remain. It cannot undo interrupts or closures that already
happened.

## Limits worth knowing

- **Local iTerm2 only: one agent or idle zsh per tab.** No split panes,
  tmux or SSH. Unknown work blocks parking rather than being silently killed.
- **A saved conversation is required.** Brand-new Codex sessions without a
  saved transcript may not be identifiable. Finish a turn and scan again.
- **Shared-server Codex sessions must be idle.** With Codex 0.160.1, discovery
  uses iTerm's native task title and foreground PID, the process folder, and an
  exact, unique match in Codex's saved session index/transcript. Decorative tab
  titles are not used for identity; duplicate names in the same folder block
  parking rather than picking the newest. Finish or cancel active turns before
  parking. Free Parking never interrupts the shared server or its child jobs.
  Older direct-transcript discovery is retained. No Codex RPC, new hook, or
  `--no-daemon` launch option is required.
- **Claude discovery uses local session metadata.** With the tested Claude
  version, the app matches its native process registry by PID, process birth
  time, working directory and exact saved transcript. No custom hook is needed.
  Older versions may require an already-installed terminal/session hook; Free
  Parking does not install hooks. Missing, stale or ambiguous metadata blocks
  parking rather than guessing. Non-default agent data directories are not
  currently supported.
- **This restores conversations, not running work.** It interrupts agents and
  may terminate their tracked child processes. It does not preserve drafts,
  running tasks, scrollback, split layouts or every custom CLI option. Detached work
  is not guaranteed to stop.
- **Geometry is saved from new parks only.** Older recovery files cannot restore
  a size they never recorded. If a display has been disconnected, restored windows
  are fitted onto an available screen. Full-screen/Spaces placement is not saved.
- **Keep the original transcripts.** Recovery files store session IDs, folders
  and restoration progress—not copies of your conversations. Moving a folder
  or deleting an agent's transcript can prevent automatic restoration.
- If a launch is ambiguous, the app keeps the car and stops instead of blindly
  opening duplicates. Inspect iTerm and use the car's recovery commands if needed.

## Privacy and recovery

No telemetry, accounts, cloud storage or network service in Free Parking.
Resumed agents retain their own normal network access. The app reads local
terminal/process metadata and agent session records.

Recovery files are private to your user and remain in:

```text
~/Library/Application Support/Car Park/
```

The original directory name is retained so the rename cannot strand backups.
**Do not publish this directory:** it contains local paths and session identities.
Removing a car archives its record; it does not delete the underlying recovery.
Automatic archival requires a verified return; permission/read errors keep the
car. Manual archival is reversible and does not need terminal permissions.

## Development

### Disposable-window testing

Production always parks all windows, one car per window; there is no window
picker. For live development tests only, create a disposable iTerm window and
record its exact iTerm window ID and session IDs in a private manifest:

```json
{"window_id":"DISPOSABLE_WINDOW_ID","session_ids":["DISPOSABLE_SESSION_ID"]}
```

Launch a separate app instance with this absolute manifest path:

```sh
open -n "dist/Free Parking.app" --args --test-scope=/absolute/private/scope.json
```

The header shows **TEST WINDOW**. Only that exact window and its journaled
restored windows are visible or mutable. If another tab is added, testing stops
rather than touching it. Recovery uses a separate `garage` directory beside the
manifest, never the production garage. The backend accepts the same scope as
`Resources/freeparking.py --test-scope /absolute/private/scope.json scan` (or
`park-all`, then `restore CAR_ID`). Never select a working window as the fixture.

The 0.160.1 compatibility change has a disposable one-tab live round trip:
park, resume the exact ID, read back the original response, and automatically
archive recovery. Shared-server exclusion and ambiguous identities also have
offline coverage; this does not establish every multi-window/CLI combination.

Offline checks use fakes and do not interact with real terminals:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_iterm.js                         # optional: needs Node.js
mkdir -p .build
swiftc Sources/FreeParking/CarDateLabel.swift tests/CarDateLabelTests.swift -o .build/date-tests
.build/date-tests
swiftc -D FREE_PARKING_DEMO Sources/FreeParking/Models.swift Sources/FreeParking/PreviewFixtures.swift tests/GarageInteractionTests.swift -o .build/ui-model-tests
.build/ui-model-tests
swiftc Sources/FreeParking/CarManoeuvre.swift tests/CarManoeuvreTests.swift -o .build/motion-tests
.build/motion-tests
```

The icon and banner are generated from Swift drawing code, with no downloaded
image or font assets. Rebuild them with `bash scripts/build-icon.sh` and
`bash scripts/build-banner.sh`.

Before publication, run `python3 scripts/check-release-files.py` and manually
inspect the exact files being committed. Local screenshots, review HTML,
session data, notes, credentials and builds must stay excluded. The scanner is
a preflight, not a guarantee.

## License

A license has not yet been selected. This public source preview does not yet
grant an open-source license.

### Disposable-test safety boundary

Live tests must use `scripts/disposable-test.py`, not the production garage.
A version-2 scope snapshots all existing window/tab and process identities
before creating anything. The runner and its ancestors are protected as well.
Only exact create receipts grant window ownership; recovery files cannot grant
it. Unknown tabs, moved tabs, protected-window changes, missing receipts, PID
reuse, shared Codex processes and unrecognized commands stop the test.

The test shim wraps both terminal actions **and process signals**. Appending a
tab gets an additional exact-identity check inside the iTerm adapter. Test state
and recovery live beside the scope, never in the production garage. Old test
manifests without a protective baseline are rejected.

For an authorized run in an environment permitting process/iTerm inspection:

```sh
python3 scripts/disposable-test.py init .local/Verification/new-run/scope.json
python3 scripts/disposable-test.py create-folder .local/Verification/new-run/scope.json
python3 scripts/disposable-test.py audit .local/Verification/new-run/scope.json
python3 scripts/disposable-test.py park-all .local/Verification/new-run/scope.json
python3 scripts/disposable-test.py list .local/Verification/new-run/scope.json
# Restore only an ID returned by this isolated garage:
python3 scripts/disposable-test.py restore .local/Verification/new-run/scope.json CAR_ID
python3 scripts/disposable-test.py audit .local/Verification/new-run/scope.json
```

The starter allows only its disposable folder-shell command. It deliberately
cannot resume an arbitrary Backtrack conversation that may be someone's active
work. Audits are read-only: they report changes, never "repair" working windows.
These boundaries have offline adversarial coverage; that is not live validation.
