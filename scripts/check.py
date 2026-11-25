"""Local verification entry point; report actual results and unverified gates."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
from threading import Event, Lock
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / '.reports'

if __package__:
    from .recovery import RECOVERY_COMMAND, capture_failure
else:
    from recovery import RECOVERY_COMMAND, capture_failure

try:
    if __package__:
        from .doctor import tool_version
        from .processes import log_tail, run_logged
        from .project import (
            ProjectError, checkout_git_environment, feedback_fingerprint, file_digest, load_project,
            local_feedback_history, product_progress, project_definition_hash, release_checks, source_identity,
            task_definition_hash, validate_model, verify_primary_model, windows_x64,
        )
        from .storage import maintain_automatically
        from .verification_evidence import (
            FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, collect_run_observation, field_observation,
            integration_validation, redacted_check_log, release_artifacts,
        )
    else:
        from doctor import tool_version
        from processes import log_tail, run_logged
        from project import (
            ProjectError, checkout_git_environment, feedback_fingerprint, file_digest, load_project,
            local_feedback_history, product_progress, project_definition_hash, release_checks, source_identity,
            task_definition_hash, validate_model, verify_primary_model, windows_x64,
        )
        from storage import maintain_automatically
        from verification_evidence import (
            FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, collect_run_observation, field_observation,
            integration_validation, redacted_check_log, release_artifacts,
        )
except Exception as error:
    if __name__ != '__main__':
        raise
    capture_failure(ROOT, error, 'imports', argv=sys.argv[1:])
    raise SystemExit(1) from error


def ci_provenance(source, env, root=ROOT):
    if env.get('GITHUB_ACTIONS') != 'true':
        return None
    repository = env.get('GITHUB_REPOSITORY', '')
    run_id = env.get('GITHUB_RUN_ID', '')
    record = {
        'provider': 'github-actions', 'repository': repository, 'run_id': run_id,
        'run_attempt': env.get('GITHUB_RUN_ATTEMPT', ''), 'event': env.get('GITHUB_EVENT_NAME', ''),
        'head_sha': env.get('SCOPEHUB_SOURCE_HEAD', ''), 'checkout_sha': source['git_head'],
        'run_url': f'https://github.com/{repository}/actions/runs/{run_id}',
    }
    validate_model(root, 'CIProvenance', record)
    if source['working_tree_dirty']:
        raise ProjectError('Hosted verification requires a clean checkout before running checks')
    return record


PRIVATE_CHECK_LOGS = RUN_OBSERVATION_CHECKS | set(FIELD_ACTIONS) | {'credential-boundaries'}
STOP_REASONS = {'interrupted', 'cleanup_failed'}
BUILD_CHECKS = {'typescript-contracts', 'ui-build', 'rust-format', 'rust-contracts-workers',
                'rust-lints', 'windows-native-build'}
WINDOWS_BROWSER_CHECKS = {'market-workflow', 'chart-history', 'ubuntu-preview',
                          'local-import-ui', 'market-live-stream', 'market-quality-states',
                          'market-worker-interruption', 'workspace-timeframe',
                          'desktop-workspace-transport', 'desktop-localization'}
WINDOWS_LANE_CHECKS = {
    'browser': WINDOWS_BROWSER_CHECKS,
    'python': {'python-contracts-workers'},
    'recovery': {'market-recovery', 'market-recovery-ui'},
}
WINDOWS_LANES = ('core', *WINDOWS_LANE_CHECKS)
CHECK_OUTPUT_LOCK = Lock()


def print_check(record):
    with CHECK_OUTPUT_LOCK:
        print(json.dumps(record), flush=True)


def execute(name, argv, env, run_dir, timeout=900, *, cancel=None):
    started = time.monotonic()
    executable = shutil.which(argv[0])
    if not executable:
        return {'id': name, 'status': 'blocked', 'command': argv, 'reason_code': 'executable_unavailable',
                'reason': f'Executable unavailable: {argv[0]}'}
    log_path = run_dir / f'{name}.log'
    print_check({'id': name, 'status': 'running', 'timeout_seconds': timeout})
    private = name in PRIVATE_CHECK_LOGS
    # Playwright, pnpm and the history bridge can leave the initial Linux group.
    containment = {'contain_linux_tree': True} if sys.platform == 'linux' and name == 'market-recovery' else {}
    exit_code, reason = run_logged([executable, *argv[1:]], cwd=ROOT, env=env,
                                  log_path=os.devnull if private else log_path, timeout=timeout, cancel=cancel,
                                  **containment)
    if private:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(redacted_check_log(exit_code))
    status = 'passed' if exit_code == 0 and reason is None else 'failed'
    if exit_code == 77 and reason is None and name in (RUN_OBSERVATION_CHECKS | FIELD_ACTIONS.keys()):
        status = 'blocked'
        reason = 'observation_unavailable'
    record = {'id': name, 'status': status, 'command': argv, 'exit_code': exit_code,
              'duration_seconds': round(time.monotonic() - started, 3), 'log': log_path.relative_to(ROOT).as_posix(),
              'log_sha256': file_digest(log_path)}
    if reason:
        record['reason_code'] = reason
    print_check(record)
    if status == 'failed':
        print_check({'id': name, 'log_tail': log_tail(log_path)})
    return record


def check_command(check):
    return [sys.executable if arg == '{python}' else arg for arg in check['command'] or []]


def check_timeout(check, system):
    default = check['timeout_seconds']
    return check.get('windows_timeout_seconds', default) if system == 'Windows' else default


def windows_lane_checks(selected, lane, *, python_split=False):
    if python_split and lane == 'python':
        return set()
    if lane == 'core':
        return selected.difference(*WINDOWS_LANE_CHECKS.values())
    return selected & WINDOWS_LANE_CHECKS[lane]


def windows_lanes_run_id(ci):
    return f"windows-change-{ci['run_id']}-{ci['run_attempt']}"


def build_window(catalog, selected, environment, index):
    # Serialize all build steps. The Python suite uses temporary checkouts and ephemeral ports.
    # Join before any workflow, browser or acceptance check consumes shared outputs.
    window = [catalog[index]]
    for check in catalog[index + 1:]:
        if (check['id'] not in BUILD_CHECKS or check['id'] not in selected or
                check['status'] != 'implemented' or
                (check['platforms'] == 'windows-x64' and not windows_x64(environment))):
            break
        window.append(check)
    return window


def run_overlapped(checks, env, run_dir, *, system):
    cancel = Event()
    interrupted = False

    def request_stop(_signum, _frame):
        # Signal delivery can interrupt process launch or lock acquisition.
        # Let each lane observe the request inside its normal cleanup path.
        nonlocal interrupted
        interrupted = True

    def cancelled():
        return interrupted or cancel.is_set()

    def run(check):
        try:
            result = execute(check['id'], check_command(check), env, run_dir,
                             check_timeout(check, system), cancel=cancelled)
        except BaseException:
            cancel.set()
            raise
        if result.get('reason_code') in STOP_REASONS:
            cancel.set()
        return result

    results = {}
    handlers = {signum: signal.signal(signum, request_stop) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            python = executor.submit(run, checks[0])

            def wait_for_python():
                # Service the main-thread signal handler even after the other check has returned.
                while not python.done():
                    wait((python,), timeout=.25)
                return python.result()

            try:
                for check in checks[1:]:
                    if cancelled():
                        break
                    results[check['id']] = run(check)
                try:
                    result = wait_for_python()
                except KeyboardInterrupt:
                    cancel.set()
                    result = wait_for_python()
                    if result.get('reason_code') not in STOP_REASONS:
                        raise
                results[checks[0]['id']] = result
            finally:
                cancel.set()
        if interrupted and not any(result.get('reason_code') in STOP_REASONS for result in results.values()):
            raise KeyboardInterrupt
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    return results


def select_checks(project, *, profile=None, task_id=None):
    if task_id:
        if task_id not in project['tasks']:
            raise ProjectError(f'Unknown task: {task_id}')
        return set(project['tasks'][task_id]['checks']) | {'environment', 'project-state'}
    if profile:
        if profile == 'release':
            return release_checks(project)
        if profile not in project['catalog'].get('profiles', {}):
            raise ProjectError(f'Unknown check profile: {profile}')
        return set(project['catalog']['profiles'][profile])
    return {key for key, check in project['checks'].items()
            if check.get('cadence', 'change') == 'change' and check['status'] != 'retired'}


TASK_CHECKS_WITHOUT_BROWSER_OR_CARGO_BUILD = {
    'environment': ['{python}', 'scripts/doctor.py'],
    'project-state': ['{python}', 'scripts/project.py', 'check'],
    'architecture': ['{python}', 'scripts/check_architecture.py'],
    'python-types': ['{python}', '-m', 'mypy'],
    'python-contracts-workers': ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
                                 '-p', 'test_*.py', '-v', '--durations', '0'],
    'dependency-review': ['{python}', 'scripts/dependencies.py'],
}
PROFILE_CHECKS_WITHOUT_BROWSER_OR_CARGO_BUILD = {
    **TASK_CHECKS_WITHOUT_BROWSER_OR_CARGO_BUILD,
    'generated-contracts': ['{python}', 'scripts/generate_contracts.py', '--check'],
}


def ci_requires_build_setup(project, task_id=None, *, profile=None):
    if not task_id and profile not in {'records', 'tooling'}:
        return True
    try:
        selected = select_checks(project, profile=profile, task_id=task_id)
    except ProjectError:
        return True
    # These checks can run with the installed toolchains and an empty Cargo target.
    # A new check or changed command keeps setup until its prerequisites are reviewed.
    reviewed = TASK_CHECKS_WITHOUT_BROWSER_OR_CARGO_BUILD if task_id else PROFILE_CHECKS_WITHOUT_BROWSER_OR_CARGO_BUILD
    return not selected or any(key not in reviewed or project['checks'].get(key, {}).get('command') != reviewed[key]
                               for key in selected)


def checked_session(project, run_dir, key, source):
    observation = collect_run_observation(ROOT, run_dir, key, source)
    verify_primary_model(project, {key: {'observation': observation}}, check_id=key)
    return observation


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-windows', action='store_true')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--profile')
    selection.add_argument('--task')
    parser.add_argument('--fail-fast', action='store_true')
    parser.add_argument('--ci-build-setup-required', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--ci-windows-lane', choices=WINDOWS_LANES, help=argparse.SUPPRESS)
    parser.add_argument('--ci-windows-aggregate', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.ci_build_setup_required:
        try:
            required = ci_requires_build_setup(load_project(ROOT), args.task, profile=args.profile)
        except Exception:
            # Leave failed project validation and recovery evidence to the actual check run.
            required = True
        print('true' if required else 'false')
        return
    project = load_project(ROOT)
    local_feedback_history(ROOT, project)
    selected = select_checks(project, profile=args.profile, task_id=args.task)
    source = source_identity(ROOT)
    ci = ci_provenance(source, os.environ, ROOT)
    split = bool(args.ci_windows_lane or args.ci_windows_aggregate)
    if split and (not ci or ci['event'] not in {'pull_request', 'push'} or platform.system() != 'Windows'
                  or args.task or args.fail_fast):
        parser.error('Windows lanes require hosted Windows change verification')
    if args.ci_windows_lane and (args.profile or args.ci_windows_aggregate or args.require_windows):
        parser.error('A Windows lane uses its fixed selection')
    if args.ci_windows_aggregate and not (args.require_windows or args.profile):
        parser.error('The Windows aggregate requires a full native or scoped selection')
    python_split = False
    if split and not args.profile:
        if __package__:
            from . import windows_python
        else:
            import windows_python
        python_split = windows_python.enabled(project, ci, selected)
    if args.ci_windows_lane:
        selected = windows_lane_checks(selected, args.ci_windows_lane, python_split=python_split)
    maintain_automatically(ROOT)
    project_hash = project_definition_hash(project)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:8]
    if split and not args.profile:
        run_id = windows_lanes_run_id(ci)
    run_dir = REPORTS / 'runs' / run_id
    env = checkout_git_environment()
    if not (env.get('CI') or env.get('GITHUB_ACTIONS')):
        env.setdefault('CARGO_INCREMENTAL', '0')
    env['SCOPEHUB_RUN_DIR'] = str(run_dir)
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    env['SCOPEHUB_PYTHON'] = sys.executable
    env['PYTHONUNBUFFERED'] = '1'
    env.pop('SCOPEHUB_REUSE_OPERATIONS_BUILD', None)
    results = []
    candidate_artifacts = None
    stopped = False
    catalog = project['catalog']['checks']
    hosted_full = ci is not None and not (args.task or args.profile or args.fail_fast)
    overlap = (hosted_full and not args.ci_windows_lane and
               not any(env.get(key) for key in ('CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_BUILD_DIR')))
    concurrent_results = {}
    python_parts = {}
    part_failed = False
    if args.ci_windows_aggregate:
        if __package__:
            from .ci_artifacts import collect_windows_lanes
        else:
            from ci_artifacts import collect_windows_lanes
        imported, elapsed = collect_windows_lanes(ROOT, args.ci_windows_aggregate, project, source, ci,
                                                    selected, os.environ, profile=args.profile)
        if args.profile:
            # A scoped PR runs the same harness on the core runner and publishes its original report.
            raise SystemExit(0 if imported['status'] == 'passed' else 1)
        environment = imported['environment']
        results = imported['checks']
        python_parts = imported.get('windows_python', {})
        started = time.monotonic() - elapsed
        catalog = []
    else:
        run_dir.mkdir(parents=True)
        environment = {
            'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
            'python': platform.python_version(), 'python_executable': sys.executable,
            'tools': {name: tool_version(name) for name in ['node', 'pnpm', 'rustc', 'cargo', 'uv']},
        }
    for index, check in enumerate(catalog):
        command = check_command(check)
        if check['id'] not in concurrent_results and (check['id'] not in selected or stopped):
            results.append({'id': check['id'], 'status': 'blocked', 'command': command,
                            'reason_code': 'earlier_failure' if stopped and check['id'] in selected else 'not_selected',
                            'reason': 'This check was not executed in this run.'})
        elif check['status'] in {'planned', 'retired'}:
            results.append({'id': check['id'], 'status': 'blocked', 'command': command,
                            'reason_code': 'not_implemented' if check['status'] == 'planned' else 'retired',
                            'reason': f"This {check['status']} check has no active implementation."})
        elif check['platforms'] == 'windows-x64' and not windows_x64(environment):
            results.append({'id': check['id'], 'status': 'blocked', 'command': command,
                            'reason_code': 'unsupported_platform', 'reason': 'Requires native Windows x64.'})
        else:
            if overlap and check['id'] == 'python-contracts-workers':
                window = build_window(catalog, selected, environment, index)
                if len(window) > 1:
                    concurrent_results = run_overlapped(window, env, run_dir, system=environment['system'])
            result = concurrent_results.pop(check['id'], None)
            if result is None:
                check_env = env
                if hosted_full and check['id'] == 'operations-workflow' and any(
                        row['id'] == 'rust-contracts-workers' and row['status'] == 'passed' and row.get('exit_code') == 0
                        for row in results):
                    check_env = {**env, 'SCOPEHUB_REUSE_OPERATIONS_BUILD': '1'}
                result = execute(check['id'], command, check_env, run_dir, check_timeout(check, environment['system']))
            if result['status'] == 'passed':
                try:
                    if check['id'] in RUN_OBSERVATION_CHECKS:
                        result['observation'] = checked_session(project, run_dir, check['id'], source)
                    elif check['id'] in FIELD_ACTIONS:
                        result['field_observation'] = field_observation(ROOT, check['id'], hosted=ci is not None)
                    elif check['id'] == 'release-artifacts':
                        candidate_artifacts = release_artifacts(run_dir, source, project['catalog']['desktop_performance'],
                                                              hosted=ci is not None)
                except (OSError, KeyError, ValueError, TypeError) as error:
                    result.update(status='failed', reason_code='missing_acceptance_evidence', reason=str(error))
            results.append(result)
            if result.get('reason_code') in STOP_REASONS or (
                    result['status'] != 'passed' and (args.fail_fast or args.profile == 'fast' or check['id'] == 'environment')):
                stopped = True
    if python_split and args.ci_windows_lane in windows_python.LANES:
        lane = args.ci_windows_lane
        label = windows_python.part_id(lane)
        part_command = windows_python.command(sys.executable, lane)
        if stopped:
            record = {'id': label, 'status': 'failed', 'command': part_command, 'reason_code': 'earlier_failure'}
        else:
            timeout = check_timeout(project['checks'][windows_python.CHECK], environment['system'])
            part_start = datetime.now(timezone.utc).isoformat()
            record = execute(label, part_command, env, run_dir, timeout)
            if 'duration_seconds' in record:
                record['started_at'] = part_start
            manifest = run_dir / f'{label}.json'
            if manifest.is_file():
                record['manifest_sha256'] = file_digest(manifest)
            elif record['status'] == 'passed':
                record.update(status='failed', reason_code='missing_acceptance_evidence',
                              reason='Windows Python part completed without its case manifest.')
        python_parts[lane] = record
        part_failed = record['status'] != 'passed'
    final_source = source_identity(ROOT)
    source_unchanged = final_source['sha256'] == source['sha256']
    if ci:
        source_unchanged = source_unchanged and not final_source['working_tree_dirty'] and final_source['git_head'] == source['git_head']
    state_error = None
    try:
        project_unchanged = project_definition_hash(load_project(ROOT)) == project_hash
    except (ProjectError, OSError, KeyError) as error:
        project_unchanged = False
        state_error = str(error)
    implemented = [result for result in results if result['id'] in selected and project['checks'][result['id']]['status'] == 'implemented']
    passed = all(result['status'] == 'passed' for result in implemented)
    if args.task or args.profile:
        passed = passed and all(result['status'] == 'passed' for result in results if result['id'] in selected)
    native_blocked = args.require_windows and (not windows_x64(environment) or
                                               not any(r['id'] == 'windows-native-build' and r['status'] == 'passed' for r in results))
    if native_blocked:
        passed = False
    integration_requested = not args.ci_windows_lane and (
        args.require_windows or (not args.task and args.profile in {None, 'release'}))
    required = project['catalog']['required_change_checks']
    observations = {result['id']: result for result in results}
    unverified = [key for key in required if observations.get(key, {}).get('status') != 'passed'
                  or observations.get(key, {}).get('exit_code') != 0]
    applicable_missing = [key for key in unverified
                          if project['checks'][key]['platforms'] == 'all' or windows_x64(environment)]
    integration_blocked = integration_requested and bool(applicable_missing)
    if integration_blocked:
        passed = False
    lane_failure = bool(args.ci_windows_aggregate and os.environ.get('SCOPEHUB_WINDOWS_LANE_RESULT') != 'success')
    failed = (any(result['status'] == 'failed' for result in implemented) or lane_failure or part_failed
              or not source_unchanged or not project_unchanged)
    milestones, acceptance = product_progress(project, results)
    report = {
        'report_version': windows_python.REPORT_VERSION if python_parts else '2.0', 'run_id': run_id, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'environment': environment, 'source': source, 'source_unchanged': source_unchanged,
        'source_end': final_source,
        'project_definition_sha256': project_hash, 'project_unchanged': project_unchanged,
        'project_state_error': state_error,
        'timing': {'elapsed_seconds': round(time.monotonic() - started, 3)},
        'native_requirement': {'requested': args.require_windows, 'satisfied': not native_blocked},
        'integration_gates': {'requested': integration_requested, 'required': required, 'unverified': unverified,
                              'status': ('blocked' if unverified else 'passed') if integration_requested else 'not_requested'},
        'selection': {'profile': (f'ci-windows-{args.ci_windows_lane}' if args.ci_windows_lane
                                  else args.profile or ('task' if args.task else 'full')), 'task_id': args.task,
                      'checks': sorted(selected)},
        'task_definitions': {task['id']: task_definition_hash(task) for task in project['tasks'].values()},
        'status_scope': 'Selected implemented checks; omitted and planned checks never establish acceptance.',
        'status': 'failed' if failed else ('passed' if passed else 'blocked'), 'checks': results,
        'milestones': milestones, 'acceptance': acceptance,
        'product_complete': not failed and all(item['status'] == 'passed' for item in [*milestones, *acceptance]),
        'integration_validation': integration_validation(results),
        'candidate_artifacts': candidate_artifacts,
        'limitations': [
            'Task completion is supported only by archived evidence for its recorded definition.',
            'Mock and local checks do not establish real-provider coverage or real-model usability.',
            'Execution snapshots exclude workflow records; the project-state check validates those records separately.',
            'Task and decision definitions must remain unchanged during verification and match when evidence is recorded.',
            'A dirty-worktree digest identifies tested inputs but does not independently preserve their source bytes.',
        ],
    }
    if python_parts:
        report['windows_python'] = python_parts
    if args.ci_windows_aggregate:
        report['limitations'].append('Elapsed time covers the interval from the first lane harness start to aggregation.')
    if lane_failure:
        report['limitations'].append('At least one Windows lane job did not succeed.')
    findings = sorted(
        feedback_fingerprint(item) for item in project['feedback']['items']
        if item['status'] != 'addressed' and item['severity'] != 'minor'
        and (not args.task or args.task in item['task_ids'])
        and selected.intersection(item['regression_checks'])
    )
    if findings:
        report['observed_feedback'] = findings
    if ci:
        report['ci'] = ci
    if not source_unchanged:
        # Retain changed paths, not file contents, to diagnose unexpected build writes.
        report['worktree_changes'] = subprocess.run(
            ['git', 'status', '--short'], cwd=ROOT, env=env, capture_output=True, check=True,
            text=True, encoding='utf-8', errors='replace',
        ).stdout.splitlines()
    validate_model(ROOT, 'HarnessReport', report)
    contents = json.dumps(report, indent=2) + '\n'
    original = run_dir / 'test-report.tmp'
    original.write_text(contents, encoding='utf-8')
    original.replace(run_dir / 'test-report.json')
    latest = REPORTS / f'{run_id}.tmp'
    latest.write_text(contents, encoding='utf-8')
    latest.replace(REPORTS / 'test-report.json')
    print(json.dumps({'report': (run_dir / 'test-report.json').relative_to(ROOT).as_posix(),
                      'status': report['status'], 'source_unchanged': source_unchanged,
                      'm0_complete': next(item['status'] == 'passed' for item in milestones if item['id'] == 'M0')}), flush=True)
    local_blocked = any(r['status'] == 'blocked' and project['checks'][r['id']]['platforms'] == 'all' for r in implemented)
    selection_blocked = bool(args.task or args.profile) and any(r['status'] != 'passed' for r in results if r['id'] in selected)
    exit_code = 1 if failed or local_blocked or selection_blocked or native_blocked or integration_blocked else 0
    if exit_code:
        recovery_command = f'pnpm project:recover --task {args.task} --save' if args.task else RECOVERY_COMMAND
        print(json.dumps({'recovery_command': recovery_command,
                          'failed_report': (run_dir / 'test-report.json').relative_to(ROOT).as_posix()}), flush=True)
    raise SystemExit(exit_code)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        capture_failure(ROOT, error, 'verification', argv=sys.argv[1:])
        raise SystemExit(1) from error
