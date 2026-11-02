import time
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from iphone_backend.airplay.frames import Frame, FrameCapture, InvalidFrame
from iphone_backend.airplay.routes import install

class FrameRoutes(unittest.TestCase):
    def client(self, value):
        async def reader(client):
            if isinstance(value, Exception):
                raise value
            return value
        app = FastAPI()
        install(app, FrameCapture(reader=reader))
        return TestClient(app)

    def test_stale_snapshot_is_visible_not_fresh(self):
        stamp = time.time()-10
        client = self.client(Frame(b'\xff\xd8\xff\xd9', stamp, 'source:3'))
        response = client.get('/frame')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-frame-id'], 'source:3')
        self.assertEqual(float(response.headers['x-frame-time']), stamp)
        self.assertEqual(client.get('/screen/status').json()['freshness'], 'stale_or_unknown')

    def test_no_frame(self):
        self.assertEqual(self.client(None).get('/frame').status_code, 204)

    def test_invalid(self):
        self.assertEqual(self.client(InvalidFrame('bad')).get('/frame').status_code, 502)

    def test_future_timestamp(self):
        self.assertEqual(self.client(Frame(b'jpeg', time.time()+10, 's:1')).get('/frame').status_code, 502)

    def test_unavailable(self):
        self.assertEqual(self.client(TimeoutError()).get('/frame').status_code, 503)
