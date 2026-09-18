"""Offline tests. Fakes only: never opens, reads, signals or closes a real terminal.

Run with unittest after explicit testing authorization.
"""
import importlib.util
import json
from pathlib import Path
import shlex
import signal
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("freeparking", Path(__file__).parents[1] / "Resources/freeparking.py")
cp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cp)

SID = "12345678-1234-4234-8234-123456789abc"
TERMINAL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ROOT_PROCESS = {"pid": 101, "ppid": 10, "tty": "/dev/fixture", "started": "Thu Sep 17 10:00:00 2026", "executable": "/fixture/codex"}
CHILD_PROCESS = {"pid": 102, "ppid": 101, "tty": "/dev/fixture", "started": "Thu Sep 17 10:01:00 2026", "executable": "/fixture/build"}


class FakeProcesses:
    def __init__(self, running=None):
        self.running = dict(running or {})
        self.signals = []

    def all(self):
        return dict(self.running)

    def send_signal(self, process, number):
        if cp.same_process(process, self.running.get(process["pid"])):
            self.signals.append((process["pid"], number))
            if number == signal.SIGTERM:
                self.running.pop(process["pid"], None)


class FakeITerm:
    def __init__(self, store, window, cancel=False, fail_create=False):
        self.store = store
        self.raw = [{"id": window["id"], "title": "Fixture", "tabs": [
            {"index": t["index"], "sessions": [{"id": t["terminal_id"], "tty": "/dev/fixture", "title": "Fixture"}]}
            for t in window["tabs"]]}]
        self.cancel = cancel
        self.fail_create = fail_create
        self.calls = []

    def windows(self):
        return self.raw

    def call(self, action, **kwargs):
        self.calls.append(action)
        # All terminal operations must already have a readable recovery file.
        assert self.store.cars()[0]
        if action == "close" and not self.cancel:
            self.raw = []
        if action == "create":
            if self.fail_create:
                raise cp.ParkError("Simulated ambiguous create failure")
            self.raw = [{"id": "2", "title": "Fixture", "tabs": [{"index": 0, "sessions": [
                {"id": "restored-fixture", "tty": "/dev/fixture", "title": "Fixture"}]}]}]
            return {"windowId": "2", "sessionId": "restored-fixture"}
        if action == "set-bounds":
            return {"ok": True, "bounds": kwargs["bounds"]}
        if action == "focus":
            present = {s["id"] for w in self.raw if w["id"] == kwargs["windowId"]
                       for t in w["tabs"] for s in t["sessions"]}
            assert set(kwargs["sessionIds"]).issubset(present)
        return {"ok": True}


class CarParkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = cp.Store(self.root / "garage")
        self.transcript = self.root / "transcript.jsonl"
        self.transcript.write_text("{}\n")
        self.tab = {"index": 0, "title": "Fixture", "terminal_id": TERMINAL, "tty": "/dev/fixture",
                    "provider": "codex", "session_id": SID, "cwd": str(self.root), "issue": "",
                    "process": ROOT_PROCESS, "has_agent": True, "transcript_path": str(self.transcript)}
        self.window = {"id": "1", "title": "Fixture", "tabs": [self.tab], "can_park": True}
        self.window["fingerprint"] = cp.fingerprint(self.window)
        self.proc = FakeProcesses({101: ROOT_PROCESS, 102: CHILD_PROCESS})
        self.iterm = FakeITerm(self.store, self.window)
        self.discover = patch.object(cp, "discover", return_value=[self.window])
        self.discover.start()
        self.addCleanup(self.discover.stop)
        self.sleep = patch.object(cp.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def park(self):
        return cp.park(self.store, self.iterm, self.proc, "1", self.window["fingerprint"])

    def parked_car(self):
        self.park()
        return self.store.cars()[0][0]

    def test_save_precedes_terminal_actions(self):
        self.park()
        self.assertEqual(self.iterm.calls, ["check", "close"])
        car = self.store.cars()[0][0]
        self.assertEqual(car["status"], "parked")
        self.assertEqual(car["tabs"][0]["session_id"], SID)
        self.assertIn((102, signal.SIGTERM), self.proc.signals)
        self.assertNotIn((10, signal.SIGTERM), self.proc.signals)

    def test_cancelled_close_keeps_backup_and_does_not_terminate(self):
        self.iterm.cancel = True
        self.park()
        self.assertEqual(self.store.cars()[0][0]["status"], "saved_open")
        self.assertTrue(self.proc.signals)
        self.assertTrue(all(number == signal.SIGINT for _, number in self.proc.signals))

    def test_save_failure_does_not_touch_terminal_or_processes(self):
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.park()
        self.assertEqual(self.iterm.calls, [])
        self.assertEqual(self.proc.signals, [])

    def test_recovery_readback_mismatch_blocks_terminal_actions(self):
        with patch.object(cp.json, "loads", return_value={}):
            with self.assertRaisesRegex(cp.ParkError, "Recovery verification failed"):
                self.park()
        self.assertEqual(self.iterm.calls, [])
        self.assertEqual(self.proc.signals, [])

    def awaiting_confirmation(self):
        car = self.parked_car()
        with patch.object(cp, "discover", return_value=[]):
            cp.restore(self.store, self.iterm, self.proc, car["id"])
        process = {**ROOT_PROCESS, "pid": 201, "started": "Thu Sep 17 11:00:00 2026"}
        self.proc.running[201] = process
        tab = {**self.tab, "terminal_id": "restored-fixture", "process": process}
        window = {**self.window, "id": "2", "tabs": [tab]}
        self.iterm.raw = FakeITerm(self.store, window).raw
        return car, window

    def test_explicit_confirmation_archives_but_keeps_recovery(self):
        car, window = self.awaiting_confirmation()
        with patch.object(cp, "discover", return_value=[window]):
            cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.cars()[0], [])
        self.assertTrue(self.store.path(car["id"]).is_file())
        self.assertEqual(self.store.load(car["id"])["status"], "archived")
        self.assertIn("confirmed_at", self.store.load(car["id"]))

    def test_unopened_car_cannot_be_cleared(self):
        car = self.parked_car()
        with self.assertRaisesRegex(cp.ParkError, "not finished opening"):
            cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "parked")

    def test_wrong_conversation_cannot_be_cleared(self):
        car, window = self.awaiting_confirmation()
        window["tabs"][0]["session_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "not identifiable"):
                cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_closed_tab_during_confirmation_keeps_car(self):
        car, window = self.awaiting_confirmation()
        self.iterm.raw = []
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "closed or changed"):
                cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_agent_exit_during_confirmation_keeps_car(self):
        car, window = self.awaiting_confirmation()
        self.proc.running.pop(201)
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "closed or changed"):
                cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_unidentified_agent_cannot_be_cleared(self):
        car, window = self.awaiting_confirmation()
        window["tabs"][0]["issue"] = "No unique transcript yet"
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "not identifiable"):
                cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_archive_write_failure_keeps_visible_car(self):
        car, window = self.awaiting_confirmation()
        with patch.object(cp, "discover", return_value=[window]), patch.object(self.store, "save", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                cp.confirm_return(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.store.load(car["id"])["status"], "restored")
        self.assertEqual(len(self.store.cars()[0]), 1)

    def second_car(self, first):
        other = self.store.load(first["id"])
        other["id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        other["status"] = "parked"
        self.store.save(other)
        return other

    def test_manual_archive_is_immediate_and_affects_only_that_car(self):
        car = self.parked_car()
        other = self.second_car(car)
        other_bytes = self.store.path(other["id"]).read_bytes()
        response = cp.remove_car(self.store, car["id"])
        self.assertEqual(response, {"message": ""})
        self.assertEqual(self.store.load(car["id"])["status"], "archived")
        self.assertEqual(self.store.path(other["id"]).read_bytes(), other_bytes)
        self.assertEqual(self.store.load(car["id"])["tabs"], car["tabs"])
        cp.unarchive_car(self.store, car["id"])
        self.assertEqual(len(self.store.cars()[0]), 2)

    def test_manual_archive_never_inspects_or_changes_terminals(self):
        car = self.parked_car()
        signals, calls = list(self.proc.signals), list(self.iterm.calls)
        with patch.object(cp, "discover", side_effect=cp.AutomationPermissionError("Access denied")) as discover:
            cp.remove_car(self.store, car["id"])
        discover.assert_not_called()
        self.assertEqual(self.proc.signals, signals)
        self.assertEqual(self.iterm.calls, calls)
        self.assertEqual(self.store.load(car["id"])["removal_basis"], "manual_archive")

    def test_manual_archive_is_idempotent(self):
        car = self.parked_car()
        cp.remove_car(self.store, car["id"])
        before = self.store.path(car["id"]).read_bytes()
        cp.remove_car(self.store, car["id"])
        self.assertEqual(self.store.path(car["id"]).read_bytes(), before)

    def test_manual_archive_write_failure_keeps_active_record(self):
        car = self.parked_car()
        before = self.store.path(car["id"]).read_bytes()
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                cp.remove_car(self.store, car["id"])
        self.assertEqual(self.store.path(car["id"]).read_bytes(), before)
        self.assertEqual(self.store.load(car["id"])["status"], "parked")

    def test_automation_error_is_classified_without_reading_real_iterm(self):
        with patch.object(cp, "run", side_effect=cp.ParkError("Not authorized (-1743)")):
            with self.assertRaises(cp.AutomationPermissionError):
                cp.ITerm().call("list")

    def test_window_changes_block_every_action(self):
        with self.assertRaises(cp.ParkError):
            cp.park(self.store, self.iterm, self.proc, "1", "outdated")
        self.assertEqual(self.iterm.calls, [])
        self.assertEqual(self.proc.signals, [])

    def test_unidentified_tabs_block_every_action(self):
        self.window["can_park"] = False
        with self.assertRaises(cp.ParkError):
            self.park()
        self.assertEqual(self.iterm.calls, [])

    def test_reused_pid_is_not_same_process(self):
        changed = {**ROOT_PROCESS, "started": "Fri Sep 18 10:00:00 2026"}
        self.assertFalse(cp.same_process(ROOT_PROCESS, changed))
        self.assertFalse(cp.same_process(ROOT_PROCESS, None))

    def test_descendants_exclude_other_sessions_and_shell(self):
        other = {**ROOT_PROCESS, "pid": 999, "ppid": 10}
        found = cp.descendants([ROOT_PROCESS], {101: ROOT_PROCESS, 102: CHILD_PROCESS, 999: other})
        self.assertEqual({p["pid"] for p in found}, {101, 102})

    def test_verified_original_tabs_are_focused_without_duplication(self):
        self.iterm.cancel = True
        car = self.parked_car()
        signals = list(self.proc.signals)
        cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertNotIn("create", self.iterm.calls)
        self.assertEqual(self.iterm.calls[-1], "focus")
        self.assertEqual(self.proc.signals, signals)
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_unidentified_original_tabs_block_duplicate_restore(self):
        self.iterm.cancel = True
        car = self.parked_car()
        with patch.object(cp, "discover", return_value=[]):
            with self.assertRaisesRegex(cp.ParkError, "original tabs"):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertNotIn("create", self.iterm.calls)
        self.assertNotIn("focus", self.iterm.calls)

    def test_opened_car_focuses_instead_of_creating_again(self):
        car, window = self.awaiting_confirmation()
        before = self.iterm.calls.count("create")
        with patch.object(cp, "discover", return_value=[window]):
            cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls.count("create"), before)
        self.assertEqual(self.iterm.calls[-1], "focus")
        self.assertEqual(self.store.load(car["id"])["status"], "restored")

    def test_recorded_tab_with_wrong_conversation_is_not_silently_reused(self):
        car, window = self.awaiting_confirmation()
        window["tabs"][0]["session_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        calls = list(self.iterm.calls)
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "not identifiable"):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls, calls)

    def test_same_session_wrong_folder_is_not_reused(self):
        car, window = self.awaiting_confirmation()
        window["tabs"][0]["cwd"] = str(self.root / "another-folder")
        calls = list(self.iterm.calls)
        with patch.object(cp, "discover", return_value=[window]):
            with self.assertRaisesRegex(cp.ParkError, "saved folder"):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls, calls)

    def test_duplicate_live_conversations_are_not_guessed(self):
        car, window = self.awaiting_confirmation()
        duplicate = {**window, "id": "3", "tabs": [{**window["tabs"][0], "terminal_id": "duplicate-fixture"}]}
        calls = list(self.iterm.calls)
        with patch.object(cp, "discover", return_value=[window, duplicate]):
            with self.assertRaisesRegex(cp.ParkError, "more than one"):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls, calls)

    def test_restore_creates_once_and_keeps_backup(self):
        car = self.parked_car()
        with patch.object(cp, "discover", return_value=[]):
            cp.restore(self.store, self.iterm, self.proc, car["id"])
        result = self.store.load(car["id"])
        self.assertEqual(result["status"], "restored")
        self.assertIsNone(result["pending_tab"])
        self.assertEqual(result["restored_targets"]["0"]["terminal_id"], "restored-fixture")
        self.assertTrue(self.store.path(car["id"]).exists())

    def test_unidentified_other_provider_does_not_block_restore(self):
        car = self.parked_car()
        unknown = {**self.tab, "provider": "claude", "session_id": "",
                   "terminal_id": "unrelated-fixture", "issue": "No session record yet"}
        with patch.object(cp, "discover", return_value=[{"tabs": [unknown]}]):
            cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls.count("create"), 1)

    def test_unidentified_same_or_unknown_provider_blocks_restore(self):
        car = self.parked_car()
        before = self.store.path(car["id"]).read_bytes()
        for provider in ("codex", ""):
            with self.subTest(provider=provider):
                unknown = {**self.tab, "provider": provider, "session_id": "",
                           "terminal_id": "unrelated-fixture", "issue": "No session record yet"}
                with patch.object(cp, "discover", return_value=[{"tabs": [unknown]}]):
                    with self.assertRaisesRegex(cp.ParkError, "could not be identified"):
                        cp.restore(self.store, self.iterm, self.proc, car["id"])
                self.assertNotIn("create", self.iterm.calls)
                self.assertEqual(self.store.path(car["id"]).read_bytes(), before)

    def test_failed_creation_records_ambiguity_instead_of_blind_retry(self):
        car = self.parked_car()
        self.iterm.fail_create = True
        with patch.object(cp, "discover", return_value=[]):
            with self.assertRaises(cp.ParkError):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
            with self.assertRaisesRegex(cp.ParkError, "automatic restore is paused"):
                cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertEqual(self.iterm.calls.count("create"), 1)
        self.assertEqual(self.store.load(car["id"])["pending_tab"], 0)

    def test_missing_folder_does_not_open_anything(self):
        car = self.parked_car()
        saved = self.store.load(car["id"])
        saved["tabs"][0]["cwd"] = str(self.root / "missing")
        self.store.save(saved)
        with self.assertRaisesRegex(cp.ParkError, "directory no longer exists"):
            cp.restore(self.store, self.iterm, self.proc, car["id"])
        self.assertNotIn("create", self.iterm.calls)

    def test_shell_quoting_never_replays_a_ledger_command(self):
        tab = {**self.tab, "cwd": "/tmp/I'm not a $(command); folder", "resume_command": "untrusted junk"}
        command = cp.resume_command(tab)
        tokens = shlex.split(command)
        self.assertEqual(tokens[1], tab["cwd"])
        self.assertNotIn("untrusted junk", command)
        launch = shlex.split(cp.launch_command(tab))
        self.assertEqual(launch[:2], ["/bin/zsh", "-lic"])
        self.assertIn("--ask-for-approval never", launch[2])

    def test_claude_permission_flag(self):
        command = cp.resume_command({**self.tab, "provider": "claude"})
        self.assertIn("--dangerously-skip-permissions --resume " + SID, command)

    def test_corrupt_backup_is_reported_not_overwritten(self):
        car = self.parked_car()
        path = self.store.path(car["id"])
        path.write_text("broken")
        cars, warnings = self.store.cars()
        self.assertEqual(cars, [])
        self.assertTrue(warnings)
        self.assertEqual(path.read_text(), "broken")

    def test_unidentified_tab_keeps_readable_title_and_folder(self):
        class ReadOnlyMetadata:
            def cwd(inner, pid):
                return str(self.root)

            def files(inner, pid):
                return []  # No saved conversation yet.

        raw = {"index": 0, "sessions": [{"id": TERMINAL, "tty": "/dev/fixture", "title": "My task title"}]}
        tab = cp.resolve_tab(raw, {101: ROOT_PROCESS}, ReadOnlyMetadata(), [])
        self.assertEqual(tab["title"], "My task title")
        self.assertEqual(tab["cwd"], str(self.root))
        self.assertEqual(tab["provider"], "codex")
        self.assertTrue(tab["issue"])  # Metadata alone does not allow parking.
        self.assertEqual(tab["session_id"], "")

    def test_malformed_valid_json_does_not_hide_other_cars(self):
        car = self.parked_car()
        bad = self.store.path("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        for payload in ([], {}, {"schema_version": 1, "id": bad.stem},
                        {**self.store.load(car["id"]), "id": bad.stem, "tabs": [None]},
                        {**self.store.load(car["id"]), "id": bad.stem, "restored_targets": []}):
            with self.subTest(payload=payload):
                bad.write_text(json.dumps(payload))
                cars, warnings = self.store.cars()
                self.assertEqual([c["id"] for c in cars], [car["id"]])
                self.assertEqual(len(warnings), 1)
                self.assertEqual(json.loads(bad.read_text()), payload)

    def test_agents_exiting_on_interrupt_do_not_close_an_undo_window(self):
        original = self.proc.send_signal
        def interrupt_exits(process, number):
            original(process, number)
            if number == signal.SIGINT:
                self.proc.running.pop(process["pid"], None)
                self.iterm.raw = []
        with patch.object(self.proc, "send_signal", side_effect=interrupt_exits):
            self.park()
        self.assertEqual(self.store.cars()[0][0]["status"], "parked")
        self.assertEqual(self.iterm.calls, ["check"])

    def test_codex_metadata_uses_latest_saved_working_root(self):
        self.transcript.write_text("\n".join(json.dumps(x) for x in [
            {"type": "session_meta", "payload": {"id": SID, "cwd": "/initial", "source": "cli"}},
            {"type": "turn_context", "payload": {"cwd": str(self.root)}},
        ]) + "\n{partial")
        self.assertEqual(cp.codex_metadata(str(self.transcript))["cwd"], str(self.root))

    def test_subagent_metadata_cannot_claim_a_tab(self):
        self.transcript.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": SID, "cwd": str(self.root), "source": {"subagent": "fixture"}}}))
        with self.assertRaises(ValueError):
            cp.codex_metadata(str(self.transcript))


if __name__ == "__main__":
    unittest.main()
