"""Select publishable evidence from a fresh hosted verification run."""

import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile

if __package__:
    from .check import PRIVATE_CHECK_LOGS, ci_provenance, redacted_check_log
    from .project import ProjectError, digest, file_digest, json_bytes, load_project, validate_model
    from .timing import TIMING_FILE, generated_contracts_sample, job_summary, report_sample
    from .verification_evidence import (
        FIELD_ACTIONS, SESSION_CHECKS, LocalFieldObservation, collect_session, field_observation,
        hosted_artifact_path, hosted_candidate_manifest, hosted_candidate_observation, hosted_field_record,
        evidence_json, integration_validation, release_artifacts, validate_notice,
    )
    from .verification_scope import SCOPED_PROFILES
else:
    from check import PRIVATE_CHECK_LOGS, ci_provenance, redacted_check_log
    from project import ProjectError, digest, file_digest, json_bytes, load_project, validate_model
    from timing import TIMING_FILE, generated_contracts_sample, job_summary, report_sample
    from verification_evidence import (
        FIELD_ACTIONS, SESSION_CHECKS, LocalFieldObservation, collect_session, field_observation,
        hosted_artifact_path, hosted_candidate_manifest, hosted_candidate_observation, hosted_field_record,
        evidence_json, integration_validation, release_artifacts, validate_notice,
    )
    from verification_scope import SCOPED_PROFILES

ROOT = Path(__file__).resolve().parents[1]
SCOPE_FILE = 'verification-scope.json'
SCOPE_FIELDS = {'profile', 'reason_code', 'base', 'checkout', 'change_count'}
COMMIT_SHA = re.compile(r'(?!0{40})[0-9a-f]{40}')
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


def stage_session(reports, destination, run, result, source):
    check = result['id']
    relative = f'{run}/{check}.observation.json'
    expected = result['observation']
    contents = bound_bytes(checked_path(reports, relative), expected['evidence_sha256'])
    evidence_json(contents)
    write_artifact(destination / relative, contents)
    if expected != collect_session(destination / run, check, source):
        raise PublicationError('Redacted session evidence differs from the reported observation.')


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


def stage_artifacts(root, destination, env):
    reports = root / '.reports'
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
    if not re.fullmatch(r'[a-zA-Z0-9_-][a-zA-Z0-9._-]*', run_id):
        raise PublicationError('Invalid verification run identifier.')
    run = f'runs/{run_id}'
    named_report = checked_path(reports, run + '/test-report.json')
    if named_report.read_bytes() != contents:
        raise PublicationError('The latest and per-run harness reports differ.')

    scope = scope_provenance(report, digest(contents), env)
    project = load_project(root)
    destination.mkdir()
    executed = {}
    # The hosted producer logs synthetic inputs and redacted outcomes. The catalog owns check identities.
    for result in report['checks']:
        check = result['id']
        passed = result['status'] == 'passed'
        if (('observation' in result and (check not in SESSION_CHECKS or not passed))
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
            if check in SESSION_CHECKS:
                stage_session(reports, destination, run, result, report['source'])
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
    if 'chart-coordinate-prototype' in executed:
        chart_frames = ('draft', 'aligned', 'resized', 'clipped', 'refit', 'switched')
        chart_images = [frame + suffix + '.png' for frame in chart_frames for suffix in ('', '-client', '-screen')]
        hover_images = [stage + part + '.png' for stage in ('hover-narrow', 'hover-wide', 'hover-refit')
                        for part in ('-client', '-screen')]
        for name in (*chart_images, *hover_images, 'transition.png'):
            relative = f'{run}/native-chart/{name}'
            image = optional_path(reports, relative)
            if image is not None:
                copy_file(image, destination / relative)
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
    if has_report and (comparison := generated_contracts_sample(report, ROOT, env)):
        sample['generated_contracts'] = comparison
    timing_path = destination.parent / TIMING_FILE
    timing_path.write_bytes(json_bytes(sample))
    if has_report:
        copy_file(timing_path, destination / TIMING_FILE)
    return timing_path, sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'stage'])
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.exit(1, 'CI artifact handling requires a hosted verification job.\n')
    try:
        if args.action == 'prepare':
            prepare_reports(ROOT)
            return
        destination = Path(tempfile.mkdtemp(prefix='scopehub-ci-', dir=os.environ['RUNNER_TEMP'])) / 'verification'
        has_report = stage_artifacts(ROOT, destination, os.environ)
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
