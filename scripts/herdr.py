"""Launch a bounded ScopeHub development session in the local Herdr core."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

if __package__:
    from .development import HERDR_ROLE_PHASES, feedback_for_task, task_launch_excerpt
    from .storage import maintain_automatically
else:
    from development import HERDR_ROLE_PHASES, feedback_for_task, task_launch_excerpt
    from storage import maintain_automatically


ROLE_PROFILES = {'implement': 'implement', 'review': 'supervise', 'coordinate': 'supervise'}
HIGHER_EFFORTS = ('high', 'xhigh', 'max', 'ultra')
CATALOG_TIMEOUT_SECONDS = 15


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


def model_catalog(root: Path, *, dry_run: bool) -> list:
    if dry_run:
        codex_home = os.environ.get('CODEX_HOME')
        cache = (root / codex_home if codex_home else Path.home() / '.codex') / 'models_cache.json'
        source = f'Codex model cache at {cache}'
        recovery = 'Start Codex to populate the cache, or retry without --dry-run.'
        try:
            payload = cache.read_bytes()
        except OSError as error:
            raise LaunchError(f'Cannot read {source}. {recovery}') from error
    else:
        source = 'catalog from codex debug models'
        recovery = 'Check the installed Codex CLI and retry.'
        try:
            result = subprocess.run(
                ['codex', 'debug', 'models'], cwd=root, capture_output=True,
                timeout=CATALOG_TIMEOUT_SECONDS, check=True,
            )
        except subprocess.TimeoutExpired as error:
            raise LaunchError(f'codex debug models timed out after {CATALOG_TIMEOUT_SECONDS} seconds.') from error
        except subprocess.CalledProcessError as error:
            raise LaunchError(f'codex debug models failed with exit status {error.returncode}. {recovery}') from error
        except OSError as error:
            raise LaunchError(f'Cannot run codex debug models. {recovery}') from error
        payload = result.stdout

    try:
        models = json.loads(payload)['models']
    except (ValueError, KeyError, TypeError) as error:
        raise LaunchError(f'Invalid {source}. {recovery}') from error
    if not isinstance(models, list):
        raise LaunchError(f'Invalid {source}. {recovery}')
    return models


def resolve_model(models: list, family: str, effort: str) -> str:
    stable_slug = re.compile(rf'gpt-([0-9]+(?:\.[0-9]+)*)-{re.escape(family)}')
    candidates = []
    for model in models:
        if not isinstance(model, dict) or model.get('visibility') != 'list':
            continue
        slug = model.get('slug')
        match = stable_slug.fullmatch(slug) if isinstance(slug, str) else None
        levels = model.get('supported_reasoning_levels')
        if match and isinstance(levels, list) and any(
            isinstance(level, dict) and level.get('effort') == effort for level in levels
        ):
            version = tuple(int(part) for part in match[1].split('.'))
            candidates.append((version, match[0]))
    if not candidates:
        raise LaunchError(f'No listed stable Codex model in family {family!r} supports effort {effort!r}.')
    return max(candidates)[1]


def role_profile(root: Path, args: argparse.Namespace) -> tuple[str, str]:
    profile = 'supervise' if args.escalate_model else ROLE_PROFILES[args.role]
    invalid_profile = f'Missing or invalid project/development.json#herdr profile {profile!r}'
    try:
        settings = read_record(root / 'project/development.json')['herdr']
        if settings['host'] != 'Herdr':
            raise LaunchError('Expected Herdr as the host in project/development.json#herdr')
        profiles = settings['profiles']
        options = profiles[profile]
    except (KeyError, TypeError) as error:
        raise LaunchError(invalid_profile) from error
    if not isinstance(profiles, dict) or not isinstance(options, dict):
        raise LaunchError(invalid_profile)
    family_policy = 'reasoning_efforts' in settings or any(
        isinstance(entry, dict) and 'family' in entry for entry in profiles.values()
    )
    if not family_policy:
        model, effort = options.get('model'), options.get('reasoning_effort')
        if not all(isinstance(value, str) and value.strip() for value in (model, effort)):
            raise LaunchError(invalid_profile)
        if args.effort is not None or args.escalate_model:
            raise LaunchError('--effort and --escalate-model require a family-based Herdr profile.')
        return model, effort

    for name in ('implement', 'supervise'):
        entry = profiles.get(name)
        family = entry.get('family') if isinstance(entry, dict) else None
        if not isinstance(family, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', family) or set(entry) != {'family'}:
            raise LaunchError(f'Missing or invalid project/development.json#herdr profile {name!r}')
    efforts = settings.get('reasoning_efforts')
    if (not isinstance(efforts, list) or not efforts or efforts[0] != 'medium'
            or any(effort not in HIGHER_EFFORTS for effort in efforts[1:]) or len(set(efforts)) != len(efforts)):
        raise LaunchError('Herdr reasoning_efforts must start with medium and list only distinct higher efforts after it.')
    effort = efforts[0] if args.effort is None else args.effort
    if effort not in efforts:
        raise LaunchError(f'Unsupported Herdr effort {effort!r}. Choose from {", ".join(efforts)}.')
    return resolve_model(model_catalog(root, dry_run=args.dry_run), options['family'], effort), effort


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


def check_review_source(root: Path, source: str) -> None:
    if __package__:
        from .project import ProjectError, require_review_source
    else:
        from project import ProjectError, require_review_source
    try:
        require_review_source(root, source)
    except ProjectError as error:
        raise LaunchError(str(error)) from error


def launch_argv(root: Path, args: argparse.Namespace) -> list[str]:
    tasks = read_record(root / 'project/tasks.json').get('tasks')
    if not isinstance(tasks, list):
        raise LaunchError('Expected a task list in project/tasks.json.')
    task = next((row for row in tasks if isinstance(row, dict) and row.get('id') == args.task_id), None)
    if task is None:
        raise LaunchError(f'Unknown ScopeHub task ID {args.task_id!r} in project/tasks.json.')
    feedback_path = root / 'project/feedback.json'
    items = read_record(feedback_path).get('items') if feedback_path.is_file() else []
    if not isinstance(items, list):
        raise LaunchError('Expected a feedback list in project/feedback.json.')
    excerpt = task_launch_excerpt(task, feedback_for_task(items, args.task_id))
    if args.source is not None:
        check_review_source(root, args.source)
    model, effort = role_profile(root, args)

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
    phase = HERDR_ROLE_PHASES[args.role]
    if args.role == 'review':
        command.extend(['--sandbox', 'read-only', '--ask-for-approval', 'never'])
        python = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
        context = f'{python} -B scripts/project.py context {args.task_id} --phase {phase}'
        if args.source is not None:
            context += f' --source {args.source}'
    else:
        command.append('--approve-for-me')
        context = f'pnpm project:context {args.task_id} --phase {phase}'
    bound_review = (
        'Run this context command before inspecting the candidate. If it rejects the source, stop and report '
        'the mismatch. Report the source with the outcome of the independent check.\n'
        if args.source is not None else ''
    )
    task_records = f'Task records\n{json.dumps(excerpt, ensure_ascii=False, indent=2)}\n' if excerpt else ''
    prompt = (
        f'ScopeHub task {args.task_id}, role {args.role}.\n'
        f'{task_records}'
        f'See `{context}` for task context.\n'
        f'{bound_review}\n'
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
    parser.add_argument('--effort', help='Reasoning effort for this assignment with a family-based profile.')
    parser.add_argument('--escalate-model', action='store_true',
                        help='Use the supervise model family for this implementation assignment.')
    parser.add_argument('--source', metavar='COMMIT', help='Bind a review to this full, clean HEAD commit.')
    parser.add_argument('--dry-run', action='store_true', help='Print launch argv without running commands.')
    args = parser.parse_args()
    if not args.assignment.strip():
        parser.error('--assignment must contain task text.')
    if args.space is not None and (not args.space or '/' in args.space or any(map(str.isspace, args.space))):
        parser.error('--space must be a local Herdr space ID.')
    if args.escalate_model and args.role != 'implement':
        parser.error('--escalate-model requires the implement role.')
    if args.source is not None and args.role != 'review':
        parser.error('--source requires the review role.')
    try:
        root = find_worktree(Path.cwd())
        command = launch_argv(root, args)
        if args.dry_run:
            print(json.dumps({'cwd': str(root), 'argv': command}, indent=2))
            return 0
        if args.space is not None:
            require_local_space(root, args.space)
        if args.source is not None:
            check_review_source(root, args.source)
        subprocess.run(command, cwd=root, check=True)
        maintain_automatically(root)
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
