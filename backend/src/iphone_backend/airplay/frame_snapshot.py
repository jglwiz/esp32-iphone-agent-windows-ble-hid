"""Immutable frame publication shared by the pipe producer and HTTP reader."""
from dataclasses import dataclass
import threading
import uuid


@dataclass(frozen=True)
class FrameSnapshot:
    jpeg: bytes
    timestamp: float
    frame_id: str


class FrameStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._instance = uuid.uuid4().hex
        self._sequence = 0
        self._value = None

    def publish(self, jpeg, timestamp):
        with self._lock:
            self._sequence += 1
            self._value = FrameSnapshot(bytes(jpeg), timestamp,
                                        f'{self._instance}:{self._sequence}')
            return self._value

    def clear(self):
        with self._lock:
            self._value = None

    def snapshot(self):
        with self._lock:
            return self._value
