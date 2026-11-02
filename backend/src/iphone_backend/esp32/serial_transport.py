"""Serial BLE HID client. Never automatically replay an uncertain input command."""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import serial
from serial.tools import list_ports


class BridgeError(RuntimeError):
    pass


def find_port() -> str:
    ports = [p for p in list_ports.comports() if (p.vid, p.pid) == (0x1A86, 0x55D3)]
    if len(ports) != 1:
        raise BridgeError(f"Expected one CH343, found {len(ports)}; specify --port after checking device identity")
    return ports[0].device


def ascii_key(char: str) -> tuple[int, int]:
    """US keyboard layout: (usage, modifier mask). Reject unsupported text before sending."""
    if len(char) != 1 or ord(char) > 127:
        raise ValueError("Only US-layout ASCII is supported")
    if 'a' <= char <= 'z':
        return ord(char) - ord('a') + 4, 0
    if 'A' <= char <= 'Z':
        return ord(char) - ord('A') + 4, 2
    if char in '1234567890':
        return '1234567890'.index(char) + 30, 0
    if char in '!@#$%^&*()':
        return '!@#$%^&*()'.index(char) + 30, 2
    plain = {'\n': 40, '\t': 43, ' ': 44, '-': 45, '=': 46, '[': 47,
             ']': 48, '\\': 49, ';': 51, "'": 52, '`': 53, ',': 54, '.': 55, '/': 56}
    shifted = dict(zip('_+{}|:"~<>?', [45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56]))
    if char in plain:
        return plain[char], 0
    if char in shifted:
        return shifted[char], 2
    raise ValueError(f"Unsupported ASCII control character: {ord(char)}")


