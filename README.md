![Free Parking — Park your tabs. Call it a day.](Artwork/FreeParkingBanner.png)

# Free Parking

Put your iTerm2 agent sessions away for the evening. One window becomes one car.
Double-click it tomorrow to reopen the same Claude and Codex conversations, in
the same folders and tab order.

**Early personal prototype.** Live tests passed for a two-tab Codex window and
for Claude Opus through the native app: park → restore → verify → remove.
Reopening focused the existing conversation instead of creating duplicates;
removing one car kept other cars and recovery files intact. These tests are
not a guarantee for every CLI version or interrupted task. Try a disposable
window first.

> **Permissions are deliberately skipped when agents resume.** Claude uses
> `--dangerously-skip-permissions`; Codex uses `--sandbox danger-full-access`
> and `--ask-for-approval never`. Only use this personal workflow with code and
> conversations you trust. This is not a safe-default agent launcher.

## Set up

You need:

- **macOS 14 or later**, iTerm2, and Xcode Command Line Tools (`xcode-select --install`).
- Swift 5.9+ and the developer-tools `/usr/bin/python3`.
- Claude Code and/or Codex CLI, installed, signed in, and available in your
  interactive login `zsh`. Live-tested with Codex 0.153.2 and Claude Code 2.1.274.

```sh
git clone https://github.com/jacobsapps/FreeParking.git
cd FreeParking
bash scripts/build-app.sh
open "dist/Free Parking.app"
```

The build creates a locally signed app; it does not launch it or touch iTerm.
You can drag **Free Parking.app** from `dist` into Applications and open it like
any other app. This is a source build, not a notarized downloadable release.

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

1. **Park my windows** reads iTerm's windows, tab counts, titles and agent folders.
   Review the list, then choose **Park** for the window you want to put away.
2. Recovery is written and read back **before** interrupting any agent or asking
   iTerm to close that window. Your normal iTerm close confirmation stays on.
3. **Double-click a car** to reopen its saved conversations, or bring already
   open, verified conversations forward. This opens terminals, not a details panel.
4. Check your conversations, then **Remove car**. An exact live match removes
   it immediately; otherwise you must explicitly confirm. Recovery files remain
   on disk either way. Removing a car never closes its tabs.

The painted **P** space stays available when cars are parked, so you can park
another window. Each car is independent. Use **⋯ → Show tabs / Recovery** only
when you want saved titles, folders or manual recovery commands. Recent cars
have weekday registration plates; older cars show dates.

Closing Free Parking itself does **not** park or close your terminals.
Cancelling iTerm's close confirmation keeps the car and window, but cannot undo
an interrupt already sent to an agent.

## Limits worth knowing

- **Local iTerm2 only, one agent per tab.** No split panes, shell-only tabs,
  tmux or SSH. An unidentified tab blocks parking its whole window rather than
  guessing which conversation to save.
- **A saved conversation is required.** Brand-new Codex sessions without a
  saved transcript may not be identifiable. Finish a turn and scan again.
- **Claude discovery uses local session metadata.** With the tested Claude
  version, the app matches its native process registry by PID, process birth
  time, working directory and exact saved transcript. No custom hook is needed.
  Older versions may require an already-installed terminal/session hook; Free
  Parking does not install hooks. Missing, stale or ambiguous metadata blocks
  parking rather than guessing. Non-default agent data directories are not
  currently supported.
- **This restores conversations, not running work.** It interrupts agents and
  may terminate their tracked child processes. It does not preserve drafts,
  running tasks, scrollback, layouts or every custom CLI option. Detached work
  is not guaranteed to stop.
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
Permission/read errors keep the car rather than treating iTerm as absent.

## Development

Offline checks use fakes and do not interact with real terminals:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_iterm.js                         # optional: needs Node.js
mkdir -p .build
swiftc Sources/FreeParking/CarDateLabel.swift tests/CarDateLabelTests.swift -o .build/date-tests
.build/date-tests
swiftc -D FREE_PARKING_DEMO Sources/FreeParking/Models.swift Sources/FreeParking/PreviewFixtures.swift tests/GarageInteractionTests.swift -o .build/ui-model-tests
.build/ui-model-tests
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
