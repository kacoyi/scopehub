"""Check retained field observations, release artifacts and measurement samples."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from math import ceil
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
from statistics import median

if __package__:
    from .project import ProjectError, digest, file_digest, json_bytes, load_project, local_path, read_json, source_identity, validate_model
    from .verification_report import (
        EXTERNAL_CLIENT_CHECK, PHYSICAL_WINDOWS_CHECK, RUN_OBSERVATION_CHECKS, SESSION_CHECKS, UBUNTU_WORKSPACE_CHECK,
    )
else:
    from project import ProjectError, digest, file_digest, json_bytes, load_project, local_path, read_json, source_identity, validate_model
    from verification_report import (
        EXTERNAL_CLIENT_CHECK, PHYSICAL_WINDOWS_CHECK, RUN_OBSERVATION_CHECKS, SESSION_CHECKS, UBUNTU_WORKSPACE_CHECK,
    )

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_CLIENT_MAX_BYTES = 8192
WORKBENCH_OBSERVATION_VERSION = 2
MANUAL_CLOCK_ALLOWANCE = timedelta(minutes=5)
UBUNTU_SURFACE = 'native-ubuntu-tauri-webkitgtk'
WORKBENCH_EDIT = {'workbench-edit', 'workbench-recover', 'workbench-reopen'}
OWNER_WORKBENCH_ACTIONS = {'workbench-from-blank', 'workbench-edit', 'workbench-refresh', 'workbench-reopen'}
FIELD_ACTIONS = {
    PHYSICAL_WINDOWS_CHECK: {
        'unconfigured-entry',
        'sample',
        'instrument',
        'timeframe',
        'style',
        'resize',
        'crosshair',
        'data-inspection',
        'exit', 'workbench-selection', 'workbench-display-scaling',
    } | WORKBENCH_EDIT,
    UBUNTU_WORKSPACE_CHECK: {
        'history',
        'live',
        'reconnect',
        'folder-refresh',
        'undo-and-restore',
        'layout',
        'indicator',
        'mock-ai',
        'evidence-selection',
        'decision-review',
        'local-bridge', 'workbench-from-blank', 'workbench-ai-reshape', 'workbench-manual-detail',
        'workbench-refresh', 'workbench-source-selection', 'workbench-undo',
    } | WORKBENCH_EDIT,
}
WINDOWS_HOST_FIELDS = {'family', 'product_type', 'build', 'arch', 'webview2'}
UBUNTU_HOST_FIELDS = {'os', 'ubuntu_version', 'webkitgtk_version', 'process_name', 'process_id',
                      'window_id', 'window_process_id'}
SOURCE_FIELDS = {'commit', 'inputs_sha256'}
SAMPLE_COUNTS = {'cold_first_frame_ms': 20, 'warm_first_frame_ms': 20,
                 'update_to_present_ms': 72000, 'private_bytes_per_minute': 120}


class LocalFieldObservation(ProjectError):
    """The producer kept some part of the field observation local."""


def hosted_artifact_path(root, relative, *, directory=False):
    parts = relative.split('/')
    if any(
        not part or part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved()
        or re.search(r'[\\:*?"<>|\x00-\x1f]', part) for part in parts
    ):
        raise ProjectError('Invalid artifact path.')
    path = root
    for index, part in enumerate(['', *parts]):
        path /= part
        info = path.lstat()
        expect_dir = index < len(parts) or directory
        expected_type = stat.S_ISDIR if expect_dir else stat.S_ISREG
        if (
            not expected_type(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not expect_dir and info.st_nlink > 1)
        ):
            raise ProjectError('Artifact paths must use ordinary directories and unlinked regular files.')
    return path


def redacted_fields(record, fields):
    if not isinstance(record, dict) or record.keys() - fields:
        raise ProjectError('Observation contains fields outside the redacted evidence format.')


def evidence_json(contents):
    def unique_fields(pairs):
        record = dict(pairs)
        if len(record) != len(pairs):
            raise ProjectError('Duplicate fields in verification evidence.')
        return record

    return json.loads(contents, object_pairs_hook=unique_fields)


def retained_reference(ref):
    redacted_fields(ref, {'path', 'sha256'})
    if not re.fullmatch(r'[a-f0-9]{64}', nonempty(ref, 'sha256')):
        raise ProjectError('Expected the SHA-256 of a retained artifact.')
    nonempty(ref, 'path')
    return ref


def nonempty(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ProjectError(f'Missing or invalid observation field: {key}')
    return value


def observed_time(record, *, completed=False):
    timestamp = nonempty(record, 'observed_at')
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ProjectError('Expected a timezone-aware observation time') from error
    if moment.tzinfo is None:
        raise ProjectError('Expected a timezone-aware observation time')
    if completed and moment > datetime.now(timezone.utc) + MANUAL_CLOCK_ALLOWANCE:
        raise ProjectError('Completed evidence cannot have a future observation time.')
    return timestamp


def bound_source(record, source):
    if (
        not source.get('git_head')
        or record.get('commit') != source['git_head']
        or record.get('inputs_sha256') != source['sha256']
    ):
        raise ProjectError('Observation belongs to another revision or execution inputs')


def verified_file(directory, ref):
    if not isinstance(ref, dict):
        raise ProjectError('Missing retained artifact reference')
    path = local_path(directory, nonempty(ref, 'path'))
    if not path.is_file() or file_digest(path) != ref.get('sha256'):
        raise ProjectError(f'Missing or changed retained artifact: {ref.get("path")}')
    return path


def windows_client(host):
    if not isinstance(host, dict):
        raise ProjectError('Missing Windows client environment')
    family, build = host.get('family'), host.get('build')
    if (
        type(host.get('product_type')) is not int
        or host['product_type'] != 1
        or host.get('arch') not in {'x64', 'amd64', 'x86_64'}
        or family not in {'Windows 10', 'Windows 11'}
        or type(build) is not int
        or build < 10240
        or ((family == 'Windows 11') != (build >= 22000))
    ):
        raise ProjectError('Expected a Windows 10 or Windows 11 x64 client build')
    nonempty(host, 'webview2')
    return family


def current_workbench_observation(record):
    version = record.get('observation_version')
    if type(version) is not int or version != WORKBENCH_OBSERVATION_VERSION:
        raise ProjectError('Current workbench acceptance requires observation_version 2.')


def native_ubuntu(host):
    if not isinstance(host, dict) or host.get('os') != 'Ubuntu':
        raise ProjectError('Expected the native Ubuntu host.')
    for field in ('ubuntu_version', 'webkitgtk_version'):
        if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)*', nonempty(host, field)):
            raise ProjectError(f'Expected the observed numerical {field}.')
    pid = host.get('process_id')
    if (host.get('process_name') != 'scopehub-desktop' or type(pid) is not int or pid <= 0
            or type(host.get('window_process_id')) is not int or host['window_process_id'] != pid
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,95}', nonempty(host, 'window_id'))):
        raise ProjectError('Expected the ScopeHub process and its own observed native window.')


def observed_actions(record, required, *, complete, redacted=False):
    actions = record.get('actions')
    if not isinstance(actions, dict):
        raise ProjectError('Expected interactive workbench actions.')
    if redacted and actions.keys() - required:
        raise ProjectError('Hosted workbench observation has an unrecognized action.')
    if complete and (missing := required - actions.keys()):
        raise ProjectError(f'Field observation lacks required actions: {", ".join(sorted(missing))}')
    for name in actions:
        if name not in required:
            continue
        result = actions[name]
        if redacted:
            redacted_fields(result, {'status', 'observed'})
        if not isinstance(result, dict) or result.get('status') not in ('passed', 'failed', 'blocked', 'not_run'):
            raise ProjectError(f'Invalid field action outcome: {name}')
        if complete and result['status'] != 'passed':
            raise ProjectError(f'Field action did not pass: {name}')
        if result['status'] != 'not_run' or 'observed' in result:
            outcome = nonempty(result, 'observed')
            if (redacted and not outcome.isprintable()) or outcome.strip().casefold() in ('passed', 'failed', 'blocked', 'not_run'):
                raise ProjectError(f'Expected the observed outcome of {name}.')


def field_host(record):
    if record['kind'] == 'physical-windows-workspace':
        if record.get('surface') != 'physical-dual-boot-native-webview2':
            raise ProjectError('Expected the physical native WebView2 observation.')
        windows_client(record.get('host'))
    else:
        if record.get('surface') != UBUNTU_SURFACE:
            raise ProjectError('Expected the native Ubuntu Tauri/WebKitGTK workspace.')
        native_ubuntu(record.get('host'))


def hosted_field_record(obs):
    if isinstance(obs, dict) and (obs.get('screenshot_scope') != 'synthetic' or obs.get('sharing_scope') != 'redacted'):
        raise LocalFieldObservation('Hosted field observations require synthetic screenshots and an explicitly redacted summary.')
    windows = isinstance(obs, dict) and obs.get('kind') == 'physical-windows-workspace'
    field_metadata = {'host', 'observation_version', 'ubuntu_reviewed_commit' if windows else 'external_sessions'}
    redacted_fields(obs, SOURCE_FIELDS | field_metadata | {'kind', 'operator', 'observed_at', 'surface', 'actions',
                                                          'screenshots', 'screenshot_scope', 'sharing_scope'})
    current_workbench_observation(obs)
    observed_time(obs, completed=True)
    observed_actions(obs, FIELD_ACTIONS[obs['kind']], complete=True, redacted=True)
    if 'host' in obs:
        redacted_fields(obs['host'], WINDOWS_HOST_FIELDS if windows else UBUNTU_HOST_FIELDS)
    field_host(obs)
    if 'external_sessions' in obs:
        redacted_fields(obs['external_sessions'], {'text', 'voice', 'cli', 'desktop'})
    images = obs.get('screenshots')
    if not isinstance(images, list) or not images:
        raise ProjectError('Field observation requires retained screenshots.')
    for ref in images:
        if PurePosixPath(retained_reference(ref)['path']).suffix.lower() != '.png':
            raise ProjectError('Expected a synthetic PNG screenshot.')
    return images


def field_observation(root, key, reports_root=None, *, hosted=False):
    reports_root = reports_root or root / '.reports'
    relative = 'observations/' + key
    if hosted:
        record_path = hosted_artifact_path(reports_root, relative + '/observation.json')
        directory = record_path.parent
    else:
        directory = local_path(reports_root, relative)
        record_path = local_path(directory, 'observation.json')
    obs = evidence_json(record_path.read_bytes())
    if not isinstance(obs, dict) or obs.get('kind') != key:
        raise ProjectError('Expected a typed interactive field observation')
    current_workbench_observation(obs)
    images = hosted_field_record(obs) if hosted else obs.get('screenshots')
    source = source_identity(root)
    if source['working_tree_dirty']:
        raise ProjectError('Field observation requires recoverable clean source')
    bound_source(obs, source)
    nonempty(obs, 'operator')
    observed_time(obs, completed=True)
    if not hosted:
        field_host(obs)
        observed_actions(obs, FIELD_ACTIONS[key], complete=True)
    if not isinstance(images, list) or not images:
        raise ProjectError('Field observation requires retained screenshots')
    for ref in images:
        if hosted:
            hosted_artifact_path(reports_root, relative + '/' + ref['path'])
        path = verified_file(directory, ref)
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ProjectError('Expected a PNG screenshot')
    if key == 'physical-windows-workspace':
        reviewed = nonempty(obs, 'ubuntu_reviewed_commit')
        ubuntu = load_project(root)['tasks'].get('M1-012')
        if not ubuntu or ubuntu['status'] != 'done':
            raise ProjectError('The physical trial requires completed Ubuntu-local acceptance')
        receipts = [read_json(local_path(root, ref['path'])) for ref in ubuntu['evidence']]
        if not any(
            receipt['source']['sha256'] == source['sha256']
            and receipt['source']['git_head'] == reviewed
            and (field := receipt.get('field_observations', {}).get('ubuntu-workspace-observation', {}))
            and field.get('observation_version') == WORKBENCH_OBSERVATION_VERSION
            and field.get('surface') == UBUNTU_SURFACE and field.get('commit') == reviewed
            for receipt in receipts
        ):
            raise ProjectError('No matching Ubuntu observation for this execution snapshot')
    else:
        statuses = obs.get('external_sessions', {})
        if not isinstance(statuses, dict) or any(
            statuses.get(name) not in {'observed-separately', 'pending'}
            for name in ['text', 'voice', 'cli', 'desktop']
        ):
            raise ProjectError('Record the independent external-session disposition')
    result = {
        'kind': key,
        'observation_version': WORKBENCH_OBSERVATION_VERSION,
        'commit': obs['commit'],
        'surface': obs['surface'],
        'observation_sha256': file_digest(record_path),
    }
    if key == 'ubuntu-workspace-observation':
        result['external_sessions'] = {name: statuses[name] for name in ['text', 'voice', 'cli', 'desktop']}
    return result


def session_observation(contents, key, source):
    obs = evidence_json(contents)
    if not isinstance(obs, dict) or obs.get('check') != key or obs.get('modality') != SESSION_CHECKS[key][1]:
        raise ProjectError(f'Missing valid {key} observation')
    bound_source(obs, source)
    observed_time(obs)
    fields = ['provider', 'route', 'data_revision', 'authorized_scope', 'observed_at']
    if key != 'configured-market-source':
        if obs.get('deployment') not in {'local', 'cloud'}:
            raise ProjectError('A configured-model observation must identify local or cloud deployment')
        fields.extend(['model', 'deployment'])
    else:
        fields.append('dataset')
    allowed = {'check', 'modality', 'commit', 'inputs_sha256', *fields}
    if key != 'configured-market-source':
        allowed.add('qualification')
    if set(obs) - allowed:
        raise ProjectError('Unexpected fields in a redacted session observation')
    result = {key: nonempty(obs, key) for key in fields}
    for field in ('provider', 'route', 'data_revision', 'dataset' if key == 'configured-market-source' else 'model'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', result[field]):
            raise ProjectError(f'Expected a redacted identifier for {field}')
    if result['authorized_scope'] != {
        'text': 'synthetic-model-qualification', 'voice': 'synthetic-voice-qualification',
        'market': 'authorized-market-verification',
    }[obs['modality']]:
        raise ProjectError('Expected the redacted grant category for this session')
    if key != 'configured-market-source':
        if __package__:
            from .session_qualification import validate_qualification
        else:
            from session_qualification import validate_qualification
        case = read_json(ROOT / 'tests/fixtures/financial-session-v2.json')
        try:
            result['qualification_case'] = validate_qualification(obs, case)
        except ValueError as error:
            raise ProjectError(str(error)) from error
    result.update(
        modality=obs['modality'],
        commit=obs['commit'],
        inputs_sha256=obs['inputs_sha256'],
        evidence_sha256=digest(contents),
    )
    return result


def collect_session(run_dir, key, source):
    """A real-session check writes a redacted, result-specific observation in its own run."""
    path = local_path(run_dir, key + '.observation.json')
    return session_observation(path.read_bytes(), key, source)


def redacted_check_log(exit_code):
    return (json.dumps({'exit_code': exit_code}) + '\n').encode('ascii')


def external_client_observation(root, contents, source):
    if len(contents) > EXTERNAL_CLIENT_MAX_BYTES:
        raise ProjectError('External-client observation exceeds the redacted size limit.')
    try:
        record = evidence_json(contents)
        validate_model(root, 'ExternalClientObservation', record)
    except (ValueError, RecursionError):
        raise ProjectError('Invalid redacted external-client observation.') from None
    if source.get('working_tree_dirty') is not False:
        raise ProjectError('An external-client observation requires clean committed inputs.')
    bound_source(record, source)
    clients = {leg['kind']: leg for leg in record['clients']}
    if set(clients) != {'cli', 'desktop'}:
        raise ProjectError('Expected one CLI and one desktop observation.')
    cli, desktop = clients['cli'], clients['desktop']
    if cli['family'] != 'codex-cli' or desktop['family'] == cli['family']:
        raise ProjectError('Expected Codex CLI and a separately identified desktop client.')
    for key in ('workspace_sha256', 'revision_sha256', 'selection_sha256'):
        if cli[key] != desktop[key] or cli[key] == '0' * 64:
            raise ProjectError('External clients must observe the same identified synthetic workspace and selection.')
    consent = {leg['consent_observation_sha256'] for leg in clients.values()}
    if len(consent) != 2 or '0' * 64 in consent:
        raise ProjectError('External clients require independent publication consent observations.')
    for leg in clients.values():
        observed_time(leg, completed=True)
    return {'evidence_sha256': digest(contents), 'observation': record}


def capture_run_observation(root, path, key, source):
    """Return the exact bytes and the observation validated from those bytes."""
    if key == EXTERNAL_CLIENT_CHECK:
        with path.open('rb') as stream:
            contents = stream.read(EXTERNAL_CLIENT_MAX_BYTES + 1)
        return contents, external_client_observation(root, contents, source)
    contents = path.read_bytes()
    return contents, session_observation(contents, key, source)


def collect_run_observation(root, run_dir, key, source):
    if key == EXTERNAL_CLIENT_CHECK:
        path = hosted_artifact_path(run_dir, key + '.observation.json')
        _, observation = capture_run_observation(root, path, key, source)
        return observation
    return collect_session(run_dir, key, source)


def retain_external_client_record(root):
    configured = os.environ.get('SCOPEHUB_EXTERNAL_CLIENT_OBSERVATION_FILE')
    if not configured:
        raise SystemExit(77)
    try:
        path = Path(configured)
        runs = (root / '.reports/runs').resolve()
        run_dir = Path(os.environ['SCOPEHUB_RUN_DIR']).resolve()
        if not path.is_file() or run_dir.parent != runs or not run_dir.is_dir():
            raise ProjectError('Expected a local summary and an isolated verification run.')
        # The input is an explicitly approved summary. Private client files are never loaded.
        _, observation = capture_run_observation(root, path, EXTERNAL_CLIENT_CHECK, source_identity(root))
        with (run_dir / (EXTERNAL_CLIENT_CHECK + '.observation.json')).open('xb') as retained:
            retained.write(json_bytes(observation['observation']))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise SystemExit('External-client observation failed validation.') from None


def performance_verdict(data, policy):
    """Score the retained presented-frame and total private byte series."""
    threshold = policy['initial_bars_to_chart_p95_ms']

    def p95(values):
        return sorted(values)[ceil(0.95 * len(values)) - 1]

    cold, warm = [p95(performance_samples(data, key)) for key in ['cold_first_frame_ms', 'warm_first_frame_ms']]
    updates = p95(performance_samples(data, 'update_to_present_ms'))
    memory = performance_samples(data, 'private_bytes_per_minute')[10:]
    windows = [median(memory[i : i + 10]) for i in range(0, len(memory), 10)]
    growth = windows[-1] - windows[0]
    rises = sum(b > a for a, b in zip(windows, windows[1:]))
    memory_status = 'passed'
    if growth > max(32 * 1024**2, 0.10 * windows[0]):
        memory_status = 'failed' if rises >= 8 else 'inconclusive'
    if (
        cold > threshold
        or warm > threshold
        or updates > policy['chart_update_p95_ms']
        or data.get('final_bar_presented') is not True
    ):
        status = 'failed'
    else:
        status = memory_status
    return {
        'status': status,
        'cold_p95_ms': cold,
        'warm_p95_ms': warm,
        'update_p95_ms': updates,
        'memory_status': memory_status,
    }


def performance_samples(data, key, *, partial=False):
    values = data.get(key)
    count = SAMPLE_COUNTS[key]
    if (
        not isinstance(values, list)
        or len(values) > count or (not partial and len(values) != count)
        or any(type(v) not in (int, float) or not 0 <= v <= 2**53 - 1 for v in values)
    ):
        raise ProjectError(f'Incomplete or invalid performance series: {key}')
    return values


def release_notices(manifest):
    notices = manifest.get('third_party_licenses', [])
    if not isinstance(notices, list):
        raise ProjectError('Expected digest-bound third-party licenses and notices.')
    for ref in notices:
        path = PurePosixPath(retained_reference(ref)['path'])
        legal_name = re.fullmatch(
            r'(?:LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE|THIRD_PARTY_NOTICES)'
            r'(?:-[A-Za-z0-9_+-]+)?(?:\.(?:txt|md|rst|license))?', path.name, re.IGNORECASE,
        )
        inside_licenses = len(path.parts) > 1 and path.parts[0] == 'third-party-licenses'
        if not (legal_name and len(path.parts) == 1 or inside_licenses and (
                legal_name or path.suffix.lower() in {'.txt', '.md', '.rst', '.license'})):
            raise ProjectError('Expected a license or notice file in the candidate legal artifact paths.')
    return notices


def validate_notice(contents):
    encoding = 'utf-16' if contents.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = contents.decode(encoding)
    except UnicodeError as error:
        raise ProjectError('Expected a text license or notice.') from error
    if not text.strip() or any(not (char.isprintable() or char in '\r\n\t') for char in text):
        raise ProjectError('Expected a text license or notice.')


def owner_workbench_reference(manifest):
    if 'owner_workbench' not in manifest:
        raise ProjectError('Candidate lacks the owner workbench observation for this installer.')
    return retained_reference(manifest['owner_workbench'])


def hosted_candidate_manifest(manifest, source, *, require_owner=False):
    redacted_fields(manifest, SOURCE_FIELDS | {'installer', 'windows_observations', 'performance', 'owner_workbench',
                                               'third_party_licenses', 'webview2'})
    bound_source(manifest, source)
    if 'webview2' in manifest and manifest['webview2'] not in ('downloadBootstrapper', 'embedBootstrapper'):
        raise ProjectError('Expected a WebView2 bootstrapper strategy.')
    installer = retained_reference(manifest.get('installer'))
    if PurePosixPath(installer['path']).suffix.lower() != '.exe':
        raise ProjectError('Expected the tested NSIS installer')
    observations = manifest.get('windows_observations', [])
    if not isinstance(observations, list) or len(observations) > 2:
        raise ProjectError('Expected at most two Windows client observations.')
    refs = [('installer', installer)]
    refs.extend(('windows', retained_reference(ref)) for ref in observations)
    if require_owner or 'owner_workbench' in manifest:
        refs.append(('owner_workbench', owner_workbench_reference(manifest)))
    if 'performance' in manifest:
        refs.append(('performance', retained_reference(manifest['performance'])))
    refs.extend(('notice', ref) for ref in release_notices(manifest))
    aliases = {ref['path'].casefold() for _, ref in refs}
    if len(aliases) != len(refs) or 'build-manifest.json' in aliases:
        raise ProjectError('Candidate references must identify distinct artifact files.')
    return refs


def hosted_candidate_observation(record, kind, source, installer_sha256):
    if kind == 'owner_workbench':
        candidate_workbench(record, source, installer_sha256, complete=False, hosted=True)
        return
    fields = SOURCE_FIELDS | {'host', 'installer_sha256', 'operator', 'observed_at', 'clean_install', 'upgrade'}
    host_fields = WINDOWS_HOST_FIELDS
    if kind == 'performance':
        fields |= SAMPLE_COUNTS.keys() | {'final_bar_presented'}
        host_fields = host_fields | {'cpu', 'ram', 'display_scale', 'refresh_rate'}
    redacted_fields(record, fields)
    bound_source(record, source)
    if record.get('installer_sha256') != installer_sha256:
        raise ProjectError('Observation belongs to another installer.')
    host = record.get('host')
    redacted_fields(host, host_fields)
    windows_client(host)
    for key in host.keys() - {'product_type', 'build'}:
        if not nonempty(host, key).isprintable():
            raise ProjectError('Expected a redacted Windows host label.')
    if kind == 'windows' or 'operator' in record:
        if not nonempty(record, 'operator').isprintable():
            raise ProjectError('Expected a redacted operator label.')
    if kind == 'windows' or 'observed_at' in record:
        observed_time(record, completed=True)
    for key in ('clean_install', 'upgrade'):
        if (kind == 'windows' or key in record) and record.get(key) not in ('passed', 'failed', 'blocked', 'not_run'):
            raise ProjectError('Expected a typed Windows installation result.')
    if kind == 'performance':
        for key in SAMPLE_COUNTS.keys() & record.keys():
            performance_samples(record, key, partial=True)
        if 'final_bar_presented' in record and type(record['final_bar_presented']) is not bool:
            raise ProjectError('Expected a boolean final-frame observation.')


def candidate_workbench(record, source, installer_sha256, *, complete, hosted):
    if not isinstance(record, dict) or record.get('kind') != 'packaged-owner-workbench':
        raise ProjectError('Expected a typed packaged owner workbench observation.')
    current_workbench_observation(record)
    bound_source(record, source)
    if record.get('installer_sha256') != installer_sha256:
        raise ProjectError('The owner workbench observation belongs to another installer.')
    if hosted:
        if record.get('sharing_scope') != 'redacted':
            raise ProjectError('Hosted owner workbench evidence requires an explicitly redacted review.')
        redacted_fields(record, SOURCE_FIELDS | {'kind', 'observation_version', 'installer_sha256', 'host', 'method',
                        'operator', 'reviewer_role', 'observed_at', 'actions', 'feedback', 'sharing_scope'})
        redacted_fields(record.get('host'), WINDOWS_HOST_FIELDS)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', nonempty(record, 'operator')):
            raise ProjectError('Expected a redacted operator label.')
    if record.get('reviewer_role') != 'product-owner' or record.get('method') != 'hands-on':
        raise ProjectError('The packaged workbench requires a hands-on observation by the product owner.')
    nonempty(record, 'operator')
    observed_time(record, completed=True)
    windows_client(record.get('host'))
    if hosted and not nonempty(record['host'], 'webview2').isprintable():
        raise ProjectError('Expected a redacted WebView2 version.')
    if complete or 'feedback' in record:
        feedback = nonempty(record, 'feedback')
        if hosted and not feedback.isprintable():
            raise ProjectError('Expected the observed owner feedback.')
    observed_actions(record, OWNER_WORKBENCH_ACTIONS, complete=complete, redacted=hosted)


def release_artifacts(run_dir, source, policy, *, expected=None, hosted=False):
    if hosted:
        manifest_path = hosted_artifact_path(run_dir, 'candidate/build-manifest.json')
        directory = manifest_path.parent
    else:
        directory = local_path(run_dir, 'candidate')
        manifest_path = local_path(directory, 'build-manifest.json')
    manifest = evidence_json(manifest_path.read_bytes())
    if not isinstance(manifest, dict):
        raise ProjectError('Missing candidate build manifest')
    bound_source(manifest, source)
    owner_ref = owner_workbench_reference(manifest)
    if hosted:
        for _, ref in hosted_candidate_manifest(manifest, source):
            hosted_artifact_path(run_dir, 'candidate/' + ref['path'])
    installer = verified_file(directory, manifest.get('installer'))
    if installer.suffix.lower() != '.exe':
        raise ProjectError('Expected the tested NSIS installer')
    installer_hash = manifest['installer']['sha256']
    observations = manifest.get('windows_observations')
    if not isinstance(observations, list) or len(observations) != 2:
        raise ProjectError('Require both Windows 10 and Windows 11 client observations')
    families = set()
    for ref in observations:
        path = verified_file(directory, ref)
        obs = evidence_json(path.read_bytes()) if hosted else read_json(path)
        if not isinstance(obs, dict):
            raise ProjectError('Invalid Windows observation')
        if hosted:
            hosted_candidate_observation(obs, 'windows', source, installer_hash)
        bound_source(obs, source)
        families.add(windows_client(obs.get('host')))
        if obs.get('installer_sha256') != installer_hash or any(
            obs.get(k) != 'passed' for k in ['clean_install', 'upgrade']
        ):
            raise ProjectError(
                'Windows observation lacks a passing installation and upgrade for this installer'
            )
        nonempty(obs, 'operator')
        observed_time(obs, completed=True)
    if families != {'Windows 10', 'Windows 11'}:
        raise ProjectError('Missing a supported Windows client family')
    owner_path = verified_file(directory, owner_ref)
    candidate_workbench(evidence_json(owner_path.read_bytes()), source, installer_hash, complete=True, hosted=hosted)
    metrics_path = verified_file(directory, manifest.get('performance'))
    metrics = evidence_json(metrics_path.read_bytes()) if hosted else read_json(metrics_path)
    if not isinstance(metrics, dict):
        raise ProjectError('Invalid performance observation')
    if hosted:
        hosted_candidate_observation(metrics, 'performance', source, installer_hash)
    elif 'observed_at' in metrics:
        observed_time(metrics, completed=True)
    bound_source(metrics, source)
    windows_client(metrics.get('host'))
    for key in ['cpu', 'ram', 'display_scale', 'refresh_rate']:
        nonempty(metrics['host'], key)
    if (
        metrics.get('installer_sha256') != installer_hash
        or performance_verdict(metrics, policy)['status'] != 'passed'
    ):
        raise ProjectError('Candidate has no passing bound performance observation')
    notices = release_notices(manifest)
    if not notices:
        raise ProjectError('An accepted candidate requires retained third-party licenses and notices.')
    for ref in notices:
        notice = verified_file(directory, ref)
        validate_notice(notice.read_bytes())
    summary = {'manifest_sha256': file_digest(manifest_path), 'installer_sha256': installer_hash}
    if expected is not None and expected != summary:
        raise ProjectError('Candidate manifest or installer differs from the checked release')
    return summary


def main(root=ROOT):
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=[*FIELD_ACTIONS, EXTERNAL_CLIENT_CHECK])
    kind = parser.parse_args().kind
    if kind == EXTERNAL_CLIENT_CHECK:
        retain_external_client_record(root)
        return
    hosted = os.environ.get('GITHUB_ACTIONS') == 'true'
    if not (root / '.reports/observations' / kind / 'observation.json').is_file():
        print('The required interactive observation has not been supplied.')
        raise SystemExit(77)
    try:
        print(json.dumps(field_observation(root, kind, hosted=hosted)))
    except LocalFieldObservation:
        print('The field observation is local. Hosted admission requires synthetic screenshots and an explicitly redacted summary.')
        raise SystemExit(77)
    except (OSError, ValueError, TypeError, KeyError) as error:
        if hosted:
            raise SystemExit('Hosted field observation failed validation.') from None
        raise SystemExit(str(error)) from error


if __name__ == '__main__':
    main()