class Bridge:
    def __init__(self, port: str | None = None, log: str | None = None):
        self.session = uuid.uuid4().hex
        self.counter = 0
        self.boot_id = None
        self.identity = {}
        self.log = open(log, 'a', encoding='utf-8') if log else None
        self.io = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
        self.io.dtr = False
        self.io.rts = False
        self.io.port = port or find_port()
        self.io.open()
        self.buffer = bytearray()
        try:
            self.request('hello', timeout=6)
        except BaseException:
            self.io.close()
            if self.log:
                self.log.close()
            raise

    def _record(self, direction, data):
        if self.log:
            self.log.write(json.dumps({'time': time.time(), 'direction': direction, 'data': data}, ensure_ascii=False) + '\n')
            self.log.flush()

    def _write(self, op, **args):
        self.counter += 1
        payload = {'v': 1, 'session': self.session, 'id': self.counter, 'op': op, **args}
        line = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        if len(line) > 512:
            raise ValueError('Frame exceeds firmware limit')
        self._record('tx', payload)
        self.io.write(line + b'\n')
        return self.counter

    def _read(self):
        if b'\n' not in self.buffer:
            self.buffer.extend(self.io.read(max(1, self.io.in_waiting)))
        if len(self.buffer) > 16384:
            self.buffer.clear()
            raise BridgeError('Serial response overflow')
        if b'\n' not in self.buffer:
            return None
        raw, _, remainder = self.buffer.partition(b'\n')
        self.buffer = bytearray(remainder)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self._record('boot_log', raw.decode('utf-8', errors='replace'))
            return None
        if not isinstance(result, dict):
            return None
        self._record('rx', result)
        return result

    def request(self, op: str, timeout: float = 10, **args):
        request_id = self._write(op, **args)
        deadline = time.monotonic() + timeout
        next_ping = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            reply = self._read()
            if reply:
                if self.boot_id and reply.get('boot_id') != self.boot_id:
                    raise BridgeError('Device rebooted; action outcome unknown; not replayed')
                if reply.get('event') == 'lease_expired':
                    raise BridgeError('Session lease expired; reconnect explicitly; not replayed')
                if reply.get('id') == request_id and reply.get('session') == self.session:
                    if reply.get('state') == 'error':
                        raise BridgeError(str(reply))
                    if reply.get('state') in ('ok', 'sent'):
                        self.boot_id = reply['boot_id']
                        if reply.get('device_id'):
                            self.identity = dict(reply)
                        return reply
            if op != 'hello' and time.monotonic() >= next_ping:
                self._write('ping')
                next_ping = time.monotonic() + 0.5
        raise BridgeError(f'Timeout on {op}; outcome unknown; not replayed')

    def type_ascii(self, text: str):
        sequence = [ascii_key(char) for char in text]
        for usage, modifiers in sequence:
            self.request('keyboard.tap', usage=usage, modifiers=modifiers)
        return {'state': 'sent', 'characters': len(sequence)}

    def reset(self, expected_device_id, expected_boot_id):
        before = self.identity
        try:
            before = self.request('status', timeout=2)
        except (BridgeError, serial.SerialException, OSError):
            pass  # Recovery may use last verified identity on this same open handle.
        if before.get('device_id') != expected_device_id or before.get('boot_id') != expected_boot_id:
            raise BridgeError('Device identity changed; reset not sent')
        release_confirmed = False
        try:
            self.request('release_all', timeout=2)
            release_confirmed = True
        except (BridgeError, serial.SerialException, OSError):
            pass
        self.io.dtr = False
        self.io.rts = True
        self.io.dtr = False  # Apply both control lines for Windows driver compatibility.
        time.sleep(.1)
        self.io.rts = False
        self.io.dtr = False
        time.sleep(.5)
        self.io.reset_input_buffer()
        self.buffer.clear()
        self.session = uuid.uuid4().hex
        self.counter = 0
        self.boot_id = None
        self.request('hello', timeout=6)
        after = self.request('status', timeout=2)
        if after.get('device_id') != expected_device_id or after.get('boot_id') == expected_boot_id:
            raise BridgeError('Reset outcome unknown; no repeat')
        return dict(after, reset_release_confirmed=release_confirmed)

    def close(self):
        try:
            if self.boot_id:
                self.request('release_all', timeout=1)
        except (BridgeError, serial.SerialException, OSError):
            pass  # Firmware also releases inputs when the two-second lease expires.
        finally:
            self.io.close()
            if self.log:
                self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Defaults to the single detected CH343')
    parser.add_argument('--log', help='JSONL request/response evidence path')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('status')
    monitor = subs.add_parser('monitor'); monitor.add_argument('--seconds', type=float, default=30)
    move = subs.add_parser('move'); move.add_argument('dx', type=int); move.add_argument('dy', type=int)
    click = subs.add_parser('click'); click.add_argument('--button', type=int, choices=[1, 2, 3], default=1)
    key = subs.add_parser('key'); key.add_argument('usage', type=int); key.add_argument('--modifiers', type=int, default=0)
    text = subs.add_parser('text'); text.add_argument('text')
    scroll = subs.add_parser('scroll'); scroll.add_argument('wheel', type=int)
    subs.add_parser('release')
    args = parser.parse_args()
    # Validate entire text before opening a serial session or sending any key.
    if args.command == 'text':
        for char in args.text:
            ascii_key(char)
    with Bridge(args.port, args.log) as bridge:
        if args.command == 'monitor':
            deadline = time.monotonic() + args.seconds
            previous = None
            while time.monotonic() < deadline:
                result = bridge.request('status')
                comparable = {k: v for k, v in result.items() if k not in ('id', 'uptime_ms')}
                if comparable != previous:
                    print(json.dumps(result), flush=True)
                    previous = comparable
                time.sleep(0.5)
            return
        if args.command == 'status': result = bridge.request('status')
        elif args.command == 'move': result = bridge.request('mouse.move', dx=args.dx, dy=args.dy)
        elif args.command == 'click': result = bridge.request('mouse.click', button=args.button)
        elif args.command == 'key': result = bridge.request('keyboard.tap', usage=args.usage, modifiers=args.modifiers)
        elif args.command == 'text': result = bridge.type_ascii(args.text)
        elif args.command == 'scroll': result = bridge.request('mouse.scroll', wheel=args.wheel)
        else: result = bridge.request('release_all')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (BridgeError, serial.SerialException, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
