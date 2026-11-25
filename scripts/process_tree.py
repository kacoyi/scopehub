"""Bounded cleanup for verification subprocesses."""

from collections.abc import Callable, Sequence
from io import BufferedReader
import json
import os
from pathlib import Path
import select
from selectors import DefaultSelector, EVENT_READ
import signal
import subprocess
import sys
import time
from typing import Any, Protocol


COMMAND_READY = b'R'


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


def start_linux_check(argv: Sequence[str], **options: Any) -> tuple[subprocess.Popen[bytes], BufferedReader]:
    # The subreaper belongs to one check, never to the multithreaded harness.
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, 'wb') as result:
        try:
            os.set_blocking(read_fd, False)
            process = subprocess.Popen(
                [getattr(sys, '_base_executable', sys.executable), '-I', '-S', __file__, str(result.fileno()), *argv],
                stdin=subprocess.PIPE, pass_fds=(result.fileno(),), **options,
            )
        except BaseException:
            os.close(read_fd)
            raise
    return process, os.fdopen(read_fd, 'rb')


def wait_linux_check(process: subprocess.Popen[bytes], result_pipe: BufferedReader, timeout: float) -> None:
    # The command outcome is ready before the supervisor finishes reaping.
    with DefaultSelector() as selector:
        selector.register(result_pipe, EVENT_READ)
        if not selector.select(timeout):
            raise subprocess.TimeoutExpired(process.args, timeout)


def _confirmed_linux_outcome(result: bytes) -> tuple[int | None, str | None]:
    if len(result) > 128 or not (result.startswith(COMMAND_READY) and result.endswith(b'\n')):
        raise OSError('Linux check process supervisor did not confirm cleanup')
    try:
        code, reason = json.loads(result[1:-1])
    except (TypeError, ValueError) as error:
        raise OSError('Invalid Linux check cleanup confirmation') from error
    if not ((type(code) is int and reason is None) or (code is None and reason in (None, 'launch_error'))):
        raise OSError('Invalid Linux check cleanup confirmation')
    return code, reason


def _stop_linux_check(process: subprocess.Popen[bytes], result_pipe: BufferedReader,
                      cancel: Callable[[], bool] | None) -> tuple[int | None, str | None]:
    deadline = time.monotonic() + 10
    interrupted = False
    while True:
        try:
            # Closing control input requests cancellation. The supervisor
            # continues cleanup even if waiting for it is interrupted.
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                raise OSError('Linux check process supervisor failed')
            # SIGCHLD ignored can make wait() report zero without an exit
            # status. Require the post-reap record; peek keeps retries safe.
            outcome = _confirmed_linux_outcome(result_pipe.peek(128))
            if interrupted or (cancel is not None and cancel()):
                return None, 'interrupted'
            return outcome
        except KeyboardInterrupt:
            interrupted = True


def stop_tree(process: subprocess.Popen[bytes], job: ProcessJob | None = None, *,
              result_pipe: BufferedReader | None = None,
              cancel: Callable[[], bool] | None = None) -> tuple[int | None, str | None] | None:
    """Checks are trusted local programs, not a sandbox for hostile code."""
    if result_pipe is not None:
        return _stop_linux_check(process, result_pipe, cancel)
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
            raise OSError('Linux check still has live descendants')
        for child in children.read_text('ascii').split():
            try:
                os.kill(int(child), signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(.01)


def supervise_linux_check(result_fd: int, argv: Sequence[str]) -> None:
    import ctypes

    if sys.platform != 'linux':
        raise OSError('Check process supervision requires Linux')
    # An inherited SIG_IGN would autoreap children and discard their status.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot supervise Linux check descendants')

    with os.fdopen(result_fd, 'wb', buffering=0) as result:
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
            try:
                result.write(COMMAND_READY)
            finally:
                reap_linux_children()
        result.write(json.dumps([exit_code, reason]).encode('ascii') + b'\n')


if __name__ == '__main__':
    supervise_linux_check(int(sys.argv[1]), sys.argv[2:])
