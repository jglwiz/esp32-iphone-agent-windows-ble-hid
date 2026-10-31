"""Bounded, cancellable capture. No serial access or receiver lifecycle control."""
import asyncio
import base64
from dataclasses import dataclass
import math
import time

import httpx


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    timestamp: float
    frame_id: str
    snapshot_version: int = 1


class InvalidFrame(ValueError):
    pass


class FrameCapture:
    def __init__(self, url='http://127.0.0.1:8891/frame', reader=None, allow_legacy=False):
        self.url = url
        self.reader = reader or self.read
        self.allow_legacy = allow_legacy

    async def read(self, client):
        async with client.stream('GET', self.url) as response:
            response.raise_for_status()
            atomic = response.headers.get('X-Frame-Snapshot-Version') == '1'
            if not atomic and not self.allow_legacy:
                raise InvalidFrame('Frame source lacks atomic snapshot v1; upgrade capture source')
            if response.status_code == 204:
                return None
            try:
                timestamp = float(response.headers['X-Frame-Time'])
                frame_id = response.headers['X-Frame-Id'] if atomic else ''
                if (atomic and not frame_id) or not math.isfinite(timestamp):
                    raise ValueError('Invalid frame metadata')
            except (KeyError, ValueError) as exc:
                raise InvalidFrame('Invalid frame timestamp or ID') from exc
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    raise InvalidFrame('Frame exceeds 8 MiB')
            if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                raise InvalidFrame('Invalid JPEG envelope')
            return Frame(bytes(data), timestamp, frame_id, 1 if atomic else 0)

    async def capture(self, after, deadline, cancelled):
        """deadline uses perf_counter; caller releases all input before entering."""
        end = min(deadline, time.perf_counter() + 2.5)
        status = {'state': 'unavailable', 'reason': 'No frame before capture deadline'}
        if cancelled():
            return {'state': 'cancelled', 'reason': 'Capture cancelled'}, None
        if time.perf_counter() >= end:
            return status, None

        async def observe():
            nonlocal status
            async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
                while time.perf_counter() < end:
                    try:
                        frame = await asyncio.wait_for(self.reader(client),
                            timeout=min(.7, max(0, end-time.perf_counter())))
                        if frame is None:
                            status = {'state': 'unavailable', 'reason': 'HTTP 204 / no frame'}
                        else:
                            received = time.time()
                            age = received - frame.timestamp
                            if not math.isfinite(frame.timestamp) or age < -1:
                                raise InvalidFrame('Invalid timestamp / clock mismatch')
                            if age > 5 or (after is not None and frame.timestamp < after):
                                status = {'state': 'stale_or_unknown',
                                          'reason': 'No fresh frame after settlement; static versus stalled unknown'}
                            else:
                                result = {'jpeg_base64': base64.b64encode(frame.jpeg).decode(),
                                          'frame_id': frame.frame_id, 'frame_time': frame.timestamp,
                                          'received_at': received, 'age_seconds': age,
                                          'after_action': None if after is None else frame.timestamp >= after,
                                          'snapshot_version': frame.snapshot_version,
                                          'atomic_snapshot': frame.snapshot_version == 1}
                                if after is not None:
                                    result['settled_at'] = after
                                return {'state': 'available'}, result
                    except InvalidFrame as exc:
                        return {'state': 'invalid', 'reason': str(exc)}, None
                    except TimeoutError:
                        # Keep the last observed stale-frame evidence when a final retry
                        # exhausts the remaining budget; don't turn it into "no frame".
                        if status['state'] != 'stale_or_unknown':
                            status = {'state': 'unavailable', 'reason': 'Frame read timed out'}
                    except httpx.HTTPError as exc:
                        status = {'state': 'unavailable', 'reason': str(exc) or 'Frame read timed out'}
                    await asyncio.sleep(min(.02, max(0, end-time.perf_counter())))
            return status, None

        task = asyncio.create_task(observe())
        try:
            while True:
                if cancelled():
                    return {'state': 'cancelled', 'reason': 'Capture cancelled'}, None
                remaining = end - time.perf_counter()
                if remaining <= 0:
                    return status, None
                if task.done():
                    return task.result()
                # Poll the thread-safe cancellation flag; early timer wakeups recompute budget.
                await asyncio.wait((task,), timeout=min(.01, remaining))
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
