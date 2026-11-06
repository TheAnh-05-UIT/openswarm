"""Thử collector bằng child Python cục bộ; không gọi Docker hoặc mạng."""
import ctypes
import os
from pathlib import Path
import sys
import time
import tracemalloc
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bounded_process import collect, Capture
import preflight


class BoundedProcessTests(unittest.TestCase):
    def child(self, source, budget=1024, timeout=5):
        return collect([sys.executable, '-B', '-c', source], timeout=timeout, budget=budget)

    def test_empty(self):
        r = self.child('pass')
        self.assertTrue(r.complete)
        self.assertEqual((r.stdout, r.stderr, r.retained_bytes, r.returncode), ('', '', 0, 0))

    def test_small_stdout_and_stderr(self):
        r = self.child("import os; os.write(1,b'output'); os.write(2,b'diagnostic')")
        self.assertEqual((r.stdout, r.stderr, r.retained_bytes), ('output', 'diagnostic', 16))
        self.assertTrue(r.complete)

    def test_exact_combined_budget(self):
        r = self.child("import os; os.write(1,b'a'*600); os.write(2,b'b'*424)")
        self.assertTrue(r.complete)
        self.assertEqual(r.retained_bytes, 1024)

    def test_exceed_by_one(self):
        r = self.child("import os; os.write(1,b'x'*1025)")
        self.assertTrue(r.truncated)
        self.assertFalse(r.complete)
        self.assertEqual(r.retained_bytes, 1024)
        self.assertGreater(r.observed_bytes, r.retained_bytes)

    def test_multibyte_counts_bytes(self):
        r = self.child("import os; os.write(1, ('\\u1ec7'*300).encode())")
        self.assertEqual((len(r.stdout), r.retained_bytes), (300, 900))
        self.assertFalse(r.decode_replaced)

    def test_cut_multibyte_is_explicit(self):
        r = self.child("import os; os.write(1, ('\\u1ec7'*400).encode())")
        self.assertTrue(r.truncated)
        self.assertTrue(r.decode_replaced)
        self.assertFalse(r.complete)
        self.assertEqual(r.retained_bytes, 1024)

    def test_invalid_utf8(self):
        r = self.child("import os; os.write(1,b'\\xff')")
        self.assertTrue(r.decode_replaced)
        self.assertFalse(r.complete)
        self.assertEqual(r.stdout, '\ufffd')

    def test_long_line_bounded_memory(self):
        tracemalloc.start()
        try:
            r = self.child("import os; b=b'x'*4096\nfor _ in range(4096): os.write(1,b)", budget=32768)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(r.truncated)
        self.assertEqual(r.retained_bytes, 32768)
        # Allocation Python của collector, không phải RSS child hoặc kernel buffers.
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_concurrent_stdout_stderr(self):
        source = """import os,threading
def write(fd):
    for _ in range(16): os.write(fd,b'x'*4096)
t=threading.Thread(target=write,args=(2,)); t.start(); write(1); t.join()
"""
        r = self.child(source, budget=200000)
        self.assertTrue(r.complete)
        self.assertEqual((len(r.stdout), len(r.stderr)), (65536, 65536))

    def test_stderr_overflow(self):
        r = self.child("import os; os.write(2,b'x'*20000)")
        self.assertTrue(r.truncated)
        self.assertLessEqual(r.retained_bytes, 1024)

    def test_nonzero_diagnostic(self):
        r = self.child("import os,sys; os.write(2,b'known error'); sys.exit(7)")
        self.assertEqual((r.returncode, r.stderr), (7, 'known error'))
        self.assertTrue(r.complete)  # Thu đủ output không có nghĩa command thành công.

    def test_timeout_reaps_child(self):
        start = time.monotonic()
        r = self.child('import time; time.sleep(30)', timeout=.3)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.complete)  # Job Object có thể kill với native exit code 0.
        self.assertLess(time.monotonic() - start, 5)

    def test_descendant_holding_pipe_is_terminated(self):
        source = """import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'])
print(p.pid,flush=True)
time.sleep(30)
"""
        r = self.child(source, timeout=1)
        self.assertTrue(r.timed_out)
        pid = int(r.stdout.strip())
        if os.name == 'nt':
            from ctypes import wintypes as w
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            api.OpenProcess.restype = w.HANDLE
            api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            api.CloseHandle.argtypes = [w.HANDLE]
            handle = api.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    self.assertEqual(api.WaitForSingleObject(handle, 2000), 0)
                finally:
                    api.CloseHandle(handle)
        else:
            stat = Path('/proc') / str(pid) / 'stat'
            for _ in range(100):
                if not stat.exists() or stat.read_text().split(')')[-1].strip().startswith('Z'):
                    break
                time.sleep(.01)
            else:
                self.fail('Descendant still running after timeout')

    def test_spawn_failure(self):
        with self.assertRaises(OSError):
            collect(['009g1-command-that-does-not-exist'], timeout=1)

    def test_invalid_limits(self):
        for budget in (0, -1, True, 16777217):
            with self.assertRaises(ValueError): self.child('pass', budget=budget)
        for timeout in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError): self.child('pass', timeout=timeout)

    def test_read_string_compatibility(self):
        r = Capture('  ok\n', '', 0, 5, 5, 1024)
        with patch.object(preflight.Runner, 'capture', return_value=r):
            self.assertEqual(preflight.Runner().read(['MOCK'], object()), 'ok')

    def test_read_rejects_partial_and_error_without_printing_secrets(self):
        for r in (Capture('partial', 'SECRET', 0, 7, 8, 7, truncated=True), Capture('', 'SECRET', 9, 6, 6, 1024)):
            with patch.object(preflight.Runner, 'capture', return_value=r):
                with self.assertRaises(preflight.CaptureError) as raised:
                    preflight.Runner().read(['MOCK', 'secret-arg'], object())
                self.assertNotIn('SECRET', str(raised.exception))
                self.assertNotIn('secret-arg', str(raised.exception))
                self.assertEqual(raised.exception.result.stderr, 'SECRET')

    def test_change_approval_and_allowlist_preserved(self):
        with patch.object(preflight.Runner, 'capture') as capture:
            with self.assertRaises(ValueError):
                preflight.Runner().change(['docker', 'container', 'stop', 'MOCK'], object())
            with self.assertRaises(ValueError):
                preflight.Runner(approved=True).change(['docker', 'system', 'prune'], object())
            capture.assert_not_called()


if __name__ == '__main__': unittest.main(verbosity=2)
