"""Interrupt the one market worker below this test's own preview host."""

from pathlib import Path
import sys

import psutil


def main():
    root = Path(__file__).resolve().parents[2]
    owner = psutil.Process().parent()
    if owner is None or Path(owner.exe()).stem.lower() != 'node' or sys.stdin.isatty():
        raise RuntimeError('A live preview test must own this helper')
    print('ready', flush=True)
    if sys.stdin.readline() != 'interrupt\n':
        return

    workers = []
    for bridge in owner.children(recursive=True):
        try:
            if bridge.name().lower() not in {'history_bridge', 'history_bridge.exe'} or Path(bridge.cwd()).resolve() != root:
                continue
            for child in bridge.children(recursive=True):
                if child.cmdline()[1:] == ['-m', 'worker_runtime', '--role', 'market', '--demo'] and Path(child.cwd()).resolve() == root:
                    workers.append(child)
        except psutil.NoSuchProcess:
            continue
    # The Windows venv redirector can parent the actual interpreter. Select
    # the interpreter, and require exactly one matching process lineage.
    parents = {child.ppid() for child in workers}
    leaves = [child for child in workers if child.pid not in parents]
    if len(leaves) != 1:
        raise RuntimeError(f'Expected one owned synthetic market interpreter, found {len(leaves)}')
    leaves[0].kill()


if __name__ == '__main__':
    main()
