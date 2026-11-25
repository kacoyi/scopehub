"""Interrupt the one market worker below this test's own preview host."""

from pathlib import Path
import sys
from uuid import UUID

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


def owned_worker(owner, root, bridge_executable, interpreter, redirector, witness):
    workers = []
    worker_script = root / 'tests' / 'ubuntu-preview' / 'market_interruption_worker.py'
    for bridge in owner.children(recursive=True):
        try:
            if (bridge.name().lower() != bridge_executable.name
                    or Path(bridge.exe()).resolve() != bridge_executable
                    or Path(bridge.cwd()).resolve() != root
                    or bridge.cmdline()[1:] != ['--test-market-interruption', witness]):
                continue
            for child in bridge.children():
                args = child.cmdline()[1:]
                if len(args) != 2 or Path(args[0]).resolve() != worker_script or args[1] != witness:
                    continue
                if redirector is not None and Path(child.exe()).resolve() == redirector:
                    # The exact venv launcher may parent the interpreter
                    # with the same command line on Windows.
                    candidates = [member for member in child.children() if member.cmdline()[1:] == args]
                else:
                    candidates = [child]
                workers.extend((member, bridge) for member in candidates
                               if Path(member.exe()).resolve() == interpreter
                               and Path(member.cwd()).resolve() == root)
        except psutil.NoSuchProcess:
            continue

    if len(workers) != 1:
        raise RuntimeError(f'Expected one owned synthetic market interpreter, found {len(workers)}')
    child, bridge = workers[0]
    if bridge not in child.parents() or owner not in bridge.parents():
        raise RuntimeError('The preview no longer owns the selected worker')
    return child


def main():
    root = Path(__file__).resolve().parents[2]
    if len(sys.argv) != 3 or not sys.argv[1].isdecimal() or sys.stdin.isatty():
        raise RuntimeError('A live preview test must own this helper')
    witness = str(UUID(sys.argv[2]))
    helper = psutil.Process()
    redirector = (root / '.venv' / 'Scripts' / 'python.exe').resolve() if sys.platform == 'win32' else None
    owner = owned_test_runner(helper, int(sys.argv[1]), redirector)
    bridge_name = 'history_bridge.exe' if sys.platform == 'win32' else 'history_bridge'
    bridge_executable = (root / 'target' / 'debug' / 'examples' / bridge_name).resolve()
    print('ready', flush=True)
    if sys.stdin.readline() != 'interrupt\n':
        raise RuntimeError('The test did not request its owned interruption')
    child = owned_worker(owner, root, bridge_executable, Path(helper.exe()).resolve(), redirector, witness)
    child.kill()


if __name__ == '__main__':
    main()
