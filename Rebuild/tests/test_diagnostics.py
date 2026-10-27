import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.diagnostics import Trace, timed, event


class DiagnosticsTests(unittest.TestCase):
    def test_nested_timings_preserve_results_and_errors(self):
        @timed('child')
        def child():
            event('checkpoint', count=2)
            raise ValueError('private text must not be logged')
        @timed('parent')
        def parent():
            try: child()
            except ValueError: return 42
        with tempfile.TemporaryDirectory() as directory:
            with Trace(directory, 'test') as trace:
                self.assertEqual(parent(), 42)
            text = trace.path.read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertNotIn('private text', text)
            self.assertEqual(trace.path.stat().st_mode & 0o777, 0o600)
            ends = [r for r in rows if r['event'] == 'span.end']
            self.assertEqual([r['outcome'] for r in ends], ['ValueError', 'ok'])
            self.assertEqual(ends[0]['parent'], ends[1]['span'])
            self.assertTrue(all(r['duration_ms'] >= 0 for r in ends))
            self.assertEqual(rows[-1]['event'], 'operation.end')

    def test_unwritable_logging_does_not_block_work(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'file'
            file.write_text('not a directory')
            with Trace(file / 'blocked', 'test'):
                event('step', count=1)
                self.assertEqual(timed('example')(lambda: 42)(), 42)
