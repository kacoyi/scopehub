"""Observe Git, path consumers and retained files before a separate native retirement."""

from collections import defaultdict
import hashlib
from itertools import dropwhile, islice, takewhile
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import time
import tomllib

if __package__:
    from . import storage, worktree
    from .operational_paths import OPERATIONAL_PATHS, is_operational_path
else:
    import storage
    import worktree
    from operational_paths import OPERATIONAL_PATHS, is_operational_path


INTEGRATION_REF = 'refs/remotes/origin/main'
FULL_COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
DURABLE_REF = re.compile(r'refs/(?:heads|tags|remotes)/.+')
GIT_OPERATIONS = (
    'index.lock', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_START',
    'MERGE_MODE', 'MERGE_MSG', 'SQUASH_MSG', 'REBASE_HEAD', 'BISECT_LOG',
    'rebase-merge', 'rebase-apply', 'sequencer',
)
PRIVATE_REF_ROOTS = ('refs/bisect', 'refs/worktree', 'refs/rewritten')
WORKTREE_ADMIN_FILES = {'HEAD', 'index', 'commondir', 'gitdir', 'COMMIT_EDITMSG', 'locked'}
FILTER_COMMAND = re.compile(r'filter\.(.+)\.(?:clean|process)\Z')
CARGO_LINK_OUTPUT = re.compile(
    storage.HASHED + r'\.(?:a|lib|dll|dll\.(?:a|exp|lib)|exe|pdb|so|dylib|dwp)'
)
CARGO_INCREMENTAL_LOCK = re.compile(r's-[a-z0-9]+-[a-z0-9]+\.lock')
PNPM_DEPENDENCY_GROUPS = ('dependencies', 'devDependencies', 'optionalDependencies')
PNPM_PEERS = re.compile(r'(?:\((?:@[a-z0-9._-]+/)?[a-z0-9._-]+@\d+\.\d+\.\d+[-+\w.]*\))*')
MAX_EVIDENCE_DETAILS = 8
MAX_CONSUMER_ENTRIES = 20_000
PROJECT_RECORDS = (
    'project/tasks.json', 'project/checks.json', 'project/feedback.json', 'project/development.json',
    'project/delivery.json', 'project/dependencies.json', 'project/dependency-inventory.json',
    'project/branch-protection.json', 'contracts/references.json',
)


def git(cwd, *args, **options):
    return worktree.git(cwd, '--no-optional-locks', '--no-replace-objects', *args, **options)


def commit_at(cwd, ref):
    result = git(cwd, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}', allowed=(0, 128))
    return result.stdout.decode('ascii').strip() if result.returncode == 0 else None


def ancestor(cwd, head, tip):
    return bool(tip and git(cwd, 'merge-base', '--is-ancestor', head, tip, allowed=(0, 1)).returncode == 0)


def is_private_ref(name):
    return any(name == root or name.startswith(root + '/') for root in PRIVATE_REF_ROOTS)


def git_ref_records(cwd, *prefixes):
    result = git(cwd, 'for-each-ref', '--format=%(refname)%09%(objectname)%09%(symref)', *prefixes)
    if result.stderr:
        raise ValueError(os.fsdecode(result.stderr).strip())
    return [tuple(os.fsdecode(line).split('\t')) for line in result.stdout.splitlines()]


def durable_git_refs(cwd):
    # A shared symbolic ref can point back into the worktree that would be removed.
    return {name: oid for name, oid, symbolic in git_ref_records(cwd)
            if not symbolic and not is_private_ref(name)}


def durable_git_objects(cwd, refs):
    tips = ''.join(f'{oid}\n' for oid in sorted(set(refs.values()))).encode('ascii')
    if not tips:
        return set()
    # Report missing objects without asking a partial clone to fetch them.
    result = git(cwd, 'rev-list', '--objects', '--no-object-names', '--missing=print', '--stdin', input=tips)
    objects = set(result.stdout.decode('ascii').splitlines())
    if any(oid.startswith('?') for oid in objects):
        raise ValueError('Shared Git references depend on missing objects')
    return objects


