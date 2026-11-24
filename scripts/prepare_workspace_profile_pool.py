"""Own the disposable workspace fixture tree through the first ACL preparation."""

import argparse
import os
from pathlib import Path
import shutil
import sys
import time

if __package__:
    from .processes import log_tail, run_logged
else:
    from processes import log_tail, run_logged


def prepare(command, pool: Path, timeout: float) -> int:
    deadline = time.monotonic() + timeout

    def expired() -> bool:
        return time.monotonic() >= deadline

    if expired():
        print('Workspace profile preparation reached its deadline before launch.', file=sys.stderr)
        return 124
    # The supervisor exclusively acquires this synthetic directory. An existing
    # path is an error, and is never taken over or removed.
    pool.mkdir(mode=0o700)
    log = pool / 'preparation.log'
    try:
        (pool / 'claims').mkdir()
    except OSError:
        try:
            pool.rmdir()
        except OSError as error:
            print(f'Workspace profile pool cleanup failed: {error}', file=sys.stderr)
        raise

    exit_code, reason = run_logged(
        command, cwd=Path.cwd(), env=os.environ.copy(), log_path=log,
        timeout=timeout, cancel=expired,
    )
    late_success = exit_code == 0 and reason is None
    if late_success and not expired():
        return 0

    print(log_tail(log), end='', file=sys.stderr)
    if reason == 'cleanup_failed':
        print(f'Workspace profile pool retained because process exit was not confirmed: {pool}', file=sys.stderr)
        return exit_code or 1
    if late_success or reason == 'timeout' or (reason == 'interrupted' and expired()):
        print('Workspace profile preparation reached its deadline.', file=sys.stderr)
        exit_code = 124
    try:
        # run_logged has confirmed the job or process group is empty here.
        shutil.rmtree(pool)
    except OSError as error:
        print(f'Workspace profile pool cleanup failed: {error}', file=sys.stderr)
    return exit_code or 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('bridge')
    parser.add_argument('pool', type=Path)
    parser.add_argument('count', type=int)
    parser.add_argument('deadline_ms', type=int)
    args = parser.parse_args()
    # A cold or abandoned interpreter may arrive late. It cannot acquire the
    # directory or start a bridge without the caller's one-byte authorization.
    print('scopehub-workspace-profile-ready', flush=True)
    if sys.stdin.buffer.read(1) != b'1':
        return 125
    remaining = min(12.0, (args.deadline_ms - time.time_ns() // 1_000_000) / 1000)
    return prepare(
        [args.bridge, '--prepare-profile-pool', str(args.pool / 'profiles'), str(args.count)],
        args.pool, remaining,
    )


if __name__ == '__main__':
    raise SystemExit(main())
