#!/usr/bin/python3
"""Free Parking's local-only backend. Importing this module does nothing to iTerm.

list: disk only. scan: read-only terminal discovery plus verified auto-archival.
park/restore: explicit terminal mutations.
No agents, hooks, preferences, Accessibility, shell history, or network services.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid


# Keep the legacy directory so renaming the app never strands existing backups.
ROOT = Path.home() / "Library/Application Support/Car Park"
HOOK_STATE = Path.home() / ".terminal-tldr/state.json"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class ParkError(Exception):
    pass


class ReturnNotVerified(ParkError):
    """The read succeeded, but this car is not a verified live return."""


class AutomationPermissionError(ParkError):
    """Permission failure is not evidence that a terminal is absent."""


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def session_uuid(value):
    return str(uuid.UUID(str(value)))


def terminal_uuid(value):
    match = UUID_RE.search(str(value or ""))
    return match.group().lower() if match else ""


def run(args, timeout=20, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise ParkError(result.stderr.strip() or "A local command failed.")
    return result.stdout


class ITerm:
    def call(self, action, **kwargs):
        if action in ("close", "set-title"):
            if action == "close":
                self.call("check", **kwargs)
            runtime = iterm_python_runtime()
            if runtime:
                # Only use an API the user already enabled. Never turn it on,
                # install a runtime, or alter their ordinary close preference.
                try:
                    output = run([str(runtime), str(Path(__file__).with_name("iterm_api.py")),
                                  json.dumps({"action": action, **kwargs})], timeout=30)
                    reply = json.loads(output)
                except Exception as error:
                    raise ParkError("The iTerm operation did not finish. Recovery is kept; check iTerm before retrying.") from error
                if reply.get("state") == ("closed" if action == "close" else "updated"):
                    return {"ok": True}
                if reply.get("state") != "unavailable":
                    raise ParkError(reply.get("error", "The saved window changed. Nothing else was closed."))
        request = json.dumps({"action": action, **kwargs})
        # The close confirmation is the user's to answer, not a timer's.
        timeout = None if action == "close" else 45
        try:
            output = run(["/usr/bin/osascript", "-l", "JavaScript",
                          str(Path(__file__).with_name("iterm.js")), request], timeout=timeout)
        except ParkError as error:
            if "-1743" in str(error):
                raise AutomationPermissionError(
                    "Allow Free Parking to access iTerm in System Settings → Privacy & Security → Automation, then try again. Accessibility permission is not needed.") from error
            raise
        return json.loads(output)

    def windows(self):
        return self.call("list")


def iterm_python_runtime():
    """An optional, already-installed iTerm runtime. No downloads or setup."""
    preference = subprocess.run(["/usr/bin/defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
                                capture_output=True, text=True, timeout=5)
    if preference.returncode or preference.stdout.strip() != "1":
        return None
    root = Path.home() / "Library/Application Support/iTerm2/iterm2env/versions"
    versions = sorted((p for p in root.glob("*") if re.fullmatch(r"\d+\.\d+\.\d+", p.name)),
                      key=lambda p: tuple(map(int, p.name.split("."))), reverse=True)
    for version in versions:
        binary = version / "bin/python3"
        if binary.is_file() and list(version.glob("lib/python*/site-packages/iterm2/__init__.py")):
            return binary
    return None


class Processes:
    def all(self):
        rows = run(["/bin/ps", "-axo", "pid=,ppid=,tty=,lstart=,comm="])
        result = {}
        for row in rows.splitlines():
            fields = row.strip().split(None, 8)
            if len(fields) != 9:
                continue
            pid, ppid, tty = fields[:3]
            result[int(pid)] = {
                "pid": int(pid), "ppid": int(ppid), "tty": "/dev/" + tty,
                "started": " ".join(fields[3:8]), "executable": fields[8],
            }
        return result

    def files(self, pid):
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-Fn"],
                                capture_output=True, text=True, timeout=15)
        # lsof exit 1 commonly means the process has just exited.
        if result.returncode not in (0, 1):
            raise ParkError(result.stderr.strip() or "Cannot inspect the agent's files.")
        return [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]

    def cwd(self, pid):
        result = subprocess.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True, timeout=10)
        return next((s[1:] for s in result.stdout.splitlines() if s.startswith("n")), "")

    def started_utc(self, pid):
        # Claude's native process registry records ps birth time in UTC.
        return " ".join(run(["/usr/bin/env", "TZ=UTC", "/bin/ps", "-p", str(pid),
                             "-o", "lstart="]).split())

    def send_signal(self, process, number):
        # PID alone is not identity. Never signal a reused PID.
        if same_process(process, self.all().get(process["pid"])):
            try:
                os.kill(process["pid"], number)
            except ProcessLookupError:
                pass


def same_process(saved, current):
    return bool(current and saved["pid"] == current["pid"]
                and saved["started"] == current["started"])


def process_key(process):
    return process["pid"], process["started"]


def descendants(roots, processes):
    found = {p["pid"]: p for p in roots if same_process(p, processes.get(p["pid"]))}
    while True:
        additions = {pid: p for pid, p in processes.items()
                     if p["ppid"] in found and pid not in found}
        if not additions:
            return list(found.values())
        found.update(additions)


def merge_processes(first, second):
    return list({process_key(p): p for p in first + second}.values())


def read_hook_records():
    try:
        return json.loads(HOOK_STATE.read_text()).get("sessions", [])
    except (OSError, ValueError):
        return []


def codex_metadata(path):
    with open(path, "rb") as f:
        first = json.loads(f.readline())
        if first.get("type") != "session_meta":
            raise ValueError("Not a Codex session")
        meta = first["payload"]
        # Ignore subagents' transcripts opened by the same Codex process.
        if meta.get("source") != "cli":
            raise ValueError("Not a top-level CLI session")
        cwd = meta["cwd"]
        # A session may have changed working root after it started. Only inspect
        # recent metadata, not prompts, and never write the transcript.
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 2 * 1024 * 1024))
        lines = f.read().splitlines()
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                if entry.get("type") == "turn_context" and entry.get("payload", {}).get("cwd"):
                    cwd = entry["payload"]["cwd"]
                    break
            except ValueError:
                continue
    return {"session_id": session_uuid(meta["id"]), "cwd": cwd,
            "transcript_path": path, "identity_source": "open transcript"}


def claude_native_metadata(process, process_api, root=None):
    """Bind the tab's actual process to Claude's own local session registry.

    Read only the PID's JSON record, never adjacent keys or messaging sockets.
    A present but stale/invalid record fails closed rather than guessing from
    older hooks, command-line arguments, or the latest session in a folder.
    """
    root = Path(root) if root is not None else Path.home() / ".claude"
    try:
        raw = (root / "sessions" / (str(process["pid"]) + ".json")).read_text()
    except FileNotFoundError:
        return None  # Older Claude versions may use the existing hook fallback.
    try:
        record = json.loads(raw)
        if (not isinstance(record, dict) or type(record.get("pid")) is not int
                or record["pid"] != process["pid"] or record.get("pidDomain") != "darwin"
                or record.get("kind") != "interactive" or record.get("entrypoint") != "cli"
                or not isinstance(record.get("procStart"), str)
                or " ".join(record["procStart"].split()) != process_api.started_utc(process["pid"])):
            raise ValueError("Process identity mismatch")
        sid = session_uuid(record["sessionId"])
        cwd = record["cwd"]
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or cwd != process_api.cwd(process["pid"]):
            raise ValueError("Working directory mismatch")
        paths = [p for p in (root / "projects").glob("*/" + sid + ".jsonl") if p.is_file()]
        if len(paths) != 1 or paths[0].stat().st_size == 0:
            raise ValueError("No unique saved transcript")
        return {"session_id": sid, "cwd": cwd, "transcript_path": str(paths[0]),
                "identity_source": "Claude process registry"}
    except (KeyError, TypeError, ValueError) as error:
        raise ParkError("Claude's session record cannot be matched to this live process and saved conversation. Finish a prompt, then refresh.") from error


def claude_hook_metadata(terminal_id, process, records):
    started = dt.datetime.strptime(process["started"], "%a %b %d %H:%M:%S %Y").timestamp()
    matches = {}
    for record in records:
        if record.get("tool") != "claude":
            continue
        binding = record.get("termSessionId") or record.get("itermSessionId")
        if not binding or terminal_uuid(binding) != terminal_uuid(terminal_id):
            continue
        try:
            stamp = dt.datetime.fromisoformat(record["lastSeenAt"].replace("Z", "+00:00")).timestamp()
            if stamp < started:
                continue  # Old conversation left over from a reused terminal tab.
            sid = session_uuid(record["agentSessionId"])
            transcript = record.get("transcriptPath", "")
            if not transcript or not Path(transcript).is_file():
                continue
            matches[sid] = {"session_id": sid, "cwd": record["cwd"],
                            "transcript_path": transcript, "identity_source": "existing Claude hook"}
        except (KeyError, ValueError, TypeError):
            continue
    # Do not pick the "latest session in this folder". Ambiguity blocks closing.
    if len(matches) != 1:
        raise ParkError("Claude has no unambiguous record for this tab yet. Finish a prompt, then refresh.")
    return next(iter(matches.values()))


def top_agent(tty, processes):
    agents = {pid: p for pid, p in processes.items()
              if Path(p["executable"]).name in ("codex", "claude")}
    candidates = []
    for pid, p in agents.items():
        if p["tty"] != tty:
            continue
        ancestor = p["ppid"]
        visited = set()
        nested = False
        while ancestor in processes and ancestor not in visited:
            if ancestor in agents:
                nested = True
                break
            visited.add(ancestor)
            ancestor = processes[ancestor]["ppid"]
        if not nested:
            candidates.append(p)
    if len(candidates) != 1:
        raise ParkError("This tab is not a single identifiable local Claude or Codex session.")
    return candidates[0]


def resolve_tab(tab, processes, process_api, hooks):
    ttys = {s["tty"] for s in tab["sessions"]}
    result = {"index": tab["index"], "title": "Tab " + str(tab["index"] + 1),
              "terminal_id": "", "tty": "", "provider": "", "session_id": "",
              "cwd": "", "issue": "",
              "has_agent": any(p["tty"] in ttys and Path(p["executable"]).name in ("codex", "claude")
                               for p in processes.values())}
    if len(tab["sessions"]) != 1:
        result["issue"] = "Split panes are not supported in this first version. This window will stay open."
        return result
    session = tab["sessions"][0]
    result.update(terminal_id=session["id"], tty=session["tty"], title=session["title"])
    try:
        if not result["has_agent"]:
            attached = [p for p in processes.values() if p["tty"] == session["tty"]]
            shells = [p for p in attached if Path(p["executable"]).name.lstrip("-") == "zsh"]
            other = [p for p in attached if Path(p["executable"]).name.lstrip("-") not in ("zsh", "login", "caffeinate")]
            if len(shells) != 1 or other:
                names = sorted({Path(p["executable"]).name for p in other})
                raise ParkError("This tab is running " + (", ".join(names) or "an unidentified command")
                                + ". Finish it or close that tab, then park again.")
            process = shells[0]
            cwd = process_api.cwd(process["pid"])
            if not os.path.isabs(cwd) or not Path(cwd).is_dir():
                raise ParkError("This shell's folder cannot be saved. Close that tab, then park again.")
            result.update(provider="shell", process=process, cwd=cwd, transcript_path="",
                          identity_source="idle shell folder",
                          auxiliary_processes=[p for p in attached if Path(p["executable"]).name == "caffeinate"])
            return result
        process = top_agent(session["tty"], processes)
        provider = Path(process["executable"]).name
        # Display what we can read even before a resumable session is identified.
        # An issue still blocks parking; this folder is not a guessed session ID.
        result.update(provider=provider, process=process, cwd=process_api.cwd(process["pid"]))
        native = claude_native_metadata(process, process_api) if provider == "claude" else None
        files = [] if native else process_api.files(process["pid"])
        candidates = {}
        if native:
            candidates[native["session_id"]] = native
        for path in files:
            if not path.endswith(".jsonl"):
                continue
            try:
                if provider == "codex" and Path(path).name.startswith("rollout-"):
                    meta = codex_metadata(path)
                    candidates[meta["session_id"]] = meta
                elif provider == "claude" and "/.claude/projects/" in path and UUID_RE.fullmatch(Path(path).stem):
                    sid = session_uuid(Path(path).stem)
                    candidates[sid] = {"session_id": sid, "cwd": process_api.cwd(process["pid"]),
                                       "transcript_path": path, "identity_source": "open transcript"}
            except (OSError, ValueError, KeyError):
                continue
        if len(candidates) == 1:
            metadata = next(iter(candidates.values()))
        elif not candidates and provider == "claude":
            metadata = claude_hook_metadata(session["id"], process, hooks)
        else:
            raise ParkError("No unique saved conversation found yet. Let a turn finish, then refresh.")
        if not os.path.isabs(metadata["cwd"]) or not Path(metadata["cwd"]).is_dir():
            raise ParkError("The agent's working directory is missing or cannot be identified.")
        result.update(metadata, provider=provider, process=process)
    except (ParkError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        result["issue"] = str(error)
    return result


def fingerprint(window):
    identity = [(t["terminal_id"], t["provider"], t["session_id"], t["cwd"],
                 t.get("process", {}).get("pid"), t.get("process", {}).get("started"), t["issue"])
                for t in window["tabs"]]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def valid_bounds(value):
    return (isinstance(value, dict)
            and all(type(value.get(k)) in (int, float) and math.isfinite(value[k])
                    for k in ("x", "y", "width", "height"))
            and value["width"] > 0 and value["height"] > 0)


def discover(iterm, process_api):
    windows = iterm.windows()
    processes, hooks = process_api.all(), read_hook_records()
    result = []
    for w in windows:
        tabs = [resolve_tab(t, processes, process_api, hooks) for t in w["tabs"]]
        row = {"id": w["id"], "title": w["title"], "tabs": tabs,
               "can_park": bool(tabs) and all(not t["issue"] for t in tabs)}
        if w.get("bounds") is not None:
            if not valid_bounds(w["bounds"]):
                raise ParkError("iTerm returned an unreadable window size. Nothing was closed.")
            row["bounds"] = w["bounds"]
        row["fingerprint"] = fingerprint(row)
        result.append(row)
    return result


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def path(self, car_id):
        return self.root / (session_uuid(car_id) + ".json")

    def load(self, car_id):
        try:
            car = json.loads(self.path(car_id).read_text())
            if not isinstance(car, dict) or car.get("schema_version") != 1 or car.get("id") != session_uuid(car_id):
                raise ValueError("Unsupported recovery file")
            for key in ("created_at", "status", "note", "source_window_id"):
                if not isinstance(car.get(key), str):
                    raise ValueError("Invalid recovery field: " + key)
            dt.datetime.fromisoformat(car["created_at"].replace("Z", "+00:00"))
            if car["status"] not in ("saved_open", "parked", "restoring", "restored", "attention", "archived"):
                raise ValueError("Invalid recovery status")
            if not isinstance(car.get("tabs"), list) or not car["tabs"]:
                raise ValueError("Recovery tabs are missing")
            for index, tab in enumerate(car["tabs"]):
                if not isinstance(tab, dict) or tab.get("index") != index:
                    raise ValueError("Invalid saved tab order")
                for key in ("title", "terminal_id", "provider", "session_id", "cwd", "issue", "transcript_path"):
                    if not isinstance(tab.get(key), str):
                        raise ValueError("Invalid saved tab field: " + key)
                resume_command(tab)
                if tab.get("resume_command") is not None and not isinstance(tab["resume_command"], str):
                    raise ValueError("Invalid saved command")
            if not isinstance(car.get("source_session_ids"), list) or not all(isinstance(x, str) for x in car["source_session_ids"]):
                raise ValueError("Invalid source sessions")
            if not isinstance(car.get("tracked_processes"), list):
                raise ValueError("Invalid tracked processes")
            for process in car["tracked_processes"]:
                if (not isinstance(process, dict) or not isinstance(process.get("pid"), int)
                        or process["pid"] <= 0 or not isinstance(process.get("started"), str)):
                    raise ValueError("Invalid saved process identity")
            if not isinstance(car.get("restored_targets"), dict):
                raise ValueError("Invalid restored targets")
            for key, target in car["restored_targets"].items():
                if (key not in {str(i) for i in range(len(car["tabs"]))}
                        or not isinstance(target, dict)
                        or not all(isinstance(target.get(k), str) for k in ("window_id", "terminal_id"))):
                    raise ValueError("Invalid restored target")
                if "title_pending" in target and type(target["title_pending"]) is not bool:
                    raise ValueError("Invalid tab title restoration progress")
            pending = car.get("pending_tab")
            if pending is not None and (type(pending) is not int or not 0 <= pending < len(car["tabs"])):
                raise ValueError("Invalid pending tab")
            if car.get("window_title") is not None and not isinstance(car["window_title"], str):
                raise ValueError("Invalid saved window title")
            if car.get("window_bounds") is not None and not valid_bounds(car["window_bounds"]):
                raise ValueError("Invalid saved window size")
            if car.get("created_window_id") is not None and not isinstance(car["created_window_id"], str):
                raise ValueError("Invalid created window identity")
            if "bounds_pending" in car and type(car["bounds_pending"]) is not bool:
                raise ValueError("Invalid window restoration progress")
            return car
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ParkError("Cannot read this car's recovery file: " + str(error))

    def save(self, car):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        path = self.path(car["id"])
        fd, temporary = tempfile.mkstemp(prefix=".saving-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(car, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            # Do not allow a caller to proceed to terminal actions unless the
            # committed recovery file can be read back exactly as written.
            if json.loads(path.read_text()) != car:
                raise ParkError("Recovery verification failed. No further terminal action was taken.")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def cars(self, archived=False):
        cars, warnings = [], []
        for path in sorted(self.root.glob("*.json")):
            try:
                car = self.load(path.stem)
                if (car.get("status") == "archived") != archived:
                    continue  # Hidden from the garage, never deleted from disk.
                car["file_path"] = str(path)
                cars.append(car)
            except ParkError:
                warnings.append("Could not read " + str(path) + ". It has not been changed.")
        return sorted(cars, key=lambda c: c["created_at"], reverse=True), warnings

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.root / ".operation.lock", "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ParkError("Another Free Parking operation is still running.")
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def resume_command(tab):
    provider = tab["provider"]
    if provider not in ("codex", "claude", "shell"):
        raise ParkError("Unknown agent type in recovery file.")
    cwd = tab["cwd"]
    if not os.path.isabs(cwd) or "\x00" in cwd:
        raise ParkError("Invalid saved working directory.")
    if provider == "shell":
        return "cd " + shlex.quote(cwd) + " && exec /bin/zsh -l"
    sid = session_uuid(tab["session_id"])
    args = (["codex", "resume", "--sandbox", "danger-full-access", "--ask-for-approval", "never", sid]
            if provider == "codex" else ["claude", "--dangerously-skip-permissions", "--resume", sid])
    return "cd " + shlex.quote(cwd) + " && command " + " ".join(shlex.quote(a) for a in args)


def launch_command(tab):
    # Start the command AS the new terminal session, not by typing into an
    # existing prompt. Login+interactive zsh loads the user's normal CLI PATH.
    body = resume_command(tab) + "; printf '\\n[Free Parking] Agent exited. Your recovery file is still saved.\\n'; exec /bin/zsh -l"
    return " ".join(shlex.quote(a) for a in ["/bin/zsh", "-lic", body])


def prepare_car(store, window, process_api):
    roots = [t["process"] for t in window["tabs"] if t["provider"] != "shell"]
    helpers = [p for t in window["tabs"] for p in t.get("auxiliary_processes", [])]
    car = {
        "schema_version": 1, "id": str(uuid.uuid4()), "created_at": now(),
        "status": "saved_open", "source_window_id": window["id"], "window_title": window["title"],
        "source_session_ids": [t["terminal_id"] for t in window["tabs"]],
        "window_bounds": window.get("bounds"),
        "tabs": window["tabs"], "tracked_processes": merge_processes(descendants(roots, process_api.all()), helpers),
        "restored_targets": {}, "pending_tab": None,
        "note": "Recovery file saved. The original window may still be open.",
    }
    for tab in car["tabs"]:
        tab["resume_command"] = resume_command(tab)
    store.save(car)  # Durable recovery MUST succeed before the first interrupt.
    return car


def close_saved_car(store, iterm, process_api, car):
    roots = [t["process"] for t in car["tabs"]]
    agent_roots = [t["process"] for t in car["tabs"] if t["provider"] != "shell"]
    args = {"windowId": car["source_window_id"], "sessionIds": car["source_session_ids"]}
    try:
        # Recheck both the window and process identities before interrupting.
        iterm.call("check", **args)
        current = process_api.all()
        if not all(same_process(p, current.get(p["pid"])) for p in roots):
            raise ParkError("An agent changed after saving. Nothing was interrupted.")
        # No synthetic keystrokes: iTerm's AppleScript write command can
        # broadcast input to other windows. Signal only captured process IDs.
        for process in reversed(car["tracked_processes"]):
            if Path(process.get("executable", "")).name != "caffeinate":
                process_api.send_signal(process, signal.SIGINT)
        if agent_roots:
            time.sleep(1)
        car["tracked_processes"] = merge_processes(
            car["tracked_processes"], descendants(agent_roots, process_api.all()))
        store.save(car)
        # SIGINT can make every agent exit, and iTerm may then close the
        # window itself. Do not target its retained Undo-window object.
        source_ids = set(car["source_session_ids"])
        after_interrupt = {s["id"] for w in iterm.windows() for t in w["tabs"] for s in t["sessions"]}
        if source_ids & after_interrupt:
            iterm.call("close", **args)  # Adapter rechecks the full ordered list.
        # Cancellation on the normal AppleScript fallback means no further
        # termination. The optional API skips only this saved window's prompt;
        # neither path changes the user's ordinary confirmation preferences.
        source_ids = set(car["source_session_ids"])
        remaining_ids = {s["id"] for w in iterm.windows() for t in w["tabs"] for s in t["sessions"]}
        if source_ids & remaining_ids:
            car["note"] = "Saved, but the window was not closed. Agents may have been interrupted. No further processes were stopped."
        else:
            # iTerm can retain closed sessions for Undo. Explicitly terminate
            # only the saved agent processes and their observed descendants.
            # Never signal a shell, iTerm itself, a process group, or all agents.
            car["tracked_processes"] = merge_processes(
                car["tracked_processes"], descendants(agent_roots, process_api.all()))
            store.save(car)
            for process in reversed(car["tracked_processes"]):
                process_api.send_signal(process, signal.SIGTERM)
            deadline = time.monotonic() + 4
            while True:
                current = process_api.all()
                alive = [p for p in car["tracked_processes"] if same_process(p, current.get(p["pid"]))]
                if not alive or time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
            car["status"] = "attention" if alive else "parked"
            car["note"] = ("Saved and closed, but some tracked processes are still running: "
                           + ", ".join(str(p["pid"]) for p in alive) + ". Restore is blocked until they stop."
                           if alive else "Parked. Click the car to reopen this window.")
    except Exception as error:
        car["status"] = "attention"
        car["note"] = "Recovery file kept. Parking did not finish: " + str(error)
    store.save(car)
    return car["note"]


def park(store, iterm, process_api, window_id, expected_fingerprint):
    window = next((w for w in discover(iterm, process_api) if w["id"] == window_id), None)
    if not window or window["fingerprint"] != expected_fingerprint:
        raise ParkError("The window or its conversations changed. Refresh before parking.")
    if not window["can_park"]:
        raise ParkError("Some tabs cannot be saved. Nothing has been interrupted or closed.")
    car = prepare_car(store, window, process_api)
    return close_saved_car(store, iterm, process_api, car)


def park_all(store, iterm, process_api):
    windows = discover(iterm, process_api)
    if not windows:
        return "No iTerm windows are open."
    blocked = [(i + 1, t) for i, w in enumerate(windows) for t in w["tabs"] if t["issue"]]
    if blocked or any(not w["can_park"] for w in windows):
        detail = "\n".join("Window " + str(i) + ", tab " + str(t["index"] + 1) + ": " + t["issue"] for i, t in blocked)
        raise ParkError("Nothing closed. " + (detail or "An empty window could not be saved."))
    # Save EVERY window before closing ANY window. A failed save touches no terminal.
    cars = [prepare_car(store, w, process_api) for w in windows]
    fresh = discover(iterm, process_api)
    if [(w["id"], w["fingerprint"]) for w in fresh] != [(w["id"], w["fingerprint"]) for w in windows]:
        raise ParkError("Your tabs changed while saving. Nothing closed; recovery is kept. Click Park my windows again.")
    for car in cars:
        close_saved_car(store, iterm, process_api, car)
        if car["status"] != "parked":
            return "Parking stopped. " + car["note"] + " Recovery for every window is kept."
    return ""


def restore(store, iterm, process_api, car_id):
    car = store.load(car_id)
    for tab in car["tabs"]:
        resume_command(tab)  # Validate provider, UUID and path before any action.
        if not Path(tab["cwd"]).is_dir():
            raise ParkError("Working directory no longer exists: " + tab["cwd"])
        if tab["provider"] != "shell" and not Path(tab["transcript_path"]).is_file():
            raise ParkError("The saved conversation file is missing: " + tab["transcript_path"])

    raw = iterm.windows()
    present = {(w["id"], s["id"]) for w in raw for t in w["tabs"] for s in t["sessions"]}
    open_ids = {terminal_id for _, terminal_id in present}
    current = process_api.all()
    live = discover(iterm, process_api)
    targets = car["restored_targets"]
    matches = {}

    # Resolve all existing conversations before opening anything. An open tab
    # ID is not enough: it may now contain a shell or a different conversation.
    for index, saved in enumerate(car["tabs"]):
        recorded = targets.get(str(index))
        shell_ids = {saved["terminal_id"]} | ({recorded["terminal_id"]} if recorded else set())
        candidates = [(w["id"], t) for w in live for t in w["tabs"]
                      if (t["provider"] == "shell" and t["terminal_id"] in shell_ids if saved["provider"] == "shell"
                          else (t["provider"], t["session_id"]) == (saved["provider"], saved["session_id"]))]
        if len(candidates) > 1:
            raise ParkError("This conversation appears in more than one open tab. Resolve the duplicate in iTerm before retrying. The car has been kept.")
        match = None
        if candidates:
            window_id, tab = candidates[0]
            if (tab["issue"] or tab["cwd"] != saved["cwd"] or not tab.get("process")
                    or not same_process(tab["process"], current.get(tab["process"]["pid"]))
                    or (window_id, tab["terminal_id"]) not in present):
                raise ParkError("An open conversation could not be verified in its saved folder. Nothing was duplicated; the car has been kept.")
            match = {"window_id": window_id, "terminal_id": tab["terminal_id"]}
        if (recorded and recorded["terminal_id"] in open_ids
                and {k: recorded[k] for k in ("window_id", "terminal_id")} != match):
            raise ParkError("A returned tab is still open, but its saved conversation is not identifiable there. Check that tab before retrying. The car has been kept.")
        if match:
            matches[str(index)] = match

    missing = len(matches) != len(car["tabs"])
    if missing and set(car["source_session_ids"]) & open_ids:
        raise ParkError("Some original tabs are still open but not all saved conversations can be verified. Check iTerm before retrying. Nothing was duplicated.")
    if missing and any(same_process(p, current.get(p["pid"])) for p in car["tracked_processes"]):
        raise ParkError("An original agent or tracked child process is still running. Nothing was duplicated.")
    pending = car.get("pending_tab")
    if pending is not None and str(pending) not in matches:
        raise ParkError("A previous launch was interrupted before it could be recorded. To avoid duplicates, automatic restore is paused. Use the recovery commands after checking iTerm.")
    missing_providers = {tab["provider"] for index, tab in enumerate(car["tabs"]) if str(index) not in matches}
    if missing and any(t["issue"] and t["has_agent"] and
                       (not t["provider"] or t["provider"] in missing_providers)
                       for w in live for t in w["tabs"]):
        raise ParkError("An open agent of the same type could not be identified. Let its session record appear, then retry. Nothing was duplicated.")

    for index, match in matches.items():
        old = targets.get(index, {})
        targets[index] = {**old, **match} if all(old.get(k) == v for k, v in match.items()) else match
    car["pending_tab"] = None
    window_id = next((target["window_id"] for target in matches.values()), None)
    opened = 0
    store.save(car)
    for index, tab in enumerate(car["tabs"]):
        if str(index) in matches:
            continue
        car["status"], car["pending_tab"] = "restoring", index
        car["note"] = "Restoring tab " + str(index + 1) + ". Recovery information is retained."
        store.save(car)  # Journal intent before starting a process.
        try:
            created = iterm.call("create", windowId=window_id, command=launch_command(tab))
        except Exception as error:
            car["status"] = "attention"
            car["note"] = "Restore stopped; earlier tabs may be open. " + str(error)
            store.save(car)
            raise ParkError(car["note"])
        if window_id is None:
            car["created_window_id"] = created["windowId"]
            car["bounds_pending"] = car.get("window_bounds") is not None
        window_id = created["windowId"]
        targets[str(index)] = {"window_id": window_id, "terminal_id": created["sessionId"], "title_pending": True}
        car["pending_tab"] = None
        opened += 1
        store.save(car)

    # Already-open conversations are brought forward, not duplicated. Select
    # the exact windows, verifying their recorded tab IDs again in the adapter.
    # Created identities are journaled before title/frame writes. Presentation
    # retries never launch another tab or retitle an unrelated existing session.
    by_window = {}
    for index in range(len(car["tabs"])):
        target = targets[str(index)]
        by_window.setdefault(target["window_id"], []).append(target["terminal_id"])
    for index, tab in enumerate(car["tabs"]):
        target = targets[str(index)]
        if target.get("title_pending"):
            iterm.call("set-title", windowId=target["window_id"], sessionId=target["terminal_id"], title=tab["title"])
            target["title_pending"] = False
            store.save(car)
    if car.get("bounds_pending"):
        created_window = car["created_window_id"]
        if created_window not in by_window:
            raise ParkError("The newly restored window moved or closed. The car has been kept.")
        result = iterm.call("set-bounds", windowId=created_window,
                            sessionIds=by_window[created_window], bounds=car["window_bounds"])
        car["restored_bounds"] = result.get("bounds")
        car["bounds_pending"] = False
        store.save(car)
    for window_id, terminal_ids in reversed(list(by_window.items())):
        iterm.call("focus", windowId=window_id, sessionIds=terminal_ids)
    car["status"] = "restored"
    car["note"] = ("Opened " + str(opened) + " tabs. Waiting to identify every saved conversation."
                   if opened else "Your conversations are already open. Their windows were brought forward.")
    store.save(car)
    return car["note"]


def verify_return(car, iterm, process_api):
    """Read-only check. Permission/IO failures propagate, never mean 'not open'.

    A terminal alone is not proof: match this car's exact window/tab, provider,
    session, cwd and process identity. This is a point-in-time check.
    """
    if (car["status"] != "restored" or car.get("pending_tab") is not None or not car["tabs"]
            or car.get("bounds_pending") or any(t.get("title_pending") for t in car["restored_targets"].values())):
        raise ReturnNotVerified("This car has not finished opening. It has been kept.")
    live = discover(iterm, process_api)
    roots, expected = [], []
    used = set()
    for index, saved in enumerate(car["tabs"]):
        target = car["restored_targets"].get(str(index))
        if not target:
            raise ReturnNotVerified("A restored tab is unaccounted for. The car has been kept.")
        identity = (target["window_id"], target["terminal_id"])
        matches = [tab for window in live if window["id"] == identity[0]
                   for tab in window["tabs"] if tab["terminal_id"] == identity[1]]
        if len(matches) != 1 or identity in used:
            raise ReturnNotVerified("A restored tab is missing or ambiguous. The car has been kept.")
        tab = matches[0]
        if (tab["issue"] or not tab.get("process")
                or (tab["provider"], tab["cwd"]) != (saved["provider"], saved["cwd"])
                or (saved["provider"] != "shell" and tab["session_id"] != saved["session_id"])):
            raise ReturnNotVerified("A saved conversation is not identifiable in its restored tab yet. Let it finish opening, check the conversation, then try again. The car has been kept.")
        used.add(identity)
        if saved["provider"] != "shell":
            copies = [t for w in live for t in w["tabs"]
                      if (t["provider"], t["session_id"]) == (saved["provider"], saved["session_id"])]
            if len(copies) != 1:
                raise ReturnNotVerified("This conversation is open more than once. The car has been kept.")
        expected.append(identity)
        roots.append(tab["process"])

    # Recheck tab presence and process birth identities immediately before the
    # archive write. Unknown, switched or exited sessions fail closed.
    raw = iterm.windows()
    present = {(w["id"], s["id"]) for w in raw for t in w["tabs"] for s in t["sessions"]}
    current = process_api.all()
    if not set(expected).issubset(present) or not all(
            same_process(p, current.get(p["pid"])) for p in roots):
        raise ReturnNotVerified("A restored session closed or changed during the check. The car has been kept.")
    window_ids = {w for w, _ in expected}
    window = next((w for w in raw if w["id"] in window_ids), None)
    if (len(window_ids) != 1 or window is None
            or [t["sessions"][0]["id"] for t in window["tabs"] if len(t["sessions"]) == 1]
            != [sid for _, sid in expected]
            or len(window["tabs"]) != len(expected)):
        raise ReturnNotVerified("The returned window no longer has the exact saved tab order and grouping. The car has been kept.")


def archive_verified_returns(store, iterm, process_api, car_ids=None, attempts=1):
    """Only successful identity checks retire a car. Launch success is not proof."""
    cars, _ = store.cars()
    pending = [car for car in cars if car["status"] == "restored"
               and (car_ids is None or car["id"] in car_ids)]
    archived = []
    for attempt in range(attempts):
        for car in list(pending):
            try:
                verify_return(car, iterm, process_api)
            except ReturnNotVerified:
                continue
            archive_car(store, car, verified=True)
            archived.append(car["id"])
            pending.remove(car)
        if not pending:
            break
        if attempt + 1 < attempts:
            time.sleep(1)
    return archived


def open_car(store, iterm, process_api, car_id):
    restore(store, iterm, process_api, car_id)
    archived = archive_verified_returns(store, iterm, process_api, {car_id}, attempts=6)
    return {"verified_archived_ids": archived,
            "message": "" if archived else "Window opened; the car stays until every saved tab is identified."}


def unarchive_car(store, car_id):
    car = store.load(car_id)
    if car["status"] != "archived":
        raise ParkError("This car is already in the car park.")
    car["status"] = "parked"
    car["note"] = "Recovered from the archive. Click the car to reopen or find its existing tabs."
    store.save(car)
    return ""


def restore_all(store, iterm, process_api):
    cars, warnings = store.cars()
    if warnings:
        raise ParkError("A recovery file needs attention. Nothing was opened. " + " ".join(warnings))
    for car in sorted(cars, key=lambda c: c["created_at"]):
        restore(store, iterm, process_api, car["id"])
    return ""


def archive_car(store, car, verified):
    car["status"] = "archived"
    car["confirmed_at"] = now()
    car["removal_basis"] = "verified_live_return" if verified else "explicit_unverified_confirmation"
    car["note"] = ("All saved sessions were identified in their open tabs. Recovery backup retained."
                   if verified else "You explicitly removed this car without a verified live return. Recovery backup retained.")
    store.save(car)
    return "Car removed from the garage. Its recovery backup is kept."


def confirm_return(store, iterm, process_api, car_id):
    """Legacy strict entry point; never silently changes to an override."""
    car = store.load(car_id)
    verify_return(car, iterm, process_api)
    return archive_car(store, car, verified=True)


def removal_token(car):
    # Bind confirmation to this exact car AND revision. It is a stale-action
    # guard, not a credential. Never consume a confirmation for another car.
    return hashlib.sha256(json.dumps(car, sort_keys=True).encode()).hexdigest()


def remove_car(store, iterm, process_api, car_id, confirmation=None):
    car = store.load(car_id)
    if car["status"] == "archived":
        raise ParkError("This car was already removed. Refresh the garage.")
    if confirmation is not None:
        if confirmation != removal_token(car):
            raise ParkError("This car changed after the confirmation appeared. Check it and choose Remove car again.")
        return {"message": archive_car(store, car, verified=False)}
    try:
        verify_return(car, iterm, process_api)
    except ReturnNotVerified as error:
        # No disk mutation: Cancel or dismiss must leave the car untouched.
        return {"confirmation_required": {"car_id": car["id"],
                "token": removal_token(car), "reason": str(error)}}
    return {"message": archive_car(store, car, verified=True)}


def main(argv):
    store, iterm, process_api = Store(), ITerm(), Processes()
    response = {"ok": True, "windows": [], "message": ""}
    try:
        action = argv[0] if argv else "list"
        if action == "list":
            pass  # App launch is disk-only. No terminal permissions prompt.
        elif action == "scan":
            response["windows"] = discover(iterm, process_api)
            with store.lock():
                response["verified_archived_ids"] = archive_verified_returns(store, iterm, process_api)
        elif action in ("park-all", "restore-all", "park", "restore", "confirm-return", "remove", "remove-confirmed", "unarchive"):
            with store.lock():
                if action == "park-all" and len(argv) == 1:
                    response["message"] = park_all(store, iterm, process_api)
                elif action == "restore-all" and len(argv) == 1:
                    response["message"] = restore_all(store, iterm, process_api)
                    response["verified_archived_ids"] = archive_verified_returns(store, iterm, process_api, attempts=6)
                elif action == "park" and len(argv) == 3:
                    response["message"] = park(store, iterm, process_api, argv[1], argv[2])
                elif action == "restore" and len(argv) == 2:
                    response.update(open_car(store, iterm, process_api, argv[1]))
                elif action == "unarchive" and len(argv) == 2:
                    response["message"] = unarchive_car(store, argv[1])
                elif action == "confirm-return" and len(argv) == 2:
                    response["message"] = confirm_return(store, iterm, process_api, argv[1])
                elif action == "remove" and len(argv) == 2:
                    response.update(remove_car(store, iterm, process_api, argv[1]))
                elif action == "remove-confirmed" and len(argv) == 3:
                    response.update(remove_car(store, iterm, process_api, argv[1], confirmation=argv[2]))
                else:
                    raise ParkError("Missing operation arguments.")
        else:
            raise ParkError("Unknown operation.")
    except Exception as error:
        response.update(ok=False, error=str(error))
        if isinstance(error, AutomationPermissionError):
            response["error_code"] = "automation_denied"
    response["cars"], response["warnings"] = store.cars()
    response["archives"], _ = store.cars(archived=True)
    print(json.dumps(response))


if __name__ == "__main__":
    main(sys.argv[1:])
