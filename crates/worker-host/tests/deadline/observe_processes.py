"""Wait on pre-opened references to the deadline fixture's owned processes."""

from contextlib import ExitStack
import os
import sys


def windows_probes(pids, stack):
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    open_process = api.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle = api.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    wait = api.WaitForMultipleObjects
    wait.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                     wintypes.BOOL, wintypes.DWORD]
    wait.restype = wintypes.DWORD

    handles = (wintypes.HANDLE * len(pids))()
    for index, pid in enumerate(pids):
        handle = open_process(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(close_handle, handle)
        handles[index] = handle

    def signaled(wait_all):
        status = wait(len(handles), handles, wait_all, 0)
        if status == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(ctypes.get_last_error())
        if status == 258:  # WAIT_TIMEOUT
            return False
        if 0 <= status < len(handles):  # WAIT_OBJECT_0 + index
            return True
        raise OSError(f'unexpected process wait status {status:#x}')

    return lambda: not signaled(False), lambda: signaled(True)


def linux_probes(pids, stack):
    import select

    poll = select.poll()
    for pid in pids:
        fd = os.pidfd_open(pid)
        stack.callback(os.close, fd)
        poll.register(fd, select.POLLIN)

    def exited_count():
        events = poll.poll(0)
        if any(not flags & select.POLLIN or flags & (select.POLLERR | select.POLLNVAL)
               for _, flags in events):
            raise OSError(f'unexpected pidfd events {events}')
        return len(events)

    return lambda: exited_count() == 0, lambda: exited_count() == len(pids)


def reply(value):
    sys.stdout.buffer.write(b'1' if value else b'0')
    sys.stdout.buffer.flush()


def main():
    # This helper stays idle with its handles open until the caller asks for
    # one sample. There is no PID resolution or retry on the sampling path.
    sys.stdout.buffer.write(b'R')
    sys.stdout.buffer.flush()
    stdin = sys.stdin.buffer
    roster = stdin.readline()
    if not roster:
        return
    pids = list(map(int, roster.split()))
    if not 1 <= len(pids) <= 3 or len(set(pids)) != len(pids) or min(pids) <= 0:
        raise ValueError(f'invalid fixture process roster {pids}')
    with ExitStack() as stack:
        probes = windows_probes if sys.platform == 'win32' else linux_probes
        all_live, all_exited = probes(pids, stack)
        reply(all_live())
        for probe in iter(lambda: stdin.read(1), b''):
            if probe != b'?':
                raise ValueError(f'invalid process probe {probe!r}')
            reply(all_exited())


if __name__ == '__main__':
    main()
