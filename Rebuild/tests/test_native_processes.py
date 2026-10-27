"""Verify real kernel reads against an owned child, never user processes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).parents[1]))
from parking.native_processes import NativeProcesses, KernelProcesses
from parking.model import ParkingError


class CacheTests(unittest.TestCase):
    def test_denied_files_are_not_empty_endpoint_proof(self):
        native=Mock();native.inspect.side_effect=ParkingError('denied')
        processes=KernelProcesses(native)
        processes.begin_inspection({42:dict(tty='/dev/test',executable='/bin/zsh')},{'/dev/test'})
        for read in (processes.files,processes.cwd,processes.loopback_listeners):
            with self.assertRaises(ParkingError):read(42)

    def test_endpoint_revalidation_does_not_use_observation_cache(self):
        native=Mock();native.inspect.side_effect=[dict(files=[],listeners={'old'}),dict(files=[],listeners={'fresh'})]
        native.cwd.return_value='/tmp'
        processes=KernelProcesses(native)
        processes.begin_inspection({42:dict(tty='/dev/test',executable='/bin/zsh')},{'/dev/test'})
        self.assertEqual(processes.loopback_listeners(42),{'old'})
        self.assertEqual(processes.fresh_listeners([42]),{42:{'fresh'}})
        processes.end_inspection();self.assertIsNone(processes._inspection)


@unittest.skipUnless(sys.platform=='darwin' and (Path(__file__).parents[2]/'.build/libparking_process.dylib').exists(),'requires built macOS inspection library')
class KernelTests(unittest.TestCase):
    def test_birth_cwd_open_file_argv_and_exact_listener(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'owned marker.txt';marker.write_text('fixture')
            script="import socket,sys; f=open(sys.argv[1]); s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1],flush=True); sys.stdin.readline()"
            child=subprocess.Popen([sys.executable,'-u','-c',script,str(marker)],cwd=folder,
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            try:
                port=int(child.stdout.readline());native=NativeProcesses()
                row=native.all()[child.pid]
                self.assertEqual(row['ppid'],os.getpid())
                self.assertTrue(row['started']);self.assertTrue(native.started_utc(child.pid))
                self.assertEqual(Path(native.cwd(child.pid)).resolve(),Path(folder).resolve())
                files=native.inspect(child.pid)
                self.assertIn(str(marker.resolve()),files['files'])
                self.assertEqual(files['listeners'],{'http://127.0.0.1:'+str(port)})
                self.assertIn(str(marker),native.command(child.pid))
                self.assertEqual(native.inspect(child.pid,True)['listeners'],files['listeners'])
            finally:
                child.stdin.write('\n');child.stdin.flush();child.wait(timeout=5)
                child.stdin.close();child.stdout.close()
            self.assertIsNone(native.info(child.pid))
