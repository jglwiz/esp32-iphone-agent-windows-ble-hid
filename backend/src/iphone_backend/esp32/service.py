"""Process-local serial actor. Only its thread may touch the transport."""
from concurrent.futures import Future
import queue
import threading
import time

from .serial_transport import Bridge

class StartupFailure(RuntimeError):
    def __init__(self, message, cleanup_known):
        super().__init__(message)
        self.cleanup_known=cleanup_known

class SerialService:
    def __init__(self, factory=Bridge, **kwargs):
        self.factory, self.kwargs = factory, kwargs
        self.jobs = queue.Queue(maxsize=32)
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.fault = None
        self.status = None
        self.transport = None
        self.close_error = None
        self.thread = threading.Thread(target=self._run, name='esp32-serial-owner')
        self.thread.start()
        if not self.ready.wait(8):
            try:self.close()
            except RuntimeError as exc:raise StartupFailure(str(exc),False) from exc
            raise StartupFailure('Serial actor startup timed out',True)
        if self.fault:
            try:self.close()
            except RuntimeError as exc:raise StartupFailure(str(exc),False) from exc
            raise StartupFailure(self.fault,True)

    def _run(self):
        try:
            try:
                self.transport = self.factory(**self.kwargs)
            except Exception as exc:
                self.fault = str(exc)
            finally:
                self.ready.set()
            next_ping = time.monotonic()+.4
            while not self.stopping.is_set():
                try:
                    op, kwargs, deadline, future = self.jobs.get(timeout=max(.001, min(.1, next_ping-time.monotonic())))
                except queue.Empty:
                    if self.transport and not self.fault and time.monotonic() >= next_ping:
                        try:
                            self.status = self.transport.request('ping', timeout=1)
                        except Exception as exc:
                            self.fault = str(exc)
                        next_ping = time.monotonic()+.4
                    continue
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Serial command expired before execution')
                    if not self.transport:
                        raise RuntimeError(self.fault or 'Serial unavailable')
                    if op == '__reset__':
                        result = self.transport.reset(**kwargs)
                        self.fault = None
                    else:
                        if self.fault and op != 'release_all':
                            raise RuntimeError(self.fault)
                        result = self.transport.request(op, timeout=max(.01, deadline-time.monotonic()), **kwargs)
                    if op in ('status','ping','__reset__'):
                        self.status = result
                    future.set_result(result)
                except Exception as exc:
                    self.fault = str(exc)
                    future.set_exception(exc)
                next_ping = time.monotonic()+.4
        finally:
            try:
                if self.transport:
                    self.transport.close()
            except Exception as exc:
                self.close_error = exc
            finally:
                while not self.jobs.empty():
                    *_, future = self.jobs.get_nowait()
                    if not future.done(): future.set_exception(RuntimeError('Serial service stopped'))

    def request(self, op, timeout=10, **kwargs):
        if self.stopping.is_set(): raise RuntimeError('Serial service stopped')
        future = Future()
        self.jobs.put_nowait((op, kwargs, time.monotonic()+timeout, future))
        try:
            return future.result(timeout=timeout+.2)
        except TimeoutError:
            future.cancel()
            raise TimeoutError('Serial result unknown; command not replayed')

    def reset(self, **kwargs):
        return self.request('__reset__', timeout=15, **kwargs)

    def close(self):
        self.stopping.set()
        self.thread.join(16)
        if self.thread.is_alive(): raise RuntimeError('Serial owner did not stop')
        if self.close_error: raise RuntimeError('Serial cleanup failed: '+str(self.close_error)) from self.close_error
