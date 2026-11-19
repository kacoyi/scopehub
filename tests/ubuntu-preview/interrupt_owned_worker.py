"""Interrupt the one market worker below this test's own preview host."""

from pathlib import Path
import sys

import psutil


def owned_test_runner(helper, expected_pid, redirector):
    parent = helper.parent()
    if parent is not None and redirector is not None:
        # The venv's redirector starts the actual interpreter using the same
        # command line. Only this exact launcher may sit between it and Node.
        if (Path(parent.exe()).resolve() == redirector.resolve()
                and parent.cmdline()[1:] == helper.cmdline()[1:]):
            parent = parent.parent()
    if parent is None or parent.pid != expected_pid or Path(parent.exe()).stem.lower() != 'node':
        raise RuntimeError('A live preview test must own this helper')
    return parent


def main():
    root = Path(__file__).resolve().parents[2]
    if len(sys.argv) != 2 or not sys.argv[1].isdecimal() or sys.stdin.isatty():
        raise RuntimeError('A live preview test must own this helper')
    redirector = root / '.venv' / 'Scripts' / 'python.exe' if sys.platform == 'win32' else None
    owner = owned_test_runner(psutil.Process(), int(sys.argv[1]), redirector)
    bridge_name = 'history_bridge.exe' if sys.platform == 'win32' else 'history_bridge'
    bridge_executable = (root / 'target' / 'debug' / 'examples' / bridge_name).resolve()
    print('ready', flush=True)
    if sys.stdin.readline() != 'interrupt\n':
        return

    workers = []
    for bridge in owner.children(recursive=True):
        try:
            if (bridge.name().lower() != bridge_name or Path(bridge.exe()).resolve() != bridge_executable
                    or Path(bridge.cwd()).resolve() != root):
                continue
            for child in bridge.children(recursive=True):
                if child.cmdline()[1:] == ['-m', 'worker_runtime', '--role', 'market', '--demo'] and Path(child.cwd()).resolve() == root:
                    workers.append((child, bridge))
        except psutil.NoSuchProcess:
            continue
    # The Windows venv redirector can parent the actual interpreter. Select
    # the interpreter, and require exactly one matching process lineage.
    parents = {child.ppid() for child, _ in workers}
    leaves = [(child, bridge) for child, bridge in workers if child.pid not in parents]
    if len(leaves) != 1:
        raise RuntimeError(f'Expected one owned synthetic market interpreter, found {len(leaves)}')
    child, bridge = leaves[0]
    if bridge not in child.parents() or owner not in bridge.parents():
        raise RuntimeError('The preview no longer owns the selected worker')
    child.kill()


if __name__ == '__main__':
    main()
