import asyncio
import json
import unittest
from iphone_backend.airplay.capture_worker import handle
from iphone_backend.airplay.frame_snapshot import FrameStore

class CaptureHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_snapshot_and_no_frame(self):
        screen=FrameStore()
        server=await asyncio.start_server(lambda r,w:handle(screen,r,w),'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        async def request():
            r,w=await asyncio.open_connection('127.0.0.1',port)
            w.write(b'GET /frame HTTP/1.1\r\nHost: localhost\r\n\r\n')
            await w.drain()
            result=await r.read()
            w.close()
            await w.wait_closed()
            return result
        try:
            self.assertIn(b'204 No Content',await request())
            frame=screen.publish(b'\xff\xd8\xff\xd9',123.0)
            responses=await asyncio.gather(*(request() for _ in range(20)))
            for result in responses:
                self.assertIn(frame.frame_id.encode(),result)
                self.assertIn(b'X-Frame-Time: 123.0',result)
                self.assertTrue(result.endswith(frame.jpeg))
        finally:
            server.close()
            await server.wait_closed()
