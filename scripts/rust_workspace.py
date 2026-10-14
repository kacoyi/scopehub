"""Use Cargo's workspace membership for architecture and host-specific verification."""

import argparse
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def workspace_packages(root=ROOT):
    result = subprocess.run(['cargo', 'metadata', '--locked', '--offline', '--no-deps', '--format-version', '1'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise ValueError(f'Cannot inspect Cargo workspace: {result.stderr.strip()}')
    metadata = json.loads(result.stdout)
    members = set(metadata['workspace_members'])
    packages = [package for package in metadata['packages'] if package['id'] in members]
    if not packages or {package['id'] for package in packages} != members:
        raise ValueError('Cargo metadata does not describe every workspace member')
    if Path(metadata['workspace_root']).resolve() != root.resolve():
        raise ValueError('Cargo selected a different workspace root')
    for package in packages:
        if not Path(package['manifest_path']).resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Workspace member outside the reviewed repository: {package["name"]}')
    return sorted(packages, key=lambda package: package['name'])


def verification_command(packages, action, *, system=None, machine=None):
    if action not in {'test', 'clippy'}:
        raise ValueError(f'Unknown Rust verification action: {action}')
    system = system or platform.system()
    machine = machine or platform.machine()
    native = system == 'Windows' and machine.lower() in {'x86_64', 'amd64'}
    included, excluded = [], []
    command = ['cargo', action, '--locked', '--workspace']
    for package in packages:
        target = (package.get('metadata') or {}).get('scopehub', {}).get('check_platform', 'all')
        if target not in {'all', 'windows-x64'}:
            raise ValueError(f'Unsupported check platform for {package["name"]}: {target}')
        if target == 'windows-x64' and not native:
            excluded.append({'package': package['name'], 'reason': 'Requires native Windows x64.'})
            command.extend(['--exclude', package['name']])
        else:
            included.append(package['name'])
    if not included:
        raise ValueError('No workspace packages are eligible on this host')
    if action == 'clippy':
        command.extend(['--all-targets', '--', '-D', 'warnings'])
    return command, {'included': included, 'excluded': excluded}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['test', 'clippy'])
    args = parser.parse_args()
    try:
        command, selection = verification_command(workspace_packages(), args.action)
        print(json.dumps({'command': command, 'workspace_coverage': selection}), flush=True)
        return subprocess.run(command, cwd=ROOT, check=False).returncode
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
