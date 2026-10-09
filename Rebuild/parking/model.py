"""Explicit identities and states. Display text is never an identity input."""
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import hashlib
import json
import uuid


class ParkingError(RuntimeError):
    pass


class NotSubmitted(ParkingError):
    """Rejected before any terminal mutation was submitted; a retry is safe."""


class Uncertain(ParkingError):
    """An operation needs observation, not blind repetition."""


class Phase(str, Enum):
    SAVED = 'saved'
    INTERRUPTING = 'interrupting'
    CLOSING = 'closing'
    PARKED = 'parked'
    OPENING = 'opening'
    CHECKING = 'checking'
    RETURNED = 'returned'


@dataclass(frozen=True)
class Process:
    pid: int
    started: str
    tty: str
    executable: str
    ppid: int = 0

    @property
    def key(self):
        return self.pid, self.started


@dataclass(frozen=True)
class Tab:
    terminal_id: str
    tty: str
    title: str
    provider: str
    session_id: str
    cwd: str
    process: Process
    activity: str = "unknown"
    turn_id: str = ""
    shell_command: str = ""

    def validate(self):
        if self.shell_command not in ('', 'caffeinate') or (self.shell_command and self.provider != 'shell'):
            raise ParkingError('Unsupported saved shell command.')
        if self.activity not in ('unknown', 'idle', 'running', 'waiting'):
            raise ParkingError('Unknown agent activity state.')
        if not self.terminal_id or not self.tty or self.process.tty != self.tty:
            raise ParkingError('Incomplete terminal identity.')
        if self.process.pid <= 0 or not self.process.started:
            raise ParkingError('Incomplete process identity.')
        if not Path(self.cwd).is_absolute() or '\0' in self.cwd:
            raise ParkingError('An absolute working folder is required.')
        if self.provider != 'shell':
            if self.provider not in ('codex', 'claude'):
                raise ParkingError('Unknown session provider.')
            uuid.UUID(self.session_id)
        elif self.session_id:
            raise ParkingError('A folder shell cannot claim an agent conversation.')

    @property
    def identity(self):
        return (self.terminal_id, self.tty, self.provider, self.session_id,
                self.cwd, self.process.key) + ((self.shell_command,) if self.shell_command else ())

    def encode(self):
        self.validate()
        return asdict(self)

    @classmethod
    def decode(cls, data):
        fields = dict(data)
        fields['process'] = Process(**fields['process'])
        tab = cls(**fields)
        tab.validate()
        return tab


@dataclass(frozen=True)
class Window:
    window_id: str
    tabs: tuple

    def validate(self):
        if not self.window_id or not self.tabs:
            raise ParkingError('An empty or unidentified window cannot be saved.')
        if len({t.terminal_id for t in self.tabs}) != len(self.tabs):
            raise ParkingError('Duplicate terminal identities.')
        for tab in self.tabs:
            tab.validate()

    @property
    def fingerprint(self):
        self.validate()
        return hashlib.sha256(json.dumps([self.window_id, [t.identity for t in self.tabs]]).encode()).hexdigest()

    def encode(self):
        self.validate()
        return dict(window_id=self.window_id, tabs=[t.encode() for t in self.tabs])

    @classmethod
    def decode(cls, data):
        window = cls(data['window_id'], tuple(Tab.decode(t) for t in data['tabs']))
        window.validate()
        return window


@dataclass(frozen=True)
class Observation:
    """Complete raw window inventory plus resolved windows and per-window issues.

    A read failure must raise, never return an empty inventory. Unsupported
    windows remain in raw, so the verifier cannot mistake them for closed ones.
    """
    raw: tuple
    windows: tuple
    issues: dict

    @property
    def terminal_ids(self):
        return {s['id'] for w in self.raw for t in w['tabs'] for s in t['sessions']}

    def raw_window(self, wid):
        return next((w for w in self.raw if w['id'] == wid), None)

    def window(self, wid):
        return next((w for w in self.windows if w.window_id == wid), None)