def worktree_git_operations(git_dir):
    return [name for name in GIT_OPERATIONS if worktree.entry_info(git_dir / name) is not None]


def reflog_objects(directory):
    for path in sorted(directory.iterdir()):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and not storage.reparse(info):
            yield from reflog_objects(path)
        else:
            # The oldest old object can be absent from `git reflog show`.
            entries = [line.split(b' ', 2) for line in read_control(path).splitlines()]
            if any(len(fields) != 3 for fields in entries):
                raise ValueError(f'Malformed worktree reflog: {path}')
            yield path, {oid.decode('ascii') for old, new, _ in entries for oid in (old, new)}


def private_git_recovery(checkout, git_dir, common, durable_objects):
    if git_dir == common:
        return {}
    if durable_objects is None:
        raise ValueError('Shared Git object reachability is unavailable')
    refs = {name: {oid} for name, oid, _ in git_ref_records(checkout, *PRIVATE_REF_ROOTS)
            if is_private_ref(name)}
    for path in git_dir.iterdir():
        name = path.name
        if name in WORKTREE_ADMIN_FILES or name in GIT_OPERATIONS:
            continue
        if name in {'logs', 'refs'}:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or storage.reparse(info):
                raise ValueError(f'Unrecognized worktree Git metadata: {path}')
            if name == 'logs':
                refs.update((source.relative_to(git_dir).as_posix(), objects)
                            for source, objects in reflog_objects(path))
        elif name == 'FETCH_HEAD':
            refs[name] = {line.partition(b'\t')[0].decode('ascii') for line in read_control(path).splitlines()}
        elif re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
            refs[name] = {read_control(path).decode('ascii').strip()}
        else:
            # Includes alternate ref backends and worktree-specific configuration.
            raise ValueError(f'Unrecognized worktree Git metadata: {path}')
    failures = {}
    for name, objects in sorted(refs.items()):
        if any(not FULL_COMMIT.fullmatch(oid) for oid in objects):
            raise ValueError(f'Malformed worktree Git object reference: {name}')
        if missing := sorted(oid for oid in objects - durable_objects if oid.strip('0')):
            failures[name] = missing
    return failures


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


def comparable_path(path):
    # Windows readlink can return a DOS or UNC substitution path, even for a dangling link.
    if isinstance(path, PureWindowsPath):
        value = str(path)
        if value[:8].lower() == '\\\\?\\unc\\':
            return type(path)('\\\\' + value[8:])
        if re.match(r'\\\\\?\\[A-Za-z]:\\', value):
            return type(path)(value[4:])
    return path


def path_is_under(value, path):
    path = comparable_path(path)
    lexical = comparable_path(Path(os.path.abspath(value)))
    return (lexical.is_relative_to(path) or
            comparable_path(value.resolve()).is_relative_to(path))


def observe_path_dependency(value, name, candidates, observed):
    lexical = comparable_path(Path(os.path.abspath(value)))
    physical = comparable_path(value.resolve())
    for candidate in candidates:
        target = comparable_path(candidate)
        if lexical.is_relative_to(target) or physical.is_relative_to(target):
            observed[candidate].add(name)


def dependency_patterns(consumer, candidates, directories, *, absolute=False):
    patterns = {}
    for path in candidates:
        if path == consumer:
            continue
        variants = {path.as_posix()} if absolute else set()
        for directory in directories:
            try:
                variants.add(Path(os.path.relpath(path, directory)).as_posix())
            except ValueError:  # Windows paths on different drives.
                pass
        if not variants:
            continue
        parts = '|'.join(re.escape(value) for value in sorted(variants, key=len, reverse=True))
        patterns[path] = re.compile(rf'(?:(?<![\w./-])|(?<=file://)|(?<=file:///))(?:{parts})'
                                    rf'(?=$|[/\s\x00\x22\x27`):,;\]])',
                                    re.IGNORECASE if os.name == 'nt' else 0)
    return patterns


