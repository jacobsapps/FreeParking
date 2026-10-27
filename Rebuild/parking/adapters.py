"""Local OS boundaries. Constructors and imports never inspect or mutate iTerm."""
from .diagnostics import timed, event

from pathlib import Path
import json
import os
import subprocess
import time
from .model import ParkingError, Uncertain, NotSubmitted


@timed('subprocess')
def run(argv, timeout=15, allow_no_matches=False):
    event('subprocess.request', executable=Path(argv[0]).name)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode and not (allow_no_matches and result.returncode == 1 and not result.stderr.strip()):
        raise ParkingError(result.stderr.strip() or 'Local helper failed with exit ' + str(result.returncode))
    return result.stdout


class ITerm:
    def __init__(self, runner=run, script=None):
        self.runner = runner
        self.script = Path(script) if script else Path(__file__).with_name('terminal.js')

    @timed('terminal.call')
    def call(self, action, **kwargs):
        if action not in ('list', 'close', 'create', 'reorder'):
            raise ParkingError('Unknown terminal adapter operation.')
        event('terminal.request', action=action)
        request = json.dumps(dict(action=action, **kwargs))
        try:
            argv = (['/usr/bin/python3', str(self.script.with_name('layout_api.py')), request] if action == 'reorder'
                    else ['/usr/bin/osascript', '-l', 'JavaScript', str(self.script), request])
            output = self.runner(argv,
                                 timeout=None if action == 'close' else 30)
            result = json.loads(output)
        except Exception as error:
            if action != 'list':
                raise Uncertain('iTerm ' + action + ' result is unconfirmed: ' + str(error)) from error
            raise ParkingError('Cannot inspect iTerm: ' + str(error)) from error
        if action == 'list' and not isinstance(result, list):
            raise ParkingError('iTerm returned an unreadable inventory—not an empty desktop.')
        if action != 'list' and not isinstance(result, dict):
            raise Uncertain('iTerm returned an unreadable operation receipt.')
        if action != 'list' and isinstance(result.get('notSubmitted'), str):
            error = NotSubmitted(result['notSubmitted'])
            error.details = result.get('details')
            raise error
        if action != 'list' and isinstance(result.get('uncertain'), str):
            raise Uncertain(result['uncertain'])
        return result

    @timed('terminal.inventory')
    def windows(self, metadata=True):
        # AppleEvents expose individual properties, not an atomic desktop.
        # Retry only these reads, from scratch. A create/close is never replayed.
        for attempt in range(4):
            try:
                return self.call('list', metadata=metadata)
            except ParkingError as error:
                transient = '-1728' in str(error) or 'FP_READ_RACE' in str(error)
                if not transient:
                    raise
                if attempt == 3:
                    raise Uncertain('iTerm’s layout is still changing. No complete window inventory could be read; nothing was assumed closed. '+str(error)) from error
                time.sleep(0.1 * (attempt + 1))

    def identity_windows(self):
        return self.windows(metadata=False)


