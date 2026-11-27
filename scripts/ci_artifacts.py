"""Select publishable evidence from a fresh hosted verification run."""

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

if __package__:
    from . import windows_python
    from .check import (PRIVATE_CHECK_LOGS, PYTHON_MANIFEST_INTERRUPTED, WINDOWS_LANES,
                        WINDOWS_LANES_RUN_PREFIX, check_timeout, ci_provenance, redacted_check_log, select_checks,
                        stops_after_check, windows_lane_checks, windows_lanes_run_id)
    from .project import (ProjectError, digest, file_digest, json_bytes, load_project,
                          project_definition_hash, read_json, source_identity, task_definition_hash, validate_model, windows_x64)
    from .review_tree import CommittedPath
    from .timing import (TIMING_FILE, duration, generated_contracts_sample, job_summary, report_sample,
                         windows_aggregate_cache)
    from .verification_evidence import (
        EXTERNAL_CLIENT_CHECK, FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, LocalFieldObservation,
        capture_run_observation, field_observation,
        hosted_artifact_path, hosted_candidate_manifest, hosted_candidate_observation, hosted_field_record,
        evidence_json, integration_validation, release_artifacts, validate_notice,
    )
    from .verification_scope import SCOPED_PROFILES, ci_base, ci_event_checkout, require_ancestor
else:
    import windows_python
    from check import (PRIVATE_CHECK_LOGS, PYTHON_MANIFEST_INTERRUPTED, WINDOWS_LANES,
                       WINDOWS_LANES_RUN_PREFIX, check_timeout, ci_provenance, redacted_check_log, select_checks,
                       stops_after_check, windows_lane_checks, windows_lanes_run_id)
    from project import (ProjectError, digest, file_digest, json_bytes, load_project,
                         project_definition_hash, read_json, source_identity, task_definition_hash, validate_model, windows_x64)
    from review_tree import CommittedPath
    from timing import (TIMING_FILE, duration, generated_contracts_sample, job_summary, report_sample,
                        windows_aggregate_cache)
    from verification_evidence import (
        EXTERNAL_CLIENT_CHECK, FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, LocalFieldObservation,
        capture_run_observation, field_observation,
        hosted_artifact_path, hosted_candidate_manifest, hosted_candidate_observation, hosted_field_record,
        evidence_json, integration_validation, release_artifacts, validate_notice,
    )
    from verification_scope import SCOPED_PROFILES, ci_base, ci_event_checkout, require_ancestor

ROOT = Path(__file__).resolve().parents[1]
SCOPE_FILE = 'verification-scope.json'
WINDOWS_GENERATOR_SAMPLE = 'windows-generator-sample.json'
SCOPE_FIELDS = {'profile', 'reason_code', 'base', 'checkout', 'change_count'}
COMMIT_SHA = re.compile(r'(?!0{40})[0-9a-f]{40}')
RUN_ID = re.compile(r'[a-zA-Z0-9_-][a-zA-Z0-9._-]*')
PRODUCER_REPORT_DIGEST = 'producer_report_sha256'
WINDOWS_PRODUCERS = 'windows_producers'
WINDOWS_PRODUCER_JOBS = {f'windows_{lane}': lane for lane in WINDOWS_LANES}
DOWNLOAD_PLATFORMS = {'windows': 'Windows', 'windows-latest': 'Windows', 'linux': 'Linux', 'ubuntu-latest': 'Linux'}
BROWSER_OUTPUTS = {
    'market-live-stream': ('results.json', 'market-stream-', ('synthetic-replay-final.png',)),
    'market-quality-states': ('results.json', 'market-quality-', ('synthetic-market-quality.png',)),
    'market-workflow': ('results.json', 'market-workflow-', ('synthetic-market-workflow.png',)),
    'market-worker-interruption': ('results.json', 'worker-interruption-', ()),
    'market-recovery-ui': ('results.json', 'market-recovery-ui-', ()),
    'market-recovery': ('results.json', 'market-closed-bar-repair-', (
        'verified-closed-bar-repair.png', 'unresolved-closed-bar-gap.png',
    )),
    'workspace-timeframe': ('results.json', 'workspace-', ()),
    'desktop-workspace-transport': ('results.json', 'desktop-workspace-', ()),
    'chart-history': ('browser-results.json', 'history-', (
        'unconfigured.png', 'history-line.png', 'history-candles.png',
    )),
    'ubuntu-preview': ('results.json', 'preview-', (
        'unconfigured.png', 'futures-candles.png', 'symbol-search.png', 'stock-data-window.png',
        'stock-line-narrow.png', 'minute-axis-desktop.png', 'minute-axis-narrow.png',
    )),
}


class PublicationError(ValueError):
    """Evidence is unsafe or does not match the current hosted run."""


def prepare_reports(root):
    # Refuse an existing root, including an empty directory or a dangling link.
    try:
        (root / '.reports').mkdir(mode=0o700)
    except FileExistsError as error:
        raise PublicationError('CI verification requires a fresh .reports directory.') from error


def checked_path(root, relative, *, directory=False):
    try:
        return hosted_artifact_path(root, relative, directory=directory)
    except ProjectError as error:
        raise PublicationError(str(error)) from error


def optional_path(root, relative, *, directory=False):
    try:
        return checked_path(root, relative, directory=directory)
    except FileNotFoundError:
        return None


def browser_artifacts(reports, relative, check):
    result_name, test_prefix, screenshots = BROWSER_OUTPUTS[check]
    result = optional_path(reports, f'{relative}/{result_name}')
    if result is not None:
        yield result
    browser = optional_path(reports, f'{relative}/browser', directory=True)
    if browser is None:
        return
    for test in sorted(browser.iterdir()):
        if not test.name.startswith(test_prefix):
            continue
        for name in (*screenshots, 'test-failed-1.png'):
            path = optional_path(reports, f'{relative}/browser/{test.name}/{name}')
            if path is not None:
                yield path


