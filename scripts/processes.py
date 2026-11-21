"""Run repository checks with durable output and bounded process lifetimes."""

import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from .process_tree import stop_tree
else:
    from process_tree import stop_tree

WINDOWS_LAUNCHER = '''
import subprocess, sys
if sys.stdin.buffer.read(1) != b'1':
    raise SystemExit(125)
raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))
'''


def run_logged(argv, *, cwd, env, log_path, timeout):
    """Write directly to disk instead of accumulating an entire build in RAM."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reason = None
    exit_code = None
    process = job = None
    with log_path.open('wb') as output:
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            if os.name == 'nt':
                if __package__:
                    from .windows_job import WindowsJob
                else:
                    from windows_job import WindowsJob
                job = WindowsJob()
            command = [sys.executable, '-I', '-S', '-c', WINDOWS_LAUNCHER, *argv] if job else argv
            process = subprocess.Popen(command, cwd=cwd, env=env,
                                       stdin=subprocess.PIPE if job else subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, **options)
            if job:
                job.assign(process.pid)
                process.stdin.write(b'1')
                process.stdin.close()
            exit_code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            reason = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted'
            output.write(f'\nCheck stopped: {reason}.\n'.encode('utf-8'))
        except OSError as error:
            output.write(f'Cannot start check: {error}\n'.encode('utf-8'))
            reason = 'launch_error'
        finally:
            try:
                if process is not None:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    stop_tree(process, job)
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                output.write(f'Process cleanup failed: {cleanup_error}\n'.encode('utf-8'))
                reason = 'cleanup_failed'
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            finally:
                if job:
                    job.close()
    return exit_code, reason


def log_tail(path, limit=6000):
    with Path(path).open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode('utf-8', errors='replace')
