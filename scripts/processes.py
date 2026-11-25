"""Run repository checks with durable output and bounded process lifetimes."""

from functools import partial
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time

if __package__:
    from .process_tree import start_linux_check, stop_tree, wait_linux_check
else:
    from process_tree import start_linux_check, stop_tree, wait_linux_check

FAILED_LOG_LIMIT = 16 * 1024 * 1024

WINDOWS_LAUNCHER = '''
import subprocess, sys
if sys.stdin.buffer.read(1) != b'1':
    raise SystemExit(125)
raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))
'''


def _wait_for_check(process, timeout, cancel, *, result_pipe=None):
    wait = process.wait if result_pipe is None else partial(wait_linux_check, process, result_pipe)
    if cancel is None:
        return wait(timeout=timeout)
    deadline = time.monotonic() + timeout
    while not cancel():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return wait(timeout=min(remaining, .25))
        except subprocess.TimeoutExpired:
            continue
    raise KeyboardInterrupt


def _terminate_failed_check(process):
    deadline = time.monotonic() + 10
    while True:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=max(0, deadline - time.monotonic()))
            return
        except KeyboardInterrupt:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, 10) from None


def run_logged(argv, *, cwd, env, log_path, timeout, cancel=None, contain_linux_tree=False):
    """Write directly to disk instead of accumulating an entire build in RAM."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reason = None
    exit_code = None
    process = job = result_pipe = None
    with log_path.open('wb') as output:
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            if cancel is not None and cancel():
                raise KeyboardInterrupt
            if os.name == 'nt':
                if __package__:
                    from .windows_job import WindowsJob
                else:
                    from windows_job import WindowsJob
                job = WindowsJob()
            # The Windows venv redirector can spawn an interpreter before job
            # assignment. Run the stdlib-only gate in the base interpreter.
            command = [sys._base_executable, '-I', '-S', '-c', WINDOWS_LAUNCHER, *argv] if job else argv
            options.update(cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
            if contain_linux_tree and sys.platform == 'linux':
                process, result_pipe = start_linux_check(command, **options)
            else:
                process = subprocess.Popen(command, stdin=subprocess.PIPE if job else subprocess.DEVNULL,
                                           **options)
            if job:
                job.assign(process.pid)
                process.stdin.write(b'1')
                process.stdin.close()
            if result_pipe is None:
                exit_code = _wait_for_check(process, timeout, cancel)
            else:
                _wait_for_check(process, timeout, cancel, result_pipe=result_pipe)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            reason = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted'
            output.write(f'\nCheck stopped: {reason}.\n'.encode('utf-8'))
        except OSError as error:
            output.write(f'Cannot start check: {error}\n'.encode('utf-8'))
            reason = 'launch_error'
        finally:
            try:
                if process is not None:
                    if result_pipe is None:
                        if process.stdin and not process.stdin.closed:
                            process.stdin.close()
                        stop_tree(process, job)
                    else:
                        command_exit, command_reason = stop_tree(process, result_pipe=result_pipe, cancel=cancel)
                        if reason is None:
                            exit_code, reason = command_exit, command_reason
                            if reason == 'interrupted':
                                output.write(b'\nCheck stopped: interrupted.\n')
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                output.write(f'Process cleanup failed: {cleanup_error}\n'.encode('utf-8'))
                reason = 'cleanup_failed'
                try:
                    _terminate_failed_check(process)
                except (OSError, subprocess.SubprocessError) as termination_error:
                    output.write(f'Could not confirm failed check termination: {termination_error}\n'.encode('utf-8'))
            finally:
                if result_pipe is not None:
                    result_pipe.close()
                if job:
                    job.close()
    return exit_code, reason


def _publish_log_snapshot(path, write):
    staged = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as snapshot:
            staged = Path(snapshot.name)
            write(snapshot)
        # Closing the staged file first confirms all bytes were written.
        # A surviving descriptor still refers to the old, unlinked file.
        staged.replace(path)
        staged = None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def seal_unconfirmed_log(path: Path) -> None:
    """Publish a bounded snapshot on an inode unavailable to surviving writers."""
    def copy_extent(snapshot):
        with path.open('rb') as capture:
            size = os.fstat(capture.fileno()).st_size
            remaining = min(size, FAILED_LOG_LIMIT)
            omitted = size - remaining
            if omitted:
                capture.seek(omitted)
                snapshot.write(f'[Log snapshot omitted {omitted} earlier bytes.]\n'.encode('ascii'))
            while remaining:
                chunk = capture.read(min(remaining, 256 * 1024))
                if not chunk:
                    snapshot.write(b'\n[Log snapshot ended before the captured extent.]\n')
                    break
                snapshot.write(chunk)
                remaining -= len(chunk)

    try:
        _publish_log_snapshot(path, copy_extent)
    except OSError:
        # Discard a partial copy. If a small replacement still fits, retain
        # the failure without certifying the output of an unconfirmed writer.
        _publish_log_snapshot(path, lambda snapshot: snapshot.write(
            b'Process cleanup failed. Original log snapshot unavailable after an I/O error.\n'))


def log_tail(path, limit=6000):
    with Path(path).open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode('utf-8', errors='replace')
