"""Own trusted verification descendants with a Windows Job Object.

The launcher waits for assignment before starting the command. This prevents
the normal-exit race of traversing descendants only after their parent exits.
"""

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import math
import sys
import time


JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ERROR_INVALID_PARAMETER = 87
ERROR_MORE_DATA = 234
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD), ('minimum_working_set', ctypes.c_size_t),
                ('maximum_working_set', ctypes.c_size_t), ('active_process_limit', wintypes.DWORD),
                ('affinity', ctypes.c_size_t), ('priority_class', wintypes.DWORD),
                ('scheduling_class', wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ['read_operations', 'write_operations', 'other_operations', 'read_bytes', 'write_bytes', 'other_bytes']]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [('user_time', ctypes.c_int64), ('kernel_time', ctypes.c_int64),
                ('period_user_time', ctypes.c_int64), ('period_kernel_time', ctypes.c_int64),
                ('page_faults', wintypes.DWORD), ('total_processes', wintypes.DWORD),
                ('active_processes', wintypes.DWORD), ('terminated_processes', wintypes.DWORD)]


class WindowsJob:
    def __init__(self, *, memory_limit_bytes: int | None = None) -> None:
        if sys.platform != 'win32':
            raise OSError('Windows Job Objects require Windows')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'IsProcessInJob': ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle: int | None = self.require(self.api.CreateJobObjectW(None, None))
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if memory_limit_bytes is not None:
            limits.basic.flags |= 0x0200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            limits.job_memory = memory_limit_bytes
        try:
            self.require(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except OSError:
            self.close()
            raise

    @staticmethod
    def require(result: int | None) -> int:
        if not result:
            if sys.platform == 'win32':
                raise ctypes.WinError(ctypes.get_last_error())
            raise OSError('Windows Job API failed')
        return result

    def assign(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE: the documented assignment rights.
        process = self.require(self.api.OpenProcess(0x0100 | 0x0001, False, pid))
        try:
            self.require(self.api.AssignProcessToJobObject(self.handle, process))
        finally:
            self.api.CloseHandle(process)

    def _member_pids(self, deadline: float) -> list[int]:
        capacity = 32
        while time.monotonic() < deadline:
            class ProcessIdList(ctypes.Structure):
                _fields_ = [('assigned', wintypes.DWORD), ('listed', wintypes.DWORD),
                            ('pids', ctypes.c_size_t * capacity)]

            process_ids = ProcessIdList()
            result = self.api.QueryInformationJobObject(
                self.handle, JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(process_ids),
                ctypes.sizeof(process_ids), None)
            if result:
                if process_ids.listed > min(process_ids.assigned, capacity):
                    raise OSError('Windows job returned an invalid process ID list')
                if process_ids.listed == process_ids.assigned:
                    return process_ids.pids[:process_ids.listed]
            elif ctypes.get_last_error() != ERROR_MORE_DATA:
                self.require(result)
            capacity = max(capacity * 2, process_ids.assigned)
        raise OSError('Timed out obtaining the Windows job process ID list')

    def _wait_for_process(self, process: int, pid: int, deadline: float) -> None:
        milliseconds = max(0, math.ceil((deadline - time.monotonic()) * 1000))
        result = self.api.WaitForSingleObject(process, milliseconds)
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result == WAIT_TIMEOUT:
            raise OSError(f'Windows job process {pid} did not finish terminating')
        if result != WAIT_OBJECT_0:
            raise OSError(f'Unexpected wait status {result} for Windows job process {pid}')

    def stop(self) -> None:
        deadline = time.monotonic() + 10
        # Job accounting can remove a process before its process handle signals.
        # Capture the current members first. Job termination still covers processes
        # created after the snapshot, which have no retained process handle here.
        with ExitStack() as handles:
            members = []
            try:
                for pid in self._member_pids(deadline):
                    if time.monotonic() >= deadline:
                        raise OSError('Timed out opening Windows job process handles')
                    process = self.api.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if not process:
                        if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
                            continue  # The process ID ceased to name a process.
                        self.require(process)
                    handles.callback(lambda handle=process: self.require(self.api.CloseHandle(handle)))
                    in_job = wintypes.BOOL()
                    self.require(self.api.IsProcessInJob(process, self.handle, ctypes.byref(in_job)))
                    if in_job.value:
                        members.append((pid, process))
            finally:
                self.require(self.api.TerminateJobObject(self.handle, 1))
            for pid, process in members:
                self._wait_for_process(process, pid, deadline)
        while True:
            accounting = Accounting()
            self.require(self.api.QueryInformationJobObject(
                self.handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(accounting),
                ctypes.sizeof(accounting), None))
            if accounting.active_processes == 0:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSError('Windows job still has active verification descendants')
            time.sleep(min(.01, remaining))

    def close(self) -> None:
        if self.handle is not None:
            self.require(self.api.CloseHandle(self.handle))
            self.handle = None


if __name__ == '__main__':
    # The preview server owns this launcher. Closing its process closes the sole
    # job handle, including when an outstanding stdio request is interrupted.
    import os
    import subprocess

    job = WindowsJob()
    job.assign(os.getpid())
    raise SystemExit(subprocess.call(sys.argv[1:], stdin=sys.stdin.buffer,
                                     stdout=sys.stdout.buffer, stderr=sys.stderr.buffer))
