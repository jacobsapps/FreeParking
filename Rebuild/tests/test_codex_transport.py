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
