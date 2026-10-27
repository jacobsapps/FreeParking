"""One persistent JSON connection per recovery operation, no per-tab helpers."""
import atexit
import json
from pathlib import Path
import select
import subprocess
import tempfile
from .diagnostics import timed, event
from .model import ParkingError, NotSubmitted, Uncertain


class NativeITerm:
    def __init__(self, legacy=False):
        self.process=None
        self.errors=None
        self.counter=0
        self.legacy=legacy
        self.legacy_required=legacy

    def close_connection(self):
        process=self.process
        if process is None:return
        self.process=None
        self.legacy=self.legacy_required
        try:
            process.stdin.close()
            # All completed replies are flushed after their transaction ends.
            # This is our private companion, not iTerm or an agent. Stop it
            # directly instead of waiting on the SDK's websocket close timer.
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        finally:
            process.stdout.close()
            if self.errors:self.errors.close();self.errors=None

    def receive(self, timeout):
        if not select.select([self.process.stdout],[],[],timeout)[0]:raise TimeoutError('The iTerm API did not reply in time.')
        line=self.process.stdout.readline()
        if not line:raise ParkingError('The iTerm API connection closed. Enable the Python API in iTerm Settings → General → Magic.')
        return json.loads(line)

    @timed('iterm.connect')
    def connect(self):
        if self.process is not None:return
        self.errors=tempfile.TemporaryFile(mode='w+b')
        self.process=subprocess.Popen(['/usr/bin/python3','-B',str(Path(__file__).with_name('terminal_api.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.errors,text=True,bufsize=1)
        atexit.register(self.close_connection)
        try:
            if self.receive(5)!=dict(ready=True):raise ParkingError('Invalid iTerm API handshake.')
        except Exception as error:
            self.close_connection()
            raise ParkingError('Cannot connect to iTerm’s Python API. Enable it in iTerm Settings → General → Magic. '+str(error)) from error

    @timed('iterm.request')
    def call(self, action, **kwargs):
        if action not in ('list','create','close','reorder'):raise ParkingError('Unknown native terminal action.')
        request=dict(action=action,**kwargs)
        try:
            self.connect()
            if self.legacy:
                # One compatibility read for incomplete saves made with numeric
                # AppleScript window IDs. New saves use stable native UUIDs.
                from .adapters import ITerm
                request['legacy']=ITerm().windows(metadata=False)
                self.legacy=False
        except Exception as error:
            if action != 'list': raise NotSubmitted(str(error)) from error
            raise
        self.counter+=1;request['id']=self.counter
        event('iterm.request',action=action)
        submitted=False
        try:
            submitted=True
            self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
            reply=self.receive(None if action=='close' else 30)
            if reply.get('id')!=request['id']:raise ParkingError('Mismatched iTerm response.')
            result=reply['result']
        except Exception as error:
            self.close_connection()
            if action!='list' and submitted:raise Uncertain('The iTerm operation result is unconfirmed; it will not be repeated. '+str(error)) from error
            raise ParkingError('Cannot inspect iTerm: '+str(error)) from error
        if isinstance(result,dict):
            if 'notSubmitted' in result:raise NotSubmitted(result['notSubmitted'])
            if 'uncertain' in result:raise Uncertain(result['uncertain'])
        if action=='list' and not isinstance(result,list):raise ParkingError('Incomplete iTerm inventory.')
        if action!='list' and not isinstance(result,dict):raise Uncertain('Incomplete iTerm receipt.')
        return result

    def windows(self):return self.call('list')
    def identity_windows(self):return self.call('list',metadata=False)
