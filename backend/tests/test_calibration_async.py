import asyncio
import unittest
import httpx
from iphone_backend.calibration import EventGate
from iphone_backend.calibration_receiver import Receiver

class CalibrationHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_listener_uses_current_loop_and_token_gate(self):
        gate=EventGate()
        receiver=Receiver(gate,host='127.0.0.1',port=0)
        receiver.loop=asyncio.get_running_loop()
        await asyncio.to_thread(receiver.start)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{receiver.server.server_port}',trust_env=False) as client:
                self.assertEqual((await client.get('/')).status_code,403)
                response=await client.get('/',params={'token':gate.token})
                self.assertEqual(response.status_code,200)
                self.assertIn('text/html',response.headers['content-type'])
                response=await client.post('/ready',json={'token':gate.token,'session':'test','width':320,'height':600})
                self.assertEqual(response.status_code,204)
                self.assertEqual(gate.page_session,'test')
                self.assertIsNone(receiver.thread)
        finally:
            await asyncio.to_thread(receiver.close)
