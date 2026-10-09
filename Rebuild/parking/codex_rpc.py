"""Bounded WebSocket/JSON-RPC over Codex's existing local Unix socket.

No server is started, updated or signalled. No remote listener or dependency.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
import uuid
from .model import ParkingError, Uncertain


class LocalWebSocket:
    def __init__(self, path):
        self.connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.connection.settimeout(30)
            self.connection.connect(str(path));self.buffer=b''
            key=base64.b64encode(os.urandom(16)).decode()
            self.connection.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+key+'\r\n\r\n').encode())
            while b'\r\n\r\n' not in self.buffer:
                data=self.connection.recv(4096)
                if not data:
                    raise ParkingError('Codex closed the handshake.')
                self.buffer+=data
                if len(self.buffer)>16384:raise ParkingError('Oversized Codex handshake.')
            headers,self.buffer=self.buffer.split(b'\r\n\r\n',1)
            lines=headers.decode().split('\r\n')
            values={k.lower():v.strip() for k,v in (line.split(':',1) for line in lines[1:])}
            accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if (lines[0].split()[1]!='101' or values.get('sec-websocket-accept')!=accept
                    or values.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in [v.strip().lower() for v in values.get('connection', '').split(',')]):
                raise ParkingError('Codex rejected the local WebSocket handshake.')
        except Exception:
            self.connection.close()
            raise

    def close(self):self.connection.close()

    def take(self,size):
        while len(self.buffer)<size:
            data=self.connection.recv(min(65536,size-len(self.buffer)))
            if not data:raise Uncertain('Codex closed the local connection.')
            self.buffer+=data
        result,self.buffer=self.buffer[:size],self.buffer[size:]
        return result

    def send(self,value,opcode=1):
        body=json.dumps(value).encode() if opcode==1 else value
        mask=os.urandom(4);size=len(body)
        prefix=bytes([0x80|opcode])
        prefix+=bytes([0x80|size]) if size<126 else (bytes([0xfe])+struct.pack('!H',size) if size<65536 else bytes([0xff])+struct.pack('!Q',size))
        self.connection.sendall(prefix+mask+bytes(b^mask[i%4] for i,b in enumerate(body)))

    def receive(self):
        message=b''; fragmented=False
        while True:
            a,b=self.take(2);opcode=a&15;size=b&127
            if a&0x70 or b&0x80:raise ParkingError('Unsupported Codex WebSocket frame.')
            if opcode>=8 and (not a&0x80 or size>125):
                raise ParkingError('Invalid Codex WebSocket control frame.')
            if size==126:size=struct.unpack('!H',self.take(2))[0]
            elif size==127:size=struct.unpack('!Q',self.take(8))[0]
            if size+len(message)>8*1024*1024:raise ParkingError('Codex response exceeded the size limit.')
            payload=self.take(size)
            if opcode==8:raise Uncertain('Codex closed its local connection.')
            if opcode==9:self.send(payload,opcode=10);continue
            if opcode==10:continue
            if opcode not in (0,1):raise ParkingError('Unexpected Codex WebSocket message.')
            if (opcode==0 and not fragmented) or (opcode==1 and fragmented):
                raise ParkingError('Invalid Codex WebSocket fragment order.')
            fragmented=True
            message+=payload
            if a&0x80:return json.loads(message)


class CodexRPC:
    def __init__(self, executable='codex', socket=None, authorize_interrupt=None):
        self.socket=Path(socket) if socket else Path.home()/'.codex/app-server-control/app-server-control.sock'
        self.authorize_interrupt=authorize_interrupt
        self._session=None
        self._next_id=0

    @contextmanager
    def connected(self):
        if self._session is not None:raise ParkingError('Codex connection is already in use.')
        self._session=LocalWebSocket(self.socket)
        try:yield self
        finally:
            self._session.close();self._session=None

    def call(self, method, params, mutation=False, authorize=None):
        if not self.socket.is_socket():raise ParkingError('The existing local Codex server is not available.')
        transport=self._session or LocalWebSocket(self.socket)
        deadline=time.monotonic()+30
        def response(identifier):
            for _ in range(1000):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise Uncertain('Codex request deadline expired.')
                transport.connection.settimeout(remaining)
                packet=transport.receive()
                if packet.get('id')==identifier:
                    if 'error' in packet:raise ParkingError(str(packet['error']))
                    return packet['result']
            raise Uncertain('Too many notifications while waiting for Codex.')
        try:
            if not getattr(transport,'initialized',False):
                self._next_id+=1;identifier=self._next_id
                transport.send(dict(id=identifier,method='initialize',params=dict(clientInfo=dict(name='free_parking',version='0.2.0'),capabilities=dict(experimentalApi=True))))
                response(identifier);transport.send(dict(method='initialized',params={}))
                transport.initialized=True
            if mutation:
                permit=authorize or self.authorize_interrupt
                if permit is None:raise ParkingError('Codex interruption requires an exact owned-session permit.')
                permit(params['threadId'],params.get('turnId'))
            self._next_id+=1;identifier=self._next_id
            transport.send(dict(id=identifier,method=method,params=params))
            return response(identifier)
        except (TimeoutError,socket.timeout) as error:
            raise Uncertain('Codex request timed out; no automatic replay.') from error
        finally:
            if self._session is None:transport.close()

    def read(self,sid):
        uuid.UUID(sid)
        thread=self.call('thread/read',dict(threadId=sid,includeTurns=False))['thread']
        turns=self.call('thread/turns/list',dict(threadId=sid,limit=1,sortDirection='desc',itemsView='notLoaded'))
        thread['turns']=turns['data']
        return thread

    def interrupt(self,sid,turn,authorize=None):
        uuid.UUID(sid)
        return self.call('turn/interrupt',dict(threadId=sid,turnId=turn),mutation=True,authorize=authorize)