def copy_file(source, destination, *, sha256=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
        if sha256 is not None and file_digest(destination) != sha256:
            raise PublicationError('Artifact bytes differ from the recorded digest.')
    except (OSError, PublicationError):
        destination.unlink(missing_ok=True)
        raise


def bound_bytes(path, sha256):
    contents = path.read_bytes()
    if sha256 is not None and digest(contents) != sha256:
        raise PublicationError('Artifact bytes differ from the recorded digest.')
    return contents


def write_artifact(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)


def scope_provenance(report, report_sha256, env):
    """Construct the versioned sidecar from fixed Actions step outputs and the verified report."""
    outcome = env.get('SCOPEHUB_SCOPE_OUTCOME')
    if outcome is None or report['ci']['event'] not in {'push', 'pull_request'}:
        return None  # Legacy, scheduled and manual runs have no selector observation.

    selected = env.get('SCOPEHUB_SCOPE_PROFILE')
    raw = env.get('SCOPEHUB_SCOPE_DECISION')
    if outcome != 'success' or not raw or selected not in {'full', *SCOPED_PROFILES}:
        decision = {
            'profile': 'full', 'base': None, 'checkout': None, 'change_count': None,
            'reason_code': 'selector_failed' if outcome == 'failure' else 'selector_output_unavailable',
        }
    else:
        if len(raw) > 1024:
            raise PublicationError('Verification scope decision exceeds the allowed size.')
        try:
            decision = evidence_json(raw.encode('utf-8'))
        except (ValueError, TypeError, RecursionError) as error:
            raise PublicationError('Invalid verification scope decision.') from error
        if not isinstance(decision, dict) or decision.keys() != SCOPE_FIELDS or decision['profile'] != selected:
            raise PublicationError('Invalid verification scope decision fields or profile.')

        base, checkout, count = (decision[key] for key in ('base', 'checkout', 'change_count'))
        if checkout is not None and (not isinstance(checkout, str) or not COMMIT_SHA.fullmatch(checkout)
                                     or checkout != report['ci']['checkout_sha']):
            raise PublicationError('Verification scope checkout differs from the hosted job.')
        if base is not None and (not isinstance(base, str) or not COMMIT_SHA.fullmatch(base)
                                 or base != env.get('SCOPEHUB_SCOPE_BASE') or checkout is None):
            raise PublicationError('Verification scope base differs from the hosted comparison.')
        if count is not None and (type(count) is not int or not 0 <= count <= 2**31 - 1 or base is None):
            raise PublicationError('Invalid verification scope change count.')

        reason = decision['reason_code']
        if selected in SCOPED_PROFILES:
            valid = reason == 'reviewed_changes' and count is not None and count > 0
        else:
            valid = ((reason == 'change_requires_full' and count is not None and count > 0)
                     or (reason == 'no_changes' and count == 0)
                     or (reason == 'comparison_unavailable' and count is None))
        if not valid:
            raise PublicationError('Invalid verification scope reason for the comparison.')

    selection = report.get('selection', {})
    if selection.get('profile') != decision['profile'] or selection.get('task_id') is not None:
        raise PublicationError('Verification scope differs from the executed harness selection.')
    return {'kind': 'scopehub-verification-scope-v1', **decision, 'report_sha256': report_sha256}


def stage_run_observation(root, reports, destination, run, result, source):
    check = result['id']
    relative = f'{run}/{check}.observation.json'
    manifest = checked_path(reports, relative)
    if check == EXTERNAL_CLIENT_CHECK and source != source_identity(root):
        raise PublicationError('External-client evidence does not match the current source inputs.')
    expected = result.get('observation')
    if expected is None:
        raise PublicationError('A passing session check needs a reported observation.')
    contents, observation = capture_run_observation(root, manifest, check, source)
    if expected != observation:
        raise PublicationError('Redacted session evidence differs from the reported observation.')
    write_artifact(destination / relative, contents)


def stage_field_observation(root, reports, destination, result):
    check = result['id']
    directory = 'observations/' + check
    manifest = directory + '/observation.json'
    expected = result['field_observation']
    contents = bound_bytes(checked_path(reports, manifest), expected['observation_sha256'])
    for reference in hosted_field_record(evidence_json(contents)):
        relative = directory + '/' + reference['path']
        copy_file(checked_path(reports, relative), destination / relative, sha256=reference['sha256'])
    (destination / manifest).write_bytes(contents)
    if expected != field_observation(root, check, destination, hosted=True):
        raise PublicationError('Redacted field evidence differs from the reported observation.')


def stage_candidate(reports, destination, run, report, policy, *, expected):
    candidate = run + '/candidate'
    manifest_path = candidate + '/build-manifest.json'
    source = report['source']
    try:
        contents = bound_bytes(checked_path(reports, manifest_path), expected['manifest_sha256'] if expected else None)
        manifest = evidence_json(contents)
        refs = hosted_candidate_manifest(manifest, source, require_owner=expected is not None)
        installer = manifest['installer']
        if expected is not None and installer['sha256'] != expected['installer_sha256']:
            raise PublicationError('Candidate installer differs from the checked release.')
        relative = candidate + '/' + installer['path']
        copy_file(checked_path(reports, relative), destination / relative, sha256=installer['sha256'])
    except (OSError, ValueError, TypeError, KeyError):
        if expected is not None:
            raise
        print('No verified installer and redacted manifest were available for the failed candidate.')
        return

    complete = True
    for kind, reference in refs[1:]:
        relative = candidate + '/' + reference['path']
        try:
            data = bound_bytes(checked_path(reports, relative), reference['sha256'])
            if kind == 'notice':
                validate_notice(data)
            else:
                hosted_candidate_observation(evidence_json(data), kind, source, installer['sha256'])
        except (OSError, ValueError, TypeError, KeyError):
            if expected is not None:
                raise
            print(f'Omitted unqualified candidate {kind} evidence.')
            complete = False
            continue
        write_artifact(destination / relative, data)

    if complete:
        (destination / manifest_path).write_bytes(contents)
    if expected is not None:
        # The downloaded layout must still pass the same validation as the original run.
        release_artifacts(destination / run, source, policy, expected=expected, hosted=True)


def python_producer_report_path(run, lane):
    return f'{run}/{windows_python.part_id(lane)}.producer.json'


def windows_producer_report_path(run, lane):
    return f'{run}/windows-{lane}.producer.json'


def validate_original_producer(root, project, reports, report, rows, lane, selected, relative, sha256, destination,
                               *, part=None, recovery_overlap=False):
    contents = bound_bytes(checked_path(reports, relative), sha256)
    producer = evidence_json(contents)
    validate_model(root, 'HostedHarnessReport', producer)
    parts = {lane: {key: value for key, value in part.items() if key != PRODUCER_REPORT_DIGEST}} if part else {}
    version = windows_python.REPORT_VERSION if part else '2.0'
    if (producer['run_id'] != report['run_id'] or producer['report_version'] != version
            or producer['environment']['python_executable'] != report['environment']['python_executable']
            or producer.get('windows_python', {}) != parts or WINDOWS_PRODUCERS in producer):
        raise PublicationError(f'Windows {lane} producer report differs from the run, attempt, role or part evidence.')
    try:
        validate_windows_lane(project, producer, report['source'], report['ci'], selected, lane, f'ci-windows-{lane}',
                              recovery_overlap=recovery_overlap)
    except (KeyError, PublicationError) as error:
        raise PublicationError(f'Invalid original Windows {lane} producer report: {error}') from error
    for row in producer['checks']:
        key = row['id']
        if key not in selected or project['checks'][key]['status'] != 'implemented':
            continue
        if row != rows[key]:
            raise PublicationError(f'The Windows {key} result differs from its original {lane} producer report.')
        if destination is None and 'log' in row:
            log = f"runs/{report['run_id']}/{key}.log"
            if (row['log'] != '.reports/' + log or file_digest(checked_path(reports, log)) != row.get('log_sha256')):
                raise PublicationError(f'The Windows {key} log differs from its original {lane} producer report.')
    if part and 'log' in part:
        validate_python_process_clock(part, producer)
    if destination is not None:
        write_artifact(destination / relative, contents)


def validate_python_process_clock(record, report):
    try:
        start, end = windows_python.process_interval(record)
        finish = datetime.fromisoformat(report['generated_at'])
        span = duration(report['timing']['elapsed_seconds'])
        if span is None or finish.utcoffset() is None:
            raise ValueError('Invalid producer report clock.')
        rounding = windows_python.CLOCK_ROUNDING
        if ((finish - start).total_seconds() > span + rounding
                or (end - finish).total_seconds() > rounding):
            raise ValueError('Windows Python process clock is outside its harness interval.')
    except (ValueError, TypeError, KeyError) as error:
        raise PublicationError(str(error)) from error


def python_parts(reports, report, lanes, destination=None, *, callback_records=None, cutoff=None):
    """Validate and optionally retain native part evidence from one authenticated report."""
    records = report.get('windows_python', {})
    if set(records) != set(lanes):
        raise PublicationError('Missing or unexpected Windows Python parts.')
    if report['report_version'] != (windows_python.REPORT_VERSION if lanes else '2.0'):
        raise PublicationError('The harness report version differs from its Windows Python evidence.')
    if not RUN_ID.fullmatch(report['run_id']):
        raise PublicationError('Invalid verification run identifier.')
    parts = []
    run = f"runs/{report['run_id']}"
    for lane in lanes:
        record = records[lane]
        label = windows_python.part_id(lane)
        command = windows_python.command(report['environment']['python_executable'], lane)
        if record['id'] != label or record['command'] != command:
            raise PublicationError('The Windows Python part command or identity differs from its producer.')
        log = f'{run}/{label}.log'
        manifest = f'{run}/{label}.json'
        has_log = 'log' in record
        passed = record['status'] == 'passed'
        if has_log and record['log'] != '.reports/' + log:
            raise PublicationError('The Windows Python part log is misplaced.')
        if has_log and ('log_sha256' not in record or 'started_at' not in record
                        or duration(record.get('duration_seconds')) is None
                        or 'exit_code' not in record):
            raise PublicationError('Incomplete Windows Python process evidence.')
        if not has_log and (record.get('reason_code') not in {'earlier_failure', 'executable_unavailable'}
                            or {'log_sha256', 'started_at', 'duration_seconds', 'exit_code'}.intersection(record)):
            raise PublicationError('Missing Windows Python process log without a blocked launch.')
        if passed and (not has_log or record.get('exit_code') != 0 or record.get('reason_code')
                       or 'manifest_sha256' not in record):
            raise PublicationError('Windows Python part claims a pass without complete successful evidence.')
        if not passed and record.get('exit_code') == 0 and not record.get('reason_code'):
            raise PublicationError('Windows Python part failure has no corresponding process result.')
        if has_log:
            validate_python_process_clock(record, report)
        raw = bound_bytes(checked_path(reports, log), record['log_sha256']) if has_log else b''
        contents = manifest_value = None
        if 'manifest_sha256' in record:
            if not has_log:
                raise PublicationError('Windows Python case manifest has no process log.')
            contents = bound_bytes(checked_path(reports, manifest), record['manifest_sha256'])
            try:
                manifest_value = evidence_json(contents)
                windows_python.verify_original(record, raw, manifest_value, lane, callback_records=callback_records,
                                               cutoff=cutoff)
            except (TypeError, ValueError, KeyError) as error:
                raise PublicationError(str(error)) from error
        if report['status'] == 'passed' and not passed:
            raise PublicationError('A successful Windows report contains a failed Python part.')
        if destination is not None:
            if has_log:
                write_artifact(destination / log, raw)
            if contents is not None:
                write_artifact(destination / manifest, contents)
        parts.append((record, raw, manifest_value))
    return parts


def combined_python(project, report, parts, *, cutoff=None):
    try:
        status, raw = windows_python.combine(parts, cutoff=cutoff)
        elapsed = windows_python.elapsed_seconds(parts)
    except (ValueError, KeyError, TypeError) as error:
        raise PublicationError(str(error)) from error
    python = report['environment']['python_executable']
    check = project['checks'][windows_python.CHECK]
    command = [python if arg == '{python}' else arg for arg in check['command']]
    expired = status == 'passed' and elapsed > check_timeout(check, 'Windows')
    if expired:
        status = 'failed'
        raw += b'\nThe combined Windows Python check exceeded its timeout.\n'
    record = {'id': windows_python.CHECK, 'status': status, 'command': command,
              'exit_code': 0, 'duration_seconds': elapsed,
              'log': f".reports/runs/{report['run_id']}/{windows_python.CHECK}.log",
              'log_sha256': digest(raw)}
    if expired:
        record.update(exit_code=None, reason_code='timeout')
    elif status == 'failed':
        failed = next(part for part, _, _ in parts if part['status'] != 'passed')
        record.update(exit_code=failed.get('exit_code', 1))
        if 'reason_code' in failed:
            record['reason_code'] = failed['reason_code']
    return record, raw


def validate_combined_python(root, project, reports, report, rows, assignments, runner, producer_reports, destination,
                             *, recovery_overlap):
    cutoff = runner_partition_cutoff(runner)
    parts = python_parts(reports, report, windows_python.LANES, destination,
                         callback_records=runner_uses_callback_records(runner), cutoff=cutoff)
    run = f"runs/{report['run_id']}"
    for lane, (record, _, _) in zip(windows_python.LANES, parts, strict=True):
        if (PRODUCER_REPORT_DIGEST in record) != producer_reports:
            raise PublicationError('Missing or unexpected original Windows Python producer report.')
        if producer_reports:
            validate_original_producer(root, project, reports, report, rows, lane, assignments[lane],
                                       python_producer_report_path(run, lane), record[PRODUCER_REPORT_DIGEST],
                                       destination, part=record, recovery_overlap=recovery_overlap)
    check, _ = combined_python(project, report, parts, cutoff=cutoff)
    if rows[windows_python.CHECK] != check:
        raise PublicationError('The Windows Python check differs from its original part results.')


def validate_windows_run_id(report):
    ci = report['ci']
    if report['run_id'] != windows_lanes_run_id(ci):
        raise PublicationError('The Windows aggregate run identifier differs from its CI run or attempt.')
    if (ci['event'] not in {'push', 'pull_request'}
            or (ci['event'] == 'push' and ci['head_sha'] != ci['checkout_sha'])):
        raise PublicationError('The Windows aggregate event differs from its CI checkout.')


def source_uses_windows_aggregate(source):
    workflow = source / '.github/workflows/verify.yml'
    if not workflow.is_file():
        return False
    import yaml

    try:
        steps = yaml.safe_load(workflow.read_text(encoding='utf-8'))['jobs']['windows_change']['steps']
    except KeyError:
        return False
    except (TypeError, yaml.YAMLError) as error:
        raise PublicationError('Cannot read the recorded source Windows verification workflow.') from error
    aggregate = {'scripts/check.py', '--require-windows', '--ci-windows-aggregate'}
    return any(isinstance(step.get('run'), str) and aggregate.issubset(step['run'].split()) for step in steps)


def source_uses_windows_python_parts(source):
    return (source / 'scripts/windows_python.py').is_file() and source_uses_windows_aggregate(source)


def source_producer_bindings(source):
    # Select the bindings from the tested source rather than from the submitted report.
    definitions = read_json(source / 'contracts/schemas/project.schema.json')['$defs']
    properties = definitions.get('HostedHarnessReport', {}).get('properties', {})
    parts = properties.get('windows_python', {}).get('additionalProperties', {}).get('properties', {})
    return WINDOWS_PRODUCERS in properties, PRODUCER_REPORT_DIGEST in parts


def committed_windows_python(source):
    return (source / 'scripts/windows_python.py').read_bytes()


def runner_uses_callback_records(runner):
    # The committed runner selects the manifest format, even if an edited
    # manifest removes the version and still matches its mutable report digest.
    return re.search(rb'^CALLBACK_RECORD_VERSION[ \t]*=', runner, re.MULTILINE) is not None


def runner_partition_cutoff(runner):
    cutoffs = re.findall(rb"^CUTOFF[ \t]*=[ \t]*'([^'\r\n]+)'[ \t]*\r?$", runner, re.MULTILINE)
    if len(cutoffs) != 1 or cutoffs[0] not in {
            value.encode('ascii') for value in (windows_python.CUTOFF, windows_python.LEGACY_CUTOFF)}:
        raise PublicationError('Unrecognized Windows Python partition in the recorded source.')
    return cutoffs[0].decode('ascii')


def runner_recovery_checks(runner):
    if runner is None:
        return set()
    try:
        declarations = [node.value for node in ast.parse(runner).body
                        if isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'RECOVERY_PR_CHECKS']
        if declarations:
            declaration, = declarations
            checks = ast.literal_eval(declaration)
            if not isinstance(checks, set) or not all(isinstance(key, str) for key in checks):
                raise ValueError('Invalid recovery check selection.')
            return checks
    except (SyntaxError, TypeError, ValueError) as error:
        raise PublicationError('Unrecognized Windows PR recovery selection in the recorded source.') from error
    if re.search(rb'^RECOVERY_OWNS_MARKET_WORKFLOW[ \t]*=[ \t]*True[ \t]*\r?$', runner, re.MULTILINE):
        return {'market-workflow'}
    return set()


def validate_hosted_platform(report, platform):
    environment = report['environment']
    if platform and (environment['system'] != platform or (platform == 'Windows' and not windows_x64(environment))):
        raise PublicationError(f'The harness report platform differs from the expected {platform} platform.')


def downloaded_platform(artifact_root, reports_root, ci):
    # Authenticated downloads use .reports/ci/<run>/<platform>. Older, noncanonical locations
    # remain readable without making an independent job or platform claim.
    locations = ((Path(os.path.abspath(artifact_root)), reports_root), (artifact_root.resolve(), reports_root.resolve()))
    platforms = set()
    for path, parent in locations:
        if path.is_relative_to(parent):
            parts = path.relative_to(parent).parts
            if len(parts) == 3 and parts[0].casefold() == 'ci' and parts[2].casefold() in DOWNLOAD_PLATFORMS:
                if parts[1] != ci['run_id']:
                    raise PublicationError('The report belongs to another downloaded CI run.')
                platforms.add(DOWNLOAD_PLATFORMS[parts[2].casefold()])
    if len(platforms) > 1:
        raise PublicationError('The downloaded artifact location claims conflicting platforms.')
    return next(iter(platforms), None)


def validate_recorded_windows_evidence(root, project, reports, report, *, platform=None, aggregate_profile=None,
                                       source=None, destination=None, lane_result=None):
    validate_hosted_platform(report, platform)
    ci = report.get('ci')
    lanes_run = report['run_id'].startswith(WINDOWS_LANES_RUN_PREFIX)
    if (aggregate_profile or lanes_run) and not ci:
        raise PublicationError('A Windows aggregate requires CI provenance.')
    profile = report.get('selection', {}).get('profile')
    if aggregate_profile:
        validate_hosted_platform(report, 'Windows')
        if profile != aggregate_profile:
            raise PublicationError('The Windows job has another aggregate selection.')
    if (aggregate_profile or lanes_run) and profile in SCOPED_PROFILES:
        validate_model(root, 'HostedHarnessReport', report)
        if lanes_run:
            validate_windows_run_id(report)
        check_scoped_windows_aggregate(project, report, profile, source=source)
        return
    if aggregate_profile or lanes_run:
        validate_model(root, 'HostedHarnessReport', report)
        validate_windows_run_id(report)
        commit = ci['checkout_sha'] if source is not None else ci['head_sha']
        validate_windows_aggregate(root, project, reports, report, source or report['source'], commit,
                                   destination, lane_result=lane_result)
        return
    if (report['report_version'] != '2.0' or 'windows_python' in report or WINDOWS_PRODUCERS in report):
        raise PublicationError('The recorded source and run do not schedule Windows producer bindings.')
    if ci and windows_x64(report['environment']) and ci['event'] in {'push', 'pull_request'}:
        committed = CommittedPath.at(root, ci['head_sha'])
        producer_reports, _ = source_producer_bindings(committed)
        source_aggregate = source_uses_windows_aggregate(committed)
        if (producer_reports or source_aggregate) and profile in SCOPED_PROFILES:
            check_scoped_windows_aggregate(project, report, profile)
        elif producer_reports or source_aggregate:
            raise PublicationError('The recorded Windows run lacks its aggregate identifier.')


def check_scoped_windows_aggregate(project, report, profile, *, source=None):
    if report['report_version'] != '2.0' or 'windows_python' in report or WINDOWS_PRODUCERS in report:
        raise PublicationError('A scoped Windows run cannot declare full producer bindings.')
    selected = select_checks(project, profile=profile)
    validate_windows_lane(project, report, source or report['source'], report['ci'], selected, 'core', profile)


def committed_lane_ownership(source):
    path = source / 'scripts/check.py'
    if not path.is_file():
        return None
    # Read the scheduler declarations without executing historical source.
    try:
        declarations = {
            node.targets[0].id: node.value
            for node in ast.parse(path.read_text(encoding='utf-8')).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        }
        lanes = declarations.get('WINDOWS_LANE_CHECKS')
        if lanes is None:
            ownership = {
                'browser': ast.literal_eval(declarations['WINDOWS_BROWSER_CHECKS']),
                'python': ast.literal_eval(declarations['WINDOWS_PYTHON_CHECKS'])
                if 'WINDOWS_PYTHON_CHECKS' in declarations else {windows_python.CHECK},
            }
        else:
            ownership = {
                ast.literal_eval(key): ast.literal_eval(declarations[value.id] if isinstance(value, ast.Name) else value)
                for key, value in zip(lanes.keys, lanes.values, strict=True)
            }
        if (not {'browser', 'python'} <= ownership.keys() <= set(WINDOWS_LANES) - {'core'}
                or any(not isinstance(checks, set) for checks in ownership.values())
                or len(set().union(*ownership.values())) != sum(len(checks) for checks in ownership.values())):
            raise ValueError('Invalid Windows lane ownership.')
        return ownership
    except (SyntaxError, AttributeError, KeyError, TypeError, ValueError) as error:
        raise PublicationError('Unrecognized Windows lane ownership in the recorded source.') from error


def windows_assignments(source, selected, *, split, recovery_checks):
    ownership = committed_lane_ownership(source)
    return {lane: windows_lane_checks(
        selected, lane, python_split=split, recovery_checks=recovery_checks, ownership=ownership,
    ) for lane in WINDOWS_LANES}


def ordinary_producer_lanes(assignments, split, retain):
    lanes = ('browser',) if split else ('browser', 'recovery')
    return {lane for lane in lanes if retain and assignments[lane]}


def validate_windows_aggregate(root, project, reports, report, source, source_commit, destination=None,
                               *, lane_result=None):
    selected = select_checks(project)
    ci = report['ci']
    committed = CommittedPath.at(root, source_commit)
    split = (windows_python.enabled(project, ci, selected)
             and source_uses_windows_python_parts(committed))
    retain, part_reports = source_producer_bindings(committed)
    runner = committed_windows_python(committed) if split else None
    recovery_checks = runner_recovery_checks(runner)
    assignments = windows_assignments(committed, selected, split=split, recovery_checks=recovery_checks)
    rows = {row['id']: row for row in report['checks']}
    native = rows.get('windows-native-build', {})
    native_ok = native.get('status') == 'passed' and native.get('exit_code') == 0
    validate_windows_lane(project, report, source, ci, selected, 'aggregate', 'full',
                          native_requirement={'requested': True, 'satisfied': native_ok})
    recovery_overlap = bool(recovery_checks)
    for lane, lane_selection in assignments.items():
        validate_producer_outcomes(report, lane_selection, lane, recovery_overlap=recovery_overlap)
    required = project['catalog']['required_change_checks']
    unverified = [key for key in required if rows[key]['status'] != 'passed' or rows[key].get('exit_code') != 0]
    expected_gates = {'requested': True, 'required': required, 'unverified': unverified,
                      'status': 'blocked' if unverified else 'passed'}
    actual_selected = [rows[key]['status'] for key in selected if project['checks'][key]['status'] == 'implemented']
    lane_failed = (lane_result != 'success' if lane_result is not None
                   else 'At least one Windows lane job did not succeed.' in report.get('limitations', ()))
    failed = 'failed' in actual_selected or lane_failed
    blocked = unverified or not native_ok or 'blocked' in actual_selected
    verdict = 'failed' if failed else 'blocked' if blocked else 'passed'
    if report['status'] != verdict or report.get('integration_gates') != expected_gates:
        raise PublicationError('The Windows aggregate verdict differs from its selected check results.')
    if split:
        validate_combined_python(root, project, reports, report, rows, assignments, runner, part_reports, destination,
                                 recovery_overlap=recovery_overlap)
    else:
        python_parts(reports, report, ())

    original_reports = report.get(WINDOWS_PRODUCERS, {})
    if set(original_reports) != ordinary_producer_lanes(assignments, split, retain):
        raise PublicationError('Missing or unexpected original Windows ordinary producer report.')
    run = f"runs/{report['run_id']}"
    for lane in original_reports:
        validate_original_producer(root, project, reports, report, rows, lane, assignments[lane],
                                   windows_producer_report_path(run, lane), original_reports[lane], destination)


def stage_artifacts(root, destination, env, *, reports=None, lane=None, collection_profile=None,
                    project=None, source=None, assignments=None, runner=None):
    reports = root / '.reports' if reports is None else reports
    latest = optional_path(reports, 'test-report.json')
    if latest is None:
        return False
    contents = latest.read_bytes()
    report = evidence_json(contents)
    validate_model(root, 'HostedHarnessReport', report)
    expected = ci_provenance(report['source'], env, root)
    if not expected or report.get('ci') != expected or expected['checkout_sha'] != env['GITHUB_SHA']:
        raise PublicationError('Report provenance does not match the current hosted job.')
    run_id = report['run_id']
    if not RUN_ID.fullmatch(run_id):
        raise PublicationError('Invalid verification run identifier.')
    run = f'runs/{run_id}'
    named_report = checked_path(reports, run + '/test-report.json')
    if named_report.read_bytes() != contents:
        raise PublicationError('The latest and per-run harness reports differ.')

    job = env.get('GITHUB_JOB')
    hosted_aggregate = not lane and (job == 'windows_change' or (
        not job and 'SCOPEHUB_WINDOWS_LANE_RESULT' in env))
    platform = 'Windows' if lane or hosted_aggregate or job in WINDOWS_PRODUCER_JOBS else env.get('RUNNER_OS')
    validate_hosted_platform(report, platform)
    scope = None if lane else scope_provenance(report, digest(contents), env)
    if project is None:
        project = load_project(root)
    destination.mkdir()
    checkout = expected['checkout_sha']
    if lane and not collection_profile:
        validate_windows_run_id(report)
        if assignments is None:
            selected = select_checks(project)
            committed = CommittedPath.at(root, checkout)
            split = (windows_python.enabled(project, expected, selected)
                     and source_uses_windows_python_parts(committed))
            runner = committed_windows_python(committed) if split else None
            recovery_checks = runner_recovery_checks(runner)
            assignments = windows_assignments(committed, selected, split=split, recovery_checks=recovery_checks)
        else:
            recovery_checks = runner_recovery_checks(runner)
        lane_selection = assignments[lane]
        profile = f'ci-windows-{lane}'
        recovery_overlap = bool(recovery_checks)
        identity_failure = not report['source_unchanged'] or not report['project_unchanged']
        if source is None and identity_failure:
            # Retain a finalized diagnostic for the failed producer. Collection
            # still requires every producer to match the aggregate's inputs.
            if report['selection'] != {'profile': profile, 'task_id': None, 'checks': sorted(lane_selection)}:
                raise PublicationError(f'Windows {lane} lane has a different selection.')
            validate_producer_outcomes(report, lane_selection, lane, recovery_overlap=recovery_overlap)
            validate_lane_verdict(project, report, lane_selection, lane, profile)
        else:
            validate_windows_lane(project, report, source or source_identity(root), expected, lane_selection, lane, profile,
                                  recovery_overlap=recovery_overlap)
        if WINDOWS_PRODUCERS in report:
            raise PublicationError('A Windows lane cannot declare its own aggregate binding.')
        required_parts = (lane,) if runner is not None and lane in windows_python.LANES else ()
        parts = python_parts(reports, report, required_parts, destination,
                             callback_records=runner_uses_callback_records(runner) if required_parts else None,
                             cutoff=runner_partition_cutoff(runner) if required_parts else None)
        if any(PRODUCER_REPORT_DIGEST in record for record, _, _ in parts):
            raise PublicationError('A Windows Python lane cannot declare its own aggregate binding.')
    elif hosted_aggregate:
        source = source_identity(root)
        _, head, _ = ci_event_checkout(root, source['git_head'], env)
        if expected['head_sha'] != head or checkout != source['git_head']:
            raise PublicationError('The Windows aggregate has another event checkout.')
        validate_recorded_windows_evidence(
            root, project, reports, report, aggregate_profile=scope['profile'] if scope else 'full',
            source=source, destination=destination, lane_result=env.get('SCOPEHUB_WINDOWS_LANE_RESULT'),
        )
    elif collection_profile or job in WINDOWS_PRODUCER_JOBS:
        profile = collection_profile or (scope and scope['profile'])
        if (lane or WINDOWS_PRODUCER_JOBS[job]) != 'core' or profile not in SCOPED_PROFILES:
            raise PublicationError('The Windows producer job requires its configured lane or scoped selection.')
        if report['selection']['profile'] != profile:
            raise PublicationError('The Windows core producer has a different scoped selection.')
        check_scoped_windows_aggregate(project, report, profile, source=source or source_identity(root))
    else:
        validate_recorded_windows_evidence(root, project, reports, report, destination=destination)
    executed = {}
    # The hosted producer logs synthetic inputs and redacted outcomes. The catalog owns check identities.
    for result in report['checks']:
        check = result['id']
        passed = result['status'] == 'passed'
        if (('observation' in result and (check not in RUN_OBSERVATION_CHECKS or not passed))
                or ('field_observation' in result and (check not in FIELD_ACTIONS or not passed))):
            raise PublicationError('The check has an unexpected observation.')
        if 'log' not in result:
            continue
        if project['checks'].get(check, {}).get('status') != 'implemented' or check in executed:
            raise PublicationError('A check log is absent from the active catalog or duplicated.')
        relative = f'{run}/{check}.log'
        if result['log'] != '.reports/' + relative:
            raise PublicationError('Check log is outside the current verification run.')
        log = checked_path(reports, relative)
        if check in PRIVATE_CHECK_LOGS:
            data = bound_bytes(log, result['log_sha256'])
            if data != redacted_check_log(result['exit_code']):
                raise PublicationError('A private check log contains more than its redacted exit status.')
            write_artifact(destination / relative, data)
        else:
            copy_file(log, destination / relative, sha256=result['log_sha256'])
        executed[check] = result['status']
        if passed:
            if check in RUN_OBSERVATION_CHECKS:
                stage_run_observation(root, reports, destination, run, result, report['source'])
            elif check in FIELD_ACTIONS:
                try:
                    stage_field_observation(root, reports, destination, result)
                except LocalFieldObservation:
                    print(f'Omitted local-only field observation: {check}.')
    if 'integration_validation' in report and report['integration_validation'] != integration_validation(report['checks']):
        raise PublicationError('The integration summary differs from the checked observations.')

    browser_roots = {'chart-history': f'chart-history/{run}', 'ubuntu-preview': f'ubuntu-preview/{run_id}'}
    for check in BROWSER_OUTPUTS:
        if check in executed:
            directory = browser_roots.get(check, f'{run}/{check}')
            for path in browser_artifacts(reports, directory, check):
                copy_file(path, destination / path.relative_to(reports))
    if 'chart-history' in executed:
        relative = browser_roots['chart-history']
        summary = optional_path(reports, relative + '/summary.json')
        if summary is not None:
            copy_file(summary, destination / relative / 'summary.json')
            copy_file(destination / relative / 'summary.json', destination / 'chart-history/latest.json')
    candidate = report.get('candidate_artifacts')
    release_check_passed = executed.get('release-artifacts') == 'passed'
    if (candidate is not None and not isinstance(candidate, dict)) or release_check_passed != (candidate is not None):
        raise PublicationError('Candidate acceptance evidence must match the release artifact check outcome.')
    attempted_release = (report.get('selection', {}).get('profile') == 'release'
                         and report['environment']['system'] == 'Windows'
                         and report['status'] in {'failed', 'blocked'}
                         and {'windows-installation', 'runtime-stability'}.intersection(executed))
    if 'release-artifacts' in executed or attempted_release:
        stage_candidate(reports, destination, run, report, project['catalog']['desktop_performance'],
                        expected=candidate)

    if scope is not None:
        write_artifact(destination / SCOPE_FILE, json_bytes(scope))
    (destination / 'test-report.json').write_bytes(contents)
    (destination / run / 'test-report.json').parent.mkdir(parents=True, exist_ok=True)
    (destination / run / 'test-report.json').write_bytes(contents)
    return True


def validate_windows_lane(project, report, source, ci, selected, lane, profile, *, native_requirement=None,
                          recovery_overlap=False):
    check_ids = [check['id'] for check in project['catalog']['checks']]
    if (report['ci'] != ci or report['source'] != source or report['source_end'] != source
            or not report['source_unchanged'] or not report['project_unchanged']
            or report['project_definition_sha256'] != project_definition_hash(project)
            or report['task_definitions'] != {key: task_definition_hash(task) for key, task in project['tasks'].items()}
            or not windows_x64(report['environment'])):
        raise PublicationError(f'Windows {lane} lane has another checkout, run, platform or project definition.')
    native = native_requirement or {'requested': False, 'satisfied': True}
    if (report['selection'] != {'profile': profile, 'task_id': None, 'checks': sorted(selected)}
            or [row['id'] for row in report['checks']] != check_ids
            or report['native_requirement'] != native):
        raise PublicationError(f'Windows {lane} lane has a different selection or catalog order.')
    python = report['environment']['python_executable']
    for row in report['checks']:
        key = row['id']
        check = project['checks'][key]
        command = [python if arg == '{python}' else arg for arg in check['command'] or []]
        if row['command'] != command:
            raise PublicationError(f'Windows {lane} lane changed the command for {key}.')
        if key not in selected or check['status'] != 'implemented':
            reasons = {'not_selected'} if key not in selected else {
                'not_implemented' if check['status'] == 'planned' else 'retired', 'earlier_failure',
            }
            if (row['status'] != 'blocked' or row.get('reason_code') not in reasons
                    or {'log', 'log_sha256', 'exit_code'}.intersection(row)):
                raise PublicationError(f'Windows {lane} lane has an unexpected result for {key}.')
        elif row.get('reason_code') in {'not_selected', 'not_implemented', 'retired'}:
            raise PublicationError(f'Windows {lane} lane has an unexpected omission for selected check {key}.')
        elif row['status'] == 'blocked' and 'log' not in row:
            if (row.get('reason_code') not in {'earlier_failure', 'executable_unavailable'}
                    or {'log_sha256', 'exit_code', 'duration_seconds'}.intersection(row)):
                raise PublicationError(f'Windows {lane} lane has an unexpected blocked result for {key}.')
        elif row['status'] == 'passed' and (row.get('reason_code') or duration(row.get('duration_seconds')) is None):
            raise PublicationError(f'Windows {lane} lane has an incomplete passing result for {key}.')
    if profile != 'full':
        validate_producer_outcomes(report, selected, lane, recovery_overlap=recovery_overlap)
        validate_lane_verdict(project, report, selected, lane, profile)


def producer_stopped(row):
    if not stops_after_check(row):
        return False
    if row['status'] == 'blocked' and row.get('reason_code') == 'executable_unavailable' and 'log' not in row:
        return True
    if row['status'] != 'failed' or not {'log', 'log_sha256', 'exit_code', 'duration_seconds'} <= row.keys():
        raise PublicationError(f"Windows producer stop for {row['id']} has no executed process evidence.")
    return True


def validate_producer_outcomes(report, selected, lane, *, recovery_overlap=False):
    """An executed producer may omit only a suffix after a stop in that producer."""
    part = report.get('windows_python', {}).get(lane)
    parallel_stop = (recovery_overlap and lane == 'recovery' and part
                     and part.get('reason') != PYTHON_MANIFEST_INTERRUPTED and producer_stopped(part))
    stopped = False
    for row in report['checks']:
        key = row['id']
        if key not in selected:
            continue
        if row.get('reason_code') == 'earlier_failure':
            if (row['status'] != 'blocked' or {'log', 'log_sha256', 'exit_code', 'duration_seconds'}.intersection(row)
                    or not (stopped or parallel_stop)):
                raise PublicationError(f'Windows {lane} has earlier_failure for {key} without a producer stop.')
            stopped = True
        elif stopped:
            raise PublicationError(f'Windows {lane} has a result for {key} after its producer stopped.')
        else:
            stopped = producer_stopped(row)
    if part and part.get('reason_code') == 'earlier_failure' and (not stopped or 'log' in part):
        raise PublicationError(f'Windows {lane} Python part has earlier_failure without a producer stop.')


def validate_lane_verdict(project, report, selected, lane, profile):
    statuses = {row['status'] for row in report['checks'] if row['id'] in selected
                and (profile in SCOPED_PROFILES or project['checks'][row['id']]['status'] == 'implemented')}
    if (not report['source_unchanged'] or not report['project_unchanged']
            or any(part['status'] != 'passed' for part in report.get('windows_python', {}).values())):
        statuses.add('failed')
    expected = 'failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses else 'passed'
    if report['status'] != expected:
        raise PublicationError(f'Windows {lane} producer verdict differs from its selected check results and parts.')


def matching_lane_timing(incoming, report):
    timing_file = optional_path(incoming, TIMING_FILE)
    if not timing_file:
        return {}
    try:
        timing = evidence_json(timing_file.read_bytes())
        if not isinstance(timing, dict) or not isinstance(timing.get('cache'), str):
            return {}
        sample = report_sample(report, cache=timing['cache'])
        # Require every projected field and preserve JSON types, including false versus zero.
        projected = {key: timing[key] for key in sample}
        if json.dumps(projected, sort_keys=True, allow_nan=False) == json.dumps(sample, sort_keys=True):
            return timing
    except (OSError, KeyError, ValueError, RecursionError):
        return {}
    return {}


def collect_windows_lanes(root, downloads, project, source, ci, selected, env, *, profile=None):
    """Recheck the event checkout and import only current, digest-checked lane outputs."""
    checkout = source['git_head']
    _, head, _ = ci_event_checkout(root, checkout, env)
    if ci['head_sha'] != head:
        raise PublicationError('Windows lane provenance differs from the event head.')
    if profile:
        if profile not in SCOPED_PROFILES:
            raise PublicationError('Unrecognized scoped Windows aggregate profile.')
        base = ci_base(root, checkout, env)
        require_ancestor(root, base, checkout)
    names = ('core',) if profile else WINDOWS_LANES
    prefix = 'verification-windows-' + ci['run_attempt'] + '-'
    expected_names = {prefix + lane for lane in names}
    if not downloads.is_dir() or {path.name for path in downloads.iterdir()} != expected_names:
        raise PublicationError('Missing or unexpected Windows lane artifacts. Rerun all jobs for this attempt.')

    reports = root / '.reports'
    run_id = windows_lanes_run_id(ci)
    rows = {}
    committed = CommittedPath.at(root, checkout)
    partitioned = (not profile and windows_python.enabled(project, ci, selected)
                   and source_uses_windows_python_parts(committed))
    retain, retain_part_producers = source_producer_bindings(committed) if not profile else (False, False)
    runner = committed_windows_python(committed) if partitioned else None
    cutoff = runner_partition_cutoff(runner) if partitioned else None
    assignments = windows_assignments(committed, selected, split=partitioned, recovery_checks=runner_recovery_checks(runner))
    retained_lanes = ordinary_producer_lanes(assignments, partitioned, retain)
    started = []
    caches = {}
    producer_parts = {}
    producer_reports = {}
    with tempfile.TemporaryDirectory(prefix='scopehub-windows-', dir=env.get('RUNNER_TEMP')) as directory:
        for lane in names:
            incoming = checked_path(downloads, prefix + lane, directory=True)
            staged = Path(directory) / lane
            if not stage_artifacts(root, staged, env, reports=incoming, lane=lane, collection_profile=profile,
                                   project=project, source=source, assignments=assignments, runner=runner):
                raise PublicationError(f'Windows {lane} lane has no finalized report.')
            contents = (staged / 'test-report.json').read_bytes()
            report = evidence_json(contents)
            lane_selection = selected if profile else assignments[lane]
            if profile and report['status'] == 'passed' and env.get('SCOPEHUB_WINDOWS_LANE_RESULT') != 'success':
                raise PublicationError('The scoped Windows lane job did not succeed.')
            if lane == 'core':
                core = report
            if not profile:
                if (report['run_id'] != run_id or report['environment']['python_executable'] !=
                        core['environment']['python_executable']):
                    raise PublicationError(f'Windows {lane} lane has another report identity or Python executable.')
                started.append(datetime.fromisoformat(report['generated_at']).timestamp() - report['timing']['elapsed_seconds'])
                rows.update((row['id'], row) for row in report['checks'] if lane == 'core' or row['id'] in lane_selection)
                if lane in retained_lanes:
                    write_artifact(reports / windows_producer_report_path(f'runs/{run_id}', lane), contents)
                    producer_reports[lane] = digest(contents)
                if partitioned and lane in windows_python.LANES:
                    (record, raw, manifest), = python_parts(staged, report, (lane,), cutoff=cutoff)
                    if retain_part_producers:
                        write_artifact(reports / python_producer_report_path(f'runs/{run_id}', lane), contents)
                        record = {**record, PRODUCER_REPORT_DIGEST: digest(contents)}
                    producer_parts[lane] = record, raw, manifest
            for file in staged.rglob('*'):
                if file.is_file() and (profile or file.name != 'test-report.json'):
                    relative = file.relative_to(staged)
                    if (reports / relative).exists():
                        raise PublicationError('Windows lane evidence paths overlap.')
                    copy_file(file, reports / relative)
            timing = matching_lane_timing(incoming, report)
            caches[lane] = timing.get('cache', 'unknown')
            if lane == 'core' and (generator := timing.get('generated_contracts')):
                write_artifact(reports / WINDOWS_GENERATOR_SAMPLE, json_bytes(generator))
    if output := env.get('GITHUB_OUTPUT'):
        with Path(output).open('a', encoding='utf-8') as stream:
            cache = caches['core'] if profile else windows_aggregate_cache(caches)
            stream.write(f'cache={cache}\n')
            if not profile:
                stream.write(f'windows_caches={json.dumps(caches, sort_keys=True)}\n')
    if profile:
        return core, 0
    if producer_reports:
        core[WINDOWS_PRODUCERS] = producer_reports
    if partitioned:
        parts = [producer_parts[lane] for lane in windows_python.LANES]
        core['windows_python'] = {lane: part[0] for lane, part in zip(windows_python.LANES, parts, strict=True)}
        python_result, raw = combined_python(project, core, parts, cutoff=cutoff)
        write_artifact(root / python_result['log'], raw)
        rows[windows_python.CHECK] = python_result
    core['checks'] = [rows[check['id']] for check in project['catalog']['checks']]
    (reports / 'runs' / run_id).mkdir(parents=True, exist_ok=True)
    return core, max(0, datetime.now(timezone.utc).timestamp() - min(started))


def stage_timing(destination, env, *, has_report):
    if has_report:
        report = evidence_json((destination / 'test-report.json').read_bytes())
    else:
        report = {
            'environment': {'system': env.get('RUNNER_OS'), 'machine': env.get('RUNNER_ARCH')},
            'ci': {
                'repository': env.get('GITHUB_REPOSITORY'), 'run_id': env.get('GITHUB_RUN_ID'),
                'run_attempt': env.get('GITHUB_RUN_ATTEMPT'), 'event': env.get('GITHUB_EVENT_NAME'),
                'head_sha': env.get('SCOPEHUB_SOURCE_HEAD'), 'checkout_sha': env.get('GITHUB_SHA'),
            },
        }
    sample = report_sample(report, cache=env.get('SCOPEHUB_TIMING_CACHE'))
    if has_report:
        if raw_caches := env.get('SCOPEHUB_TIMING_WINDOWS_CACHES'):
            try:
                caches = json.loads(raw_caches)
                if windows_aggregate_cache(caches) != sample['cache']:
                    raise ValueError('Producer states differ from the aggregate cache state.')
            except ValueError as error:
                raise PublicationError('Invalid Windows cache observations.') from error
            sample['windows_caches'] = caches
        if 'SCOPEHUB_WINDOWS_LANE_RESULT' in env:
            observed = optional_path(ROOT / '.reports', WINDOWS_GENERATOR_SAMPLE)
            comparison = evidence_json(observed.read_bytes()) if observed else None
        else:
            comparison = generated_contracts_sample(report, ROOT, env)
        if comparison:
            sample['generated_contracts'] = comparison
    timing_path = destination.parent / TIMING_FILE
    timing_path.write_bytes(json_bytes(sample))
    if has_report:
        copy_file(timing_path, destination / TIMING_FILE)
    return timing_path, sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'stage', 'stage-windows-lane'])
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.exit(1, 'CI artifact handling requires a hosted verification job.\n')
    try:
        if args.action == 'prepare':
            prepare_reports(ROOT)
            return
        destination = Path(tempfile.mkdtemp(prefix='scopehub-ci-', dir=os.environ['RUNNER_TEMP'])) / 'verification'
        lane = None
        if args.action == 'stage-windows-lane':
            lane = WINDOWS_PRODUCER_JOBS.get(os.environ.get('GITHUB_JOB'))
            if lane is None:
                raise PublicationError('The current job is not a configured Windows producer.')
        has_report = stage_artifacts(ROOT, destination, os.environ, lane=lane)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            if has_report:
                output.write(f'path={destination}\n')
                output.flush()
            try:
                timing_path, sample = stage_timing(destination, os.environ, has_report=has_report)
                output.write(f'timing_path={timing_path}\n')
            except (OSError, ValueError, TypeError, KeyError) as error:
                print(f'::warning::Verification timing artifact unavailable ({type(error).__name__}).')
            else:
                if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
                    try:
                        with open(summary, 'a', encoding='utf-8') as summary_file:
                            summary_file.write(job_summary(sample))
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        print(f'::warning::Verification timing summary unavailable ({type(error).__name__}).')
        if not has_report:
            print('No finalized harness report is available for artifact publication.')
    except PublicationError as error:
        parser.exit(1, str(error) + '\n')
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Could not prepare CI evidence for publication ({type(error).__name__}).\n')


if __name__ == '__main__':
    main()
