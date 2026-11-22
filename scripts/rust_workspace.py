"""Use Cargo's workspace membership for architecture and host-specific verification."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS_TEST = 'workspace_process'
OPERATIONS_SUCCESS = re.compile(
    rb'test result: ok\. [1-9][0-9]* passed; 0 failed; 0 ignored; '
    rb'[0-9]+ measured; [0-9]+ filtered out; finished in [0-9]+\.[0-9]+s',
)


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
    if action not in {'test', 'clippy', 'operations'}:
        raise ValueError(f'Unknown Rust verification action: {action}')
    system = system or platform.system()
    machine = machine or platform.machine()
    native = system == 'Windows' and machine.lower() in {'x86_64', 'amd64'}
    included, excluded, portable_tests = [], [], []
    command = ['cargo', 'clippy' if action == 'clippy' else 'test', '--locked', '--workspace']
    for package in packages:
        scope = (package.get('metadata') or {}).get('scopehub', {})
        target = scope.get('check_platform', 'all')
        if target not in {'all', 'windows-x64'}:
            raise ValueError(f'Unsupported check platform for {package["name"]}: {target}')
        if target == 'windows-x64' and not native:
            excluded.append({'package': package['name'], 'reason': 'Requires native Windows x64.'})
            command.extend(['--exclude', package['name']])
            if action == 'test' and scope.get('portable_test_lib') is True:
                portable_tests.append(package['name'])
        else:
            included.append(package['name'])
    if not included:
        raise ValueError('No workspace packages are eligible on this host')
    if action == 'clippy':
        command.extend(['--all-targets', '--', '-D', 'warnings'])
    elif action == 'operations':
        owners = [package['name'] for package in packages if package['name'] in included
                  and any(target['kind'] == ['test'] and target['name'] == OPERATIONS_TEST
                          for target in package['targets'])]
        if owners != ['scopehub-persistence']:
            raise ValueError(f'Expected one eligible scopehub-persistence {OPERATIONS_TEST} test target')
        # Workspace selection keeps the full test feature graph. Cargo builds and runs only this target.
        command.extend(['--test', OPERATIONS_TEST, 'operations_workflow::'])
    return command, {'included': included, 'excluded': excluded, 'portable_library_tests': portable_tests}


def run_operations(command):
    summaries = 0
    passed = False
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            if line.startswith(b'test result: '):
                summaries += 1
                passed = OPERATIONS_SUCCESS.fullmatch(line.rstrip(b'\r\n')) is not None
        status = process.wait()
    if status:
        return status
    if summaries == 1 and passed:
        return 0
    print(json.dumps({'status': 'failed',
                      'error': 'Expected one operations test summary with passing cases and zero ignored cases'}), flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['test', 'clippy', 'operations'])
    args = parser.parse_args()
    try:
        if args.action == 'operations':
            if os.environ.get('SCOPEHUB_REUSE_OPERATIONS_BUILD') == '1':
                command, _ = verification_command(workspace_packages(), args.action)
            else:
                command = ['cargo', 'test', '--locked', '-p', 'scopehub-persistence',
                           '--test', OPERATIONS_TEST, 'operations_workflow::']
            print(json.dumps({'command': command}), flush=True)
            return run_operations(command)
        command, selection = verification_command(workspace_packages(), args.action)
        print(json.dumps({'command': command, 'workspace_coverage': selection}), flush=True)
        status = subprocess.run(command, cwd=ROOT, check=False).returncode
        if status or not selection['portable_library_tests']:
            return status
        library_tests = ['cargo', 'test', '--locked', '--lib']
        for package in selection['portable_library_tests']:
            library_tests.extend(['--package', package])
        print(json.dumps({'command': library_tests}), flush=True)
        return subprocess.run(library_tests, cwd=ROOT, check=False).returncode
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
