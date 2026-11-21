"""Observe Git, path consumers and retained files before a separate native retirement."""

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time

if __package__:
    from . import storage, worktree
else:
    import storage
    import worktree


INTEGRATION_REF = 'refs/remotes/origin/main'
FULL_COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
DURABLE_REF = re.compile(r'refs/(?:heads|tags|remotes)/.+')
GIT_OPERATIONS = (
    'index.lock', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_START',
    'rebase-merge', 'rebase-apply', 'sequencer',
)
MAX_EVIDENCE_DETAILS = 8
MAX_CONSUMER_ENTRIES = 20_000
PROJECT_RECORDS = (
    'project/tasks.json', 'project/feedback.json', 'project/development.json', 'project/delivery.json',
)
CONTROL_PATHS = ('scripts', '.github', '.cargo', 'AGENTS.md', 'package.json', '.gitmodules')
CONTROL_FILES = {'AGENTS.md', 'package.json', '.gitmodules', '.cargo/config', '.cargo/config.toml'}
SCRIPT_SUFFIXES = {'.py', '.sh', '.bash', '.fish', '.nu', '.ps1', '.cmd', '.bat', '.js', '.mjs', '.cjs', '.ts'}


def git(cwd, *args, **options):
    return worktree.git(cwd, '--no-optional-locks', '--no-replace-objects', *args, **options)


def commit_at(cwd, ref):
    result = git(cwd, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}', allowed=(0, 128))
    return result.stdout.decode('ascii').strip() if result.returncode == 0 else None


def ancestor(cwd, head, tip):
    return bool(tip and git(cwd, 'merge-base', '--is-ancestor', head, tip, allowed=(0, 1)).returncode == 0)


def safe_relative(value):
    path = PurePosixPath(value)
    return bool(path.parts and not path.is_absolute() and '..' not in path.parts
                and ':' not in value and '\\' not in value and path.as_posix() == value)


def read_dispositions(cwd, reference):
    if reference is None:
        return None, {}
    commit, separator, name = reference.partition(':')
    if not separator or not FULL_COMMIT.fullmatch(commit) or not safe_relative(name) or commit_at(cwd, commit) != commit:
        raise worktree.WorktreeError('Supply a full commit and a repository-relative path for --dispositions.')
    try:
        records = json.loads(git(cwd, 'show', f'{commit}:{name}').stdout)
    except ValueError as error:
        raise worktree.WorktreeError(f'Cannot read committed worktree dispositions: {error}') from error
    if not isinstance(records, dict) or records.get('version') != 1 or not isinstance(records.get('dispositions'), list):
        raise worktree.WorktreeError('Expected a version 1 worktree dispositions document.')
    indexed = {}
    fields = {'head', 'disposition', 'reason', 'recoverable_ref'}
    for entry in records['dispositions']:
        if (not isinstance(entry, dict) or set(entry) != fields or
                not all(isinstance(value, str) and value.strip() for value in entry.values()) or
                not FULL_COMMIT.fullmatch(entry['head']) or entry['head'] in indexed or
                entry['disposition'] not in {'abandoned', 'superseded'} or
                not DURABLE_REF.fullmatch(entry['recoverable_ref']) or
                git(cwd, 'check-ref-format', entry['recoverable_ref'], allowed=(0, 1)).returncode):
            raise worktree.WorktreeError('Invalid or duplicate exact-head worktree disposition.')
        indexed[entry['head']] = entry
    return commit, indexed


