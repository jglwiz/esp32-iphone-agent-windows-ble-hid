"""Token-scoped calibration listener hosted on the API event loop."""
import asyncio
from importlib.resources import files
import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs

class Receiver:
    def __init__(self, gate, host='0.0.0.0', port=8893):
        self.gate=gate
        self.loop=None
        self.thread=None
        self.http=None
        self.socket=socket.socket()
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        self.socket.bind((host,port))
        self.socket.setblocking(False)
        self.server=SimpleNamespace(server_port=self.socket.getsockname()[1])
        self.page=files('iphone_backend').joinpath('calibration_page/index.html').read_bytes()

    async def handle(self, reader, writer):
        status, body, kind=404,b'', 'application/json'
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
            lines=head.decode('ascii').split('\r\n')
            method,path,_=lines[0].split()
            headers=dict(line.split(':',1) for line in lines[1:] if ':' in line)
            headers={k.lower():v.strip() for k,v in headers.items()}
            parsed=urlsplit(path); query=parse_qs(parsed.query)
            if method=='GET':
                if query.get('token',[None])[0]!=self.gate.token: status=403
                elif parsed.path=='/': status,body,kind=200,self.page,'text/html; charset=utf-8'
                elif parsed.path=='/probe':
                    if query.get('session',[None])[0]!=self.gate.page_session: status=403
                    else:
                        with self.gate.lock:
                            self.gate.last_poll=time.monotonic()
                            probe=self.gate.armed
                        status,body=200,json.dumps({'probe':probe}).encode()
            elif method=='POST' and path in ('/ready','/ack','/event'):
                size=int(headers.get('content-length','0'))
                if not 0<size<4096: raise ValueError('size')
                data=json.loads(await asyncio.wait_for(reader.readexactly(size),2))
                g=self.gate
                if path=='/ready':g.ready(data['token'],data['session'],data['width'],data['height'])
                elif path=='/ack':g.ack(data['token'],data['session'],data['probe'])
                else:g.event(data['token'],data['session'],data['probe'],data['kind'],data['x'],data['y'],data['width'],data['height'],data.get('client_time'))
                status=204
        except (ValueError,KeyError,TypeError,UnicodeError,asyncio.IncompleteReadError,asyncio.LimitOverrunError,TimeoutError):
            status=400
        try:
            writer.write((f'HTTP/1.1 {status} Response\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n').encode()+body)
            await asyncio.wait_for(writer.drain(),2)
        except (ConnectionError,TimeoutError):pass
        finally:
            writer.close()
            try:await writer.wait_closed()
            except ConnectionError:pass

    def start(self):
        if self.loop is None:
            self.loop=asyncio.new_event_loop()
            self.thread=threading.Thread(target=self.loop.run_forever,name='standalone-calibration-loop')
            self.thread.start()
        async def start():
            self.http=await asyncio.start_server(self.handle,sock=self.socket,limit=8192)
        asyncio.run_coroutine_threadsafe(start(),self.loop).result(3)

    def close(self):
        async def close():
            if self.http:
                self.http.close()
                await self.http.wait_closed()
            self.socket.close()
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(close(),self.loop).result(3)
            if self.thread:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(3)
                self.loop.close()
        else:self.socket.close()
