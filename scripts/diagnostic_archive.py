"""Resolve retained diagnostics by immutable identity without consulting the checkout."""

import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import stat


class DiagnosticArchiveError(ValueError):
    """A locator or its retained diagnostic cannot be verified."""


_SHA256 = re.compile(r'[a-f0-9]{64}')
_COMMIT = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
_UNPORTABLE = re.compile(r'[\\:*?"<>|\x00-\x1f\x7f]')
_LOCATOR_FIELDS = {'run_id', 'source_commit', 'report_sha256'}
_LAYOUT = {'runs': 'test-report.json', 'recovery': 'diagnostic.json'}
_HARNESS_STATUSES = ('passed', 'failed', 'blocked')
_RECOVERY_STATUSES = ('attention_required', 'no_trigger_observed')


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _portable_component(name):
    return bool(name) and not (
        name.endswith(('.', ' ')) or _UNPORTABLE.search(name) or PureWindowsPath(name).is_reserved()
    )


def _identifier(value):
    return _matches(_IDENTIFIER, value) and _portable_component(value)


def validate_locator(locator):
    """Validate an identifier without accessing an archive or Git."""
    if not isinstance(locator, dict) or locator.keys() != _LOCATOR_FIELDS:
        raise DiagnosticArchiveError('Expected run_id, source_commit and report_sha256')
    if not _identifier(locator['run_id']):
        raise DiagnosticArchiveError('Expected a portable diagnostic run identifier')
    commit = locator['source_commit']
    if not _matches(_COMMIT, commit) or set(commit) == {'0'}:
        raise DiagnosticArchiveError('Expected a full source Git commit ID')
    if not _matches(_SHA256, locator['report_sha256']):
        raise DiagnosticArchiveError('Expected a report SHA-256')


def _parts(relative):
    if not isinstance(relative, str) or not all(_portable_component(part) for part in relative.split('/')):
        raise DiagnosticArchiveError(f'Invalid archive-relative path: {relative!r}')
    return relative.split('/')


def _path_type(path):
    info = path.lstat()
    if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise DiagnosticArchiveError(f'Archive path uses a reparse point: {path}')
    if stat.S_ISDIR(info.st_mode):
        return 'directory'
    if stat.S_ISREG(info.st_mode):
        return 'file'
    raise DiagnosticArchiveError(f'Archive paths must use ordinary directories and regular files: {path}')


def _path(root, relative, *, directory=False):
    parts = _parts(relative)
    result = root
    for index, part in enumerate(parts):
        result /= part
        expected = 'directory' if directory or index < len(parts) - 1 else 'file'
        if _path_type(result) != expected:
            raise DiagnosticArchiveError(f'Expected an archive {expected}: {result}')
    return result


def _file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _verify_file(root, relative, expected):
    try:
        if _matches(_SHA256, expected) and _file_hash(_path(root, relative)) == expected:
            return
    except FileNotFoundError:
        pass
    raise DiagnosticArchiveError(f'Missing or changed diagnostic bytes: {relative}')


