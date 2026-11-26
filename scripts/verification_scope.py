"""Select a verification profile from a verified Git comparison."""

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def windows_x64(environment):
    machine = environment.get('machine')
    return (environment.get('system') == 'Windows' and isinstance(machine, str)
            and machine.lower() in {'amd64', 'x86_64'})


def windows_lanes_run_id(ci):
    return f"windows-change-{ci['run_id']}-{ci['run_attempt']}"


def windows_lane_report_matches(ci, run_id, environment):
    return run_id == windows_lanes_run_id(ci) and windows_x64(environment)


WINDOWS_BROWSER_CHECKS = {'market-workflow', 'chart-history', 'ubuntu-preview',
                          'local-import-ui', 'market-live-stream', 'market-quality-states',
                          'market-worker-interruption', 'market-recovery-ui', 'workspace-timeframe',
                          'desktop-workspace-transport', 'desktop-localization'}
WINDOWS_LANE_CHECKS = {
    'browser': WINDOWS_BROWSER_CHECKS,
    'python': {'python-contracts-workers'},
    'recovery': {'market-recovery'},
}
WINDOWS_LANES = ('core', *WINDOWS_LANE_CHECKS)
LEGACY_WINDOWS_LANES = WINDOWS_LANES[:-1]


def windows_lane_checks(selected, lane, lanes=WINDOWS_LANES, *, assignment=WINDOWS_LANE_CHECKS):
    if lane not in lanes:
        return set()
    if lane == 'core':
        return selected.difference(*(assignment[name] for name in lanes if name != 'core'))
    return selected & assignment[lane]


SCOPED_PROFILES = frozenset({'records', 'tooling', 'localization'})


def publishing_check_catalog(catalog):
    """Validate the check inventory and references used by hosted selection."""
    rows = catalog['checks']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Invalid check catalog')
    checks = {}
    for check in rows:
        key, status, command = check['id'], check['status'], check['command']
        if (not isinstance(key, str) or not CHECK_ID.fullmatch(key)
                or status not in {'implemented', 'planned', 'retired'}
                or check.get('cadence', 'change') not in {'change', 'task', 'release'}):
            raise ValueError('Invalid check catalog entry')
        if key in checks:
            raise ValueError('Duplicate check ID')
        if (status == 'implemented') != (command is not None):
            raise ValueError(f'Check command/status mismatch: {key}')
        if command is not None and (not isinstance(command, list) or not command
                                    or any(not isinstance(arg, str) or not arg for arg in command)):
            raise ValueError(f'Invalid check command: {key}')
        checks[key] = check

    def catalog_refs(values, label):
        if (not isinstance(values, list) or not values or any(not isinstance(key, str) for key in values)
                or len(set(values)) != len(values)):
            raise ValueError(f'{label}: invalid check list')
        missing = set(values) - checks.keys()
        if missing:
            raise ValueError(f'{label}: unknown references {sorted(missing)}')
        return set(values)

    required = catalog_refs(catalog['required_change_checks'], 'Required change checks')
    implemented = {key for key, check in checks.items()
                   if check['status'] == 'implemented' and check.get('cadence', 'change') == 'change'}
    if required != implemented:
        raise ValueError('Required change checks must equal the implemented change checks')
    profiles = catalog.get('profiles', {})
    if not isinstance(profiles, dict) or ('profiles' in catalog and not profiles):
        raise ValueError('Invalid check profiles')
    for name, values in profiles.items():
        catalog_refs(values, f'Profile {name}')
    return checks


def hosted_check_selection(catalog, profile):
    if profile:
        return set(catalog.get('profiles', {}).get(profile, ()))
    return {check['id'] for check in catalog['checks']
            if check.get('cadence', 'change') == 'change' and check['status'] != 'retired'}


def windows_published_scope(selected, profile, lanes, catalog, *, assignment=WINDOWS_LANE_CHECKS):
    if profile in SCOPED_PROFILES:
        expected = hosted_check_selection(catalog, profile)
    else:
        expected = hosted_check_selection(catalog, None)
        if profile != 'full':
            lane = profile.removeprefix('ci-windows-')
            expected = (windows_lane_checks(expected, lane, lanes, assignment=assignment)
                        if profile.startswith('ci-windows-') else set())
    return bool(expected) and len(selected) == len(expected) and set(selected) == expected


