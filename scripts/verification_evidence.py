"""Check retained field observations, release artifacts and measurement samples."""

import argparse
from datetime import datetime
import json
from math import ceil
from pathlib import Path
from statistics import median

if __package__:
    from .project import ProjectError, file_digest, load_project, local_path, read_json, source_identity
else:
    from project import ProjectError, file_digest, load_project, local_path, read_json, source_identity

ROOT = Path(__file__).resolve().parents[1]
SESSION_CHECKS = {
    'configured-model-session': ('configured_models', 'text'),
    'configured-voice-session': ('configured_models', 'voice'),
    'configured-market-source': ('real_market_data', 'market'),
}
FIELD_ACTIONS = {
    'physical-windows-workspace': {
        'unconfigured-entry',
        'sample',
        'instrument',
        'timeframe',
        'style',
        'resize',
        'crosshair',
        'data-inspection',
        'exit',
    },
    'ubuntu-workspace-observation': {
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
        'local-bridge',
    },
}


def nonempty(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ProjectError(f'Missing or invalid observation field: {key}')
    return value


def observed_time(record):
    timestamp = nonempty(record, 'observed_at')
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ProjectError('Expected a timezone-aware observation time') from error
    if moment.tzinfo is None:
        raise ProjectError('Expected a timezone-aware observation time')
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


def field_observation(root, key, reports_root=None):
    directory = local_path(reports_root or root / '.reports', 'observations/' + key)
    record_path = local_path(directory, 'observation.json')
    obs = read_json(record_path)
    if not isinstance(obs, dict) or obs.get('kind') != key:
        raise ProjectError('Expected a typed interactive field observation')
    source = source_identity(root)
    if source['working_tree_dirty']:
        raise ProjectError('Field observation requires recoverable clean source')
    bound_source(obs, source)
    nonempty(obs, 'operator')
    observed_time(obs)
    actions = obs.get('actions')
    if not isinstance(actions, dict) or not FIELD_ACTIONS[key].issubset(actions):
        raise ProjectError('Field observation lacks required actions')
    for action in FIELD_ACTIONS[key]:
        result = actions[action]
        if not isinstance(result, dict) or result.get('status') != 'passed':
            raise ProjectError(f'Field action did not pass: {action}')
        nonempty(result, 'observed')
    images = obs.get('screenshots')
    if not isinstance(images, list) or not images:
        raise ProjectError('Field observation requires retained screenshots')
    for ref in images:
        path = verified_file(directory, ref)
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ProjectError('Expected a PNG screenshot')
    if key == 'physical-windows-workspace':
        windows_client(obs.get('host'))
        reviewed = nonempty(obs, 'ubuntu_reviewed_commit')
        ubuntu = load_project(root)['tasks'].get('M1-012')
        if not ubuntu or ubuntu['status'] != 'done':
            raise ProjectError('The physical trial requires completed Ubuntu-local acceptance')
        receipts = [read_json(local_path(root, ref['path'])) for ref in ubuntu['evidence']]
        if not any(
            receipt['source']['sha256'] == source['sha256']
            and receipt['source']['git_head'] == reviewed
            and 'ubuntu-workspace-observation' in receipt.get('field_observations', {})
            for receipt in receipts
        ):
            raise ProjectError('No matching Ubuntu observation for this execution snapshot')
        if obs.get('surface') != 'physical-dual-boot-native-webview2':
            raise ProjectError('Expected the physical native WebView2 observation')
    else:
        if obs.get('surface') != 'ubuntu-graphical-workspace':
            raise ProjectError('Expected the graphical Ubuntu workspace observation')
        statuses = obs.get('external_sessions', {})
        if not isinstance(statuses, dict) or any(
            statuses.get(name) not in {'observed-separately', 'pending'}
            for name in ['text', 'voice', 'cli', 'desktop']
        ):
            raise ProjectError('Record the independent external-session disposition')
    result = {
        'kind': key,
        'commit': obs['commit'],
        'surface': obs['surface'],
        'observation_sha256': file_digest(record_path),
    }
    if key == 'ubuntu-workspace-observation':
        result['external_sessions'] = {name: statuses[name] for name in ['text', 'voice', 'cli', 'desktop']}
    return result


def collect_session(run_dir, key, source):
    """A real-session check writes a redacted, result-specific observation in its own run."""
    record_path = local_path(run_dir, key + '.observation.json')
    obs = read_json(record_path)
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
    result = {key: nonempty(obs, key) for key in fields}
    if key != 'configured-market-source':
        if __package__:
            from .session_qualification import validate_qualification
        else:
            from session_qualification import validate_qualification
        case = read_json(ROOT / 'engineering-contract-v1.json')['ai']['qualified_session_acceptance']['reference_case']
        try:
            result['qualification_case'] = validate_qualification(obs, case)
        except ValueError as error:
            raise ProjectError(str(error)) from error
    result.update(
        modality=obs['modality'],
        commit=obs['commit'],
        inputs_sha256=obs['inputs_sha256'],
        evidence_sha256=file_digest(record_path),
    )
    return result


def integration_validation(results):
    summary = {
        kind: {'status': 'not_evaluated', 'observations': []}
        for kind in ['configured_models', 'cloud_models', 'real_market_data']
    }
    summary['mock_ai_evaluated'] = any(
        r['id'] in {'ai-workflow', 'ai-replay-evaluations'} and r['status'] == 'passed' for r in results
    )
    for r in results:
        if r['id'] in SESSION_CHECKS and r['status'] == 'passed' and 'observation' in r:
            kind, _ = SESSION_CHECKS[r['id']]
            observation = r['observation']
            groups = [kind]
            if kind == 'configured_models' and observation['deployment'] == 'cloud':
                groups.append('cloud_models')
            for key in groups:
                summary[key]['status'] = 'observed'
                summary[key]['observations'].append(observation)
    return summary


def performance_verdict(data, policy):
    """Score the retained presented-frame and total private byte series."""
    threshold = policy['initial_bars_to_chart_p95_ms']

    def samples(key, count):
        values = data.get(key)
        if (
            not isinstance(values, list)
            or len(values) != count
            or any(type(v) not in (int, float) or not 0 <= v <= 2**53 - 1 for v in values)
        ):
            raise ProjectError(f'Incomplete or invalid performance series: {key}')
        return values

    def p95(values):
        return sorted(values)[ceil(0.95 * len(values)) - 1]

    cold, warm = [p95(samples(key, 20)) for key in ['cold_first_frame_ms', 'warm_first_frame_ms']]
    updates = p95(samples('update_to_present_ms', 72000))
    memory = samples('private_bytes_per_minute', 120)[10:]
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


def release_artifacts(run_dir, source, policy, *, expected=None):
    directory = local_path(run_dir, 'candidate')
    manifest_path = local_path(directory, 'build-manifest.json')
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ProjectError('Missing candidate build manifest')
    bound_source(manifest, source)
    installer = verified_file(directory, manifest.get('installer'))
    if installer.suffix.lower() != '.exe':
        raise ProjectError('Expected the tested NSIS installer')
    installer_hash = manifest['installer']['sha256']
    observations = manifest.get('windows_observations')
    if not isinstance(observations, list) or len(observations) != 2:
        raise ProjectError('Require both Windows 10 and Windows 11 client observations')
    families = set()
    for ref in observations:
        obs = read_json(verified_file(directory, ref))
        if not isinstance(obs, dict):
            raise ProjectError('Invalid Windows observation')
        bound_source(obs, source)
        families.add(windows_client(obs.get('host')))
        if obs.get('installer_sha256') != installer_hash or any(
            obs.get(k) != 'passed' for k in ['clean_install', 'upgrade']
        ):
            raise ProjectError(
                'Windows observation lacks a passing installation and upgrade for this installer'
            )
        nonempty(obs, 'operator')
        observed_time(obs)
    if families != {'Windows 10', 'Windows 11'}:
        raise ProjectError('Missing a supported Windows client family')
    metrics = read_json(verified_file(directory, manifest.get('performance')))
    if not isinstance(metrics, dict):
        raise ProjectError('Invalid performance observation')
    bound_source(metrics, source)
    windows_client(metrics.get('host'))
    for key in ['cpu', 'ram', 'display_scale', 'refresh_rate']:
        nonempty(metrics['host'], key)
    if (
        metrics.get('installer_sha256') != installer_hash
        or performance_verdict(metrics, policy)['status'] != 'passed'
    ):
        raise ProjectError('Candidate has no passing bound performance observation')
    summary = {'manifest_sha256': file_digest(manifest_path), 'installer_sha256': installer_hash}
    if expected is not None and expected != summary:
        raise ProjectError('Candidate manifest or installer differs from the checked release')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=FIELD_ACTIONS)
    kind = parser.parse_args().kind
    if not (ROOT / '.reports/observations' / kind / 'observation.json').is_file():
        print('The required interactive observation has not been supplied.')
        raise SystemExit(77)
    try:
        print(json.dumps(field_observation(ROOT, kind)))
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SystemExit(str(error)) from error
