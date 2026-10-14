"""Preserve scaffold diagnostics without importing project validation or dependencies.

This is an observation tool, not a repair executor or an acceptance check. It can
run with the repository Python and -S when uv, pnpm or site packages are broken.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONTROL_FILES = (
    'project/tasks.json', 'project/checks.json', 'project/feedback.json',
    'project/delivery.json', 'contracts/schemas/project.schema.json', 'package.json',
    '.reports/test-report.json',
)
OPTIONAL_CONTROL_FILES = (
    'project/dependencies.json', 'project/dependency-inventory.json', 'project/branch-protection.json',
)
MAX_FILE_BYTES = 4 * 1024 * 1024
RECOVERY_COMMAND = 'pnpm project:recover --save'
BOOTSTRAP_COMMANDS = {
    'posix': '.venv/bin/python -S scripts/recovery.py --save',
    'windows': '.venv\\Scripts\\python.exe -S scripts\\recovery.py --save',
}


def read_control(root, relative):
    """Read only bounded, known files inside the checkout; retain exact bytes."""
    record = {'path': relative}
    raw = None
    value = None
    try:
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError('Path resolves outside the checkout')
        if not path.is_file():
            raise OSError('Expected an existing regular file')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raw = None
            raise ValueError('File exceeds the diagnostic size limit; preserve it manually')
        record['sha256'] = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        record['status'] = 'read'
    except (OSError, ValueError, RuntimeError) as error:
        record.update(status='unavailable' if raw is None else 'malformed', error=str(error))
        value = None
    return record, value, raw


def git_observation(root):
    observed = {}
    for name, args in [('head', ['rev-parse', 'HEAD']), ('working_tree', ['status', '--short'])]:
        try:
            result = subprocess.run(['git', '--no-optional-locks', *args], cwd=root, timeout=10, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace')
            observed[name] = result.stdout.strip() if result.returncode == 0 else None
            if result.returncode:
                observed[name + '_error'] = result.stderr.strip()[:2000]
        except (OSError, subprocess.SubprocessError) as error:
            observed[name + '_error'] = str(error)
    return observed


def diagnose(root=ROOT, *, task_id=None, save=False, failure=None):
    root = root.resolve()
    files, documents, originals, signals = [], {}, {}, []

    def signal(kind, **details):
        signals.append({'kind': kind, **details})

    for relative in (*CONTROL_FILES, *(path for path in OPTIONAL_CONTROL_FILES if (root / path).exists())):
        record, value, raw = read_control(root, relative)
        files.append(record)
        documents[relative] = value or {}
        if raw is not None:
            originals[relative] = raw
        if record['status'] != 'read' and (
                relative != '.reports/test-report.json' or (root / relative).exists()):
            signal('control_file', path=relative, detail=record['error'])

    def records(path, key):
        value = documents[path].get(key)
        if not isinstance(value, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in value):
            signal('control_shape', path=path, detail=f'Expected {key} records with string ids')
            return {}
        indexed = {item['id']: item for item in value}
        if len(indexed) != len(value):
            signal('control_shape', path=path, detail=f'Duplicate {key} ids')
        return indexed

    tasks = records('project/tasks.json', 'tasks')
    checks = records('project/checks.json', 'checks')
    feedback = records('project/feedback.json', 'items')
    focus = documents['project/tasks.json'].get('focus_task_id')
    selected_task = task_id if task_id is not None else focus
    task = tasks.get(selected_task) if isinstance(selected_task, str) else None
    if task is None:
        signal('task_unavailable', task_id=selected_task)
    else:
        required = task.get('checks')
        if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
            signal('control_shape', path='project/tasks.json', detail='Task checks must be string ids')
        else:
            for key in required:
                check = checks.get(key)
                if check is None or check.get('status') != 'implemented':
                    signal('task_capability_gap', check_id=key,
                           observed_status=check.get('status') if check else 'missing')
        for item in feedback.values():
            references = item.get('task_ids')
            if isinstance(references, list) and selected_task in references and item.get('status') != 'addressed':
                signal('task_feedback', feedback_id=item['id'], recorded_status=item.get('status'))

    report = documents['.reports/test-report.json']
    observations = []
    if report:
        if report.get('status') == 'failed':
            signal('recorded_run_failure', run_id=report.get('run_id'))
        entries = report.get('checks')
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            signal('control_shape', path='.reports/test-report.json', detail='Expected check observations')
        else:
            for item in entries:
                observation = {key: item.get(key) for key in ['id', 'status', 'reason_code', 'reason', 'log']}
                observations.append(observation)
                key = item.get('id')
                definition = checks.get(key, {}) if isinstance(key, str) else {}
                if item.get('status') == 'failed' or (
                        item.get('status') == 'blocked' and definition.get('status') == 'implemented'
                        and item.get('reason_code') not in ('not_selected', 'unsupported_platform', 'earlier_failure')):
                    signal('recorded_check_failure', **observation)

    if failure is not None:
        signal('harness_exception', **failure)
    diagnostic = {
        'diagnostic_version': '1.0', 'kind': 'scaffold_recovery',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': 'attention_required' if signals else 'no_trigger_observed',
        'acceptance': 'not_evaluated', 'recorded_report_freshness': 'not_verified',
        'resume_task_id': selected_task, 'recorded_focus_task_id': focus,
        'environment': {'python': platform.python_version(), 'executable': sys.executable,
                        'system': platform.system()},
        'git': git_observation(root), 'files': files, 'signals': signals,
        'recorded_run': {'run_id': report.get('run_id'), 'status': report.get('status'),
                         'observations': observations},
        'next_steps': [
            'Treat observations as leads: report freshness and full schemas have not been validated.',
            'Preserve the failed run and reproduce the first actionable failure; classify product defect, scaffold defect, environment issue, incorrect expectation or concrete capability gap.',
            'Read project/delivery.json scaffold_maintenance and AGENTS.md; retain the interrupted task and create or update bounded repair work with a regression.',
            'If project records cannot be validated, make only the minimal bootstrap repair first, then restore normal validation and record the work before completion.',
            'Run fast and affected checks, archive matching acceptance evidence, close feedback and resume the interrupted task. This diagnostic cannot support task completion.',
        ],
        'recovery_command': RECOVERY_COMMAND, 'bootstrap_commands': BOOTSTRAP_COMMANDS,
        'limitations': [
            'No repair code, commands from records, network operations or dependency installations are executed.',
            'Only the listed control files are retained; preserve source changes with a reviewed Git checkpoint and retain the original run logs separately.',
            'Future checks and expected platform omissions remain unverified; absence of a diagnostic signal is not a health or acceptance result.',
            'A working Python and this script are required; compare broken recovery code with a reviewed Git checkpoint without discarding unrelated changes.',
        ],
    }
    if save:
        directory = root / '.reports/recovery'
        if not directory.resolve().is_relative_to(root):
            raise ValueError('Recovery output resolves outside the checkout')
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:8]
        destination = directory / run_id
        destination.mkdir(parents=True, exist_ok=False)
        for relative, raw in originals.items():
            path = destination / 'originals' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        diagnostic['snapshot'] = (destination / 'diagnostic.json').relative_to(root).as_posix()
        (destination / 'diagnostic.json').write_text(json.dumps(diagnostic, indent=2) + '\n', encoding='utf-8')
    return diagnostic


def capture_failure(root, error, phase, *, argv=()):
    """Best-effort preservation must never turn the original exception into a pass."""
    record = {'status': 'failed', 'error': f'{type(error).__name__}: {error}',
              'recovery_command': RECOVERY_COMMAND, 'bootstrap_commands': BOOTSTRAP_COMMANDS}
    try:
        task_id = None
        # Capture intent even before the normal argument parser can be imported.
        for index, argument in enumerate(argv):
            if argument.startswith('--task='):
                task_id = argument.partition('=')[2]
            elif argument == '--task' and index + 1 < len(argv):
                task_id = argv[index + 1]
        diagnostic = diagnose(root, task_id=task_id, save=True, failure={
            'phase': phase, 'arguments': list(argv), 'error': record['error'],
            'traceback': traceback.format_exc(),
        })
        record['recovery_snapshot'] = diagnostic['snapshot']
    except Exception as snapshot_error:
        record['recovery_snapshot_error'] = f'{type(snapshot_error).__name__}: {snapshot_error}'
    print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', help='Task to resume; defaults to the readable recorded focus')
    parser.add_argument('--save', action='store_true', help='Retain diagnostic and original control bytes')
    args = parser.parse_args()
    try:
        diagnostic = diagnose(task_id=args.task, save=args.save)
        print(json.dumps(diagnostic, indent=2))
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'diagnostic_failed', 'acceptance': 'not_evaluated', 'error': str(error)}))
        return 1
    # Zero means diagnostic collection succeeded, not that checks or tasks passed.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
