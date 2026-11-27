"""Derive report claims from check results without loading the harness."""

from datetime import datetime, timedelta
import re


WINDOWS_PYTHON_CHECK = 'python-contracts-workers'
WINDOWS_PYTHON_PART_LANES = ('python', 'recovery')
SESSION_CHECKS = {
    'configured-model-session': ('configured_models', 'text'),
    'configured-voice-session': ('configured_models', 'voice'),
    'configured-market-source': ('real_market_data', 'market'),
}
EXTERNAL_CLIENT_CHECK = 'configured-external-ai-clients'
RUN_OBSERVATION_CHECKS = SESSION_CHECKS.keys() | {EXTERNAL_CLIENT_CHECK}
RUN_OBSERVATION_POLICIES = {
    'session': frozenset(SESSION_CHECKS),
    'session-and-external-clients': frozenset(RUN_OBSERVATION_CHECKS),
}
PHYSICAL_WINDOWS_CHECK = 'physical-windows-workspace'
UBUNTU_WORKSPACE_CHECK = 'ubuntu-workspace-observation'
FIELD_OBSERVATION_CHECKS = {PHYSICAL_WINDOWS_CHECK, UBUNTU_WORKSPACE_CHECK}
OBSERVATION_CHECKS = RUN_OBSERVATION_CHECKS | FIELD_OBSERVATION_CHECKS


def integration_validation(results):
    summary = {
        kind: {'status': 'not_evaluated', 'observations': []}
        for kind in ('configured_models', 'cloud_models', 'real_market_data')
    }
    summary['mock_ai_evaluated'] = False
    for row in results:
        if row['status'] != 'passed':
            continue
        check = row['id']
        if check in {'ai-workflow', 'ai-replay-evaluations'}:
            summary['mock_ai_evaluated'] = True
        if check in SESSION_CHECKS and 'observation' in row:
            kind, _ = SESSION_CHECKS[check]
            observation = row['observation']
            groups = [kind]
            if kind == 'configured_models' and observation['deployment'] == 'cloud':
                groups.append('cloud_models')
            for key in groups:
                summary[key]['status'] = 'observed'
                summary[key]['observations'].append(observation)
    return summary


def integration_summary_matches(summary, checks):
    expected = integration_validation(checks)
    return (isinstance(summary, dict) and summary.get('mock_ai_evaluated') is expected['mock_ai_evaluated']
            and summary == expected)


def passed_observation_checks(checks):
    return {row['id'] for row in checks if row['status'] == 'passed' and row['id'] in OBSERVATION_CHECKS}


def publishing_observation_checks(catalog, run_observations):
    owners = run_observations | FIELD_OBSERVATION_CHECKS
    return {row['id'] for row in catalog['checks'] if row['id'] in owners and row['status'] == 'implemented'}


def report_observation_issue(report, *, run_observations=RUN_OBSERVATION_CHECKS):
    """Validate observation claims available in both raw publication and timing inputs."""
    checks = report['checks']
    for row in checks:
        check, passed = row['id'], row['status'] == 'passed'
        for field, owners in (('observation', run_observations),
                              ('field_observation', FIELD_OBSERVATION_CHECKS)):
            expected = passed and check in owners
            if field in row and (not expected or not isinstance(row[field], dict)):
                return 'an unexpected observation'
            if expected and field not in row:
                return 'a passing check without its observation'
        if passed and check in SESSION_CHECKS and SESSION_CHECKS[check][0] == 'configured_models':
            if row['observation'].get('deployment') not in ('local', 'cloud'):
                return 'a configured model observation without a valid deployment'
    if ('integration_validation' in report
            and not integration_summary_matches(report['integration_validation'], checks)):
        return 'an integration summary that contradicts its check results'
    return None


def archived_windows_python_lanes(profile):
    if profile == 'full':
        return WINDOWS_PYTHON_PART_LANES
    lane = profile.removeprefix('ci-windows-')
    return (lane,) if lane in WINDOWS_PYTHON_PART_LANES else ()


def archived_windows_python_issue(report, lanes, retained_producers):
    """Check source-qualified report metadata before the compact timing projection drops it."""
    parts = report.get('windows_python', {})
    producers = report.get('windows_producers', {})
    full = report['selection']['profile'] == 'full'

    def digest(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)

    if (not isinstance(producers, dict) or producers.keys() != retained_producers
            or ('windows_producers' in report and not retained_producers)
            or not all(digest(value) for value in producers.values())):
        return 'invalid Windows producer report bindings'
    if (not isinstance(parts, dict) or parts.keys() != set(lanes)
            or ('windows_python' in report and not lanes)):
        return 'Windows Python parts that differ from the publishing lane'

    intervals = []
    for lane, part in parts.items():
        label = f'windows-python-{lane}'
        command = [report['environment']['python_executable'], 'scripts/windows_python.py', lane]
        if (not isinstance(part, dict) or part.get('id') != label or part.get('command') != command
                or part.get('status') not in ('passed', 'failed', 'blocked')
                or ('producer_report_sha256' in part) != full
                or any(not digest(part[key]) for key in ('producer_report_sha256', 'manifest_sha256', 'log_sha256')
                       if key in part)):
            return 'an invalid Windows Python part identity'
        passed = part['status'] == 'passed'
        reason, code = part.get('reason_code'), part.get('exit_code')
        if 'log' in part:
            if (part['log'] != f".reports/runs/{report['run_id']}/{label}.log" or 'log_sha256' not in part
                    or 'exit_code' not in part or (code is not None and type(code) is not int)):
                return 'an invalid Windows Python process reference'
            try:
                start = datetime.fromisoformat(part['started_at'])
                finish = datetime.fromisoformat(report['generated_at'])
                measured = part['duration_seconds']
                if (start.utcoffset() is None or finish.utcoffset() is None
                        or type(measured) not in (int, float) or not 0 <= measured <= timedelta.max.total_seconds()):
                    return 'an invalid Windows Python process interval'
                end = start + timedelta(seconds=measured)
                # The archived harness rounds elapsed time independently to milliseconds.
                if ((end - finish).total_seconds() > .002
                        or (finish - start).total_seconds() > report['timing']['elapsed_seconds'] + .002):
                    return 'a Windows Python process outside its harness interval'
            except (TypeError, ValueError, KeyError, OverflowError):
                return 'an invalid Windows Python process interval'
            intervals.append((start, end))
        elif (reason not in ('earlier_failure', 'executable_unavailable')
              or {'started_at', 'duration_seconds', 'exit_code', 'log_sha256', 'manifest_sha256'} & part.keys()):
            return 'unavailable Windows Python process evidence'
        if passed and (code != 0 or reason is not None or 'manifest_sha256' not in part):
            return 'an incomplete passing Windows Python part'
        if not passed and (report['status'] == 'passed' or (code == 0 and not reason)):
            return 'a Windows Python part failure without a corresponding report result'

    if full and lanes:
        elapsed = (max(end for _, end in intervals) - min(start for start, _ in intervals)).total_seconds() if intervals else 0
        combined = [row for row in report['checks'] if row['id'] == WINDOWS_PYTHON_CHECK]
        if (len(combined) != 1 or combined[0].get('duration_seconds') != round(elapsed, 3)
                or (combined[0]['status'] == 'passed' and any(part['status'] != 'passed' for part in parts.values()))):
            return 'a Windows Python aggregate measurement that differs from its parts'
    return None
