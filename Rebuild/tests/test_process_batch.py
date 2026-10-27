import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.adapters import Processes
from parking.model import ParkingError

FILES = "p10\nfcwd\ntDIR\nn/tmp/one\nf3\ntREG\nn/tmp/rollout.jsonl\nf4\ntIPv4\nn127.0.0.1:5001\nTST=LISTEN\np20\nfcwd\ntDIR\nn/tmp/two\nf5\ntIPv4\nn127.0.0.1:5002->127.0.0.1:80\nTST=ESTABLISHED\n"

class BatchProcessTests(TestCase):
    def test_one_file_read_supplies_metadata_only_until_observation_ends(self):
        runner = Mock(return_value=FILES)
        process = Processes(runner)
        rows = {pid: dict(tty='/dev/test', executable='/bin/codex') for pid in [10,20]}
        process.begin_inspection(rows, {'/dev/test'})
        self.assertEqual(process.cwd(10), '/tmp/one')
        self.assertEqual(process.cwd(20), '/tmp/two')
        self.assertIn('/tmp/rollout.jsonl', process.files(10))
        self.assertEqual(process.loopback_listeners(10), {'http://127.0.0.1:5001'})
        self.assertEqual(process.loopback_listeners(20), set())
        self.assertEqual(runner.call_count, 1)
        runner.return_value = FILES.replace(':5001', ':6001')
        self.assertEqual(process.fresh_listeners([10,20])[10], {'http://127.0.0.1:6001'})
        self.assertEqual(runner.call_count, 2)
        process.end_inspection()
        runner.return_value = 'n/tmp/new\n'
        self.assertEqual(process.cwd(10), '/tmp/new')
        self.assertEqual(runner.call_count, 3)

    def test_missing_pid_and_non_loopback_sockets_are_not_invented(self):
        rows = Processes.parse_files(FILES.replace('127.0.0.1:5001', '*:5001'), [10,20,30])
        self.assertEqual(rows[10]['listeners'], set())
        self.assertEqual(rows[30]['files'], [])
        process = Processes(Mock(return_value=''))
        process.begin_inspection({10: dict(tty='/dev/test', executable='/bin/codex')}, {'/dev/test'})
        with self.assertRaises(ParkingError): process.cwd(10)
        with self.assertRaises(ParkingError): Processes.parse_files(FILES, [10])
