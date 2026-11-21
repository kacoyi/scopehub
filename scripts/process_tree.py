"""Bounded cleanup for an already owned process group or Windows Job."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Protocol


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


def stop_tree(process: subprocess.Popen[bytes], job: ProcessJob | None = None) -> None:
    """Checks are trusted local programs, not a sandbox for hostile code."""
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
