import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pc'))
from bridge import Bridge, BridgeError, ascii_key


class FakeSerial:
    def __init__(self, incoming=b'', chunk=5):
        self.incoming = incoming
        self.chunk = chunk
        self.written = []

    @property
    def in_waiting(self):
        return min(len(self.incoming), self.chunk)

    def read(self, n):
        data, self.incoming = self.incoming[:n], self.incoming[n:]
        return data

    def write(self, data):
        self.written.append(json.loads(data))


def client(incoming=b''):
    bridge = Bridge.__new__(Bridge)
    bridge.session = 'test-session'
    bridge.counter = 0
    bridge.boot_id = 'boot-1'
    bridge.log = None
    bridge.buffer = bytearray()
    bridge.io = FakeSerial(incoming)
    return bridge


def frame(state='sent', **extra):
    return (json.dumps({'id': 1, 'session': 'test-session', 'boot_id': 'boot-1', 'state': state, **extra}) + '\n').encode()


class ClientTests(unittest.TestCase):
    def test_fragmented_serial_and_boot_noise(self):
        bridge = client(b'ROM boot noise\n' + frame('accepted') + frame())
        self.assertEqual(bridge.request('mouse.click')['state'], 'sent')
        self.assertEqual(len(bridge.io.written), 1)

    def test_timeout_never_replays_click(self):
        bridge = client()
        with self.assertRaisesRegex(BridgeError, 'not replayed'):
            bridge.request('mouse.click', timeout=0.01)
        self.assertEqual([p['op'] for p in bridge.io.written], ['mouse.click'])

    def test_reboot_invalidates_pending_input(self):
        bridge = client(frame(boot_id='boot-2'))
        with self.assertRaisesRegex(BridgeError, 'rebooted'):
            bridge.request('keyboard.tap', usage=4)
        self.assertEqual(len(bridge.io.written), 1)

    def test_firmware_error_propagates(self):
        bridge = client(frame('error', error='hid_not_ready'))
        with self.assertRaisesRegex(BridgeError, 'hid_not_ready'):
            bridge.request('mouse.move', dx=1, dy=0)

    def test_non_ascii_rejected_before_any_input(self):
        bridge = client()
        with self.assertRaises(ValueError):
            bridge.type_ascii('abc中文')
        self.assertEqual(bridge.io.written, [])

    def test_us_layout_printable_ascii_is_defined(self):
        for n in range(32, 127):
            usage, modifiers = ascii_key(chr(n))
            self.assertTrue(4 <= usage <= 56)
            self.assertIn(modifiers, (0, 2))
        self.assertEqual(ascii_key('A'), (4, 2))
        self.assertEqual(ascii_key('0'), (39, 0))
        self.assertEqual(ascii_key('?'), (56, 2))


if __name__ == '__main__':
    unittest.main()
