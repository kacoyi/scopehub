"""Observe or interrupt the SQLite writer belonging to this browser test's Vite process."""

import os
import sys
from pathlib import Path

import psutil


def main():
    root = Path(__file__).resolve().parents[2]
    executable = root / 'target/debug' / ('workspace_bridge.exe' if os.name == 'nt' else 'workspace_bridge')
    owner = psutil.Process().parent()
    if owner is None or Path(owner.exe()).stem.lower() != 'node':
        raise RuntimeError('The browser test must own the process probe')
    server_pid = int(sys.argv[1])
    hosts = [child for child in owner.children() if child.pid == server_pid
             and Path(child.exe()).stem.lower() == 'node' and 'ubuntu-workspace' in child.cmdline()]
    if len(hosts) != 1:
        raise RuntimeError('The test no longer owns its workspace service')
    writers = [child for child in hosts[0].children() if Path(child.exe()).resolve() == executable]
    if len(writers) != 1:
        raise RuntimeError('Expected one local workspace writer')
    writer = writers[0]
    if len(sys.argv) > 2:
        if writer.pid != int(sys.argv[3]):
            raise RuntimeError('The observed workspace writer has changed')
        {'suspend': writer.suspend, 'resume': writer.resume, 'kill': writer.kill}[sys.argv[2]]()
    print(writer.pid)


if __name__ == '__main__':
    main()
