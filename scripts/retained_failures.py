"""Read original local harness reports. Clearing a failure requires the canonical report schema."""

from datetime import datetime, timezone
from hashlib import sha256
from heapq import nlargest
import json
import os
import re
import stat
import sys

RUN_ID = re.compile(r'[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}')
CHECK_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,100}')
DIGEST = re.compile(r'[0-9a-f]{64}')
RUN_LIMIT = 256
FILE_LIMIT = 4 * 1024 * 1024
SCAN_BYTES = 24 * 1024 * 1024


class ScanBudgetReached(ValueError):
    pass


def ordinary(path, directory=False):
    info = path.lstat()
    match = stat.S_ISDIR if directory else stat.S_ISREG
    if (not match(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not directory and info.st_nlink != 1)):
        raise ValueError('Expected an ordinary local path')
    return info


def retained_bytes(root, run, name, budget):
    """Only fixed names and ordinary files inside the checkout-local run directory."""
    parents = (root / '.reports', root / '.reports/runs', root / '.reports/runs' / run)
    identities = [ordinary(path, directory=True) for path in parents]
    path = parents[-1] / name
    before = ordinary(path)
    if before.st_size > FILE_LIMIT:
        raise ValueError('File exceeds the per-file limit')
    if before.st_size > budget:
        raise ScanBudgetReached('File exceeds the remaining scan budget')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        opened = os.fstat(handle.fileno())
        if not os.path.samestat(before, opened) or any(
            not os.path.samestat(expected, ordinary(parent, directory=True))
            for parent, expected in zip(parents, identities)
        ):
            raise ValueError('Retained path changed during inspection')
        data = handle.read(before.st_size + 1)
        after = os.fstat(handle.fileno())
    if len(data) != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise ValueError('Retained file changed during inspection')
    return data