def windows_result_issue(rows, selected, catalog):
    """Check the ordered results common to a native report and its timing projection."""
    checks = catalog['checks']
    if [row['id'] for row in rows] != [check['id'] for check in checks]:
        return 'a different check result catalog or order'
    for row, check in zip(rows, checks, strict=True):
        key = check['id']
        state, reason, code = row['status'], row.get('reason_code'), row.get('exit_code')
        measured = row.get('duration_seconds')
        omitted = all(row.get(field) is None for field in ('exit_code', 'duration_seconds', 'log', 'log_sha256'))
        if key not in selected:
            valid = state == 'blocked' and reason == 'not_selected' and omitted
        elif check['status'] != 'implemented':
            planned_reason = 'not_implemented' if check['status'] == 'planned' else 'retired'
            valid = state == 'blocked' and reason in {planned_reason, 'earlier_failure'} and omitted
        elif state == 'passed':
            valid = (reason is None and type(code) is int and code == 0
                     and type(measured) in (int, float) and 0 <= measured <= timedelta.max.total_seconds())
        elif state == 'blocked':
            valid = ((reason in {'earlier_failure', 'executable_unavailable'} and omitted)
                     or (reason == 'observation_unavailable' and code == 77))
        else:
            valid = (state == 'failed' and (type(code) is int and code != 0 or reason in {
                'timeout', 'interrupted', 'cleanup_failed', 'missing_acceptance_evidence',
            }))
        if not valid:
            return f'an unexpected result for {key}'
    return None


