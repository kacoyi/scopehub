"""Own the dedicated interpreter and its ordinary local descendants."""

import ctypes
import os
import subprocess
import sys
from threading import Event, Thread
from typing import BinaryIO

from scripts.process_tree import stop_tree

ABANDONED = 125
PR_SET_CHILD_SUBREAPER = 36


def send_frame(pipe: BinaryIO, frame: bytes, abandoned: Event) -> None:
    try:
        with pipe:
            if pipe.write(frame) != len(frame):
                raise OSError('Incomplete indicator request write')
    except OSError:
        abandoned.set()


def watch_parent(pipe: BinaryIO, max_bytes: int, abandoned: Event) -> None:
    try:
        incoming = sys.stdin.buffer
        frame = incoming.readline(max_bytes + 1)
        if len(frame) <= max_bytes and frame.endswith(b'\n'):
            # If the child stops consuming input, the watcher can still see
            # the parent close its pipe and the supervisor can stop the tree.
            Thread(target=send_frame, args=(pipe, frame, abandoned), daemon=True).start()
            # The Rust parent sends one frame and subsequently signals only
            # by EOF. Read the descriptor without holding a buffered lock at exit.
            os.read(incoming.fileno(), 1)
    finally:
        abandoned.set()


def enable_subreaper() -> None:
    if sys.platform == 'linux':
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))


def exited(process: subprocess.Popen[bytes]) -> bool:
    if sys.platform == 'linux':
        # Reserve the group leader's PID until the group has been signalled.
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None
    return process.poll() is not None


def reap_adopted(group: int) -> None:
    if sys.platform == 'linux':
        while True:
            try:
                os.waitpid(-group, 0)
            except ChildProcessError:
                return


def supervise(max_bytes: int) -> int:
    env = dict(os.environ)
    interpreter = sys.executable
    job = None
    if sys.platform == 'win32':
        from scripts.windows_job import WindowsJob

        # Start the real interpreter, not the venv redirector. Forward no
        # selected code until the process belongs to the private Job.
        interpreter = sys._base_executable  # type: ignore[attr-defined]
        env['__PYVENV_LAUNCHER__'] = sys.executable
        job = WindowsJob()
    else:
        enable_subreaper()

    try:
        argv = [interpreter, '-P', '-s', '-B', '-X', 'utf8', '-m', 'worker_runtime.indicator']
        process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=sys.stdout.buffer,
                                   stderr=subprocess.DEVNULL, start_new_session=job is None)
        if job is not None:
            try:
                job.assign(process.pid)
            except OSError:
                process.kill()
                process.wait()
                raise
        finished = False
        try:
            assert process.stdin is not None
            abandoned = Event()
            Thread(target=watch_parent, args=(process.stdin, max_bytes, abandoned), daemon=True).start()
            while not abandoned.wait(.01):
                if exited(process):
                    finished = True
                    break
        finally:
            stop_tree(process, job)
            reap_adopted(process.pid)
        status = process.returncode
        return status if finished and status is not None else ABANDONED
    finally:
        if job is not None:
            job.close()


if __name__ == '__main__':
    try:
        raise SystemExit(supervise(int(sys.argv[1])))
    except OSError:
        raise SystemExit(ABANDONED) from None
