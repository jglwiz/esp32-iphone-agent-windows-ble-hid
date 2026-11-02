r"""Windows: the phone's screen arrives as raw I420 frames through a named pipe.

The receiver (uxplay-windows) is driven by a configuration file (see `_write_config`) that
ends its video pipeline in `filesink location=\\.\pipe\hidagent-frames`, so decoded frames
land in a pipe this process owns.  No display window and no GPU rendering are involved --
the D3D video sinks of the bundled GStreamer crash on this machine (they stall the pipeline
about half a second after the phone starts streaming, which drops the connection).

Frames come out fixed-size (I420 at W x H, enforced by caps in the configuration file), so
the byte stream frames itself; we convert a rate-limited subset to JPEG and expose the same
interface as screen.Screen (frame / updated / live / aspect / settled / fresh).
"""

import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .frame_snapshot import FrameStore
from .receiver_process import EXE_DIR, PROC_NAME, _pids_of

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("HIDAGENT_DATA", Path.home() / ".hidagent"))
RC_FILE = DATA_DIR / "uxplayrc.conf"  # the receiver's options live here (read via -rc)
ARGS_FILE = Path(os.environ.get("APPDATA", "")) / "leapbtw" / "uxplay-windows" / "arguments.txt"
PIPE_NAME = r"\\.\pipe\hidagent-frames"
# The width must be divisible by 4: GStreamer aligns every I420 row to 4 bytes, so a width
# like 498 would come out with a 500-byte Y stride and we would slice the stream 2 bytes off
# per row (the picture shears into diagonal colour bands).  496 has no padding at all.
W, H = 496, 1080  # must match the caps in RC_LINES below (portrait iPhone aspect)
FRAME = W * H * 3 // 2  # I420, tight-packed

# The file the engine reads literally contains the doubled backslashes: its rc parser
# treats a backslash as an escape, so `\\\\.\\pipe\\...` is what it unescapes to the
# real path.  The wrapper's own argument file only points at this file (it strips -vs
# pairs from arguments.txt, but never sees options coming from an rc file).
RC_LINES = (
    "n {name}\n"
    "nh\n"
    "avdec\n"
    "reset 0\n"
    "d\n"
    f"s {W}x{H}\n"
    f'vc "videoconvert ! videoscale ! video/x-raw,format=I420,width={W},height={H}"\n'
    r'vs "filesink location=\\\\.\\pipe\\hidagent-frames"' "\n"
)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateNamedPipeW.restype = wt.HANDLE
_k32.CreateNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPVOID]
_k32.ConnectNamedPipe.argtypes = [wt.HANDLE, wt.LPVOID]
_k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
_k32.CloseHandle.argtypes = [wt.HANDLE]

PIPE_ACCESS_INBOUND = 0x00000001
ERROR_PIPE_CONNECTED = 535


