"""Bounded cleanup for verification subprocesses."""

from collections.abc import Sequence
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from typing import Any, BinaryIO, Protocol, cast


def running_group(group: int) -> bool:
    """Linux zombies cannot execute or write; wait for all live group members."""
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_bytes().rsplit(b')', 1)[1].split()
            if int(fields[2]) == group and fields[0] not in {b'Z', b'X'}:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


class ProcessJob(Protocol):
    def stop(self) -> None: ...


def start_linux_market_check(argv: Sequence[str], **options: Any) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    # The subreaper belongs to one check, never to the multithreaded harness.
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, 'wb') as result:
        try:
            process = subprocess.Popen(
                [getattr(sys, '_base_executable', sys.executable), '-I', '-S', __file__, str(result.fileno()), *argv],
                stdin=subprocess.PIPE, pass_fds=(result.fileno(),), **options,
            )
        except BaseException:
            os.close(read_fd)
            raise
    return process, os.fdopen(read_fd, 'rb')


def stop_tree(process: subprocess.Popen[bytes], job: ProcessJob | None = None, *,
              result_pipe: BinaryIO | None = None) -> Sequence[int | str | None] | None:
    """Checks are trusted local programs, not a sandbox for hostile code."""
    if result_pipe is not None:
        # Closing the control pipe requests cancellation. The supervisor
        # reports only after all its children have been reaped.
        with result_pipe:
            if process.wait(timeout=10) != 0 or not (result := result_pipe.read(128)):
                raise OSError('Linux market recovery process supervisor failed')
        return cast(list[int | str | None], json.loads(result))
    if sys.platform == 'win32':
        assert job is not None
        job.stop()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)
    if sys.platform == 'linux':
        deadline = time.monotonic() + 10
        while running_group(process.pid):
            if time.monotonic() >= deadline:
                raise OSError('Verification process group still has live members')
            time.sleep(.01)
    return None


def reap_linux_children() -> None:
    if sys.platform != 'linux':
        raise OSError('Descendant reaping requires Linux')
    # A subreaper adopts orphaned descendants, even across sessions. These
    # are exact child PIDs and cannot be reused before this process waits.
    children = Path(f'/proc/self/task/{os.getpid()}/children')
    deadline = time.monotonic() + 10
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            continue
        if time.monotonic() >= deadline:
            raise OSError('Linux market recovery still has live descendants')
        for child in children.read_text('ascii').split():
            try:
                os.kill(int(child), signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(.01)


def supervise_linux_market_check(result_fd: int, argv: Sequence[str]) -> None:
    import ctypes

    if sys.platform != 'linux':
        raise OSError('Market process supervision requires Linux')
    # An inherited SIG_IGN would autoreap children and discard their status.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot supervise Linux market recovery descendants')

    with os.fdopen(result_fd, 'wb') as result:
        exit_code = reason = None
        try:
            if not select.select([0], [], [], 0)[0]:
                # Preserve the command's own session so a group signal cannot
                # kill the supervisor that will reap its detached descendants.
                command = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
                while (exit_code := command.poll()) is None:
                    if select.select([0], [], [], .02)[0]:
                        break
        except OSError as error:
            print(f'Cannot start check: {error}', file=sys.stderr)
            reason = 'launch_error'
        finally:
            reap_linux_children()
        result.write(json.dumps([exit_code, reason]).encode('ascii'))


if __name__ == '__main__':
    supervise_linux_market_check(int(sys.argv[1]), sys.argv[2:])