class Processes:
    def __init__(self, runner=run):
        self.runner = runner
        self._inspection = None

    @staticmethod
    def parse_files(output, pids):
        """lsof field output, grouped by PID and file (never by display names)."""
        import re
        result = {pid: dict(files=[], cwd=[], listeners=set()) for pid in pids}
        pid = None
        entry = {}
        def finish():
            if pid is None or not entry: return
            row = result[pid]
            name = entry.get('n')
            if name is not None:
                row['files'].append(name)
                if entry.get('f') == 'cwd': row['cwd'].append(name)
                if entry.get('TST') == 'LISTEN' and re.fullmatch(r'127\.0\.0\.1:[0-9]+', name):
                    row['listeners'].add('http://' + name)
        for line in output.splitlines():
            if not line: continue
            if line[0] == 'p':
                finish(); entry = {}
                pid = int(line[1:])
                if pid not in result: raise ParkingError('Unexpected process in file inventory.')
            elif line[0] == 'f':
                finish(); entry = {'f': line[1:]}
            elif line.startswith('TST='): entry['TST'] = line[4:]
            elif line[0] in ('n', 't'): entry[line[0]] = line[1:]
        finish()
        return result

    @timed('process.batch_files')
    def begin_inspection(self, processes, ttys):
        # Only lifetime of one read-only Observer call; never reused across
        # mutation boundaries. A second endpoint/process read closes the scan.
        pids = [pid for pid,p in processes.items() if p['tty'] in ttys and
                (Path(p['executable']).name.lstrip('-') in ('zsh','codex','claude','caffeinate')
                 or '/claude/' in p['executable'])]
        output = self.runner(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(map(str,pids)), '-FpnftT'],
                             allow_no_matches=True) if pids else ''
        self._inspection = self.parse_files(output, pids)

    def end_inspection(self):
        self._inspection = None

    @timed('process.batch_listeners')
    def fresh_listeners(self, pids):
        output = self.runner(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(map(str,pids)),
                              '-iTCP', '-sTCP:LISTEN', '-FpnftT'], allow_no_matches=True) if pids else ''
        return {pid: row['listeners'] for pid,row in self.parse_files(output, pids).items()}


    @timed('process.inventory')
    def all(self):
        result = {}
        for line in self.runner(['/bin/ps', '-axo', 'pid=,ppid=,tty=,lstart=,comm=']).splitlines():
            fields = line.strip().split(None, 8)
            if len(fields) != 9:
                raise ParkingError('Incomplete process inventory; no terminal action authorized.')
            pid, ppid, tty = fields[:3]
            result[int(pid)] = dict(pid=int(pid), ppid=int(ppid), tty='/dev/' + tty,
                                    started=' '.join(fields[3:8]), executable=fields[8])
        if not result:
            raise ParkingError('Empty process inventory; inspection is unavailable.')
        return result

    @timed('process.cwd')
    def cwd(self, pid):
        if self._inspection is not None:
            names = self._inspection.get(pid, {}).get('cwd', [])
            if len(names) != 1: raise ParkingError('Cannot identify this process working directory.')
            return names[0]
        lines = self.runner(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines()
        names = [line[1:] for line in lines if line.startswith('n')]
        if len(names) != 1:
            raise ParkingError('Cannot identify this process working directory.')
        return names[0]

    @timed('process.files')
    def files(self, pid):
        if self._inspection is not None: return self._inspection.get(pid, {}).get('files', [])
        return [line[1:] for line in self.runner(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn']).splitlines() if line.startswith('n')]

    @timed('process.listeners')
    def loopback_listeners(self, pid):
        if self._inspection is not None: return self._inspection.get(pid, {}).get('listeners', set())
        output = self.runner(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid),
                              '-iTCP', '-sTCP:LISTEN', '-Fn'])
        import re
        return {'http://' + line[1:] for line in output.splitlines()
                if re.fullmatch(r'n127\.0\.0\.1:[0-9]+', line)}

    def executable(self, pid):
        import ctypes
        library = ctypes.CDLL('/usr/lib/libproc.dylib')
        function = library.proc_pidpath
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        function.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        if function(pid, buffer, len(buffer)) <= 0:
            raise ParkingError('Cannot verify this helper’s executable path.')
        return buffer.value.decode()

    @timed('process.command')
    def command(self, pid):
        return self.runner(['/bin/ps', '-p', str(pid), '-o', 'args=']).strip()

    @timed('process.birth')
    def started_utc(self, pid):
        return ' '.join(self.runner(['/usr/bin/env', 'TZ=UTC', '/bin/ps', '-p', str(pid), '-o', 'lstart=']).split())

    @timed('process.signal')
    def send_signal(self, saved, number):
        try:
            import signal
            if number not in (signal.SIGINT, signal.SIGTERM):
                raise ParkingError('Unsupported agent signal.')
            current = self.all().get(saved['pid'])
            if not current or current['started'] != saved['started'] or current['tty'] != saved['tty']:
                raise ParkingError('Agent process identity changed; no signal sent.')
            if '/app-server-daemon/' in current['executable']:
                raise ParkingError('Never signal the shared Codex server.')
            os.kill(saved['pid'], number)
        except Exception as error:
            raise NotSubmitted(str(error)) from error