def editable_python_paths(checkout, mounts):
    # Python reads startup path files directly from these virtual environment locations.
    paths = [checkout / '.venv']
    while paths:
        path = paths.pop()
        info = worktree.entry_info(path)
        if info is None:
            continue
        if storage.reparse(info):
            yield path, False
            continue
        if path in mounts or (not mounts and path.is_mount()):
            raise OSError(f'Python consumer path crosses a mount boundary: {path}')
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(f'Expected a Python environment directory: {path}')
        if path.name == 'site-packages':
            for entry in path.iterdir():
                if entry.suffix in {'.pth', '.egg-link'} or (
                        entry.name.startswith('__editable__') and entry.name.endswith('_finder.py')):
                    yield entry, True
        elif path.name == '.venv':
            paths.extend(path / name for name in ('lib', 'lib64', 'Lib'))
            config = path / 'pyvenv.cfg'
            if worktree.entry_info(config) is not None:
                yield config, True
        elif path.name in {'lib', 'lib64', 'Lib'}:
            paths.append(path / 'site-packages')
            paths.extend(entry for entry in path.iterdir()
                         if re.fullmatch(r'python\d+\.\d+[a-z]*', entry.name))
        else:
            paths.append(path / 'site-packages')


def local_consumer_source(consumer, path, mounts):
    source = path.resolve(strict=True)
    if not source.is_relative_to(consumer) or any(
            part in mounts or (not mounts and part.is_mount())
            for part in (source, *source.parents) if part != consumer and part.is_relative_to(consumer)):
        raise OSError(f'Project input is outside its checkout filesystem: {path}')
    return source


