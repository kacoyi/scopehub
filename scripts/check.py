"""Local verification entry point; report actual results and unverified gates."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from uuid import uuid4

if __package__:
    from .doctor import tool_version
    from .processes import log_tail, run_logged
    from .project import ProjectError, file_digest, load_project, project_definition_hash, product_progress, source_identity, task_definition_hash, windows_x64
else:
    from doctor import tool_version
    from processes import log_tail, run_logged
    from project import ProjectError, file_digest, load_project, project_definition_hash, product_progress, source_identity, task_definition_hash, windows_x64

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / '.reports'


def execute(name, argv, env, run_dir, timeout=900):
    started = time.monotonic()
    executable = shutil.which(argv[0])
    if not executable:
        return {'id': name, 'status': 'blocked', 'command': argv, 'reason': f'Executable unavailable: {argv[0]}'}
    log_path = run_dir / f'{name}.log'
    print(json.dumps({'id': name, 'status': 'running', 'timeout_seconds': timeout}), flush=True)
    exit_code, reason = run_logged([executable, *argv[1:]], cwd=ROOT, env=env,
                                  log_path=log_path, timeout=timeout)
    status = 'passed' if exit_code == 0 and reason is None else 'failed'
    record = {'id': name, 'status': status, 'command': argv, 'exit_code': exit_code,
              'duration_seconds': round(time.monotonic() - started, 3), 'log': log_path.relative_to(ROOT).as_posix(),
              'log_sha256': file_digest(log_path)}
    if reason:
        record['reason_code'] = reason
    print(json.dumps(record), flush=True)
    if status == 'failed':
        print(log_tail(log_path), flush=True)
    return record


def select_checks(project, *, profile=None, task_id=None):
    if task_id:
        if task_id not in project['tasks']:
            raise ProjectError(f'Unknown task: {task_id}')
        return set(project['tasks'][task_id]['checks']) | {'environment', 'project-state'}
    if profile:
        if profile == 'release':
            return {key for key, check in project['checks'].items() if check['status'] != 'retired'}
        if profile not in project['catalog'].get('profiles', {}):
            raise ProjectError(f'Unknown check profile: {profile}')
        return set(project['catalog']['profiles'][profile])
    return {key for key, check in project['checks'].items()
            if check.get('cadence', 'change') == 'change' and check['status'] != 'retired'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-windows', action='store_true')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--profile')
    selection.add_argument('--task')
    parser.add_argument('--fail-fast', action='store_true')
    args = parser.parse_args()
    project = load_project(ROOT)
    selected = select_checks(project, profile=args.profile, task_id=args.task)
    source = source_identity(ROOT)
    project_hash = project_definition_hash(project)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:8]
    run_dir = REPORTS / 'runs' / run_id
    run_dir.mkdir(parents=True)
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    env['SCOPEHUB_PYTHON'] = sys.executable
    env['PYTHONUNBUFFERED'] = '1'
    environment = {
        'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
        'python': platform.python_version(), 'python_executable': sys.executable,
        'tools': {name: tool_version(name) for name in ['node', 'pnpm', 'rustc', 'cargo', 'uv']},
    }
    results = []
    stopped = False
    for check in project['catalog']['checks']:
        command = [sys.executable if arg == '{python}' else arg for arg in check['command'] or []]
        if check['id'] not in selected or stopped:
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
            result = execute(check['id'], command, env, run_dir, check['timeout_seconds'])
            results.append(result)
            if result.get('reason_code') in {'interrupted', 'cleanup_failed'} or (
                    result['status'] != 'passed' and (args.fail_fast or args.profile == 'fast' or check['id'] == 'environment')):
                stopped = True
    source_unchanged = source_identity(ROOT)['sha256'] == source['sha256']
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
    failed = any(result['status'] == 'failed' for result in implemented) or not source_unchanged or not project_unchanged
    milestones, acceptance = product_progress(project, results)
    report = {
        'report_version': '2.0', 'run_id': run_id, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'environment': environment, 'source': source, 'source_unchanged': source_unchanged,
        'project_definition_sha256': project_hash, 'project_unchanged': project_unchanged,
        'project_state_error': state_error,
        'native_requirement': {'requested': args.require_windows, 'satisfied': not native_blocked},
        'selection': {'profile': args.profile or ('task' if args.task else 'full'), 'task_id': args.task,
                      'checks': sorted(selected)},
        'task_definitions': {task['id']: task_definition_hash(task) for task in project['tasks'].values()},
        'status_scope': 'Selected implemented checks; omitted and planned checks never establish acceptance.',
        'status': 'failed' if failed else ('passed' if passed else 'blocked'), 'checks': results,
        'milestones': milestones, 'acceptance': acceptance,
        'product_complete': not failed and all(item['status'] == 'passed' for item in [*milestones, *acceptance]),
        'integration_validation': {'real_market_data': 'not_evaluated', 'cloud_models': 'not_evaluated'},
        'limitations': [
            'Task completion is supported only by archived evidence for its recorded definition.',
            'Mock and local checks do not establish real-provider coverage or real-model usability.',
            'Execution snapshots exclude workflow records; the project-state check validates those records separately.',
            'Task and decision definitions must remain unchanged during verification and match when evidence is recorded.',
            'A dirty-worktree digest identifies tested inputs but does not independently preserve their source bytes.',
        ],
    }
    contents = json.dumps(report, indent=2) + '\n'
    (run_dir / 'test-report.json').write_text(contents, encoding='utf-8')
    latest = REPORTS / f'{run_id}.tmp'
    latest.write_text(contents, encoding='utf-8')
    latest.replace(REPORTS / 'test-report.json')
    print(json.dumps({'report': (run_dir / 'test-report.json').relative_to(ROOT).as_posix(),
                      'status': report['status'], 'source_unchanged': source_unchanged,
                      'm0_complete': next(item['status'] == 'passed' for item in milestones if item['id'] == 'M0')}), flush=True)
    local_blocked = any(r['status'] == 'blocked' and project['checks'][r['id']]['platforms'] == 'all' for r in implemented)
    selection_blocked = bool(args.task or args.profile) and any(r['status'] != 'passed' for r in results if r['id'] in selected)
    raise SystemExit(1 if failed or local_blocked or selection_blocked or native_blocked else 0)


if __name__ == '__main__':
    try:
        main()
    except (ProjectError, OSError, subprocess.SubprocessError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), flush=True)
        raise SystemExit(1) from error
