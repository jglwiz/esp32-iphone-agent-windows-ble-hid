import threading
import time
import unittest
from iphone_backend.esp32.service import SerialService

class Transport:
    def __init__(self):
        self.threads = [threading.get_ident()]
        self.operations = []
    def request(self, op, **kwargs):
        self.threads.append(threading.get_ident())
        self.operations.append(op)
        return {'boot_id':'one'}
    def close(self):
        self.threads.append(threading.get_ident())

class SerialActorTests(unittest.TestCase):
    def test_single_owner_and_independent_heartbeat(self):
        transport = []
        def factory():
            value = Transport()
            transport.append(value)
            return value
        actor = SerialService(factory)
        try:
            actor.request('status')
            time.sleep(.6)
            self.assertIn('ping', transport[0].operations)
        finally:
            actor.close()
        self.assertEqual(set(transport[0].threads), {actor.thread.ident})
        self.assertFalse(actor.thread.is_alive())

    def test_failed_open_leaves_no_owner_thread(self):
        def fail(): raise RuntimeError('unplugged')
        with self.assertRaisesRegex(RuntimeError, 'unplugged'):
            SerialService(fail)
