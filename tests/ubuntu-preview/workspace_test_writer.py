"""Observe or interrupt the SQLite writer belonging to this browser test's Vite process."""

import os
import sys
from pathlib import Path

import psutil


def owning_node(probe, expected_pid, redirector):
    owner = probe.parent()
    if owner is not None and redirector is not None:
        actual = Path(owner.exe())
        if actual.name.lower() == redirector.name.lower() and actual.parent.resolve() == redirector.parent.resolve():
            owner = owner.parent()
    if owner is None or owner.pid != expected_pid or Path(owner.exe()).stem.lower() != 'node':
        raise RuntimeError('The browser test must own the process probe')
    return owner


def owned_writer(owner, server_pid, executable):
    executable = executable.resolve()
    hosts = [child for child in owner.children() if child.pid == server_pid
             and Path(child.exe()).stem.lower() == 'node' and 'ubuntu-workspace' in child.cmdline()]
    if len(hosts) != 1:
        raise RuntimeError('The test no longer owns its workspace service')
    host = hosts[0]
    writers = [child for child in host.children() if Path(child.exe()).resolve() == executable]
    if len(writers) != 1:
        raise RuntimeError('Expected one local workspace writer')
    writer = writers[0]
    if host.parent() != owner or writer.parent() != host:
        raise RuntimeError('The test no longer owns its workspace writer')
    return writer


def main():
    owner_pid, server_pid, *action_args = sys.argv[1:]
    root = Path(__file__).resolve().parents[2]
    windows = os.name == 'nt'
    redirector = root / '.venv' / 'Scripts' / 'python.exe' if windows else None
    owner = owning_node(psutil.Process(), int(owner_pid), redirector)
    executable = root / 'target/debug' / ('workspace_bridge.exe' if windows else 'workspace_bridge')
    writer = owned_writer(owner, int(server_pid), executable)
    if action_args:
        action, observed_pid = action_args
        if writer.pid != int(observed_pid):
            raise RuntimeError('The observed workspace writer has changed')
        {'suspend': writer.suspend, 'resume': writer.resume, 'kill': writer.kill}[action]()
    print(writer.pid)


if __name__ == '__main__':
    main()