def manifest_paths(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'path' and isinstance(item, str):
                yield item
            elif isinstance(item, (dict, list)):
                yield from manifest_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from manifest_paths(item)


def observe_dependencies(consumer, candidates, common, git_dir, config, ignored, mounts):
    common_directories = {consumer, consumer / 'project', consumer / 'scripts', git_dir}
    patterns = dependency_patterns(consumer, candidates, common_directories, absolute=True)
    nested_checkout = any(path.is_relative_to(consumer) for path in patterns)
    relative_patterns = {}
    observed = defaultdict(set)

    def observe_text(value, name, directory=None):
        value = value.replace('\\', '/') if os.name == 'nt' else value
        for target, pattern in patterns.items():
            if pattern.search(value):
                observed[target].add(name)
        if directory is None or directory in common_directories:
            return
        depth = len(directory.relative_to(consumer).parts)
        if not nested_checkout and '../' * (depth + 1) not in value:
            return
        if directory not in relative_patterns:
            relative_patterns[directory] = dependency_patterns(consumer, candidates, (directory,))
        for target, pattern in relative_patterns[directory].items():
            if pattern.search(value):
                observed[target].add(name)

    for name in PROJECT_RECORDS:
        path = consumer / name
        info = worktree.entry_info(path)
        if info is None:
            continue
        observe_symlink(path, patterns, observed)
        source = local_consumer_source(consumer, path, mounts)
        for value in strings(json.loads(read_control(source))):
            observe_text(value, name, source.parent)

    # Effective Git configuration may include external files or checkout-specific overrides.
    observe_text(config, 'Git configuration')

    for alternates in {directory / 'objects/info/alternates' for directory in (common, git_dir)}:
        if alternates.is_file():
            for value in os.fsdecode(read_control(alternates)).splitlines():
                observe_path_dependency(alternates.parent.parent / value, 'Git object alternates',
                                        patterns, observed)

    tracked = os.fsdecode(git(consumer, 'ls-files', '--stage', '-z').stdout).split('\0')
    for entry in tracked:
        if entry.startswith('160000 '):
            raise ValueError('Nested Git submodule consumer scope needs independent observation')
        if entry:
            observe_symlink(consumer / entry.partition('\t')[2], patterns, observed)
    controls = os.fsdecode(git(consumer, 'ls-files', '--cached', '--others', '--exclude-standard', '-z',
                                '--', 'AGENTS.md', *OPERATIONAL_PATHS).stdout)
    for name in set(controls.split('\0')) - {''}:
        if name != 'AGENTS.md' and not is_operational_path(name):
            continue
        path = consumer / name
        if path.name == 'Cargo.toml' and worktree.entry_info(path) is not None:
            source = local_consumer_source(consumer, path, mounts)
            try:
                contents = read_control(source).decode('utf-8')
                manifest = tomllib.loads(contents)
            except ValueError as error:
                raise ValueError(f'Cannot parse Cargo manifest {path}: {error}') from error
            observe_text(contents, name, path.parent)
            for value in manifest_paths(manifest):
                for directory in {path.parent, source.parent}:
                    observe_path_dependency(directory / value, name, patterns, observed)
        elif worktree.regular_file(path):
            observe_text(path.read_text(encoding='utf-8', errors='surrogateescape'), name, path.parent)
    untracked = os.fsdecode(git(consumer, 'ls-files', '--others', '--exclude-standard', '-z').stdout)
    for name in untracked.split('\0'):
        if name:
            observe_symlink(consumer / name, patterns, observed)
    for path, regular in ignored_files(consumer, ignored, mounts, include_generated_links=True):
        if not regular:
            observe_symlink(path, patterns, observed)
    for path, startup_file in editable_python_paths(consumer, mounts):
        if observe_symlink(path, patterns, observed) or not startup_file:
            continue
        if path in mounts:
            raise OSError(f'Python consumer path crosses a mount boundary: {path}')
        value = os.fsdecode(read_control(path))
        name = path.relative_to(consumer).as_posix()
        if path.suffix not in {'.pth', '.egg-link'}:
            observe_text(value, name)
            continue
        for line in value.splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            if line.startswith(('import ', 'import\t')):
                observe_text(line, name)
                continue
            observe_path_dependency(path.parent / line.rstrip(), name, patterns, observed)
    return observed


def observe_symlink(path, candidates, observed):
    info = worktree.entry_info(path)
    if info is not None and storage.reparse(info):
        target = path.parent / os.readlink(path)
        observe_path_dependency(target, str(path), candidates, observed)
        return True
    return False


def git_status(checkout):
    config = os.fsdecode(git(checkout, 'config', '--null', '--list', '--includes').stdout)
    commands = {}
    for line in config.split('\0'):
        key, _, value = line.partition('\n')
        if match := FILTER_COMMAND.fullmatch(key):
            commands[key] = (match[1], value)
    configured = {driver for driver, value in commands.values() if value}
    filters = set()
    if configured:
        tracked = git(checkout, 'ls-files', '--cached', '-z').stdout
        attributes = git(checkout, 'check-attr', '-z', '--stdin', 'filter', input=tracked).stdout
        selected = set(os.fsdecode(attributes).split('\0')[2::3]) & configured
        filters = {key for key, (driver, value) in commands.items() if value and driver in selected}
    # Disable every configured command before reading status, including unused drivers.
    overrides = []
    for driver in sorted({driver for driver, _ in commands.values()}):
        for key, value in (('clean', ''), ('process', ''), ('required', 'false')):
            overrides.extend(('-c', f'filter.{driver}.{key}={value}'))
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


def cargo_build_output(parts):
    # Cargo owns these filenames. A build script can write arbitrary files under out/.
    if len(parts) not in {5, 6} or not re.fullmatch(r'[\w-]+-[0-9a-f]{16}', parts[3]):
        return False
    if len(parts) == 5:
        fingerprint = parts[3].rsplit('-', 1)[1]
        return (parts[4] in {'invoked.timestamp', 'root-output', 'output', 'stderr'} or
                re.fullmatch(rf'(?:build-script-build|build_script_build-{fingerprint})'
                             r'(?:\.d|\.exe|\.pdb)?', parts[4]) is not None)
    return parts[4] == 'out' and re.fullmatch(r'[0-9a-f]{16}-.+\.(?:o|obj)', parts[5]) is not None


def cargo_generated_file(parts, info):
    if not stat.S_ISREG(info.st_mode):
        return False
    name = parts[-1]
    if len(parts) == 2:
        return name in {'CACHEDIR.TAG', '.rustc_info.json'}
    if len(parts) < 3 or parts[1] != 'debug':
        return False
    if len(parts) == 3:
        return name in storage.CARGO_LOCKS
    group = parts[2]
    if group == 'build':
        return cargo_build_output(parts)
    if group == 'deps' and len(parts) == 4:
        return bool(storage.DEP_FILE.fullmatch(name) or CARGO_LINK_OUTPUT.fullmatch(name) or
                    (info.st_mode & 0o111 and storage.TEST_BINARY.fullmatch(name)))
    if group == '.fingerprint' and len(parts) == 5:
        return bool(re.fullmatch(storage.HASHED, parts[3]) and storage.FINGERPRINT_FILE.fullmatch(name))
    if group == 'incremental' and len(parts) in {5, 6} and storage.INCREMENTAL_CRATE.fullmatch(parts[3]):
        if len(parts) == 5:
            return bool(CARGO_INCREMENTAL_LOCK.fullmatch(name) and info.st_size == 0)
        return bool(storage.INCREMENTAL_SESSION.fullmatch(parts[4]) and
                    (storage.INCREMENTAL_FILE.fullmatch(name) or name == 'dep-graph.part.bin'))
    return False


def pnpm_lock_spellings(value):
    return (value, "'" + value.replace("'", "''") + "'", json.dumps(value, ensure_ascii=False))


def pnpm_lock_block(lines, key, indent):
    # Read only pnpm's canonical block mappings in the v9 lockfile.
    prefix = ' ' * indent
    headers = {f'{prefix}{spelling}:' for spelling in pnpm_lock_spellings(key)}
    starts = [index for index, line in enumerate(lines) if line in headers]
    if len(starts) != 1:
        return ()
    return tuple(takewhile(lambda line: not line or line.startswith(prefix + ' '),
                           islice(lines, starts[0] + 1, None)))


def pnpm_lock_field(lines, field):
    prefix = f'        {field}: '
    values = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
    if len(values) != 1:
        return None
    value = values[0]
    if value.startswith('"'):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, str) else None
    if re.fullmatch(r"'(?:''|[^'])*'", value):
        return value[1:-1].replace("''", "'")
    return value