def archive_directory(value, common, registered):
    if value is None:
        return None
    root = Path(value)
    if (not root.is_absolute() or root == root.parent or root.resolve(strict=True) != root or
            not root.is_dir() or root.is_relative_to(common) or common.is_relative_to(root) or
            any(root.is_relative_to(entry.path) or entry.path.is_relative_to(root) for entry in registered)):
        raise worktree.WorktreeError('The archive root must be a canonical absolute directory outside every checkout.')
    return root


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def read_control(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or storage.reparse(info):
        raise OSError(f'Expected a regular control record: {path}')
    return path.read_bytes()


def saved_herdr_paths():
    # Reading persisted session state avoids hc's client-side daemon startup.
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'herdr-core'
    if not state.is_absolute():
        raise ValueError('Herdr state root is not absolute')
    sessions = sorted(state.iterdir()) if state.exists() else []
    paths = {state} if sessions else set()
    for session in sessions:
        if not session.is_dir() or session.name.startswith('.'):
            continue
        terminals = json.loads(read_control(session / 'terminals.json'))['terminals']
        if not isinstance(terminals, dict):
            raise ValueError('Unrecognized Herdr terminal state')
        for terminal in terminals.values():
            if terminal['state'] == 'finished':
                continue
            foreground = terminal.get('foreground') or {}
            terminal_paths = {Path(value) for value in (terminal.get('cwd'), terminal.get('last_cwd'),
                                                        foreground.get('cwd'))
                              if isinstance(value, str) and Path(value).is_absolute()}
            if not terminal_paths:
                raise ValueError('Cannot identify a live Herdr terminal path')
            paths.update(terminal_paths)
        docs = session / 'docs.json'
        if docs.exists():
            app = json.loads(read_control(docs)).get('herdr-app', {})
            for value in strings(app):
                if Path(value).is_absolute():
                    paths.add(Path(value))
    return paths


def observe_activity():
    paths = set()
    uncertainty = []
    try:
        context = storage.linux_context(time.monotonic() + 10)
        paths.update(context.active_paths)
        if context.inaccessible_processes:
            uncertainty.append('process_observation_incomplete')
    except (OSError, ValueError, storage.StorageError):
        uncertainty.append('host_process_observation_unavailable')
    try:
        paths.update(saved_herdr_paths())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        uncertainty.append('herdr_observation_unavailable')
    return paths, uncertainty


def path_is_under(value, path):
    return value.is_relative_to(path) or value.resolve().is_relative_to(path)


def dependency_patterns(consumer, git_dir, candidates):
    # Project references can be relative to either the checkout or its project record.
    patterns = {}
    for path in candidates:
        if path == consumer:
            continue
        variants = {path.as_posix()}
        for directory in (consumer, consumer / 'project', consumer / 'scripts', git_dir):
            try:
                variants.add(Path(os.path.relpath(path, directory)).as_posix())
            except ValueError:  # Windows paths on different drives.
                pass
        parts = '|'.join(re.escape(value) for value in sorted(variants, key=len, reverse=True))
        patterns[path] = re.compile(rf'(?:(?<![\w./-])|(?<=file://)|(?<=file:///))(?:{parts})'
                                    rf'(?=$|[/\s\x00\x22\x27`):,;\]])',
                                    re.IGNORECASE if os.name == 'nt' else 0)
    return patterns


def observe_dependencies(consumer, candidates, common, git_dir, config, ignored, mounts):
    patterns = dependency_patterns(consumer, git_dir, candidates)
    observed = defaultdict(set)

    def observe_text(value, name):
        value = value.replace('\\', '/') if os.name == 'nt' else value
        for target, pattern in patterns.items():
            if pattern.search(value):
                observed[target].add(name)

    for name in PROJECT_RECORDS:
        path = consumer / name
        if not path.exists():
            continue
        for value in strings(json.loads(read_control(path))):
            observe_text(value, name)

    # Effective Git configuration may include external files or checkout-specific overrides.
    observe_text(config, 'Git configuration')

    for alternates in {directory / 'objects/info/alternates' for directory in (common, git_dir)}:
        if alternates.is_file():
            for value in os.fsdecode(read_control(alternates)).splitlines():
                source = alternates.parent.parent / value
                for target in patterns:
                    if path_is_under(source, target):
                        observed[target].add('Git object alternates')

    tracked = os.fsdecode(git(consumer, 'ls-files', '--stage', '-z').stdout).split('\0')
    for entry in tracked:
        if entry.startswith('160000 '):
            raise ValueError('Nested Git submodule consumer scope needs independent observation')
        if entry.startswith('120000 '):
            observe_symlink(consumer / entry.partition('\t')[2], patterns, observed)
    controls = os.fsdecode(git(consumer, 'ls-files', '--cached', '--others', '--exclude-standard', '-z',
                                '--', *CONTROL_PATHS).stdout)
    for name in set(controls.split('\0')) - {''}:
        relative = PurePosixPath(name)
        operational = (name in CONTROL_FILES or relative.suffix in SCRIPT_SUFFIXES or
                       (relative.parts[0] == '.github' and relative.suffix in {'.yml', '.yaml'}))
        path = consumer / name
        if operational and path.is_file() and not path.is_symlink():
            observe_text(path.read_text(encoding='utf-8', errors='surrogateescape'), name)
    untracked = os.fsdecode(git(consumer, 'ls-files', '--others', '--exclude-standard', '-z').stdout)
    for name in untracked.split('\0'):
        if name:
            observe_symlink(consumer / name, patterns, observed)
    for path, regular in ignored_files(consumer, ignored, mounts, include_generated_links=True):
        if not regular:
            observe_symlink(path, patterns, observed)
    return observed


def observe_symlink(path, candidates, observed):
    if path.is_symlink():
        target = path.parent / os.readlink(path)
        for candidate in candidates:
            if path_is_under(target, candidate):
                observed[candidate].add(str(path))


def git_status(checkout):
    config = os.fsdecode(git(checkout, 'config', '--null', '--list', '--includes').stdout)
    filters = []
    for line in config.split('\0'):
        key, _, value = line.partition('\n')
        if value and key.startswith('filter.') and key.endswith(('.clean', '.process')):
            filters.append(key)
    overrides = [option for key in filters for option in ('-c', f'{key}=')]
    required = {key.rpartition('.')[0] + '.required' for key in filters}
    overrides.extend(option for key in sorted(required) for option in ('-c', f'{key}=false'))
    output = git(checkout, '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', *overrides,
                 'status', '--porcelain=v1', '-z', '--untracked-files=normal',
                 '--ignored=matching', '--ignore-submodules=all').stdout
    records = [item for item in os.fsdecode(output).split('\0') if item]
    hidden = any(item and (item[0] == 'S' or item[0].islower())
                 for item in os.fsdecode(git(checkout, 'ls-files', '-v', '-z').stdout).split('\0'))
    ignored = [
        checkout / item[3:].removesuffix('/') for item in records if item.startswith('!! ')
    ]
    return any(not item.startswith('!! ') for item in records), hidden, ignored, config, filters


def cache_tag(path):
    marker = path / 'CACHEDIR.TAG'
    if marker.is_symlink() or not marker.is_file():
        return False
    with marker.open('rb') as handle:
        return handle.read(len(storage.CARGO_TAG)) == storage.CARGO_TAG


def rebuildable(path, checkout, info):
    # Ignore only known generated layouts, even when broader Git ignore rules apply.
    relative = path.relative_to(checkout)
    if (path.parent.name == '__pycache__' and stat.S_ISREG(info.st_mode) and not storage.reparse(info) and
            (match := re.fullmatch(r'(.+)\.cpython-\d+(?:\.opt-\d+)?\.pyc', path.name))):
        return (path.parent.parent / f'{match[1]}.py').is_file()
    if storage.reparse(info) and path.parent.name != 'node_modules':
        return relative.as_posix() == '.venv/lib64' and os.readlink(path) == 'lib' and cache_tag(checkout / '.venv')
    if relative.parts[0] == '.venv' and len(relative.parts) == 2:
        return ((checkout / 'uv.lock').is_file() and cache_tag(checkout / '.venv') and
                path.name in {'.gitignore', '.lock', 'CACHEDIR.TAG', 'pyvenv.cfg', 'bin', 'lib', 'lib64',
                              'Scripts', 'Lib', 'Include'})
    if relative.parts[0] == '.uv-cache' and len(relative.parts) == 2:
        return (cache_tag(checkout / '.uv-cache') and
                (path.name in {'.gitignore', '.lock', 'CACHEDIR.TAG'} or
                 bool(re.fullmatch(r'(?:archive|builder|interpreter|sdists|simple|wheels)-v\d+', path.name))))
    if relative.parts[0] == 'target' and (checkout / 'Cargo.lock').is_file() and cache_tag(checkout / 'target'):
        if len(relative.parts) == 2:
            return path.name in {'CACHEDIR.TAG', '.rustc_info.json'}
        if len(relative.parts) == 3 and relative.parts[1] == 'debug':
            return path.name in {'deps', '.fingerprint', 'build', 'incremental', *storage.CARGO_LOCKS}
    if path.parent.name == 'node_modules':
        root_modules = checkout / 'node_modules'
        if ((checkout / 'pnpm-lock.yaml').is_file() and (root_modules / '.modules.yaml').is_file() and
                (path.parent.parent / 'package.json').is_file()):
            return path.name in {'.bin', '.modules.yaml', '.pnpm', '.pnpm-task-run-state-v1',
                                 '.pnpm-workspace-state-v1.json'} or (
                path.is_symlink() and path_is_under(path, root_modules / '.pnpm')
            )
    return False


def ignored_files(checkout, paths, mounts, *, include_generated_links=False, exclusions=None):
    pending = sorted(paths, reverse=True)
    inspected = 0
    while pending:
        path = pending.pop()
        inspected += 1
        if inspected > MAX_CONSUMER_ENTRIES:
            raise OSError('Ignored path inventory exceeded the observation budget')
        info = path.lstat()
        if path in mounts or (not mounts and stat.S_ISDIR(info.st_mode) and path.is_mount()):
            if include_generated_links:
                raise OSError(f'Consumer path crosses a mount boundary: {path}')
            yield path, False
        elif rebuildable(path, checkout, info):
            if exclusions is not None:
                exclusions.append(path)
            if include_generated_links and path.is_symlink():
                yield path, False
            elif include_generated_links and path == checkout / '.venv/bin':
                pending.extend(sorted(path.iterdir(), reverse=True))
            continue
        elif stat.S_ISDIR(info.st_mode) and not storage.reparse(info):
            pending.extend(sorted(path.iterdir(), reverse=True))
        else:
            yield path, stat.S_ISREG(info.st_mode) and not storage.reparse(info)


def hash_file(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError(f'Expected a regular retained file: {path}')
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        after = path.lstat()
    if (not os.path.samestat(before, after) or storage.reparse(after) or
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise OSError(f'File changed during observation: {path}')
    return digest.hexdigest(), (before.st_dev, before.st_ino)


def retained(checkout, archive, head, source):
    if archive is None:
        return False
    name = source.relative_to(checkout)
    possibilities = [archive / 'worktrees' / head / 'files' / name]
    if len(name.parts) >= 4 and name.parts[0] == '.reports' and name.parts[1] in {'runs', 'recovery'}:
        possibilities.append(archive.joinpath(*name.parts[1:]))
    source_identity = None
    for target in possibilities:
        if target.resolve() != target or not target.is_file():
            continue
        if source_identity is None:
            source_identity = hash_file(source)
        source_hash, source_inode = source_identity
        target_hash, target_inode = hash_file(target)
        if source_hash == target_hash and source_inode != target_inode:
            return True
    return False


def registration_snapshot(registered):
    # Isolated CLI execution loads the pinned entrypoint as __main__.
    return [(entry.path, entry.head, entry.branch, entry.flags, entry.lock_reason) for entry in registered]


def checkout_limited_to_owner(path):
    owner = os.geteuid()
    for entry in (path, *path.parents):
        info = entry.stat()
        if info.st_uid == owner and not info.st_mode & 0o011:
            return True
    return False


def retirement_report(cwd, common, registered, *, archive_root=None, dispositions=None):
    archive_error = None
    archive = None
    if archive_root is None:
        archive_root = os.environ.get('SCOPEHUB_ARCHIVE_ROOT')
    try:
        archive = archive_directory(archive_root, common, registered)
    except (OSError, RuntimeError) as error:
        archive_error = str(error)
    disposition_commit, disposition_by_head = read_dispositions(cwd, dispositions)
    integrated = commit_at(cwd, INTEGRATION_REF)
    worktrees = []
    dependencies = defaultdict(set)
    uncertain_consumers = []
    checkouts = [entry.path for entry in registered]
    mounts_unavailable = False
    mounts = {}
    if sys.platform == 'linux':
        try:
            mounts, _ = storage.mount_table()
        except (OSError, IndexError, ValueError):
            mounts_unavailable = True
    for position, entry in enumerate(registered):
        checkout = entry.path
        holds = {}
        row = {'path': str(checkout), 'head': entry.head, 'branch': entry.branch,
               'integration': None, 'recovery_ref': None, 'clean': None,
               'local_evidence': None, 'details': holds}
        worktrees.append(row)
        if position == 0:
            holds['primary_checkout'] = True
        if checkout == cwd:
            holds['invoking_checkout'] = True
        if 'locked' in entry.flags:
            holds['worktree_locked'] = entry.lock_reason or True
        ignored = None
        try:
            worktree.verify_checkout(cwd, common, entry)
        except (OSError, RuntimeError) as error:
            holds['registration_unavailable'] = str(error)
            # Git's prunable entry for a missing path has no checkout files to consume.
            try:
                missing = worktree.entry_mode(checkout) is None
            except OSError:
                missing = False
            if not missing:
                uncertain_consumers.append(str(checkout))
            continue
        try:
            if not entry.head or commit_at(checkout, 'HEAD') != entry.head:
                holds['checkout_head_unverified'] = True
            elif ancestor(cwd, entry.head, integrated):
                row['integration'] = {'kind': 'ancestry', 'ref': INTEGRATION_REF, 'tip': integrated}
                row['recovery_ref'] = INTEGRATION_REF
            elif (record := disposition_by_head.get(entry.head)):
                ref = record['recoverable_ref']
                tip = commit_at(cwd, ref)
                if (ancestor(cwd, entry.head, tip) and
                        (ancestor(cwd, disposition_commit, tip) or ancestor(cwd, disposition_commit, integrated))):
                    row['integration'] = {'kind': 'disposition', 'source': dispositions, 'retained_tip': tip, **record}
                    row['recovery_ref'] = ref
            if row['integration'] is None:
                holds['not_integrated_or_disposed'] = True

            git_dir = worktree.git_path(checkout, '--git-dir')
            dirty, hidden, ignored, config, filters = git_status(checkout)
            if dirty:
                holds['dirty_checkout'] = True
            if hidden:
                holds['index_visibility_restricted'] = True
            if filters:
                holds['external_clean_filter_unverified'] = sorted(set(filters))
            operations = [name for name in GIT_OPERATIONS if worktree.entry_mode(git_dir / name) is not None]
            if operations:
                holds['git_operation_in_progress'] = operations
            row['clean'] = not (dirty or hidden or filters or operations)
            if sys.platform == 'linux' and not checkout_limited_to_owner(checkout):
                holds['other_user_process_visibility_unverified'] = True
            evidence = []
            excluded = []
            verified = 0
            for source, regular in ignored_files(checkout, ignored, mounts, exclusions=excluded):
                if not regular or not retained(checkout, archive, entry.head, source):
                    evidence.append(str(source.relative_to(checkout)))
                else:
                    verified += 1
                if len(evidence) == MAX_EVIDENCE_DETAILS:
                    break
            row['local_evidence'] = {'verified_retained_files': verified, 'generated_entries_excluded': len(excluded),
                                     'inventory_complete': len(evidence) < MAX_EVIDENCE_DETAILS}
            if evidence:
                holds['unretained_local_evidence'] = evidence
        except (OSError, RuntimeError, ValueError) as error:
            holds['checkout_observation_incomplete'] = str(error)

        try:
            if ignored is None:
                raise OSError('Cannot inspect ignored checkout paths')
            for target, locations in observe_dependencies(checkout, checkouts, common, git_dir, config,
                                                          ignored, mounts).items():
                dependencies[target].update(f'{checkout}: {location}' for location in locations)
        except (OSError, RuntimeError, ValueError) as error:
            uncertain_consumers.append(f'{checkout}: {error}')

    active, uncertainty = observe_activity()
    if mounts_unavailable:
        uncertainty.append('mount_observation_unavailable')
    canonical_active = set(active)
    for path in active:
        try:
            canonical_active.add(path.resolve())
        except (OSError, RuntimeError):
            uncertainty.append('active_path_resolution_incomplete')
    changing_registrations = registration_snapshot(worktree.registrations(cwd)) != registration_snapshot(registered)
    changing_integration = commit_at(cwd, INTEGRATION_REF) != integrated
    for row in worktrees:
        path = Path(row['path'])
        holds = row['details']
        active_paths = sorted(str(value) for value in canonical_active if value.is_relative_to(path))
        if active_paths:
            holds['active_session_or_process'] = active_paths[:MAX_EVIDENCE_DETAILS]
        if dependencies[path]:
            holds['downstream_path_consumer'] = sorted(dependencies[path])
        if uncertain_consumers:
            holds['path_consumer_observation_incomplete'] = uncertain_consumers
        for reason in sorted(set(uncertainty)):
            holds[reason] = True
        if changing_registrations:
            holds['worktree_registration_changed'] = True
        if changing_integration:
            holds['integration_ref_changed'] = True
        integration = row['integration']
        if integration and integration['kind'] == 'disposition' and (
                commit_at(cwd, integration['recoverable_ref']) != integration['retained_tip']):
            holds['recovery_ref_changed'] = True
        row['active_use_observed'] = bool(active_paths)
        row['holds'] = list(holds)
        row['state'] = 'held' if holds else 'candidate'

    return {
        'scope': 'Read-only snapshot of local Git references, registered checkout consumers, Herdr state, and '
                 'same-user Linux host processes. Recheck before any later worktree operation.',
        'integration_ref': INTEGRATION_REF,
        'integration_tip': integrated,
        'archive_root': str(archive) if archive else None,
        'archive_error': archive_error,
        'activity_issues': sorted(set(uncertainty)),
        'worktrees': worktrees,
    }
