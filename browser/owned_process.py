"""Track our exact Chromium process to verify Windows shutdown races safely."""
import ctypes
import os
import select
import signal
import sys
import subprocess
from ctypes import wintypes

class OwnedProcess:
    def __init__(self, pid):
        self.handle = None
        self.pidfd = None
        self.pid=pid
        if sys.platform == 'win32':
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x100000,False,pid)  # SYNCHRONIZE only
            if not self.handle:
                raise RuntimeError('Cannot track owned Chromium process')
        elif sys.platform == 'linux':
            self.pidfd = os.pidfd_open(pid)

    def _linux_exited(self, timeout_ms):
        poll = select.poll()
        poll.register(self.pidfd, select.POLLIN | select.POLLHUP)
        return any(events & (select.POLLIN | select.POLLHUP) for _, events in poll.poll(timeout_ms))

    def exited(self):
        if self.pidfd is not None:
            return self._linux_exited(1000)
        return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle,1000) == 0

    def status_now(self):
        """Nonblocking status at a failure boundary; never log a PID or command line."""
        if self.pidfd is not None:
            return 'TERMINATED' if self._linux_exited(0) else 'ALIVE'
        if not self.handle:return 'UNKNOWN'
        status=self.kernel.WaitForSingleObject(self.handle,0)
        return 'TERMINATED' if status==0 else 'ALIVE' if status==258 else 'UNKNOWN'

    def terminate(self):
        if self.exited():return True
        if self.pidfd is not None:
            try:
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return self.exited()
        if not self.handle:return False
        # Original retained handle identifies this process; never target other Chrome instances.
        subprocess.run(['taskkill','/PID',str(self.pid),'/T','/F'],capture_output=True,timeout=2,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return self.exited()

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
