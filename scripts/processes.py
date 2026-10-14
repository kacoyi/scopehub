"""Run repository checks with durable output and bounded process lifetimes."""

import os
from pathlib import Path
import signal
import subprocess


def stop_tree(process):
    """Checks are trusted local programs, not a sandbox for hostile code."""
    if os.name == 'nt':
        # Keep the root alive until taskkill has traversed its descendants.
        result = subprocess.run(
            ['taskkill', '/PID', str(process.pid), '/T', '/F'],
            capture_output=True, timeout=10, check=False,
        )
        if result.returncode and process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_logged(argv, *, cwd, env, log_path, timeout):
    """Write directly to disk instead of accumulating an entire build in RAM."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reason = None
    with log_path.open('wb') as output:
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, **options)
        except OSError as error:
            output.write(f'Cannot start check: {error}\n'.encode('utf-8'))
            return None, 'launch_error'
        try:
            exit_code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            reason = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted'
            try:
                stop_tree(process)
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                output.write(f'Process cleanup failed: {cleanup_error}\n'.encode('utf-8'))
                process.kill()
                process.wait(timeout=10)
                reason = 'cleanup_failed'
            output.write(f'\nCheck stopped: {reason}.\n'.encode('utf-8'))
            exit_code = None
    return exit_code, reason


def log_tail(path, limit=6000):
    with Path(path).open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode('utf-8', errors='replace')