def windows_raw_timing_issue(report, *, catalog=None, source=None):
    """Validate native publisher inputs that the compact timing format omits."""
    initial = report.get('source')
    final = report.get('source_end')
    ci = report['ci']
    if (not isinstance(initial, dict) or not isinstance(final, dict)
            or any(set(snapshot) != {'snapshot_kind', 'sha256', 'file_count', 'git_head',
                                     'working_tree_dirty', 'exclusions'}
                   or snapshot['snapshot_kind'] != 'execution-inputs-v1'
                   or not isinstance(snapshot['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', snapshot['sha256'])
                   or type(snapshot['file_count']) is not int or snapshot['file_count'] < 1
                   or snapshot['git_head'] != ci.get('checkout_sha') or snapshot['working_tree_dirty'] is not False
                   or not isinstance(snapshot['exclusions'], list)
                   or not all(isinstance(path, str) and path for path in snapshot['exclusions'])
                   or len(set(snapshot['exclusions'])) != len(snapshot['exclusions'])
                   for snapshot in (initial, final))
            or initial != final or (source is not None and initial != source)
            or report.get('source_unchanged') is not True or report.get('project_unchanged') is not True):
        return 'an invalid native harness source fence'

    selection = report['selection']
    profile = selection['profile']
    selected = selection.get('checks')
    requirement = report.get('native_requirement')
    python = report['environment'].get('python_executable')
    if (set(selection) != {'profile', 'task_id', 'checks'} or selection['task_id'] is not None
            or not isinstance(selected, list) or not all(isinstance(key, str) and key for key in selected)
            or selected != sorted(set(selected))):
        return 'an invalid native harness report selection'
    if (not isinstance(requirement, dict) or set(requirement) != {'requested', 'satisfied'}
            or requirement['requested'] is not (profile == 'full') or requirement['satisfied'] is not True
            or not isinstance(python, str) or not python
            or report.get('report_version') != '2.0'
            or report.get('status') not in {'passed', 'failed', 'blocked'}
            or not isinstance(report.get('project_definition_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', report['project_definition_sha256'])
            or not isinstance(report.get('task_definitions'), dict)
            or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in report['task_definitions'].values())
            or ci.get('provider') != 'github-actions'
            or ci.get('run_url') != f"https://github.com/{ci.get('repository')}/actions/runs/{ci.get('run_id')}"):
        return 'invalid native harness metadata'

    commands = {row['id']: row['command'] for row in catalog['checks']} if catalog is not None else None
    for row in report['checks']:
        key = row['id']
        command = row.get('command')
        if (not isinstance(command, list) or not all(isinstance(arg, str) and arg for arg in command)
                or (commands is not None and (key not in commands
                    or command != [python if arg == '{python}' else arg for arg in commands[key] or ()]))):
            return f'an invalid native harness command for {key}'
        if 'log' in row or row['status'] == 'passed':
            if (row.get('log') != f".reports/runs/{report['run_id']}/{key}.log"
                    or not isinstance(row.get('log_sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', row['log_sha256'])):
                return f'an invalid native harness log reference for {key}'
    return None


SCOPE_REASONS = {
    'records': 'Only reviewed record paths or approved narrative fields changed.',
    'tooling': 'Only isolated Herdr tooling and eligible records changed.',
    'localization': 'Only wording in known zh-Hans catalogs and eligible records changed.',
}
DOCUMENT_FILES = {b'README.md'}
RECORD_FILES = {b'project/tasks.json', b'project/feedback.json', b'contracts/references.json'}
EVIDENCE_FILE = re.compile(rb'project/evidence/M[0-9]+-[0-9]{3}-[0-9a-f]{16}\.json')
TASK_REVIEW_FILE = re.compile(rb'contracts/reviews/task-decisions/M[0-9]+-[0-9]{3}\.json')
NARRATIVE_FIELDS = {
    b'engineering-contract-v1.json': (
        ('product', 'purpose'),
        ('product', 'design_principles'),
    ),
    b'contracts/contract.json': (('clarifications',),),
}
HERDR_SETTINGS = b'project/development.json'
HERDR_ROUTING_FIELDS = tuple(('herdr', field) for field in ('profiles', 'reasoning_efforts', 'model_selection'))
HERDR_TEST = b'tests/project/test_herdr.py'
HERDR_FAMILY = re.compile(r'[a-z][a-z0-9-]*')
HERDR_HIGHER_EFFORTS = ('high', 'xhigh', 'max', 'ultra')
LOCALE_FILES = {
    b'apps/desktop/ui/src/i18n/locales/zh-Hans/' + name + b'.json'
    for name in (b'core', b'data', b'indicators', b'market', b'research', b'startup')
}
PLURAL_FORMS = {'zero', 'one', 'two', 'few', 'many', 'other'}
PLACEHOLDER = re.compile(r'\{[A-Za-z0-9_]+\}')
OBJECT_ID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
CHECK_ID = re.compile(r'[a-z][a-z0-9-]{0,63}')
DIFF_FLAGS = (
    '--raw', '-z', '--no-abbrev', '--no-renames', '--no-relative',
    '--no-color', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none', '--ita-invisible-in-index',
)


class ScopeError(Exception):
    """A comparison that cannot justify reduced verification."""

    def __init__(self, message, reason_code='comparison_unavailable'):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class Change:
    path: bytes
    status: bytes
    old_mode: bytes
    new_mode: bytes
    old_oid: bytes
    new_oid: bytes
    worktree: bool = False

    def normal_file(self):
        return (
            self.new_mode == b'100644'
            and (self.status, self.old_mode) in {(b'A', b'000000'), (b'M', b'100644')}
        )


@dataclass(frozen=True)
class JsonNumber:
    # Keep the original token so booleans, integers, and rounded floats never compare equal.
    token: str


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def invalid_number(_token):
    raise ValueError('Invalid JSON number')


def validate_unicode(value):
    if isinstance(value, str):
        value.encode('utf-8')
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_unicode(key)
            validate_unicode(item)
    elif isinstance(value, list):
        for item in value:
            validate_unicode(item)


def json_object(data):
    value = json.loads(
        data.decode('utf-8'), object_pairs_hook=unique_object,
        parse_int=JsonNumber, parse_float=JsonNumber, parse_constant=invalid_number,
    )
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    validate_unicode(value)
    return value


def checkout_git_environment():
    # Git aliases and hooks export repository-local state. Keep transport, global configuration and CI identity.
    # `git rev-parse --local-env-vars` identifies the standard repository and inline configuration selectors.
    local = {'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_CONFIG', 'GIT_CONFIG_COUNT',
             'GIT_CONFIG_PARAMETERS', 'GIT_DIR', 'GIT_GRAFT_FILE', 'GIT_IMPLICIT_WORK_TREE', 'GIT_INDEX_FILE',
             'GIT_INTERNAL_SUPER_PREFIX', 'GIT_NAMESPACE', 'GIT_OBJECT_DIRECTORY', 'GIT_PREFIX',
             'GIT_QUARANTINE_PATH', 'GIT_REPLACE_REF_BASE', 'GIT_SHALLOW_FILE', 'GIT_WORK_TREE'}
    return {key: value for key, value in os.environ.items()
            if key not in local and not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}


def git(root, *args, input=None):
    options = ['-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false']
    if os.name != 'nt':
        options.extend(('-c', 'core.fileMode=true'))
    result = subprocess.run(
        ['git', '--no-replace-objects', '--no-optional-locks', *options, *args],
        cwd=root, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        env={**checkout_git_environment(), 'GIT_GRAFT_FILE': os.devnull, 'GIT_NO_LAZY_FETCH': '1',
             'GIT_ALLOW_PROTOCOL': ''},
    )
    if result.returncode:
        raise ScopeError('Git could not verify the comparison.')
    return result.stdout


# These are the released selector forms. Recognize the small partition expression
# before reading its literals. A new execution strategy must define how to verify
# its assignment; merely finding a constant or a claimed lane is insufficient.
_LOCAL_WINDOWS_SELECTOR = """def windows_lane_checks(selected, lane):
    if lane == 'core':
        return selected.difference(*WINDOWS_LANE_CHECKS.values())
    return selected & WINDOWS_LANE_CHECKS[lane]"""
_INITIAL_WINDOWS_SELECTOR = """def windows_lane_checks(selected, lane):
    python = {'python-contracts-workers'}
    if lane == 'python':
        return selected & python
    if lane == 'browser':
        return selected & WINDOWS_BROWSER_CHECKS
    return selected - python - WINDOWS_BROWSER_CHECKS"""
_LEGACY_WINDOWS_SELECTOR = """def windows_lane_checks(selected, lane):
    if lane == 'python':
        return selected & WINDOWS_PYTHON_CHECKS
    if lane == 'browser':
        return selected & WINDOWS_BROWSER_CHECKS
    return selected - WINDOWS_PYTHON_CHECKS - WINDOWS_BROWSER_CHECKS"""
_SHARED_WINDOWS_SELECTOR = """def windows_lane_checks(selected, lane, lanes=WINDOWS_LANES):
    if lane not in lanes:
        return set()
    if lane == 'core':
        return selected.difference(*(WINDOWS_LANE_CHECKS[name] for name in lanes if name != 'core'))
    return selected & WINDOWS_LANE_CHECKS[lane]"""
_CURRENT_WINDOWS_SELECTOR = _SHARED_WINDOWS_SELECTOR.replace(
    'lanes=WINDOWS_LANES', 'lanes=WINDOWS_LANES, *, assignment=WINDOWS_LANE_CHECKS',
).replace('WINDOWS_LANE_CHECKS[name]', 'assignment[name]').replace('WINDOWS_LANE_CHECKS[lane]', 'assignment[lane]')

def _publishing_module(root, revision, file):
    path = f'{revision}:scripts/{file}'
    if int(git(root, 'cat-file', '-s', path)) > 256 * 1024:
        raise ValueError('Oversized Windows publishing source')
    return ast.parse(git(root, 'cat-file', 'blob', path))


def _publishing_selector(module, name):
    functions = [node for node in module.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(functions) > 1:
        raise ValueError('Ambiguous Windows publishing selector')
    return functions[0] if functions else None


def _publishing_assignment_node(module, name):
    matches = [
        stmt for stmt in module.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in stmt.targets)
    ]
    assignment, = matches
    if len(assignment.targets) != 1:
        raise ValueError('Ambiguous Windows publishing assignment')
    return assignment


def _publishing_ids(node):
    if not isinstance(node, ast.Set):
        raise ValueError('Expected literal Windows publishing check IDs')
    ids = [ast.literal_eval(item) for item in node.elts]
    if (not all(isinstance(key, str) and CHECK_ID.fullmatch(key) for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Invalid Windows publishing check IDs')
    return frozenset(ids)


def read_windows_assignment(root, revision):
    """Read the lane partition from an independently identified publisher program.

    An empty mapping identifies a monolithic publisher. None means the known
    source could not supply its checked literal assignment.
    """
    try:
        if not isinstance(revision, str) or not OBJECT_ID.fullmatch(revision):
            return None
        harness = _publishing_module(root, revision, 'check.py')
        module = harness
        selector = _publishing_selector(harness, 'windows_lane_checks')
        imports = {(node.module, name.name) for node in ast.walk(harness) if isinstance(node, ast.ImportFrom)
                   for name in node.names if (name.asname or name.name) == 'windows_lane_checks'}
        if selector is None and not imports:
            return {}
        if selector is None and imports == {('verification_scope', 'windows_lane_checks')}:
            module = _publishing_module(root, revision, 'verification_scope.py')
            selector = _publishing_selector(module, 'windows_lane_checks')
        elif imports:
            return None
        if selector is None:
            return None
        def literal(name):
            return _publishing_assignment_node(module, name).value

        shape = ast.unparse(selector)
        if module is harness and shape in {_INITIAL_WINDOWS_SELECTOR, _LEGACY_WINDOWS_SELECTOR}:
            python = (selector.body[0].value if shape == _INITIAL_WINDOWS_SELECTOR
                      else literal('WINDOWS_PYTHON_CHECKS'))
            assignment = {
                'browser': _publishing_ids(literal('WINDOWS_BROWSER_CHECKS')),
                'python': _publishing_ids(python),
            }
        elif shape in ({_LOCAL_WINDOWS_SELECTOR} if module is harness
                       else {_SHARED_WINDOWS_SELECTOR, _CURRENT_WINDOWS_SELECTOR}):
            declared = literal('WINDOWS_LANE_CHECKS')
            if not isinstance(declared, ast.Dict):
                return None
            names = [ast.literal_eval(key) for key in declared.keys]
            if sorted(names) != ['browser', 'python', 'recovery']:
                return None
            assignment = {
                name: _publishing_ids(literal(node.id)
                                      if isinstance(node, ast.Name) and node.id == 'WINDOWS_BROWSER_CHECKS' else node)
                for name, node in zip(names, declared.values, strict=True)
            }
        else:
            return None
        lanes = ast.unparse(literal('WINDOWS_LANES'))
        if lanes != ("('core', 'browser', 'python')" if len(assignment) == 2
                     else "('core', *WINDOWS_LANE_CHECKS)"):
            return None
        if sum(map(len, assignment.values())) != len(set().union(*assignment.values())):
            return None
        return assignment
    except (ScopeError, OSError, subprocess.SubprocessError, SyntaxError, ValueError, KeyError, TypeError, RecursionError):
        return None


def oid(value):
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value) or not value.strip('0'):
        raise ScopeError('A required commit SHA is absent or invalid.')
    return value


def resolve_commit(root, ref):
    return oid(git(root, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').strip().decode('ascii'))


def exact_commit(root, value):
    value = oid(value)
    if resolve_commit(root, value) != value:
        raise ScopeError('A required Git object is not the named commit.')
    return value


def require_complete_history(root):
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ScopeError('Shallow or unverifiable Git history.')


def require_ancestor(root, base, head):
    try:
        git(root, 'merge-base', '--is-ancestor', base, head)
    except ScopeError:
        raise ScopeError('The base is not a verified ancestor of the checkout.') from None


def nul_fields(output):
    if not output:
        return []
    if not output.endswith(b'\0'):
        raise ScopeError('Incomplete Git path records.')
    return output[:-1].split(b'\0')


def changes(root, *revisions, worktree=False):
    fields = nul_fields(git(root, 'diff', *DIFF_FLAGS, *revisions, '--'))
    if len(fields) % 2:
        raise ScopeError('Unexpected Git diff records.')
    result = []
    for metadata, path in zip(fields[::2], fields[1::2]):
        if not metadata.startswith(b':') or not path:
            raise ScopeError('Unexpected Git diff record.')
        old_mode, new_mode, old_oid, new_oid, status = metadata[1:].split()
        result.append(Change(path, status, old_mode, new_mode, old_oid, new_oid, worktree))
    return result


def inspect_index(root, *, include_untracked):
    options = ('--cached', '--others', '--exclude-standard') if include_untracked else ()
    entries = nul_fields(git(root, 'ls-files', '-v', '-z', *options))
    # Unmerged, skip-worktree, and assume-unchanged entries prevent a complete preview.
    untracked = []
    for entry in entries:
        if include_untracked and entry.startswith(b'? '):
            untracked.append(entry[2:])
        elif not entry.startswith(b'H '):
            raise ScopeError('The index contains conflicts or entries hidden from ordinary worktree checks.')
    return untracked


def working_file(root, path):
    # Only exact ASCII names or anchored ASCII record names reach this function.
    parts = path.decode('ascii').split('/')
    parent = root
    for part in parts[:-1]:
        parent /= part
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or is_reparse_point(info):
            raise ScopeError('A working file has an unsafe parent directory.')
    file = parent / parts[-1]
    info = file.lstat()
    if (not stat.S_ISREG(info.st_mode) or is_reparse_point(info)
            or (os.name != 'nt' and info.st_mode & stat.S_IXUSR)):
        raise ScopeError('A working file has an unsafe mode.')
    return file


def is_reparse_point(info):
    return bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def read_versions(root, change):
    def blob(object_id):
        return git(root, 'cat-file', 'blob', object_id.decode('ascii'))

    old = blob(change.old_oid) if change.status == b'M' else None
    new = working_file(root, change.path).read_bytes() if change.worktree else blob(change.new_oid)
    return old, new


def same_json(left, right):
    # Consumers may iterate objects, so preserve key order outside reviewed fields.
    if isinstance(left, dict) and isinstance(right, dict):
        return list(left) == list(right) and all(same_json(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    return left == right


def only_fields_changed(old, new, fields):
    """Remove the explicitly reviewed values and compare all remaining JSON."""
    edits = []
    for field in fields:
        left, right = old, new
        for key in field[:-1]:
            left, right = left.get(key), right.get(key)
            if not isinstance(left, dict) or not isinstance(right, dict):
                return None
        key = field[-1]
        if key not in left or key not in right:
            return None
        edits.append((left.pop(key), right.pop(key)))
    return edits if same_json(old, new) and any(not same_json(left, right) for left, right in edits) else None


def narrative_edit(old, new, fields):
    edits = only_fields_changed(old, new, fields)
    if edits is None:
        return False
    for field, (left, right) in zip(fields, edits, strict=True):
        if field == ('product', 'purpose'):
            if not isinstance(left, str) or not isinstance(right, str):
                return False
        elif (not isinstance(left, list) or not isinstance(right, list)
                or len(left) != len(right)
                or not all(isinstance(text, str) for text in left + right)):
            return False
    return True


def herdr_routing(profiles, efforts, selection):
    if not isinstance(profiles, dict) or profiles.keys() != {'implement', 'supervise'}:
        return False
    for profile in profiles.values():
        if not isinstance(profile, dict) or profile.keys() != {'family'}:
            return False
        family = profile['family']
        if not isinstance(family, str) or not HERDR_FAMILY.fullmatch(family):
            return False
    return (
        isinstance(efforts, list) and efforts and efforts[0] == 'medium'
        and all(isinstance(effort, str) and effort in HERDR_HIGHER_EFFORTS for effort in efforts[1:])
        and len(efforts) == len(set(efforts))
        and isinstance(selection, list) and selection
        and all(isinstance(rule, str) and rule.strip() for rule in selection)
        and len(selection) == len(set(selection))
    )


def same_interpolation(old, new):
    return (
        isinstance(old, str) and isinstance(new, str)
        and Counter(PLACEHOLDER.findall(old)) == Counter(PLACEHOLDER.findall(new))
    )


def wording_edit(old, new):
    if old.keys() != new.keys() or old == new:
        return False
    for key, left in old.items():
        right = new[key]
        if isinstance(left, dict) and isinstance(right, dict):
            if (left.keys() != right.keys() or not left.keys() <= PLURAL_FORMS or 'other' not in left
                    or not all(same_interpolation(left[form], right[form]) for form in left)):
                return False
        elif not same_interpolation(left, right):
            return False
    return True


def text_file(data):
    data.decode('utf-8')
    return b'\0' not in data


def classify_file(root, change):
    path = change.path
    if not change.normal_file():
        return 'full'

    if path in DOCUMENT_FILES or path == HERDR_TEST:
        old, new = read_versions(root, change)
        if text_file(new) and (old is None or text_file(old)):
            return 'records' if path in DOCUMENT_FILES else 'tooling'
        return 'full'

    record = path in RECORD_FILES or EVIDENCE_FILE.fullmatch(path) or TASK_REVIEW_FILE.fullmatch(path)
    if not (record or path in NARRATIVE_FIELDS or path == HERDR_SETTINGS or path in LOCALE_FILES):
        return 'full'

    old_bytes, new_bytes = read_versions(root, change)
    new = json_object(new_bytes)
    old = json_object(old_bytes) if old_bytes is not None else None
    if record:
        return 'records'
    if old is None:
        return 'full'
    if path in NARRATIVE_FIELDS and narrative_edit(old, new, NARRATIVE_FIELDS[path]):
        return 'records'
    if path == HERDR_SETTINGS:
        edits = only_fields_changed(old, new, HERDR_ROUTING_FIELDS)
        if edits and all(herdr_routing(*values) for values in zip(*edits)):
            return 'tooling'
    if path in LOCALE_FILES and wording_edit(old, new):
        return 'localization'
    return 'full'


def classify(root, decision, entries):
    decision['change_count'] = len({change.path for change in entries})
    if not entries:
        raise ScopeError('The comparison contains no changes.', 'no_changes')
    profiles = set()
    for change in entries:
        try:
            profile = classify_file(root, change)
        except ScopeError as error:
            raise ScopeError(str(error), 'change_requires_full') from None
        except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
            raise ScopeError('A changed file has unreadable or ambiguous content.', 'change_requires_full') from None
        if profile == 'full':
            raise ScopeError('A changed path, mode, or content requires full verification.', 'change_requires_full')
        profiles.add(profile)
    narrower = profiles - {'records'}
    if len(narrower) > 1:
        raise ScopeError('The comparison crosses tooling and localization.', 'change_requires_full')
    decision['profile'] = narrower.pop() if narrower else 'records'
    decision['reason'] = SCOPE_REASONS[decision['profile']]
    decision['reason_code'] = 'reviewed_changes'


def full_decision():
    return dict(profile='full', reason=None, base=None, checkout=None, change_count=None,
                reason_code='comparison_unavailable')


def fallback(decision, error):
    if isinstance(error, ScopeError):
        decision.update(reason=str(error), reason_code=error.reason_code)
    else:
        decision['reason'] = 'Git or event data could not be verified.'
    return decision


def committed_scope(root: Path, base: str, head: str) -> dict:
    """Compare two full commit SHAs and require the base to be an ancestor."""
    decision = full_decision()
    try:
        head = decision['checkout'] = exact_commit(root, head)
        require_complete_history(root)
        base = exact_commit(root, base)
        require_ancestor(root, base, head)
        decision['base'] = base
        classify(root, decision, changes(root, base, head))
    except (ScopeError, OSError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        fallback(decision, error)
    decision.pop('reason_code')
    return decision


def ci_event_checkout(root, checkout, env):
    """Bind the actual checkout to the event without deciding if its diff is comparable."""
    name = env.get('GITHUB_EVENT_NAME')
    if name not in {'pull_request', 'push'}:
        raise ScopeError('The GitHub event has no supported change comparison.')
    if oid(env.get('GITHUB_SHA')) != checkout:
        raise ScopeError('The checked out HEAD does not match GITHUB_SHA.')
    event_file = env.get('GITHUB_EVENT_PATH')
    if not event_file:
        raise ScopeError('The GitHub event file is missing.')
    event = json_object(Path(event_file).read_bytes())
    if name == 'pull_request':
        number = event.get('number')
        if (not isinstance(number, JsonNumber) or not re.fullmatch(r'[1-9][0-9]*', number.token)
                or env.get('GITHUB_REF') != f'refs/pull/{number.token}/merge'):
            raise ScopeError('The pull request number does not match the GitHub merge ref.')
        pull = event['pull_request']
        base = oid(pull['base']['sha'])
        head = oid(pull['head']['sha'])
        if event.get('action') == 'synchronize':
            # A synchronize payload can retain the previous preview SHA.
            if oid(event.get('after')) != head:
                raise ScopeError('The synchronized head does not match the pull request head.')
        # The commit headers retain the actual parents even at a shallow boundary.
        headers = git(root, 'cat-file', 'commit', checkout).split(b'\n\n', 1)[0]
        parents = [line.removeprefix(b'parent ') for line in headers.splitlines() if line.startswith(b'parent ')]
        if parents != [base.encode('ascii'), head.encode('ascii')]:
            raise ScopeError('The checkout does not have the event base and head as its two parents.')
        return event, head, base

    if event.get('ref') != 'refs/heads/main' or env.get('GITHUB_REF') != 'refs/heads/main':
        raise ScopeError('Only a push to main has a supported comparison.')
    if oid(event.get('after')) != checkout:
        raise ScopeError('The push after SHA does not match the checkout.')
    if event.get('deleted', False) is not False:
        raise ScopeError('The push does not identify a checked out main revision.')
    return event, checkout, None


def ci_base(root, checkout, env):
    event, head, pr_base = ci_event_checkout(root, checkout, env)
    if pr_base is not None:
        preview = event['pull_request'].get('merge_commit_sha')
        if preview is not None:
            preview = oid(preview)
        if event.get('action') != 'synchronize' and preview is not None and preview != checkout:
            raise ScopeError('The event merge SHA does not match the checkout.')
        resolved = git(root, 'rev-parse', '--revs-only', f'{pr_base}^{{commit}}', f'{head}^{{commit}}', '--')
        if resolved.splitlines() != [pr_base.encode('ascii'), head.encode('ascii')]:
            raise ScopeError('The pull request comparison parents are unavailable as commits.')
        return pr_base
    if any(event.get(flag, False) is not False for flag in ('forced', 'created')):
        raise ScopeError('The push does not describe an ordinary update to main.')
    return exact_commit(root, event.get('before'))


def preview(root, *, base_ref='origin/main', ci_env=None):
    decision = full_decision()
    try:
        shallow, checked_ref, commit = git(
            root, 'rev-parse', '--revs-only', '--is-shallow-repository', 'HEAD', 'HEAD^{commit}', '--',
        ).splitlines()
        if checked_ref != commit:
            raise ScopeError('A required Git object is not the named commit.')
        head = decision['checkout'] = oid(commit.decode('ascii'))
        if ci_env is None:
            try:
                base = decision['base'] = resolve_commit(root, base_ref)
            except ScopeError:
                raise ScopeError('The local base ref could not be resolved to a commit.') from None
        if shallow != b'false':
            raise ScopeError('Shallow or unverifiable Git history.')
        if ci_env is not None:
            base = ci_base(root, head, ci_env)
        require_ancestor(root, base, head)
        decision['base'] = base
        untracked = inspect_index(root, include_untracked=ci_env is None)
        if ci_env is not None:
            if git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none'):
                raise ScopeError('The CI checkout has local changes.')
            entries = changes(root, base, head)
        else:
            # Each edge is inspected so unstaged edits cannot hide what is staged.
            entries = changes(root, base, head)
            entries.extend(changes(root, '--cached', head))
            entries.extend(changes(root, worktree=True))
            entries.extend(
                Change(path, b'A', b'000000', b'100644', b'', b'', worktree=True)
                for path in untracked
            )
        classify(root, decision, entries)
    except (ScopeError, OSError, KeyError, TypeError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        return fallback(decision, error)
    return decision


def describe_local_checks(root, decision):
    """Report the harness selection from the local catalog without loading the project."""
    decision['checks'] = None
    try:
        catalog = json_object(working_file(root, b'project/checks.json').read_bytes())
        rows = catalog['checks']
        if not isinstance(rows, list):
            raise ValueError('Expected check records')
        checks = {}
        for row in rows:
            key, status = row['id'], row['status']
            cadence = row.get('cadence', 'change')
            if (not isinstance(key, str) or not CHECK_ID.fullmatch(key) or key in checks
                    or status not in ('implemented', 'planned', 'retired')
                    or cadence not in ('change', 'task', 'release')):
                raise ValueError('Invalid check record')
            checks[key] = (status, cadence)
        full = [key for key, (status, cadence) in checks.items() if status != 'retired' and cadence == 'change']
        if not full:
            raise ValueError('No full check selection')
    except (ScopeError, OSError, KeyError, TypeError, ValueError, RecursionError):
        decision['profile'] = 'full'
        decision['reason'] += ' The local check catalog is unavailable or invalid.'
        return

    profile = decision['profile']
    if profile in SCOPED_PROFILES:
        profiles = catalog.get('profiles')
        ids = profiles.get(profile) if isinstance(profiles, dict) else None
        if (isinstance(ids, list) and ids and all(isinstance(key, str) and key in checks for key in ids)
                and len(ids) == len(set(ids))):
            selected = set(ids)
            decision['checks'] = [key for key in checks if key in selected]
            return
        decision['profile'] = 'full'
        decision['reason'] += ' The selected profile has missing or invalid check IDs.'
    decision['checks'] = full


def repository_root(cwd):
    output = git(cwd, 'rev-parse', '--show-toplevel')
    # Git for Windows may emit CRLF. POSIX paths can contain a literal final CR.
    terminator = b'\r\n' if sys.platform == 'win32' and output.endswith(b'\r\n') else b'\n'
    return Path(os.fsdecode(output.removesuffix(terminator)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--ci', action='store_true', help='Verify and classify the GitHub event checkout')
    source.add_argument('--base', help='Compare locally against this Git ref (default: origin/main)')
    parser.add_argument('--run', action='store_true', help='Run the existing harness for the local selection')
    args = parser.parse_args()
    if args.ci and args.run:
        parser.error('--run is only supported with a local comparison')

    root = Path.cwd()
    try:
        root = repository_root(root)
        decision = preview(
            root, base_ref=args.base if args.base is not None else 'origin/main',
            ci_env=os.environ if args.ci else None,
        )
    except (ScopeError, OSError, ValueError, subprocess.SubprocessError) as error:
        decision = fallback(full_decision(), error)
    if not args.ci:
        describe_local_checks(root, decision)
    reason_code = decision.pop('reason_code')
    print(json.dumps(decision), flush=True)

    profile = decision['profile']
    output = os.environ.get('GITHUB_OUTPUT') if args.ci else None
    if output:
        provenance = dict(profile=profile, reason_code=reason_code, base=decision['base'],
                          checkout=decision['checkout'], change_count=decision['change_count'])
        with Path(output).open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(f'profile={profile}\n')
            stream.write(f'decision={json.dumps(provenance, separators=(",", ":"))}\n')

    if args.run:
        if decision['base'] is not None:
            history = [sys.executable, 'scripts/project.py', 'history', '--base', decision['base']]
            result = subprocess.run(history, cwd=root, check=False, env=checkout_git_environment())
            if result.returncode:
                return result.returncode
        command = [sys.executable, 'scripts/check.py']
        if profile in SCOPED_PROFILES:
            command.extend(('--profile', profile))
        return subprocess.run(command, cwd=root, check=False, env=checkout_git_environment()).returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