def _json_document(data):
    def unique_keys(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise DiagnosticArchiveError('Duplicate fields in diagnostic JSON')
        return result

    def invalid_constant(value):
        raise DiagnosticArchiveError(f'Invalid JSON constant in diagnostic: {value}')

    parsed = json.loads(data, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    if not isinstance(parsed, dict):
        raise DiagnosticArchiveError('Expected a diagnostic JSON object')
    return parsed


def _parse_report(data, report_sha256):
    report = _json_document(data)
    status = report.get('status')
    if report.get('report_version') in ('2.0', '2.1') and isinstance(report.get('source'), dict):
        kind = 'harness'
        run_id = report.get('run_id')
        source_commit = report['source'].get('git_head')
        dirty = report['source'].get('working_tree_dirty')
        if status not in _HARNESS_STATUSES or type(dirty) is not bool:
            raise DiagnosticArchiveError('Invalid recorded harness outcome or source state')
    elif report.get('diagnostic_version') == '1.0' and report.get('kind') == 'scaffold_recovery':
        kind = 'recovery'
        snapshot, git = report.get('snapshot'), report.get('git')
        if not isinstance(snapshot, str) or not isinstance(git, dict):
            raise DiagnosticArchiveError('Expected a saved recovery snapshot with a source Git commit')
        parts = snapshot.split('/')
        run_id = parts[2] if len(parts) == 4 and parts[:2] == ['.reports', 'recovery'] and (
            parts[3] == 'diagnostic.json'
        ) else None
        source_commit = git.get('head')
        working_tree = git.get('working_tree')
        dirty = bool(working_tree) if isinstance(working_tree, str) else None
        if status not in _RECOVERY_STATUSES or report.get('acceptance') != 'not_evaluated':
            raise DiagnosticArchiveError('Invalid recorded recovery outcome')
    else:
        raise DiagnosticArchiveError('Expected a supported harness report or recovery snapshot')
    locator = {'run_id': run_id, 'source_commit': source_commit, 'report_sha256': report_sha256}
    validate_locator(locator)
    return locator, kind, dirty, report


def diagnostic_locator(report_path):
    """Read an original report to derive its locator for an explicitly retained copy."""
    path = Path(report_path)
    try:
        data = _path(path.parent.resolve(strict=True), path.name).read_bytes()
        locator, _, _, _ = _parse_report(data, hashlib.sha256(data).hexdigest())
        return locator
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as error:
        raise DiagnosticArchiveError(f'Cannot identify diagnostic: {error}') from error


def _checked_logs(directory, report):
    run_id, checks = report['run_id'], report.get('checks')
    if not isinstance(checks, list):
        raise DiagnosticArchiveError('Expected a harness check list')
    seen, verified = set(), []
    for check in checks:
        if (
            not isinstance(check, dict) or not _identifier(check.get('id'))
            or check.get('status') not in _HARNESS_STATUSES or check['id'].casefold() in seen
        ):
            raise DiagnosticArchiveError('Invalid or duplicate diagnostic check')
        check_id, status = check['id'], check['status']
        seen.add(check_id.casefold())
        if status == 'passed' and (type(check.get('exit_code')) is not int or check['exit_code'] != 0):
            raise DiagnosticArchiveError(f'Invalid recorded exit code for passed check: {check_id}')
        if status == 'blocked' and not {'log', 'log_sha256', 'exit_code'} & check.keys():
            continue
        relative = check_id + '.log'
        if check.get('log') != f'.reports/runs/{run_id}/{relative}':
            raise DiagnosticArchiveError(f'Missing log or log outside its diagnostic run: {check_id}')
        _verify_file(directory, relative, check.get('log_sha256'))
        verified.append(check_id)
    return verified


def _checked_observations(root, directory, checks):
    verified = []
    for check in checks:
        check_id = check['id']
        if 'browser_results_sha256' in check:
            relative = f'{check_id}/results.json'
            sha256 = check['browser_results_sha256']
            if 'log' not in check or (check['status'] == 'passed' and sha256 is None):
                raise DiagnosticArchiveError(f'Missing browser results binding: {check_id}')
            if sha256 is not None:
                _verify_file(directory, relative, sha256)
                verified.append(relative)
            else:
                try:
                    _path(directory, relative)
                except FileNotFoundError:
                    pass
                else:
                    raise DiagnosticArchiveError(f'Unexpected unbound diagnostic bytes: {relative}')
        if 'observation' in check:
            binding = check['observation']
            relative = f'{check_id}.observation.json'
            if not isinstance(binding, dict):
                raise DiagnosticArchiveError(f'Invalid session observation binding: {check_id}')
            _verify_file(directory, relative, binding.get('evidence_sha256'))
            verified.append(relative)
        if 'field_observation' in check:
            binding = check['field_observation']
            observation_dir = f'observations/{check_id}'
            relative = f'{observation_dir}/observation.json'
            if not isinstance(binding, dict):
                raise DiagnosticArchiveError(f'Invalid field observation binding: {check_id}')
            # A run can hold its own snapshot. The harness and hosted exports
            # write field observations next to their runs directory.
            try:
                _path(directory, observation_dir, directory=True)
            except FileNotFoundError:
                observation_root = root
            else:
                observation_root = directory
            try:
                contents = _path(observation_root, relative).read_bytes()
            except FileNotFoundError as error:
                raise DiagnosticArchiveError(f'Missing field observation: {relative}') from error
            if hashlib.sha256(contents).hexdigest() != binding.get('observation_sha256'):
                raise DiagnosticArchiveError(f'Missing or changed diagnostic bytes: {relative}')
            screenshots = _json_document(contents).get('screenshots')
            if not isinstance(screenshots, list) or not screenshots:
                raise DiagnosticArchiveError(f'Missing field observation screenshot references: {relative}')
            for ref in screenshots:
                if not isinstance(ref, dict):
                    raise DiagnosticArchiveError(f'Invalid field observation screenshot reference: {relative}')
                path = ref.get('path')
                _parts(path)
                _verify_file(observation_root, f'{observation_dir}/{path}', ref.get('sha256'))
            verified.append(relative)
    return verified


def _checked_windows_python(directory, report):
    records = report.get('windows_python', {})
    if not isinstance(records, dict) or records.keys() - {'python', 'recovery'}:
        raise DiagnosticArchiveError('Invalid Windows Python diagnostic part identities')
    verified = []
    for lane, part in records.items():
        name = f'windows-python-{lane}'
        if not isinstance(part, dict) or part.get('id') != name or part.get('status') not in _HARNESS_STATUSES:
            raise DiagnosticArchiveError(f'Invalid Windows Python diagnostic part: {lane}')
        log = name + '.log'
        manifest = name + '.json'
        if part['status'] == 'passed' and not {'log', 'log_sha256', 'manifest_sha256'} <= part.keys():
            raise DiagnosticArchiveError(f'Missing passing Windows Python diagnostic evidence: {lane}')
        if {'log', 'log_sha256', 'manifest_sha256'}.intersection(part):
            if part.get('log') != f".reports/runs/{report['run_id']}/{log}":
                raise DiagnosticArchiveError(f'Missing or misplaced Windows Python diagnostic log: {lane}')
            _verify_file(directory, log, part.get('log_sha256'))
            verified.append(log)
        if 'manifest_sha256' in part:
            _verify_file(directory, manifest, part['manifest_sha256'])
            verified.append(manifest)
        if 'producer_report_sha256' in part:
            producer = name + '.producer.json'
            _verify_file(directory, producer, part['producer_report_sha256'])
            verified.append(producer)
    return verified


def _checked_windows_producers(directory, report):
    records = report.get('windows_producers', {})
    if not isinstance(records, dict) or records.keys() - {'browser', 'recovery', 'workspace'}:
        raise DiagnosticArchiveError('Invalid Windows diagnostic producer identities')
    verified = []
    for lane, sha256 in records.items():
        relative = f'windows-{lane}.producer.json'
        _verify_file(directory, relative, sha256)
        verified.append(relative)
    return verified


def _files(directory):
    for path in directory.iterdir():
        _parts(path.name)
        if _path_type(path) == 'directory':
            yield from _files(path)
        else:
            yield path


def _verify_candidate(directory, report):
    expected = report.get('candidate_artifacts')
    if expected is None:
        return False
    relative = 'candidate/build-manifest.json'
    contents = _path(directory, relative).read_bytes()
    if not isinstance(expected, dict) or hashlib.sha256(contents).hexdigest() != expected.get('manifest_sha256'):
        raise DiagnosticArchiveError('Candidate manifest differs from the recorded diagnostic')
    manifest = _json_document(contents)
    source = report['source']
    if (
        manifest.get('commit') != source['git_head'] or not _matches(_SHA256, source.get('sha256'))
        or manifest.get('inputs_sha256') != source['sha256']
    ):
        raise DiagnosticArchiveError('Candidate manifest refers to different source inputs')
    installer = manifest.get('installer')
    if not isinstance(installer, dict) or installer.get('sha256') != expected.get('installer_sha256'):
        raise DiagnosticArchiveError('Candidate installer differs from the recorded diagnostic')
    windows, notices = manifest.get('windows_observations'), manifest.get('third_party_licenses')
    if not isinstance(windows, list) or not isinstance(notices, list):
        raise DiagnosticArchiveError('Missing candidate observations or notices')
    seen = {'build-manifest.json'}
    references = [installer, *windows, manifest.get('performance'), *notices]
    if 'owner_workbench' in manifest:
        references.append(manifest['owner_workbench'])
    for ref in references:
        if not isinstance(ref, dict):
            raise DiagnosticArchiveError('Invalid candidate manifest file reference')
        path = ref.get('path')
        _parts(path)
        if path.casefold() in seen:
            raise DiagnosticArchiveError(f'Duplicate candidate manifest file reference: {path}')
        _verify_file(directory, f'candidate/{path}', ref.get('sha256'))
        seen.add(path.casefold())
    return True


def _verified_originals(directory, report):
    records = report.get('files')
    if not isinstance(records, list) or not records:
        raise DiagnosticArchiveError('Missing recovery snapshot file manifest')
    seen, verified = set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise DiagnosticArchiveError('Invalid recovery snapshot file record')
        relative = record.get('path')
        _parts(relative)
        status = record.get('status')
        if relative.casefold() in seen or status not in ('read', 'malformed', 'unavailable'):
            raise DiagnosticArchiveError(f'Invalid or duplicate recovery snapshot file record: {relative}')
        seen.add(relative.casefold())
        if status == 'unavailable' and 'sha256' not in record:
            continue
        if status == 'unavailable':
            raise DiagnosticArchiveError(f'Unavailable recovery file has a recorded checksum: {relative}')
        _verify_file(directory, f'originals/{relative}', record.get('sha256'))
        verified.add(relative)

    try:
        originals = _path(directory, 'originals', directory=True)
    except FileNotFoundError:
        actual = set()
    else:
        actual = {path.relative_to(originals).as_posix() for path in _files(originals)}
    if actual != verified:
        raise DiagnosticArchiveError('Retained originals differ from the recovery snapshot file manifest')
    return sorted(verified)


def _verify_checksums(directory):
    try:
        manifest = _path(directory, 'SHA256SUMS')
    except FileNotFoundError:
        return False
    expected, seen = set(), set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64}) [ *](.+)', line)
        if not match:
            raise DiagnosticArchiveError('Invalid SHA256SUMS entry')
        sha256, relative = match.groups()
        _parts(relative)
        folded = relative.casefold()
        if folded == 'sha256sums' or folded in seen:
            raise DiagnosticArchiveError(f'Invalid or duplicate SHA256SUMS entry: {relative}')
        _verify_file(directory, relative, sha256)
        expected.add(relative)
        seen.add(folded)
    actual = {path.relative_to(directory).as_posix() for path in _files(directory) if path != manifest}
    if actual != expected:
        raise DiagnosticArchiveError('Archive files differ from SHA256SUMS')
    return True


