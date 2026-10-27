"""Real SQLite durability, compare-and-swap revisions and per-window locks."""
from .diagnostics import timed, event

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import copy
import datetime
import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from .model import ParkingError, Phase, Window


@dataclass(frozen=True)
class Receipt:
    record_id: str
    revision: int
    digest: str


def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def validate(record):
    if record.get('schema') != 2:
        raise ParkingError('Unsupported recovery schema; original data is retained.')
    uuid.UUID(record['id'])
    Phase(record['phase'])
    source = Window.decode(record['source'])
    if record['fingerprint'] != source.fingerprint:
        raise ParkingError('Saved identities do not match their recovery fingerprint.')
    if type(record['revision']) is not int or record['revision'] < 0:
        raise ParkingError('Invalid recovery revision.')
    if not isinstance(record['targets'], dict) or not isinstance(record['events'], list):
        raise ParkingError('Invalid recovery journal.')
    for index, target in record['targets'].items():
        if index not in {str(i) for i in range(len(source.tabs))}:
            raise ParkingError('Invalid restored tab index.')
        for key in ('window_id', 'terminal_id', 'receipt'):
            if not isinstance(target.get(key), str) or not target[key]:
                raise ParkingError('Invalid restored target receipt.')
    for index, outcome in record.get('interruptions', {}).items():
        if index not in {str(i) for i in range(len(source.tabs))}:
            raise ParkingError('Invalid interruption tab index.')
        tab = source.tabs[int(index)]
        if (not isinstance(outcome, dict) or outcome.get('outcome') not in ('interrupted', 'not_interrupted', 'unconfirmed')
                or outcome.get('session_id') != tab.session_id or outcome.get('provider') != tab.provider):
            raise ParkingError('Interruption receipt belongs to another session.')
    for index, checkpoint in record.get('close_interruptions', {}).items():
        if index not in {str(i) for i in range(len(source.tabs))}:
            raise ParkingError('Invalid close-interruption tab index.')
        tab = source.tabs[int(index)]
        if (tab.provider != 'claude' or checkpoint.get('provider') != tab.provider
                or checkpoint.get('session_id') != tab.session_id or checkpoint.get('method') != 'terminal-close'):
            raise ParkingError('Close checkpoint belongs to another session.')
        uuid.UUID(checkpoint['request_id'])
    for index, archived in record.get('native_archives', {}).items():
        if index not in {str(i) for i in range(len(source.tabs))} or type(archived) is not bool or source.tabs[int(index)].provider != 'codex':
            raise ParkingError('Invalid native archive receipt.')
    intent = record.get('intent')
    if intent is not None:
        if intent.get('kind') not in ('close', 'create', 'interrupt', 'native_archive', 'stop_client', 'reorder'):
            raise ParkingError('Invalid operation intent.')
        if intent['kind'] in ('create', 'interrupt', 'native_archive', 'stop_client'):
            if type(intent.get('index')) is not int or not 0 <= intent['index'] < len(source.tabs):
                raise ParkingError('Invalid operation tab index.')
        if intent['kind'] == 'stop_client' and source.tabs[intent['index']].provider == 'shell':
            raise ParkingError('An idle shell is not an agent client.')
        if intent['kind'] == 'native_archive':
            if type(intent.get('archived')) is not bool or source.tabs[intent['index']].provider != 'codex':
                raise ParkingError('Invalid native archive intent.')
        if intent['kind'] == 'create':
            if type(intent.get('index')) is not int or not 0 <= intent['index'] < len(source.tabs):
                raise ParkingError('Invalid launch intent index.')
            uuid.UUID(intent['receipt'])
            if 'continue_message' in intent:
                from .settings import continuation_message
                tab = source.tabs[intent['index']]
                message = intent['continue_message']
                if (not isinstance(message, str) or not message.strip() or len(message) > 2000 or '\0' in message
                        or continuation_message(tab, record.get('interruptions', {}).get(str(intent['index'])),
                                                dict(auto_continue_interrupted=True, continue_message=message)) != message):
                    raise ParkingError('Continuation has no matching confirmed interruption.')
    return source