def unique_keys(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('Ambiguous report fields')
    return value


def invalid_constant(value):
    raise ValueError(f'Invalid JSON constant: {value}')


def observed_checks(run, data):
    """Identify executed checks and selected commands whose executable was missing."""
    report = json.loads(data, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    if report['report_version'] not in {'2.0', '2.1'} or report['run_id'] != run or report['status'] not in {'passed', 'failed', 'blocked'}:
        raise ValueError('Invalid original report')
    started = datetime.strptime(run[:23], '%Y%m%dT%H%M%S.%fZ').replace(tzinfo=timezone.utc)
    finished = datetime.fromisoformat(report['generated_at'].replace('Z', '+00:00'))
    if finished < started:
        raise ValueError('Invalid verification interval')
    source = report['source']
    inputs, project = source['sha256'], report['project_definition_sha256']
    env = report['environment']
    system, arch = env['system'], env['machine']
    if (not DIGEST.fullmatch(inputs) or not DIGEST.fullmatch(project) or source['snapshot_kind'] != 'execution-inputs-v1'
            or not all(isinstance(text, str) and text and text.isprintable() for text in (system, arch, env['python_executable']))
            or type(report['source_unchanged']) is not bool or type(report['project_unchanged']) is not bool):
        raise ValueError('Unidentified verification inputs or platform')
    stable = report['source_unchanged'] and report['project_unchanged'] and report.get('source_end') == source
    selection = report['selection']['checks']
    rows = report['checks']
    if not isinstance(rows, list) or not isinstance(selection, list):
        raise ValueError('Missing check selection')
    selected = set(selection)
    ids = [row['id'] for row in rows]
    if (len(set(ids)) != len(ids) or len(selected) != len(selection) or not selected.issubset(ids)
            or not all(CHECK_ID.fullmatch(key) for key in ids)):
        raise ValueError('Ambiguous check selection')
    checks = []
    for row in rows:
        key, status, command = row['id'], row['status'], row['command']
        if status not in {'passed', 'failed', 'blocked'} or not isinstance(command, list) or not all(
            isinstance(arg, str) and arg for arg in command
        ):
            raise ValueError('Invalid check result')
        if status == 'blocked':
            code = row.get('reason_code')
            if (key not in selected or not command or not (code == 'executable_unavailable'
                    or code is None and row.get('reason') == f'Executable unavailable: {command[0]}')):
                continue
            executed_fields = ('log', 'log_sha256', 'exit_code', 'duration_seconds', 'observation', 'field_observation')
            if report['status'] == 'passed' or any(field in row for field in executed_fields):
                raise ValueError('An unavailable executable cannot have an execution result')
        else:
            if key not in selected or not command or (status == 'failed' and report['status'] != 'failed'):
                raise ValueError('Inconsistent executed check')
            if (row.get('log') != f'.reports/runs/{run}/{key}.log' or not DIGEST.fullmatch(row['log_sha256'])
                    or status == 'passed' and (type(row.get('exit_code')) is not int or row['exit_code'] != 0 or row.get('reason_code'))):
                raise ValueError('Unbound executed check')
        normalized = ('{python}', *command[1:]) if command[0] == env['python_executable'] else tuple(command)
        identity = (inputs, project, system, arch.casefold(), key, normalized)
        # A report with qualified field or session evidence still needs its dedicated verifier.
        observation = ('field_observation' in row or 'observation' in row or key == 'release-artifacts'
                       or row.get('reason_code') == 'missing_acceptance_evidence'
                       or 'scripts/verification_evidence.py' in command)
        checks.append((identity, status, row.get('log_sha256'), observation))
    return started, finished, stable, checks


def recheck_validator(root, schema):
    # Isolated bootstrap diagnostics remain available with a broken virtual environment or schema.
    if sys.flags.no_site:
        return None
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry

        if schema is None:
            schema = json.loads((root / 'contracts/schemas/project.schema.json').read_bytes())
        definitions = schema['$defs']
        if not isinstance(definitions['HarnessReport'], dict):
            return None
        return Draft202012Validator(
            {'$ref': '#/$defs/HarnessReport', '$defs': definitions},
            format_checker=FormatChecker(), registry=Registry(),
        )
    except (ImportError, OSError, TypeError, ValueError, KeyError, RecursionError):
        return None


def retained_failure_view(root, reference, *, basis, detail=False, schema=None, latest_run=None):
    directory = root / '.reports/runs'
    scan = {'limit': RUN_LIMIT, 'byte_limit': SCAN_BYTES, 'originals_examined': 0, 'older_runs_omitted': 0, 'unavailable': 0,
            'unverified_rechecks': 0, 'invalid_recheck_reports': 0, 'schema_unavailable': False, 'bytes_limited': False}
    scan['unavailable_run_ids'] = []
    count = 0
    latest_seen = False

    def names():
        nonlocal count, latest_seen
        with os.scandir(directory) as children:
            for child in children:
                if RUN_ID.fullmatch(child.name):
                    count += 1
                    latest_seen |= child.name == latest_run
                    yield child.name

    try:
        ordinary(directory.parent, directory=True)
        ordinary(directory, directory=True)
        recent = nlargest(RUN_LIMIT + 1, names())
    except FileNotFoundError:
        recent = []
    except (OSError, ValueError):
        recent = []
        scan['unavailable'] += 1
    if isinstance(latest_run, str) and RUN_ID.fullmatch(latest_run) and not latest_seen:
        scan['unavailable'] += 1
        scan['unavailable_run_ids'].append(latest_run)
    scan['first_omitted_run_id'] = recent.pop() if len(recent) > RUN_LIMIT else None
    scan['older_runs_omitted'] = count - len(recent)
    observations = []
    remaining = SCAN_BYTES
    for position, run in enumerate(recent):
        try:
            if remaining <= 0:
                raise ScanBudgetReached('Report scan budget exhausted')
            contents = retained_bytes(root, run, 'test-report.json', remaining)
            remaining -= len(contents)
            observations.append((run, *observed_checks(run, contents), contents))
            scan['originals_examined'] += 1
        except ScanBudgetReached:
            scan['bytes_limited'] = True
            scan['first_omitted_run_id'] = run
            scan['older_runs_omitted'] += len(recent) - position
            break
        except (KeyError, TypeError, ValueError, AttributeError, OSError, RecursionError, OverflowError):
            scan['unavailable'] += 1
            if detail or len(scan['unavailable_run_ids']) < 3:
                scan['unavailable_run_ids'].append(run)

    pending = {}
    validator = None
    for run, start, end, stable, checks, raw in reversed(observations):
        valid_report = None
        for identity, status, log_digest, observation in checks:
            key = identity[4]
            if status in {'failed', 'blocked'}:
                group = (identity, stable, status)
                previous = pending.get(group)
                if previous is None:
                    pending[group] = (end, run, observation, run)
                else:
                    latest = max((end, run), previous[:2])
                    pending[group] = (*latest, observation or previous[2], previous[3])
            elif stable and pending:
                candidates = [(identity, stable, outcome) for outcome in ('failed', 'blocked')]
                candidates = [group for group in candidates if group in pending and pending[group][0] <= start]
                rechecked = [group for group in candidates if not observation and not pending[group][2]]
                if len(rechecked) != len(candidates):
                    scan['unverified_rechecks'] += 1
                if not rechecked:
                    continue
                if valid_report is None:
                    if validator is None and not scan['schema_unavailable']:
                        validator = recheck_validator(root, schema)
                        scan['schema_unavailable'] = validator is None
                    if validator is None:
                        valid_report = False
                    else:
                        try:
                            # These are the same bounded bytes already used to derive the result.
                            valid_report = validator.is_valid(json.loads(raw))
                        except Exception:
                            # A damaged schema or unresolved reference cannot establish a recheck.
                            scan['schema_unavailable'] = True
                            validator = None
                            valid_report = False
                        if not valid_report and not scan['schema_unavailable']:
                            scan['invalid_recheck_reports'] += 1
                if not valid_report:
                    scan['unverified_rechecks'] += 1
                    continue
                try:
                    contents = retained_bytes(root, run, key + '.log', remaining)
                    remaining -= len(contents)
                    if sha256(contents).hexdigest() != log_digest:
                        raise ValueError('Changed recheck log')
                except ScanBudgetReached:
                    scan['bytes_limited'] = True
                    scan['unverified_rechecks'] += 1
                    continue
                except (OSError, ValueError):
                    scan['unverified_rechecks'] += 1
                    continue
                for group in rechecked:
                    del pending[group]

    scan['coverage'] = 'complete' if count else 'no_retained_runs'
    if scan['older_runs_omitted'] or scan['unavailable'] or scan['unverified_rechecks'] or scan['bytes_limited']:
        scan['coverage'] = 'incomplete'
    outcomes = {outcome: {'matching_inputs': [], 'other_or_unverified_inputs': []} for outcome in ('failed', 'blocked')}
    for (identity, stable, outcome), (_, run, observation, first_run) in sorted(pending.items(), key=lambda row: row[1], reverse=True):
        inputs, project, system, arch, key, command = identity
        name = 'matching_inputs' if stable and (inputs, project) == reference else 'other_or_unverified_inputs'
        report_path = (directory / run / 'test-report.json').relative_to(root).as_posix()
        item = {'check_id': key, 'run_id': run, 'report': report_path, 'system': system, 'machine': arch,
                'inputs_sha256': inputs, 'stable_inputs': stable, 'requires_observation_review': observation}
        if outcome == 'blocked':
            item.update(first_blocked_run=first_run, reason_code='executable_unavailable', executed=False)
            if command[0].isprintable() and len(command[0]) <= 256:
                item['executable'] = command[0]
        else:
            item['first_failed_run'] = first_run
        outcomes[outcome][name].append(item)

    def summarize(groups):
        return {name: {'count': len(items), 'items': items if detail else items[:(10 if name == 'matching_inputs' else 3)]}
                for name, items in groups.items()}

    return {
        'basis': basis, 'scan': scan, **summarize(outcomes['failed']),
        'environment_blockers': summarize(outcomes['blocked']),
        'scope': 'Local check diagnostics. Matching rechecks use the same execution and project inputs, command and platform. '
                 'An unavailable executable left the selected check unexecuted. Without schema validation, rechecks remain pending. Other inputs need separate triage. '
                 'Product, field and release acceptance use their own evidence.',
    }
