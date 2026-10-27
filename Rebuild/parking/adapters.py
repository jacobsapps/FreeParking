"""Local OS boundaries. Constructors and imports never inspect or mutate iTerm."""
from pathlib import Path
import json
import os
import subprocess
from .model import ParkingError, Uncertain, NotSubmitted


def run(argv, timeout=15):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise ParkingError(result.stderr.strip() or 'Local helper failed with exit ' + str(result.returncode))
    return result.stdout


class ITerm:
    def __init__(self, runner=run, script=None):
        self.runner = runner
        self.script = Path(script) if script else Path(__file__).with_name('terminal.js')

    def call(self, action, **kwargs):
        if action not in ('list', 'close', 'create'):
            raise ParkingError('Unknown terminal adapter operation.')
        request = json.dumps(dict(action=action, **kwargs))
        try:
            output = self.runner(['/usr/bin/osascript', '-l', 'JavaScript', str(self.script), request],
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
            raise NotSubmitted(result['notSubmitted'])
        return result

    def windows(self):
        return self.call('list')


class Processes:
    def __init__(self, runner=run):
        self.runner = runner

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

    def cwd(self, pid):
        lines = self.runner(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines()
        names = [line[1:] for line in lines if line.startswith('n')]
        if len(names) != 1:
            raise ParkingError('Cannot identify this process working directory.')
        return names[0]

    def files(self, pid):
        return [line[1:] for line in self.runner(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn']).splitlines() if line.startswith('n')]

    def loopback_listeners(self, pid):
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

    def command(self, pid):
        return self.runner(['/bin/ps', '-p', str(pid), '-o', 'args=']).strip()

    def started_utc(self, pid):
        return ' '.join(self.runner(['/usr/bin/env', 'TZ=UTC', '/bin/ps', '-p', str(pid), '-o', 'lstart=']).split())

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