class Store:
    def __init__(self, root):
        self.progress = None
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'recovery.sqlite'
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        try:
            # DELETE avoids abandoned WAL sidecars; FULL sync protects each
            # individual committed receipt before an external operation starts.
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def operation(self, window_id):
        name = hashlib.sha256(window_id.encode()).hexdigest() + '.lock'
        fd = os.open(self.root / name, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ParkingError('An operation for this window is already running.') from error
            yield
        finally:
            os.close(fd)

    def load(self, record_id):
        uuid.UUID(record_id)
        with self.connection() as db:
            row = db.execute('SELECT body FROM records WHERE id=?', (record_id,)).fetchone()
        if not row:
            raise ParkingError('Recovery record not found.')
        record = json.loads(row[0])
        validate(record)
        return record

    def all(self):
        with self.connection() as db:
            rows = db.execute('SELECT body FROM records ORDER BY rowid DESC').fetchall()
        records = [json.loads(row[0]) for row in rows]
        for record in records:
            validate(record)  # Corruption is visible, never silently skipped.
        return records

    def readable(self):
        """Show intact recoveries alongside errors; never rewrite invalid rows."""
        with self.connection() as db:
            rows = db.execute('SELECT id, body FROM records ORDER BY rowid DESC').fetchall()
        records, errors = [], []
        for ident, body in rows:
            try:
                record = json.loads(body)
                validate(record)
                if record['id'] != ident: raise ParkingError('Record ID disagrees with storage.')
                records.append(record)
            except Exception as error:
                errors.append('Recovery '+ident+' could not be read: '+str(error)+'. The original data is kept in '+str(self.path))
        return records, errors

    def begin(self, window):
        window.validate()
        for record in self.readable()[0]:
            if record['phase'] != Phase.RETURNED.value and record['fingerprint'] == window.fingerprint:
                return record, self.receipt(record)
        record = dict(schema=2, id=str(uuid.uuid4()), revision=0,
                      created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      source=window.encode(), fingerprint=window.fingerprint,
                      phase=Phase.SAVED.value, targets={}, intent=None, events=[], issue=None)
        return record, self.save(record)

    @timed('store.save')
    def save(self, record):
        candidate = copy.deepcopy(record)
        validate(candidate)
        expected = candidate['revision']
        candidate['revision'] += 1
        body = canonical(candidate)
        with self.connection() as db:
            if expected == 0:
                db.execute('INSERT INTO records VALUES (?,?,?)', (candidate['id'], 1, body))
            else:
                changed = db.execute('UPDATE records SET revision=?,body=? WHERE id=? AND revision=?',
                                     (candidate['revision'], body, candidate['id'], expected)).rowcount
                if changed != 1:
                    raise ParkingError('Recovery changed in another operation; no further terminal action is permitted.')
        loaded = self.load(candidate['id'])
        if canonical(loaded) != body:
            raise ParkingError('Durable recovery readback failed.')
        record.clear()
        record.update(candidate)
        event('recovery.saved', phase=record['phase'], tabs=len(record['source']['tabs']), opened=len(record['targets']), intent=(record.get('intent') or {}).get('kind'))
        if self.progress:
            self.progress(record)
        return self.receipt(record)

    def receipt(self, record):
        body = canonical(record)
        return Receipt(record['id'], record['revision'], hashlib.sha256(body.encode()).hexdigest())

    @timed('store.verify')
    def verify(self, receipt):
        current = self.load(receipt.record_id)
        if self.receipt(current) != receipt:
            raise ParkingError('Missing or stale durable-save receipt; no mutation authorized.')
        return current

    def transition(self, record, phase, intent=None, issue=None):
        record.update(phase=phase.value, intent=intent, issue=issue, issue_kind=None)
        if phase != Phase.CLOSING: record.pop('close_confirmed', None)
        record['events'].append(dict(phase=phase.value, intent=intent, issue=issue,
            at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        return self.save(record)


def atomic_json(path, value):
    """Private, durable settings/checkpoint write with readback."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.parking-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        if json.loads(path.read_text()) != value: raise ParkingError('Settings readback failed.')
    finally:
        if os.path.exists(name): os.unlink(name)
