"""Thu stdout/stderr theo byte budget chung; không dùng communicate/capture_output.

Chỉ quản lý cây tiến trình CLI do chính lời gọi tạo, không quản lý containers.
Linux dùng process group riêng; Windows dùng Job Object kill-on-close.
"""
from dataclasses import dataclass
import math
import os
import signal
import subprocess
import threading
import time

DEFAULT_BUDGET = 1024 * 1024
CHUNK_SIZE = 4096


@dataclass
class Capture:
    stdout: str
    stderr: str
    returncode: int
    retained_bytes: int
    observed_bytes: int
    budget_bytes: int
    truncated: bool = False
    timed_out: bool = False
    decode_replaced: bool = False
    read_error: bool = False

    @property
    def complete(self):
        return not (self.truncated or self.timed_out or self.decode_replaced or self.read_error)

    def metadata(self):
        return {key: getattr(self, key) for key in (
            'returncode', 'retained_bytes', 'observed_bytes', 'budget_bytes',
            'truncated', 'timed_out', 'decode_replaced', 'read_error', 'complete')}


class _WindowsJob:
    """Không cho tiến trình con sống tiếp sau khi collector đóng job của mình."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)

        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                        ('LimitFlags', w.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                        ('MaxWorkingSet', ctypes.c_size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', ctypes.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                'ReadOperations', 'WriteOperations', 'OtherOperations', 'ReadBytes', 'WriteBytes', 'OtherBytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                        ('JobMemory', ctypes.c_size_t), ('PeakProcessMemory', ctypes.c_size_t),
                        ('PeakJobMemory', ctypes.c_size_t)]

        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def collect(args, *, timeout, cwd=None, env=None, budget=DEFAULT_BUDGET):
    """Giữ tối đa budget byte raw cho CẢ HAI pipe; phần vượt làm evidence incomplete.

Mỗi reader giữ thêm tối đa một chunk 4096 byte. Buffer bytearray tránh danh sách
vô hạn khi child ghi từng byte. Decode UTF-8 sau khi thu, có báo replacement.
observed_bytes chỉ là số byte đã đọc, không phải tổng output child định phát.
"""
    if type(budget) is not int or not 1 <= budget <= 16 * DEFAULT_BUDGET:
        raise ValueError('Byte budget must be 1..16777216')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('A finite positive process timeout is required')
    started = time.monotonic()
    job = _WindowsJob() if os.name == 'nt' else None
    process = None
    threads = []
    buffers = [bytearray(), bytearray()]
    state = {'retained': 0, 'observed': 0, 'truncated': False, 'read_error': False}
    lock = threading.Lock()
    wake = threading.Event()
    done = [threading.Event(), threading.Event()]
    timed_out = False

    def terminate_tree():
        if job:
            job.close()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None and process.poll() is None:
            process.kill()

    def reader(index, stream):
        try:
            while True:
                chunk = os.read(stream.fileno(), CHUNK_SIZE)
                if not chunk:
                    break
                with lock:
                    state['observed'] += len(chunk)
                    keep = min(len(chunk), budget - state['retained'])
                    buffers[index].extend(chunk[:keep])
                    state['retained'] += keep
                    if keep < len(chunk):
                        state['truncated'] = True
                        wake.set()
                        break
        except OSError:
            state['read_error'] = True
            wake.set()
        finally:
            done[index].set()

    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, bufsize=0, cwd=cwd, env=env,
                                   start_new_session=os.name == 'posix')
        if job:
            # Nếu không gắn job được thì fail closed, không fallback bỏ quản lý child.
            job.assign(process)
        for index, stream in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=reader, args=(index, stream), name='bounded-pipe-' + str(index))
            threads.append(thread)
            thread.start()
        while not (all(event.is_set() for event in done) and process.poll() is not None):
            if wake.is_set():
                break
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            wake.wait(min(remaining, .01))
    finally:
        # Bao gồm timeout, overflow, lỗi và KeyboardInterrupt; không gọi Docker lifecycle.
        terminate_tree()
        if process is not None:
            process.wait()
        for thread in threads:
            thread.join()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                stream.close()
        if job:
            job.close()

    decoded = []
    replaced = False
    for raw in buffers:
        try:
            decoded.append(raw.decode('utf-8'))
        except UnicodeDecodeError:
            decoded.append(raw.decode('utf-8', errors='replace'))
            replaced = True
    return Capture(*decoded, process.returncode, state['retained'], state['observed'], budget,
                   state['truncated'], timed_out, replaced, state['read_error'])
