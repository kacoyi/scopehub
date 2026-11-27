"""Local verification entry point; report actual results and unverified gates."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from io import UnsupportedOperation
import json
import os
from pathlib import Path
import platform
import select
import shutil
import signal
import stat
import subprocess
import sys
from threading import Event, Lock, current_thread, main_thread
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
        from .processes import log_tail, run_logged, seal_unconfirmed_log
        from .project import (
            ProjectError, checkout_git_environment, feedback_fingerprint, file_digest, load_project,
            local_feedback_history, product_progress, project_definition_hash, release_checks, source_identity,
            task_definition_hash, validate_model, verify_primary_model, windows_x64,
        )
        from .storage import maintain_automatically
        from .verification_evidence import (
            FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, collect_run_observation, field_observation,
            hosted_artifact_path, integration_validation, redacted_check_log, release_artifacts,
        )
    else:
        from doctor import tool_version
        from processes import log_tail, run_logged, seal_unconfirmed_log
        from project import (
            ProjectError, checkout_git_environment, feedback_fingerprint, file_digest, load_project,
            local_feedback_history, product_progress, project_definition_hash, release_checks, source_identity,
            task_definition_hash, validate_model, verify_primary_model, windows_x64,
        )
        from storage import maintain_automatically
        from verification_evidence import (
            FIELD_ACTIONS, RUN_OBSERVATION_CHECKS, collect_run_observation, field_observation,
            hosted_artifact_path, integration_validation, redacted_check_log, release_artifacts,
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
INTERRUPTED_BEFORE_START = 'interrupted_before_start'
PYTHON_MANIFEST_INTERRUPTED = 'Interrupted while finalizing the Windows Python manifest.'
RECOVERY_LANE_INTERRUPTED = 'The Windows recovery lane was interrupted.'
WINDOWS_PYTHON_INTERRUPTION = {
    'python': 'The Windows Python lane was interrupted.',
    'recovery': RECOVERY_LANE_INTERRUPTED,
}
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
WINDOWS_WORKSPACE_LANE = 'workspace'
ALL_WINDOWS_LANES = (*WINDOWS_LANES, WINDOWS_WORKSPACE_LANE)
# Full PRs have two independent browser runners and two toolchain-only Python producers.
WINDOWS_PR_CHECK_TRANSFERS = {
    'browser': {'market-recovery'},
    'workspace': {'workspace-timeframe', 'desktop-workspace-transport',
                  'market-recovery-ui', 'desktop-localization'},
}
WINDOWS_WORKSPACE_RESULT_CHECKS = WINDOWS_PR_CHECK_TRANSFERS[WINDOWS_WORKSPACE_LANE]
BROWSER_RESULTS_DIGEST = 'browser_results_sha256'
WINDOWS_LANES_RUN_PREFIX = 'windows-change-'
CHECK_OUTPUT_LOCK = Lock()
CHECK_CONSOLE = ContextVar('check_console', default=None)


class CheckConsole:
    def __init__(self):
        self.interrupted = False
        self.muted = False
        self.cooperative = False

    def write_line(self, line):
        if sys.platform == 'linux':
            try:
                fd = sys.stdout.fileno()
            except (AttributeError, UnsupportedOperation):
                pass
            else:
                self.write_linux_line(fd, line)
                return
        print(line, flush=True)

    def write_linux_line(self, fd, line):
        owned = None
        try:
            if self.cooperative or self.interrupted:
                original = os.fstat(fd)
                if stat.S_ISFIFO(original.st_mode):
                    # Reopen the pipe with a separate file description. dup()
                    # would share flags with the caller and other writers.
                    owned = os.open(f'/proc/self/fd/{fd}', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                    if not os.path.samestat(original, os.fstat(owned)):
                        raise OSError('Console pipe changed while reopening it')
                    fd = owned

            # Write directly so shutdown cannot retry a buffered console write.
            pending = memoryview((line + '\n').encode('ascii'))
            writable = select.poll()
            writable.register(fd, select.POLLOUT)
            while pending and not self.muted:
                if (self.cooperative or self.interrupted) and not writable.poll(0 if self.interrupted else 50):
                    if self.interrupted:
                        self.muted = True
                    continue
                try:
                    written = os.write(fd, pending[:select.PIPE_BUF])
                except BlockingIOError:
                    if owned is None:
                        raise
                    # Readiness is not a reservation. Another writer can fill
                    # the pipe before write; wait again only while uncancelled.
                    if self.interrupted:
                        self.muted = True
                    continue
                if not written:
                    raise OSError('Console write made no progress')
                pending = pending[written:]
        finally:
            if owned is not None:
                os.close(owned)

    def emit(self, record):
        try:
            with CHECK_OUTPUT_LOCK:
                if not self.muted:
                    self.write_line(json.dumps(record))
        except KeyboardInterrupt:
            self.interrupted = self.muted = True
        except OSError:
            # The console is optional. Run evidence is written separately.
            self.muted = True
        return self.muted and self.interrupted


@contextmanager
def check_console():
    if CHECK_CONSOLE.get() is not None:
        yield
        return
    token = CHECK_CONSOLE.set(CheckConsole())
    try:
        yield
    finally:
        CHECK_CONSOLE.reset(token)


def print_check(record):
    console = CHECK_CONSOLE.get() or CheckConsole()
    return console.emit(record)


@contextmanager
def defer_finalization_sigint():
    interrupted = False

    def request_stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    # Overlapped runs already deliver signals through their cancellation
    # callback. Preserve other callers' dispositions and worker threads.
    own_handler = (current_thread() is main_thread()
                   and signal.getsignal(signal.SIGINT) == signal.default_int_handler)
    if own_handler:
        signal.signal(signal.SIGINT, request_stop)
    try:
        yield lambda: interrupted
    finally:
        if own_handler:
            signal.signal(signal.SIGINT, signal.default_int_handler)


@check_console()
def execute(name, argv, env, run_dir, timeout=900, *, cancel=None):
    started = time.monotonic()
    executable = shutil.which(argv[0])
    if not executable:
        return {'id': name, 'status': 'blocked', 'command': argv, 'reason_code': 'executable_unavailable',
                'reason': f'Executable unavailable: {argv[0]}'}
    log_path = run_dir / f'{name}.log'
    console = CHECK_CONSOLE.get()
    if print_check({'id': name, 'status': 'running', 'timeout_seconds': timeout}):
        cancel = lambda: True
    private = name in PRIVATE_CHECK_LOGS
    # Check launchers can leave the initial process group on Linux.
    exit_code, reason = run_logged([executable, *argv[1:]], cwd=ROOT, env=env,
                                  log_path=os.devnull if private else log_path, timeout=timeout, cancel=cancel,
                                  contain_linux_tree=True)
    with defer_finalization_sigint() as interrupted:
        if private:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(redacted_check_log(exit_code))
        elif reason == 'cleanup_failed' and sys.platform == 'linux':
            seal_unconfirmed_log(log_path)
        log_sha256 = file_digest(log_path)
        tail = log_tail(log_path) if exit_code != 0 or reason is not None or interrupted() else None
    interrupted_finalization = interrupted() or (
        reason not in STOP_REASONS and cancel is not None and cancel())
    if interrupted_finalization and reason not in STOP_REASONS:
        reason = 'interrupted'
    status = 'passed' if exit_code == 0 and reason is None else 'failed'
    if exit_code == 77 and reason is None and name in (RUN_OBSERVATION_CHECKS | FIELD_ACTIONS.keys()):
        status = 'blocked'
        reason = 'observation_unavailable'
    record = {'id': name, 'status': status, 'command': argv, 'exit_code': exit_code,
              'duration_seconds': round(time.monotonic() - started, 3), 'log': log_path.relative_to(ROOT).as_posix(),
              'log_sha256': log_sha256}
    if reason:
        record['reason_code'] = reason
    if interrupted_finalization:
        record['reason'] = 'Interrupted while finalizing check diagnostics.'
    if reason == 'interrupted' or interrupted():
        console.interrupted = True
    output_interrupted = print_check(record)
    if not output_interrupted and status == 'failed' and tail is not None:
        output_interrupted = print_check({'id': name, 'log_tail': tail})
    if output_interrupted:
        if reason != 'cleanup_failed':
            record.update(status='failed', reason_code='interrupted')
        record.setdefault('reason', 'Interrupted while publishing check output.')
    return record


def check_command(check):
    return [sys.executable if arg == '{python}' else arg for arg in check['command'] or []]


def check_timeout(check, system):
    default = check['timeout_seconds']
    return check.get('windows_timeout_seconds', default) if system == 'Windows' else default


def stops_after_check(result, *, fail_fast=False):
    return (result.get('reason_code') in STOP_REASONS
            or result.get('reason_code') == INTERRUPTED_BEFORE_START
            or (result['status'] != 'passed' and (fail_fast or result['id'] == 'environment')))


def check_failed(result):
    return result['status'] == 'failed' or result.get('reason_code') == INTERRUPTED_BEFORE_START


def windows_lane_checks(selected, lane, *, python_split=False, recovery_checks=frozenset(), ownership=None,
                        pr_transfers=None):
    if ownership is None:
        ownership = WINDOWS_LANE_CHECKS
    if pr_transfers:
        moving = set().union(*pr_transfers.values())
        ownership = {key: (checks - moving) | pr_transfers.get(key, set()) for key, checks in ownership.items()}
        ownership.update({key: checks for key, checks in pr_transfers.items() if key not in ownership})
    if python_split and lane == 'python':
        return set()
    if lane == 'core':
        return selected.difference(*ownership.values())
    owned = ownership.get(lane, set())
    if python_split:
        if lane == 'browser':
            owned = owned - recovery_checks
        elif lane == 'recovery':
            owned = owned | (ownership['browser'] & recovery_checks)
    return selected & owned


def windows_lanes_run_id(ci):
    return f"{WINDOWS_LANES_RUN_PREFIX}{ci['run_id']}-{ci['run_attempt']}"


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


@contextmanager
def cooperative_stop_requests(console):
    interrupted = False
    finished = False

    def request_stop(_signum, _frame):
        # Signal delivery can interrupt process launch or lock acquisition.
        # Let each lane observe the request inside its normal cleanup path.
        nonlocal interrupted
        if not finished:
            interrupted = True
        console.interrupted = True

    def stop_requested(*, finish=False):
        nonlocal finished
        if finish:
            finished = True
        return interrupted

    previous_cooperation = console.cooperative
    console.cooperative = True
    handlers = {signum: signal.signal(signum, request_stop) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield stop_requested
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        console.cooperative = previous_cooperation


def run_overlapped(checks, env, run_dir, *, system, timed_first=False, stop_requested=None):
    cancel = Event()
    console = CHECK_CONSOLE.get() or CheckConsole()

    def cancelled():
        return interrupted() or cancel.is_set()

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

    def run_first():
        started_at = datetime.now(timezone.utc).isoformat() if timed_first else None
        result = run(checks[0])
        if started_at is not None and 'duration_seconds' in result:
            result['started_at'] = started_at
        return result

    results = {}
    unstarted = None
    token = CHECK_CONSOLE.set(console)
    try:
        with ExitStack() as overlap:
            interrupted = stop_requested or overlap.enter_context(cooperative_stop_requests(console))
            with ThreadPoolExecutor(max_workers=1) as executor:
                python = executor.submit(copy_context().run, run_first)

                def wait_for_python():
                    # Service the main-thread signal handler even after the other check has returned.
                    while not python.done():
                        wait((python,), timeout=.25)
                    return python.result()

                try:
                    for check in checks[1:]:
                        if cancelled():
                            unstarted = check
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
            if interrupted() and not any(result.get('reason_code') in STOP_REASONS for result in results.values()):
                if stop_requested is None:
                    raise KeyboardInterrupt
                if unstarted is not None:
                    results[unstarted['id']] = {
                        'id': unstarted['id'], 'status': 'blocked', 'command': check_command(unstarted),
                        'reason_code': INTERRUPTED_BEFORE_START, 'reason': 'Interrupted before this check was started.',
                    }
    finally:
        CHECK_CONSOLE.reset(token)
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
    with check_console(), ExitStack() as producer_signals:
        run_verification(producer_signals)


def publish_report(run_dir, report):
    validate_model(ROOT, 'HarnessReport', report)
    contents = json.dumps(report, indent=2) + '\n'
    # Complete both disk publications before attempting final notification.
    with defer_finalization_sigint() as interrupted_publication:
        original = run_dir / 'test-report.tmp'
        original.write_text(contents, encoding='utf-8')
        original.replace(run_dir / 'test-report.json')
        latest = REPORTS / f"{report['run_id']}.tmp"
        latest.write_text(contents, encoding='utf-8')
        latest.replace(REPORTS / 'test-report.json')
    if interrupted_publication():
        CHECK_CONSOLE.get().interrupted = True


def run_verification(producer_signals):
    started = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-windows', action='store_true')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--profile')
    selection.add_argument('--task')
    parser.add_argument('--fail-fast', action='store_true')
    parser.add_argument('--ci-build-setup-required', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--ci-windows-lane', choices=ALL_WINDOWS_LANES, help=argparse.SUPPRESS)
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
        transfers = WINDOWS_PR_CHECK_TRANSFERS if ci['event'] == 'pull_request' else {}
        if args.ci_windows_lane == WINDOWS_WORKSPACE_LANE and not transfers:
            parser.error('The Windows workspace producer requires a full pull request')
        selected = windows_lane_checks(
            selected, args.ci_windows_lane, python_split=python_split,
            recovery_checks=windows_python.RECOVERY_PR_CHECKS if python_split else frozenset(),
            pr_transfers=transfers,
        )
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
    producer_reports = {}
    part_failed = False
    part_interruption = None
    part_stop_requested = None
    imported_interruptions = []
    lane = args.ci_windows_lane
    recovery_window = []
    part_check = None
    if python_split and lane in windows_python.LANES:
        part_interruption = WINDOWS_PYTHON_INTERRUPTION[lane]
        part_stop_requested = producer_signals.enter_context(cooperative_stop_requests(CHECK_CONSOLE.get()))
        part_check = {**project['checks'][windows_python.CHECK], 'id': windows_python.part_id(lane),
                      'command': windows_python.command(sys.executable, lane)}
        if lane == 'recovery' and windows_python.RECOVERY_PR_CHECKS:
            # Python uses self-contained test checkouts and ephemeral listeners.
            # Keep the browser and Cargo checks serial on the other worker.
            recovery_window = [check for check in catalog if check['id'] in selected
                               and check['status'] == 'implemented' and check['platforms'] == 'all']
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
        producer_reports = imported.get('windows_producers', {})
        imported_interruptions = [marker for marker in WINDOWS_PYTHON_INTERRUPTION.values()
                                  if marker in imported.get('limitations', ())]
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
        if not stopped and recovery_window and check['id'] == recovery_window[0]['id']:
            concurrent_results = run_overlapped([part_check, *recovery_window], env, run_dir,
                                                system=environment['system'], timed_first=True,
                                                stop_requested=part_stop_requested)
            stopped = concurrent_results[part_check['id']].get('reason_code') in STOP_REASONS
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
            if lane and check['id'] in WINDOWS_WORKSPACE_RESULT_CHECKS and 'log' in result:
                result[BROWSER_RESULTS_DIGEST] = None
                try:
                    artifact = hosted_artifact_path(run_dir, f"{check['id']}/results.json")
                    result[BROWSER_RESULTS_DIGEST] = file_digest(artifact)
                except (OSError, ProjectError) as error:
                    if result['status'] == 'passed':
                        result.update(status='failed', reason_code='missing_acceptance_evidence', reason=str(error))
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
            if stops_after_check(result, fail_fast=args.fail_fast or args.profile == 'fast'):
                stopped = True
    if part_check is not None:
        label, part_command = part_check['id'], part_check['command']
        record = concurrent_results.pop(label, None)
        if record is None and stopped:
            record = {'id': label, 'status': 'failed', 'command': part_command, 'reason_code': 'earlier_failure'}
        else:
            if record is None:
                timeout = check_timeout(part_check, environment['system'])
                part_start = datetime.now(timezone.utc).isoformat()
                record = execute(label, part_command, env, run_dir, timeout, cancel=part_stop_requested)
                if 'duration_seconds' in record:
                    record['started_at'] = part_start
            manifest = run_dir / f'{label}.json'
            if manifest.is_file() and 'log_sha256' in record:
                try:
                    # execute() has already closed and digested the complete original log.
                    windows_python.seal_manifest(manifest, record['log_sha256'])
                    record['manifest_sha256'] = file_digest(manifest)
                except (OSError, ValueError) as error:
                    if record.get('reason_code') not in STOP_REASONS:
                        record.update(status='failed', reason_code='missing_acceptance_evidence', reason=str(error))
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
    failed = (any(check_failed(result) for result in implemented) or lane_failure or part_failed or bool(imported_interruptions)
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
            *imported_interruptions,
        ],
    }
    if python_parts:
        report['windows_python'] = python_parts
    if producer_reports:
        report['windows_producers'] = producer_reports
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
    console = CHECK_CONSOLE.get()

    def record_producer_interruption():
        if part_interruption in report['limitations']:
            return False
        report['limitations'].append(part_interruption)
        report.update(status='failed', product_complete=False)
        return True

    if part_stop_requested and part_stop_requested():
        record_producer_interruption()
    publish_report(run_dir, report)
    # Freeze the decision after both publications. Later signals can still
    # unblock notification, but cannot change the recorded producer verdict.
    if part_stop_requested and part_stop_requested(finish=True) and record_producer_interruption():
        publish_report(run_dir, report)
    print_check({'report': (run_dir / 'test-report.json').relative_to(ROOT).as_posix(),
                 'status': report['status'], 'source_unchanged': source_unchanged,
                 'm0_complete': next(item['status'] == 'passed' for item in milestones if item['id'] == 'M0')})
    local_blocked = any(r['status'] == 'blocked' and project['checks'][r['id']]['platforms'] == 'all' for r in implemented)
    selection_blocked = bool(args.task or args.profile) and any(r['status'] != 'passed' for r in results if r['id'] in selected)
    exit_code = 1 if report['status'] == 'failed' or local_blocked or selection_blocked or native_blocked or integration_blocked else 0
    if exit_code:
        recovery_command = f'pnpm project:recover --task {args.task} --save' if args.task else RECOVERY_COMMAND
        print_check({'recovery_command': recovery_command,
                     'failed_report': (run_dir / 'test-report.json').relative_to(ROOT).as_posix()})
    raise SystemExit(exit_code or int(part_stop_requested is None and console.interrupted))


if __name__ == '__main__':
    with check_console():
        try:
            main()
        except Exception as error:
            capture_failure(ROOT, error, 'verification', argv=sys.argv[1:], emit=print_check)
            raise SystemExit(1) from error
