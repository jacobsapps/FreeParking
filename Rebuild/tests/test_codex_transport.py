"""Wire-level local transport tests, with no live server connections."""
import json
from pathlib import Path
import socket
import struct
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from parking.codex_rpc import LocalWebSocket
from parking.model import ParkingError, Uncertain

class WireTests(unittest.TestCase):
    def setUp(self):
        self.left,self.right=socket.socketpair()
        self.addCleanup(self.left.close);self.addCleanup(self.right.close)
        self.left.settimeout(1);self.right.settimeout(1)
        self.transport=LocalWebSocket.__new__(LocalWebSocket)
        self.transport.connection=self.left;self.transport.buffer=b''

    def test_client_frames_are_masked_and_payload_round_trips(self):
        value=dict(method='turn/interrupt',params=dict(threadId='own',turnId='exact'))
        self.transport.send(value)
        a,b=self.right.recv(2);self.assertEqual(a,0x81);self.assertTrue(b&128)
        n=b&127;mask=self.right.recv(4);body=self.right.recv(n)
        self.assertEqual(json.loads(bytes(x^mask[i%4] for i,x in enumerate(body))),value)

    def test_fragmented_json_and_ping_are_handled(self):
        self.right.sendall(b'\x01\x06{"id":'+b'\x89\x01x'+b'\x80\x021}')
        self.assertEqual(self.transport.receive(),dict(id=1))
        header=self.right.recv(2);self.assertEqual(header[0],0x8a)

    def test_closed_socket_is_uncertain_not_an_empty_reply(self):
        self.right.close()
        with self.assertRaises(Uncertain):self.transport.receive()

    def test_oversized_response_is_rejected_before_payload_read(self):
        self.right.sendall(b'\x81\x7f'+struct.pack('!Q',9*1024*1024))
        with self.assertRaises(ParkingError):self.transport.receive()

    def test_unnegotiated_compression_is_rejected(self):
        self.right.sendall(b'\xc1\x00')
        with self.assertRaises(ParkingError):self.transport.receive()

    def test_control_frame_cannot_be_fragmented(self):
        self.right.sendall(b'\x09\x00')
        with self.assertRaises(ParkingError):self.transport.receive()

    def test_continuation_cannot_start_a_message(self):
        self.right.sendall(b'\x80\x02{}')
        with self.assertRaises(ParkingError):self.transport.receive()

    def test_text_frame_cannot_replace_unfinished_fragment(self):
        self.right.sendall(b'\x01\x01{'+b'\x81\x01}')
        with self.assertRaises(ParkingError):self.transport.receive()

    def test_failed_handshake_closes_socket(self):
        from unittest.mock import Mock, patch
        connection=Mock();connection.recv.return_value=b'HTTP/1.1 500 Failed\r\n\r\n'
        with patch('parking.codex_rpc.socket.socket',return_value=connection):
            with self.assertRaises(ParkingError):LocalWebSocket('/test-only')
        connection.close.assert_called_once()

    def test_connect_failure_also_closes_socket(self):
        from unittest.mock import Mock, patch
        connection=Mock();connection.connect.side_effect=OSError('not available')
        with patch('parking.codex_rpc.socket.socket',return_value=connection):
            with self.assertRaises(OSError):LocalWebSocket('/test-only')
        connection.close.assert_called_once()


class SubmissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock, patch
        from parking.codex_rpc import CodexRPC
        self.rpc = CodexRPC(socket='/fixture-only')
        self.rpc.socket = Mock()
        self.rpc.socket.is_socket.return_value = True
        self.transport = Mock()
        self.transport.initialized = False
        self.sent = []
        self.transport.send.side_effect = self.sent.append
        self.transport.receive.side_effect = lambda: dict(id=self.sent[-1]['id'], result={})
        replacement = patch('parking.codex_rpc.LocalWebSocket', return_value=self.transport)
        replacement.start(); self.addCleanup(replacement.stop)

    def mutate(self, authorize=lambda *args: None):
        return self.rpc.call('thread/unarchive', dict(threadId='fixture-only'),
                             mutation=True, authorize=authorize)

    def assert_no_mutation(self):
        self.assertNotIn('thread/unarchive', [p['method'] for p in self.sent])

    def test_unavailable_socket_is_not_submitted(self):
        from parking.model import NotSubmitted
        self.rpc.socket.is_socket.return_value = False
        with self.assertRaises(NotSubmitted): self.mutate()
        self.assertEqual(self.sent, [])

    def test_failed_connection_is_not_submitted(self):
        from parking.model import NotSubmitted
        from unittest.mock import patch
        with patch('parking.codex_rpc.LocalWebSocket', side_effect=OSError('No connection')):
            with self.assertRaises(NotSubmitted): self.mutate()
        self.assertEqual(self.sent, [])

    def test_failed_initialization_is_not_submitted(self):
        from parking.model import NotSubmitted
        self.transport.receive.side_effect = Uncertain('Lost initialization reply')
        with self.assertRaises(NotSubmitted): self.mutate()
        self.assert_no_mutation()
        self.transport.close.assert_called_once()

    def test_failed_authorization_is_not_submitted(self):
        from parking.model import NotSubmitted
        def reject(*args): raise Uncertain('Inventory changed during permit check')
        with self.assertRaises(NotSubmitted): self.mutate(reject)
        self.assert_no_mutation()

    def test_partial_method_send_is_uncertain_not_retryable(self):
        from parking.model import NotSubmitted
        def send(packet):
            self.sent.append(packet)
            if packet['method'] == 'thread/unarchive': raise OSError('Partial socket send')
        self.transport.send.side_effect = send
        with self.assertRaises(OSError): self.mutate()
        self.assertEqual([p['method'] for p in self.sent].count('thread/unarchive'), 1)

    def test_lost_method_reply_is_uncertain_not_retryable(self):
        self.transport.initialized = True
        self.transport.receive.side_effect = socket.timeout()
        with self.assertRaises(Uncertain): self.mutate()
        self.assertEqual(len(self.sent), 1)

    def test_successful_mutation_is_submitted_once(self):
        self.assertEqual(self.mutate(), {})
        self.assertEqual([p['method'] for p in self.sent], ['initialize', 'initialized', 'thread/unarchive'])

    def test_slow_authorization_does_not_expire_unsent_request_deadline(self):
        from unittest.mock import patch
        now = [100.0]
        def authorize(*args): now[0] += 31
        with patch('parking.codex_rpc.time.monotonic', side_effect=lambda: now[0]):
            self.assertEqual(self.mutate(authorize), {})
        self.assertEqual([p['method'] for p in self.sent].count('thread/unarchive'), 1)