def pnpm_store_entry(name, version, resolution):
    if not resolution or not resolution.startswith(version):
        return None
    peers = resolution.removeprefix(version)
    if not PNPM_PEERS.fullmatch(peers):
        return None
    peers = peers.replace('(', '_').replace(')', '').replace('/', '+')
    return f"{name.replace('/', '+')}@{version}{peers}"


def pnpm_lock_has_entry(lines, key):
    headers = {f'  {spelling}:{suffix}' for spelling in pnpm_lock_spellings(key) for suffix in ('', ' {}')}
    return sum(line in headers for line in lines) == 1


def pnpm_declares_projection(lock, manifest, metadata, importer, name, version, store_entry):
    if not lock.startswith("lockfileVersion: '9.0'\n") or not isinstance(manifest, dict):
        return False
    lines = lock.splitlines()
    package_key = f'{name}@{version}'
    if not pnpm_lock_has_entry(pnpm_lock_block(lines, 'packages', 0), package_key):
        return False
    importers = pnpm_lock_block(lines, 'importers', 0)
    imported = pnpm_lock_block(importers, importer, 2)
    for group in PNPM_DEPENDENCY_GROUPS:
        dependencies = manifest.get(group, {})
        specifier = dependencies.get(name) if isinstance(dependencies, dict) else None
        if not isinstance(specifier, str):
            continue
        entries = pnpm_lock_block(pnpm_lock_block(imported, group, 4), name, 6)
        resolution = pnpm_lock_field(entries, 'version')
        if (pnpm_lock_field(entries, 'specifier') == specifier and
                pnpm_store_entry(name, version, resolution) == store_entry):
            return True
    hoisted = metadata.get('hoistedDependencies')
    if importer != '.' or not isinstance(hoisted, dict):
        return False
    snapshots = pnpm_lock_block(lines, 'snapshots', 0)
    for key, aliases in hoisted.items():
        if (key.startswith(name + '@') and isinstance(aliases, dict) and aliases.get(name) == 'public' and
                pnpm_store_entry(name, version, key.removeprefix(name + '@')) == store_entry and
                pnpm_lock_has_entry(snapshots, key)):
            return True
    return False