class Screen:
    """Keeps the latest JPEG of the phone's screen, read from the receiver's pipe."""

    def __init__(self, name: str = "iPhone Agent", fps: int = 8, quality: int = 75):
        self.name, self.fps, self.quality = name, fps, quality
        self.frame, self.updated = None, 0.0
        self._stopping = False
        self._frames = FrameStore()
        self._pipe = None
        self._seen_at, self._seen_n = 0.0, False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._supervise, daemon=True, name='airplay-frame-reader')
        self.thread.start()

    def stop(self) -> None:
        """Stop reading.  The receiver keeps running (it is a standalone tray app)."""
        self._stopping = True
        if self._pipe is not None:
            _k32.CloseHandle(self._pipe)  # unblocks the reader thread (or it dies with us)

    @property
    def live(self) -> bool:
        return self._running and self.frame is not None and time.time() - self.updated < 90

    def snapshot(self):
        return self._frames.snapshot()

    def publish_frame(self, jpeg, timestamp):
        snapshot = self._frames.publish(jpeg, timestamp)
        # Retain the legacy Screen interface; HTTP uses snapshot() exclusively.
        self.frame, self.updated = snapshot.jpeg, snapshot.timestamp

    def clear_frame(self):
        self._frames.clear()
        self.frame = None

    @property
    def _running(self) -> bool:
        if time.time() - self._seen_at >= 1:  # a process listing is not free
            self._seen_n = bool(_pids_of(PROC_NAME))
            self._seen_at = time.time()
        return self._seen_n

    def _supervise(self) -> None:
        """Write the receiver config, then keep a pipe open and read whatever the receiver sends."""
        while not self._stopping:
            try:
                self._write_config()
                self._pipe = self._create_pipe()
                self._ensure_receiver()
                log.info("screen: waiting for the receiver to connect to the frame pipe")
                if not self._connect(self._pipe):
                    time.sleep(2)
                    continue
                log.info("screen: frame pipe connected")
                self._read_loop(self._pipe)
            except Exception as e:
                log.warning("screen: pipe session failed: %s", e)
            finally:
                if self._pipe is not None:
                    _k32.CloseHandle(self._pipe)
                    self._pipe = None
            self.clear_frame()
            if not self._stopping:
                log.info("screen: pipe closed; reopening frame pipe without restarting receiver")
                time.sleep(2)
        log.info("screen: pipe loop stopped")

    def _write_config(self) -> None:
        """Keep the receiver's config in sync: our rc file holds the whole pipeline.

        (The wrapper's argument file cannot carry `-vs` -- the wrapper strips those pairs --
        and the engine's own rc-file reader is the one place a quoted, multi-word value
        survives, so the frame pipe is configured from there.)
        """
        RC_FILE.parent.mkdir(parents=True, exist_ok=True)
        RC_FILE.write_text(RC_LINES.format(name=self.name.replace(" ", "-")), encoding="ascii")
        ARGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ARGS_FILE.write_text(f"-rc {RC_FILE}", encoding="ascii")

    def _create_pipe(self) -> int:
        handle = _k32.CreateNamedPipeW(PIPE_NAME, PIPE_ACCESS_INBOUND | 0x00080000, 0, 1, 1 << 16, 1 << 16, 0, None)
        if not handle or handle in (-1, 2 ** 64 - 1):
            raise OSError(f"CreateNamedPipe failed, error {ctypes.get_last_error()}")
        return handle

    def _connect(self, pipe: int) -> bool:
        if _k32.ConnectNamedPipe(pipe, None):
            return True
        return ctypes.get_last_error() == ERROR_PIPE_CONNECTED

    def _ensure_receiver(self) -> None:
        # The receiver belongs to the user and may have an active mirroring session.
        # A backend restart or broken frame pipe must never terminate it.
        if _pids_of(PROC_NAME):
            log.info("screen: reusing running receiver; leaving its session untouched")
            return
        exe = EXE_DIR / PROC_NAME
        if not exe.exists():
            log.error("screen: receiver not found: %s (set UXPLAY_DIR)", exe)
            return
        log.info("screen: starting receiver %s", exe)
        subprocess.Popen([str(exe)], cwd=str(EXE_DIR),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)

    def _read_loop(self, pipe: int) -> None:
        buf = ctypes.create_string_buffer(1 << 16)
        n = wt.DWORD()
        pending = b""
        gap = 1.0 / self.fps
        last_kept = 0.0
        while not self._stopping:
            if not _k32.ReadFile(pipe, buf, 1 << 16, ctypes.byref(n), None):
                return  # pipe closed or handle closed by stop()
            if n.value == 0:
                return
            pending += buf.raw[:n.value]
            while len(pending) >= FRAME:
                frame, pending = pending[:FRAME], pending[FRAME:]
                now = time.time()
                if now - last_kept < gap:
                    continue  # skip frames: encode at most `fps` per second
                last_kept = now
                try:
                    jpeg = self._encode(frame)
                except Exception as e:
                    log.warning("screen: frame encode failed: %s", e)
                    continue
                self.publish_frame(jpeg, now)

    def _encode(self, i420: bytes) -> bytes:
        raw = np.frombuffer(i420, np.uint8).reshape(H * 3 // 2, W)
        bgr = cv2.cvtColor(raw, cv2.COLOR_YUV2BGR_I420)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return buf.tobytes()