def resolve_diagnostic(locator, archive_root):
    """Verify the original bytes and available diagnostic bindings inside a host archive."""
    validate_locator(locator)
    if archive_root is None:
        raise DiagnosticArchiveError('An archive root is required to resolve a diagnostic')
    try:
        root = Path(archive_root).resolve(strict=True)
        if _path_type(root) != 'directory':
            raise DiagnosticArchiveError('Expected an archive root directory')
        matches = []
        for folder, name in _LAYOUT.items():
            relative = f'{folder}/{locator["run_id"]}/{name}'
            try:
                matches.append(_path(root, relative))
            except FileNotFoundError:
                continue
        if len(matches) != 1:
            raise DiagnosticArchiveError(f'Expected one archived diagnostic for {locator["run_id"]}, found {len(matches)}')
        report_path = matches[0]
        data = report_path.read_bytes()
        report_sha256 = hashlib.sha256(data).hexdigest()
        if report_sha256 != locator['report_sha256']:
            raise DiagnosticArchiveError('Archived report SHA-256 differs from the locator')
        actual, kind, dirty, report = _parse_report(data, report_sha256)
        if actual != locator:
            raise DiagnosticArchiveError('Archived run ID or source Git commit differs from the locator')
        if report_path.name != ('test-report.json' if kind == 'harness' else 'diagnostic.json'):
            raise DiagnosticArchiveError('Archived document was found under the wrong diagnostic layout')
        directory = report_path.parent
        logs = _checked_logs(directory, report) if kind == 'harness' else []
        observations = _checked_observations(root, directory, report['checks']) if kind == 'harness' else []
        if kind == 'harness':
            observations.extend(_checked_windows_python(directory, report))
            observations.extend(_checked_windows_producers(directory, report))
        candidate_manifest = _verify_candidate(directory, report) if kind == 'harness' else False
        originals = _verified_originals(directory, report) if kind == 'recovery' else []
        manifest = _verify_checksums(directory)
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as error:
        raise DiagnosticArchiveError(f'Cannot verify archived diagnostic: {error}') from error
    check_statuses = {check['id']: check['status'] for check in report.get('checks', [])} if kind == 'harness' else {}
    selection = report.get('selection')
    selected = selection.get('checks') if isinstance(selection, dict) else None
    return {
        **locator,
        'report_kind': kind,
        'recorded_status': report['status'],
        'selected_check_statuses': {key: status for key, status in check_statuses.items()
                                    if not isinstance(selected, list) or key in selected},
        'source_working_tree_dirty': dirty,
        'verified_logs': logs,
        'verified_observations': observations,
        'verified_originals': originals,
        'manifest_verified': kind == 'recovery' or candidate_manifest or manifest,
        'report_path': str(report_path),
        'scope': 'Archived diagnostic integrity and recorded outcome only.',
    }