def scoped_pnpm_projection(path, checkout, mounts):
    scope = path.parent.name
    modules = path.parent.parent
    if (modules.name != 'node_modules' or not re.fullmatch(r'@[a-z0-9][a-z0-9._-]*', scope) or
            not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', path.name)):
        return False
    root_modules = checkout / 'node_modules'
    store = root_modules / '.pnpm'
    link = Path(os.readlink(path))
    # Relative pnpm projections ascend first and then descend into the virtual store.
    if '..' in dropwhile(lambda part: part == '..', link.parts):
        return False
    target = Path(os.path.abspath(comparable_path(path.parent / link)))
    if not target.is_relative_to(store):
        return False
    parts = target.relative_to(store).parts
    if len(parts) != 4 or parts[1:] != ('node_modules', scope, path.name):
        return False
    try:
        if not target.is_dir() or path.resolve(strict=True) != target:
            return False
        if any(parent in mounts or (not mounts and parent.is_mount())
               for parent in (target, *target.parents) if parent.is_relative_to(root_modules)):
            return False
        metadata = json.loads(read_control(root_modules / '.modules.yaml'))
        if (not isinstance(metadata, dict) or metadata.get('nodeLinker') != 'isolated' or
                metadata.get('virtualStoreDir') != '.pnpm' or metadata.get('layoutVersion') != 5 or
                not str(metadata.get('packageManager', '')).startswith('pnpm@')):
            return False
        package = json.loads(read_control(target / 'package.json'))
        name = f'{scope}/{path.name}'
        if not isinstance(package, dict) or package.get('name') != name:
            return False
        version = package.get('version')
        if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+[-+\w.]*', version):
            return False
        lock = read_control(checkout / 'pnpm-lock.yaml').decode('utf-8').replace('\r\n', '\n')
        manifest = json.loads(read_control(modules.parent / 'package.json'))
        importer = modules.parent.relative_to(checkout).as_posix()
        return pnpm_declares_projection(lock, manifest, metadata, importer, name, version, parts[0])
    except (OSError, ValueError):
        return False


def rebuildable(path, checkout, info, mounts, cargo_cache):
    # Ignore only known generated layouts, even when broader Git ignore rules apply.
    relative = path.relative_to(checkout)
    if (path.parent.name == '__pycache__' and stat.S_ISREG(info.st_mode) and not storage.reparse(info) and
            (match := re.fullmatch(r'(.+)\.cpython-\d+(?:\.opt-\d+)?\.pyc', path.name))):
        return (path.parent.parent / f'{match[1]}.py').is_file()
    if storage.reparse(info) and path.parent.name != 'node_modules':
        return ((relative.as_posix() == '.venv/lib64' and os.readlink(path) == 'lib' and
                 cache_tag(checkout / '.venv')) or scoped_pnpm_projection(path, checkout, mounts))
    if relative.parts[0] == '.venv' and len(relative.parts) == 2:
        return ((checkout / 'uv.lock').is_file() and cache_tag(checkout / '.venv') and
                path.name in {'.gitignore', '.lock', 'CACHEDIR.TAG', 'pyvenv.cfg', 'bin', 'lib', 'lib64',
                              'Scripts', 'Lib', 'Include'})
    if relative.parts[0] == '.uv-cache' and len(relative.parts) == 2:
        return (cache_tag(checkout / '.uv-cache') and
                (path.name in {'.gitignore', '.lock', 'CACHEDIR.TAG'} or
                 bool(re.fullmatch(r'(?:archive|builder|interpreter|sdists|simple|wheels)-v\d+', path.name))))
    if relative.parts[0] == 'target' and cargo_cache:
        return cargo_generated_file(relative.parts, info)
    if path.parent.name == 'node_modules':
        root_modules = checkout / 'node_modules'
        if ((checkout / 'pnpm-lock.yaml').is_file() and (root_modules / '.modules.yaml').is_file() and
                (path.parent.parent / 'package.json').is_file()):
            return path.name in {'.bin', '.modules.yaml', '.pnpm', '.pnpm-task-run-state-v1',
                                 '.pnpm-workspace-state-v1.json'} or (
                not path.name.startswith('@') and path.is_symlink() and path_is_under(path, root_modules / '.pnpm')
            )
    return False


def ignored_files(checkout, paths, mounts, *, include_generated_links=False, exclusions=None):
    target = checkout / 'target'
    target_info = worktree.entry_info(target)
    cargo_cache = (target_info is not None and stat.S_ISDIR(target_info.st_mode) and
                   not storage.reparse(target_info) and worktree.regular_file(checkout / 'Cargo.lock') and
                   cache_tag(target))
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
        elif rebuildable(path, checkout, info, mounts, cargo_cache):
            if exclusions is not None:
                exclusions.append(path)
            if include_generated_links and path.is_symlink():
                yield path, False
            elif include_generated_links and path == checkout / '.venv/bin':
                queue_directory(path, pending, inspected)
            continue
        elif stat.S_ISDIR(info.st_mode) and not storage.reparse(info):
            queue_directory(path, pending, inspected)
        else:
            yield path, stat.S_ISREG(info.st_mode) and not storage.reparse(info)


def queue_directory(path, pending, inspected):
    remaining = MAX_CONSUMER_ENTRIES - inspected - len(pending)
    if remaining < 0:
        raise OSError('Ignored path inventory exceeded the observation budget')
    with os.scandir(path) as entries:
        paths = [Path(entry.path) for entry in islice(entries, remaining + 1)]
    if len(paths) > remaining:
        raise OSError('Ignored path inventory exceeded the observation budget')
    pending.extend(sorted(paths, reverse=True))


def hash_file(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or storage.reparse(initial):
        raise OSError(f'Expected a regular retained file: {path}')
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or not before.st_ino:
            raise OSError(f'Expected a regular retained file: {path}')
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or storage.reparse(current):
            raise OSError(f'File changed during observation: {path}')
        if os.name == 'nt':
            # Use the same handle-based file ID on both sides of the Windows comparison.
            with os.fdopen(os.open(path, flags), 'rb') as reopened:
                after = os.fstat(reopened.fileno())
        else:
            after = current
    if (not os.path.samestat(before, after) or
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


def verify_consumer_checkout(common, entry, *, primary):
    if not primary:
        worktree.verify_checkout(common, entry)
        return
    path = entry.path
    if (entry.flags & {'bare', 'prunable'} or path.resolve() != path or
            worktree.git_path(path, '--show-toplevel') != path or worktree.git_path(path, '--git-dir') != common or
            worktree.git_path(path, '--git-common-dir') != common):
        raise worktree.WorktreeError(f'Cannot verify the primary checkout at {path}.')


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
    shared_refs = durable_objects = None
    try:
        shared_refs = durable_git_refs(cwd)
        durable_objects = durable_git_objects(cwd, shared_refs)
    except (OSError, RuntimeError, ValueError):
        pass  # Linked rows will hold because recovery cannot be established.
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
            verify_consumer_checkout(common, entry, primary=position == 0)
        except (OSError, RuntimeError) as error:
            holds['registration_unavailable'] = str(error)
            # Git's prunable entry for a missing path has no checkout files to consume.
            try:
                missing = worktree.entry_info(checkout) is None
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
            operations = worktree_git_operations(git_dir)
            if operations:
                holds['git_operation_in_progress'] = operations
            row['clean'] = not (dirty or hidden or filters or operations)
            private_state = private_git_recovery(checkout, git_dir, common, durable_objects)
            if private_state:
                holds['unrecoverable_worktree_git_state'] = private_state
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
    try:
        changing_recovery = shared_refs is not None and durable_git_refs(cwd) != shared_refs
    except (OSError, RuntimeError, ValueError):
        changing_recovery = True
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
        if changing_recovery:
            holds['recovery_ref_changed'] = True
        integration = row['integration']
        if integration and integration['kind'] == 'disposition' and (
                commit_at(cwd, integration['recoverable_ref']) != integration['retained_tip']):
            holds['recovery_ref_changed'] = True
        row['active_use_observed'] = bool(active_paths)
        row['review_intake'] = set(holds) == {'process_observation_incomplete'}
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
