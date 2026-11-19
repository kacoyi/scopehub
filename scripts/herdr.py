"""Launch a bounded ScopeHub development session in the local Herdr core."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4


ROLE_PROFILES = {'implement': 'implement', 'review': 'supervise', 'coordinate': 'supervise'}


class LaunchError(ValueError):
    """The requested session cannot be launched as specified."""


def find_worktree(start: Path) -> Path:
    # Read Git's worktree marker directly so even a dry run needs no executable.
    for directory in (start, *start.parents):
        marker = directory / '.git'
        if marker.is_dir():
            git_dir = marker
        elif marker.is_file():
            pointer = os.fsdecode(marker.read_bytes()).rstrip('\r\n')
            prefix = 'gitdir: '
            if not pointer.startswith(prefix) or pointer == prefix:
                raise LaunchError(f'Invalid ScopeHub Git worktree marker {marker}')
            git_dir = directory / pointer.removeprefix(prefix)
        else:
            continue
        if not (git_dir / 'HEAD').is_file():
            raise LaunchError(f'Cannot find ScopeHub Git worktree metadata at {git_dir}')
        return directory
    raise LaunchError('Run this command from a ScopeHub Git worktree.')


def read_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise LaunchError(f'Cannot read ScopeHub record {path}: {error}') from error
    if not isinstance(record, dict):
        raise LaunchError(f'Expected a JSON object in {path}')
    return record


def role_profile(root: Path, role: str) -> tuple[str, str]:
    profile = ROLE_PROFILES[role]
    try:
        settings = read_record(root / 'project/development.json')['herdr']
        if settings['host'] != 'Herdr':
            raise LaunchError('Expected Herdr as the host in project/development.json#herdr')
        options = settings['profiles'][profile]
        model, effort = options['model'], options['reasoning_effort']
    except (KeyError, TypeError) as error:
        raise LaunchError(f'Missing or invalid project/development.json#herdr profile {profile!r}') from error
    if not all(isinstance(value, str) and value.strip() for value in (model, effort)):
        raise LaunchError(f'Herdr profile {profile!r} requires a model and reasoning_effort.')
    return model, effort


def require_local_space(root: Path, space_id: str) -> None:
    result = subprocess.run(
        ['herdr-app', 'ls'], cwd=root, check=True, stdout=subprocess.PIPE,
    )
    try:
        spaces = json.loads(result.stdout)['spaces']
    except (ValueError, KeyError, TypeError) as error:
        raise LaunchError('Cannot read the space list from herdr-app ls.') from error
    if not isinstance(spaces, list):
        raise LaunchError('Cannot read the space list from herdr-app ls.')
    for space in spaces:
        if isinstance(space, dict) and space.get('id') == space_id and space.get('machine') is None:
            return
    raise LaunchError(f'No existing local Herdr space has ID {space_id!r}.')


def launch_argv(root: Path, args: argparse.Namespace) -> list[str]:
    tasks = read_record(root / 'project/tasks.json').get('tasks')
    if not isinstance(tasks, list):
        raise LaunchError('Expected a task list in project/tasks.json.')
    if not any(isinstance(task, dict) and task.get('id') == args.task_id for task in tasks):
        raise LaunchError(f'Unknown ScopeHub task ID {args.task_id!r} in project/tasks.json.')
    model, effort = role_profile(root, args.role)

    # Herdr restricts names to 32 characters and requires live names to be unique.
    prefix = re.sub(r'[^a-z0-9_-]', '-', f'{args.task_id.lower()}-{args.role}')[:19]
    name = f'{prefix}-{uuid4().hex[:12]}'
    command = [
        'hc', 'run', '--bg', '--cwd', str(root), '--name', name,
        '--label', f'task={args.task_id}', '--label', f'role={args.role}',
    ]
    if args.space is not None:
        command.extend(['--label', f'herdr.space={args.space}'])
    command.extend(['--', 'codex', '--no-daemon', '--model', model, '-c',
                    f'model_reasoning_effort={json.dumps(effort)}'])
    if args.role == 'review':
        command.extend(['--sandbox', 'read-only', '--ask-for-approval', 'never'])
        python = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
        context = f'{python} -B scripts/project.py context {args.task_id}'
    else:
        command.append('--approve-for-me')
        context = f'pnpm project:context {args.task_id}'
    prompt = (
        f'ScopeHub task {args.task_id}, role {args.role}.\n'
        f'See `{context}` for task context.\n\n'
        f'{args.assignment}'
    )
    command.extend(['--', prompt])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(prog='pnpm project:herdr', description=__doc__, allow_abbrev=False)
    parser.add_argument('task_id')
    parser.add_argument('role', choices=ROLE_PROFILES)
    parser.add_argument('--assignment', required=True,
                        help='Bounded task text. Use --assignment=TEXT if it begins with a dash.')
    parser.add_argument('--space', metavar='ID', help='Existing local Herdr space for a visible tab.')
    parser.add_argument('--dry-run', action='store_true', help='Print launch argv without running commands.')
    args = parser.parse_args()
    if not args.assignment.strip():
        parser.error('--assignment must contain task text.')
    if args.space is not None and (not args.space or '/' in args.space or any(map(str.isspace, args.space))):
        parser.error('--space must be a local Herdr space ID.')
    try:
        root = find_worktree(Path.cwd())
        command = launch_argv(root, args)
        if args.dry_run:
            print(json.dumps({'cwd': str(root), 'argv': command}, indent=2))
            return 0
        if args.space is not None:
            require_local_space(root, args.space)
        subprocess.run(command, cwd=root, check=True)
    except subprocess.CalledProcessError as error:
        tool = ' '.join(error.cmd[:2])
        print(f'{parser.prog}: {tool} failed with exit status {error.returncode}.', file=sys.stderr)
        return error.returncode if error.returncode > 0 else 128 - error.returncode
    except (LaunchError, OSError) as error:
        print(f'{parser.prog}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
