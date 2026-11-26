"""Launch a bounded ScopeHub development session in the local Herdr core."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

if __package__:
    from .development import (
        HERDR_ROLE_PHASES, feedback_for_task, feedback_row_routable, read_only_project_command, task_launch_excerpt,
    )
    from .review_tree import CommittedPath
    from .herdr_lifecycle import Lifecycle, LifecycleHold, require_supported_host
    from .herdr_native import ADDRESS, NativeLifecycle, connect, core_socket
    from .herdr_returns import ReturnHold, probe_return
    from .verification_scope import ScopeError
    from .storage import maintain_automatically
else:
    from development import (
        HERDR_ROLE_PHASES, feedback_for_task, feedback_row_routable, read_only_project_command, task_launch_excerpt,
    )
    from review_tree import CommittedPath
    from herdr_lifecycle import Lifecycle, LifecycleHold, require_supported_host
    from herdr_native import ADDRESS, NativeLifecycle, connect, core_socket
    from herdr_returns import ReturnHold, probe_return
    from verification_scope import ScopeError
    from storage import maintain_automatically


ROLE_PROFILES = {'implement': 'implement', 'review': 'supervise', 'coordinate': 'supervise'}
HIGHER_EFFORTS = ('high', 'xhigh', 'max', 'ultra')
CATALOG_TIMEOUT_SECONDS = 15
MANAGED_TIERS = {'implement': 'priority', 'review': 'default'}
WATCH_SECONDS = 24 * 60 * 60
LAUNCH_SETTLE_SECONDS = 90
WATCH_FINAL_HOLDS = {'unregistered_assignment', 'foreign_terminal', 'terminal_finished_without_archive',
                     'terminal_turn_changed', 'turn_changed', 'turn_interrupted', 'wrong_core'}


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


def read_record(path: Path | CommittedPath) -> dict:
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


def role_profile(root: Path, records: Path | CommittedPath, args: argparse.Namespace) -> tuple[str, str]:
    profile = 'supervise' if args.escalate_model else ROLE_PROFILES[args.role]
    invalid_profile = f'Missing or invalid project/development.json#herdr profile {profile!r}'
    try:
        settings = read_record(records / 'project/development.json')['herdr']
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
    records = root
    if args.source is not None:
        check_review_source(root, args.source)
        records = CommittedPath.at(root, args.source)
    tasks = read_record(records / 'project/tasks.json').get('tasks')
    if not isinstance(tasks, list):
        raise LaunchError('Expected a task list in project/tasks.json.')
    task = next((row for row in tasks if isinstance(row, dict) and row.get('id') == args.task_id), None)
    if task is None:
        raise LaunchError(f'Unknown ScopeHub task ID {args.task_id!r} in project/tasks.json.')
    feedback_issue = None
    try:
        items = read_record(records / 'project/feedback.json').get('items')
        if not isinstance(items, list):
            raise LaunchError('Expected a feedback list in project/feedback.json.')
    except LaunchError:
        if args.source is not None:
            raise
        items = []
        feedback_issue = 'The optional feedback record could not be read.'
    if any(not feedback_row_routable(item) for item in items):
        feedback_issue = 'Malformed rows were skipped in project/feedback.json.'
        if args.source is not None:
            raise LaunchError(feedback_issue + ' Run pnpm project:check on the candidate.')
    excerpt = task_launch_excerpt(task, feedback_for_task(items, args.task_id), args.source)
    if feedback_issue:
        excerpt['feedback_diagnostic'] = (feedback_issue + ' Run pnpm project:check for the error or '
                                          f'pnpm project:recover --task {args.task_id} --save to retain it.')
    model, effort = role_profile(root, records, args)

    # Herdr restricts names to 32 characters and requires live names to be unique.
    prefix = re.sub(r'[^a-z0-9_-]', '-', f'{args.task_id.lower()}-{args.role}')[:19]
    name = f'{prefix}-{uuid4().hex[:12]}'
    command = [
        'hc', 'run', '--bg', '--cwd', str(root), '--name', name,
        '--label', f'task={args.task_id}', '--label', f'role={args.role}',
    ]
    if args.space is not None:
        command.extend(['--label', f'herdr.space={args.space}'])
    if args.parent is not None:
        command.extend(['--parent', args.parent])
    if args.no_notify:
        command.append('--no-notify')
    if args.managed:
        command.extend(['--label', f'scopehub.assignment={args.assignment_id}'])
    command.extend(['--', 'codex', '--no-daemon', '--model', model, '-c',
                    f'model_reasoning_effort={json.dumps(effort)}'])
    if args.tier is not None:
        command.extend(['-c', f'service_tier={json.dumps(args.tier)}'])
    phase = HERDR_ROLE_PHASES[args.role]
    if args.role == 'review':
        command.extend(['--sandbox', 'read-only', '--ask-for-approval', 'never'])
        context = read_only_project_command('context', args.task_id, '--phase', phase, source_commit=args.source)
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


def git_value(root, *args):
    return subprocess.check_output(['git', '--no-optional-locks', *args], cwd=root, text=True, timeout=10).strip()


def state_directory(root, explicit):
    if explicit is not None:
        return explicit.absolute()
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    common = git_value(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    return base / f'scopehub-herdr-{sha256(os.fsencode(common)).hexdigest()[:20]}'


def checked_return(binding, terminal):
    try:
        return probe_return(binding, terminal)
    except ReturnHold as error:
        raise LifecycleHold(str(error)) from None


def collector(state, parent):
    native = NativeLifecycle(core_socket(), checked_return)
    lifecycle = Lifecycle(state, parent, observe=native.observe, retire=native.retire, probe=checked_return)
    return lifecycle, native


def inherited_parent():
    address = os.environ.get('HC_TERMINAL_ID')
    endpoint = os.environ.get('HC_TERMINAL_SOCKET')
    if not address or not ADDRESS.fullmatch(address) or not endpoint:
        return None
    try:
        if Path(endpoint).absolute() == core_socket():
            return address
    except LifecycleHold:
        pass
    return None


def launch_managed(root, args, command):
    state = state_directory(root, args.state_dir)
    lifecycle, native = collector(state, args.parent)
    with connect(native.path) as (core, core_id):
        parent = core.call('terminal.get', terminal=args.parent)
        if not parent or parent.get('address') != args.parent or parent.get('state') != 'live':
            raise LifecycleHold('owner_terminal_unavailable')
    source = args.source or git_value(root, 'rev-parse', 'HEAD')
    check_review_source(root, source)
    session_root = (root / os.environ['CODEX_HOME'] if os.environ.get('CODEX_HOME') else Path.home() / '.codex')
    argv = command[command.index('--') + 1:]
    labels = {'task': args.task_id, 'role': args.role, 'scopehub.assignment': args.assignment_id}
    if args.space is not None:
        labels['herdr.space'] = args.space
    binding = {
        'id': args.assignment_id, 'core_id': core_id, 'parent': args.parent, 'task': args.task_id,
        'role': args.role, 'checkout': str(root), 'source': source, 'space': args.space,
        'name': command[command.index('--name') + 1], 'labels': labels,
        'prompt_sha256': sha256(argv[-1].encode()).hexdigest(), 'codex_argv': argv[:-1],
        'model': argv[argv.index('--model') + 1], 'effort': json.loads(argv[argv.index('-c') + 1].split('=', 1)[1]),
        'tier': args.tier, 'sessions': str((session_root / 'sessions').resolve()),
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    lifecycle.register(binding)
    env = {**os.environ, 'HC_SOCKET': str(native.path)}
    worker = [sys.executable, '-B', str(Path(__file__).resolve()), 'collect', '--watch',
              '--state-dir', str(state), '--parent', args.parent, '--assignment-id', args.assignment_id]
    subprocess.Popen(worker, cwd=root, env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reply = subprocess.run([*command[:3], '--json', *command[3:]], cwd=root, env=env, capture_output=True,
                           timeout=60, check=True)
    try:
        address = json.loads(reply.stdout)['address']
        if not isinstance(address, str) or not ADDRESS.fullmatch(address):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise LifecycleHold('launch_reply_unavailable') from None
    print(json.dumps({'version': 1, 'assignment': args.assignment_id, 'terminal': address,
                      'source': source, 'role': args.role, 'model': binding['model'],
                      'effort': binding['effort'], 'tier': args.tier}))


def collect_main():
    parser = argparse.ArgumentParser(prog='pnpm project:herdr collect', allow_abbrev=False,
                                     description='Recover and retire registered assignments of one local owner.')
    parser.add_argument('--parent', required=True, help='Exact local Herdr owner terminal address.')
    parser.add_argument('--state-dir', type=Path, help='Existing private lifecycle root override.')
    parser.add_argument('--assignment-id', help='Inspect only this registered assignment.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Inspect without changing records or terminals.')
    mode.add_argument('--watch', action='store_true', help='Resume an assignment watcher after interruption.')
    args = parser.parse_args(sys.argv[2:])
    if not ADDRESS.fullmatch(args.parent):
        parser.error('--parent must be an exact local terminal address.')
    if args.watch and args.assignment_id is None:
        parser.error('--watch requires --assignment-id.')
    try:
        require_supported_host()
        root = find_worktree(Path.cwd())
        lifecycle, native = collector(state_directory(root, args.state_dir), args.parent)
        started = time.monotonic()
        observed = False
        while True:
            result = lifecycle.collect(args.assignment_id, dry_run=args.dry_run)
            if not args.watch:
                break
            lane = result['assignments'][0]
            elapsed = time.monotonic() - started
            reason = lane.get('reason')
            if lane['status'] == 'retired' or reason in WATCH_FINAL_HOLDS or elapsed >= WATCH_SECONDS:
                break
            if not observed and reason == 'terminal_missing' and elapsed >= LAUNCH_SETTLE_SECONDS:
                break
            observed = observed or reason in {'terminal_not_ready', 'terminal_turn_unconfirmed', 'original_missing'}
            waiting_since = time.monotonic()
            native.wait_for_change(args.assignment_id)
            # A lost connection must not turn the detached watcher into a busy loop.
            time.sleep(max(0, 1 - (time.monotonic() - waiting_since)))
        if args.assignment_id is not None and result['assignments'][0]['status'] == 'retired':
            lane = lifecycle.retired / args.assignment_id
            result['assignments'][0].update(receipt=str(lane / 'assignment.json'),
                                            original=str(lane / 'original.jsonl'))
        print(json.dumps(result, indent=2))
    except (LaunchError, LifecycleHold, OSError, subprocess.SubprocessError) as error:
        print(f'{parser.prog}: {error if isinstance(error, (LaunchError, LifecycleHold)) else "collection unavailable"}',
              file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if sys.argv[1:2] == ['collect']:
        return collect_main()
    parser = argparse.ArgumentParser(prog='pnpm project:herdr', description=__doc__, allow_abbrev=False)
    parser.add_argument('task_id')
    parser.add_argument('role', choices=ROLE_PROFILES)
    parser.add_argument('--assignment', required=True,
                        help='Bounded task text. Use --assignment=TEXT if it begins with a dash.')
    parser.add_argument('--space', metavar='ID', help='Existing local Herdr space for a visible tab.')
    parser.add_argument('--parent', metavar='TERMINAL', help='Explicit local Herdr parent terminal address.')
    parser.add_argument('--no-notify', action='store_true', help='Suppress Herdr messages to the parent.')
    parser.add_argument('--tier', choices=('priority', 'default'), help='Codex service tier (Fast or Standard).')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--managed', action='store_true', help='Explicitly register and automatically retire this assignment.')
    mode.add_argument('--standalone', action='store_true', help='Keep this terminal interactive without automatic retirement.')
    parser.add_argument('--state-dir', type=Path, help='Private root override for a managed assignment.')
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
    if args.parent is not None and not ADDRESS.fullmatch(args.parent):
        parser.error('--parent must be an exact local terminal address.')
    eligible_role = args.role == 'implement' or args.role == 'review' and args.source is not None
    legacy_preview = None
    if not args.standalone and sys.platform == 'linux' and eligible_role and (args.space or args.managed):
        parent = args.parent or inherited_parent()
        if parent is not None:
            if args.dry_run and args.parent is None and not args.managed:
                # Preserve the original argv contract for coordinators which hand it to hc themselves.
                legacy_preview = argparse.Namespace(**vars(args))
            args.parent = parent
            args.managed = True
    if args.managed and (args.parent is None or not eligible_role):
        parser.error('--managed requires a matching local parent and an implementation or source-bound review.')
    if args.state_dir is not None and not args.managed:
        parser.error('--state-dir requires a managed assignment.')
    if args.managed:
        args.assignment_id = uuid4().hex
        args.tier = args.tier or MANAGED_TIERS[args.role]
    try:
        root = find_worktree(Path.cwd())
        command = launch_argv(root, args)
        if args.dry_run:
            if args.source is not None:
                check_review_source(root, args.source)
            plan = {'cwd': str(root), 'argv': command}
            if legacy_preview is not None:
                plan.update(argv=launch_argv(root, legacy_preview), managed_argv=command)
            if args.managed:
                plan['lifecycle'] = {'version': 1, 'managed_entry_required': True,
                                     'parent': args.parent, 'tier': args.tier, 'host': 'linux'}
            print(json.dumps(plan, indent=2))
            return 0
        if args.managed:
            require_supported_host()
        if args.space is not None:
            require_local_space(root, args.space)
        if args.source is not None:
            check_review_source(root, args.source)
        if args.managed:
            launch_managed(root, args, command)
        else:
            subprocess.run(command, cwd=root, check=True)
        maintain_automatically(root)
    except subprocess.CalledProcessError as error:
        tool = ' '.join(error.cmd[:2])
        print(f'{parser.prog}: {tool} failed with exit status {error.returncode}.', file=sys.stderr)
        return error.returncode if error.returncode > 0 else 128 - error.returncode
    except subprocess.TimeoutExpired:
        print(f'{parser.prog}: launch or verification timed out.', file=sys.stderr)
        return 1
    except (LaunchError, ScopeError, LifecycleHold, OSError) as error:
        print(f'{parser.prog}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
