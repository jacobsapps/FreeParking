"""Direct libproc reads through a small SDK-compiled C ABI; no subprocesses."""
import ctypes as C
import errno
import os
from pathlib import Path
import struct
import time
from .diagnostics import timed
from .model import ParkingError

class ProcessInfo(C.Structure):
    _fields_ = [('pid',C.c_int32),('ppid',C.c_int32),('seconds',C.c_uint64),('micros',C.c_uint64),
                ('tty',C.c_char*128),('executable',C.c_char*4096)]
class FileInfo(C.Structure):
    _fields_ = [('kind',C.c_int32),('value',C.c_char*4096)]

class NativeProcesses:
    def __init__(self, path=None):
        if path is None:
            path=Path(__file__).parents[1]/'libparking_process.dylib'
            if not path.exists(): path=Path(__file__).parents[2]/'.build/libparking_process.dylib'
        self.lib=C.CDLL(str(path), use_errno=True)
        self.lib.fp_pids.argtypes=[C.POINTER(C.c_int32),C.c_int]
        self.lib.fp_process.argtypes=[C.c_int32,C.POINTER(ProcessInfo)]
        self.lib.fp_cwd.argtypes=[C.c_int32,C.c_void_p,C.c_int]
        self.lib.fp_files.argtypes=[C.c_int32,C.POINTER(FileInfo),C.c_int,C.c_int]
        self.lib.fp_argv.argtypes=[C.c_int32,C.c_void_p,C.c_int]

    @staticmethod
    def check(result):
        if result < 0: raise ParkingError('Native process inspection failed: '+os.strerror(-result))
        return result

    def info(self, pid):
        row=ProcessInfo();result=self.lib.fp_process(pid,C.byref(row))
        if result in (-errno.ESRCH,-errno.ENOENT): return None
        self.check(result)
        return row

    @timed('kernel.process_inventory')
    def all(self):
        size=4096
        while size<=131072:
            pids=(C.c_int32*size)();count=self.lib.fp_pids(pids,size)
            if count!=-errno.ENOSPC: break
            size*=2
        self.check(count);result={}
        for pid in pids[:count]:
            if pid<=0: continue
            row=self.info(pid)
            if row is None: continue
            result[pid]=dict(pid=pid,ppid=row.ppid,tty=row.tty.decode(),
                started=' '.join(time.strftime('%a %b %e %H:%M:%S %Y',time.localtime(row.seconds)).split()),
                executable=row.executable.decode())
        if not result: raise ParkingError('Native process inventory is empty.')
        return result

    @timed('kernel.cwd')
    def cwd(self,pid):
        buf=C.create_string_buffer(4096);self.check(self.lib.fp_cwd(pid,buf,len(buf)))
        return buf.value.decode()

    @timed('kernel.files')
    def inspect(self,pid,sockets_only=False):
        size=256
        while size<=65536:
            rows=(FileInfo*size)();count=self.lib.fp_files(pid,rows,size,int(sockets_only))
            if count!=-errno.ENOSPC:break
            size*=2
        self.check(count)
        return dict(files=[r.value.decode() for r in rows[:count] if r.kind==1],
                    listeners={r.value.decode() for r in rows[:count] if r.kind==2})

    def started_utc(self,pid):
        row=self.info(pid)
        if row is None:raise ParkingError('The process exited during inspection.')
        return ' '.join(time.strftime('%a %b %e %H:%M:%S %Y',time.gmtime(row.seconds)).split())

    def command(self,pid):
        buf=C.create_string_buffer(1024*1024);size=self.check(self.lib.fp_argv(pid,buf,len(buf)))
        data=buf.raw[:size]
        if len(data)<4:raise ParkingError('Native argument list is incomplete.')
        argc=struct.unpack_from('i',data)[0]
        end=data.find(b'\0',4)
        if end<0 or not 0<argc<=65536:raise ParkingError('Native argument list is unreadable.')
        start=end+1
        while start<len(data) and data[start]==0:start+=1
        argv=data[start:].split(b'\0')[:argc]
        if len(argv)!=argc:raise ParkingError('Native argument list is incomplete.')
        return ' '.join(arg.decode() for arg in argv)

from .adapters import Processes

class KernelProcesses(Processes):
    def __init__(self, native=None):
        super().__init__()
        self._native=native

    @property
    def native(self):
        if self._native is None:self._native=NativeProcesses()
        return self._native

    def all(self):return self.native.all()
    def command(self,pid):return self.native.command(pid)
    def started_utc(self,pid):return self.native.started_utc(pid)

    def begin_inspection(self,processes,ttys):
        rows={}
        for pid,p in processes.items():
            if p['tty'] not in ttys or not (Path(p['executable']).name.lstrip('-') in ('zsh','codex','claude','caffeinate') or '/claude/' in p['executable']):continue
            try:
                rows[pid]={**self.native.inspect(pid),'cwd':[self.native.cwd(pid)]}
            except ParkingError:
                # Keep the failure attached to this PID. Never use an empty
                # result as proof that an inaccessible agent has no endpoint.
                rows[pid]={'error':True}
        self._inspection=rows

    def cached(self,pid):
        row=self._inspection.get(pid,{})
        if row.get('error'):raise ParkingError('Cannot inspect this process’s files or working folder.')
        return row

    def cwd(self,pid):
        if self._inspection is None:return self.native.cwd(pid)
        paths=self.cached(pid).get('cwd',[])
        if len(paths)!=1:raise ParkingError('Cannot identify this process working directory.')
        return paths[0]

    def files(self,pid):
        return (self.cached(pid) if self._inspection is not None else self.native.inspect(pid))['files']

    def loopback_listeners(self,pid):
        return (self.cached(pid) if self._inspection is not None else self.native.inspect(pid,True))['listeners']

    def fresh_listeners(self,pids):
        return {pid:self.native.inspect(pid,True)['listeners'] for pid in pids}
