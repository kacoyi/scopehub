"""Run and combine the two ordered parts of ordinary Windows PR Python discovery."""

from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
import unittest

CHECK = 'python-contracts-workers'
LANES = ('python', 'recovery')
CUTOFF = 'tests.project.test_verification_scope'
COMMAND = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
           '-p', 'test_*.py', '-v', '--durations', '0']
PROGRESS_END = b'=== ScopeHub Python part: unittest diagnostics ==='
DIAGNOSTICS_END = b'=== ScopeHub Python part: unittest summary ==='
COUNTS = ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses')
FAILURES = ('failures', 'errors', 'unexpectedSuccesses')


def enabled(project, ci, selected):
    return (ci and ci['event'] == 'pull_request' and CHECK in selected
            and project['checks'][CHECK]['command'] == COMMAND)


def part_id(lane):
    return f'windows-python-{lane}'


def command(python, lane):
    return [python, 'scripts/windows_python.py', lane]


def partition(inventory):
    if (not isinstance(inventory, list) or not all(isinstance(case, str) and len(case) > 2 for case in inventory)
            or len(inventory) != len(set(inventory))):
        raise ValueError('Python discovery has invalid or duplicate test IDs.')
    # Keep whole modules and their class/module fixtures in the original order.
    boundary = next((i for i, case in enumerate(inventory) if case.startswith(CUTOFF + '.')), 0)
    if not boundary:
        raise ValueError('Windows Python discovery is missing its partition boundary.')
    if any(case.rsplit('.', 2)[0] >= CUTOFF for case in inventory[:boundary]) or any(
            case.rsplit('.', 2)[0] < CUTOFF for case in inventory[boundary:]):
        raise ValueError('Windows Python discovery is not ordered by module at the partition boundary.')
    return dict(zip(LANES, (inventory[:boundary], inventory[boundary:]), strict=True))


def tests(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from tests(test)
        else:
            yield test


class PartResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.fixture_skips = []

    def startTest(self, test):
        self.cases.append([test.id(), str(test), None, False, False])
        super().startTest(test)

    def addDuration(self, test, elapsed):
        self.cases[-1][2] = elapsed
        super().addDuration(test, elapsed)

    def addSkip(self, test, reason):
        identity = test.id()
        if self.cases and self.cases[-1][0] == identity:
            self.cases[-1][3] = True
        elif identity.startswith(('setUpClass (', 'setUpModule (')):
            self.fixture_skips.append(identity)
        super().addSkip(test, reason)

    def stopTest(self, test):
        self.cases[-1][4] = True
        super().stopTest(test)

    def printErrors(self):
        self.stream.writeln(PROGRESS_END.decode('ascii'))
        super().printErrors()


class PartRunner(unittest.TextTestRunner):
    def _printDurations(self, result):
        # Delimit the actual runner diagnostics for the catalog-order aggregate log.
        self.stream.writeln(DIAGNOSTICS_END.decode('ascii'))
        super()._printDurations(result)


def run(lane):
    discovered = list(tests(unittest.defaultTestLoader.discover('tests', 'test_*.py', '.')))
    inventory = [test.id() for test in discovered]
    live = set(partition(inventory)[lane])
    suite = unittest.TestSuite(test for test in discovered if test.id() in live)
    warnings = None if sys.warnoptions else 'default'
    result = PartRunner(verbosity=2, durations=0, warnings=warnings, resultclass=PartResult).run(suite)
    manifest = {
        'inventory': inventory,
        'cases': result.cases,
        'counts': {key: len(getattr(result, key)) for key in COUNTS},
        'fixture_skips': result.fixture_skips,
    }
    destination = Path(os.environ['SCOPEHUB_RUN_DIR']) / (part_id(lane) + '.json')
    pending = destination.with_suffix('.tmp')
    pending.write_bytes((json.dumps(manifest, allow_nan=False) + '\n').encode('utf-8'))
    pending.replace(destination)
    return 0 if result.wasSuccessful() else 1


def verify_manifest(manifest, lane, passed):
    if not isinstance(manifest, dict) or set(manifest) != {'inventory', 'cases', 'counts', 'fixture_skips'}:
        raise ValueError('Incomplete Windows Python case manifest.')
    expected = partition(manifest['inventory'])[lane]
    cases = manifest['cases']
    if not isinstance(cases, list):
        raise ValueError('Invalid Windows Python case list.')
    for case in cases:
        if (not isinstance(case, list) or len(case) != 5
                or not all(isinstance(value, str) for value in case[:2])
                or type(case[3]) is not bool or case[4] is not True):
            raise ValueError('Incomplete Windows Python case outcome.')
        elapsed = case[2]
        if elapsed is None and not case[3] or elapsed is not None and (
                type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError('Missing or invalid duration for a non-skipped Windows Python case.')
    started = [case[0] for case in cases]
    started_set = set(started)
    if [case for case in expected if case in started_set] != started:
        raise ValueError('Windows Python part has unexpected, duplicate or reordered tests.')
    counts = manifest['counts']
    if (not isinstance(counts, dict) or set(counts) != set(COUNTS)
            or not all(type(count) is int and 0 <= count for count in counts.values())):
        raise ValueError('Invalid Windows Python result totals.')
    skips = manifest['fixture_skips']
    if not isinstance(skips, list) or len(skips) + sum(case[3] for case in cases) > counts['skipped']:
        raise ValueError('Invalid Windows Python fixture skips.')
    skipped_prefixes = []
    for skip in skips:
        if not isinstance(skip, str) or not skip.startswith(('setUpClass (', 'setUpModule (')) or not skip.endswith(')'):
            raise ValueError('Invalid Windows Python fixture skip.')
        skipped_prefixes.append(skip.split(' (', 1)[1][:-1] + '.')
    if any(case.startswith(tuple(skipped_prefixes)) for case in started):
        raise ValueError('Windows Python fixture skips overlap executed outcomes.')
    if passed and (any(counts[key] for key in FAILURES) or any(
            case not in started_set and not case.startswith(tuple(skipped_prefixes)) for case in expected)):
        raise ValueError('Successful Windows Python part has incomplete test outcomes.')


def log_sections(raw):
    lines = raw.splitlines(keepends=True)
    first = [index for index, line in enumerate(lines) if line.rstrip(b'\r\n') == PROGRESS_END]
    second = [index for index, line in enumerate(lines) if line.rstrip(b'\r\n') == DIAGNOSTICS_END]
    if len(first) != 1 or len(second) != 1 or first[0] >= second[0]:
        raise ValueError('Windows Python part has incomplete or ambiguous unittest output.')
    return b''.join(lines[:first[0]]), b''.join(lines[first[0] + 1:second[0]])


def elapsed_seconds(parts):
    times = [(datetime.fromisoformat(record['started_at']).timestamp(), record['duration_seconds'])
             for record, _, _ in parts if 'started_at' in record]
    return round(max(start + length for start, length in times) - min(start for start, _ in times), 3) if times else 0


def combine(parts):
    """Build the canonical check log and verdict. Original producer logs stay separate."""
    completed = []
    result = 'passed'
    for lane, (record, raw, manifest) in zip(LANES, parts, strict=True):
        passed = record['status'] == 'passed'
        if not passed:
            result = 'failed'
        if manifest is not None:
            verify_manifest(manifest, lane, passed)
            completed.append(manifest)
        elif passed:
            raise ValueError('Successful Windows Python part has no case manifest.')
    if len(completed) == 2 and completed[0]['inventory'] != completed[1]['inventory']:
        raise ValueError('Windows Python parts have different ordered discovery inventories.')
    if len(completed) < 2:
        log = b''.join(f'\n=== Windows Python {lane} part ===\n'.encode() + raw
                       for lane, (_, raw, _) in zip(LANES, parts, strict=True))
        return result, log

    outputs = [log_sections(raw) for _, raw, _ in parts]
    progress = b''.join(lines for lines, _ in outputs)
    details = b''.join(lines for _, lines in outputs)
    durations = sorted(((name, elapsed) for manifest in completed
                        for _, name, elapsed, _, _ in manifest['cases'] if elapsed is not None),
                       key=lambda row: row[1], reverse=True)
    summary = []
    if durations:
        summary.append('Slowest test durations\n----------------------------------------------------------------------\n')
        summary.extend(f'{elapsed:.3f}s'.ljust(10) + f' {name}\n' for name, elapsed in durations)
        summary.append('\n')
    count = sum(len(manifest['cases']) for manifest in completed)
    elapsed = elapsed_seconds(parts)
    summary.append(f'\n----------------------------------------------------------------------\nRan {count} tests in {elapsed:.3f}s\n\n')
    totals = [(key, sum(manifest['counts'][key] for manifest in completed)) for key in COUNTS]
    names = {'expectedFailures': 'expected failures', 'unexpectedSuccesses': 'unexpected successes'}
    annotations = ', '.join(f'{names.get(key, key)}={value}' for key, value in totals if value)
    outcome = 'OK' if result == 'passed' else 'FAILED'
    summary.append(outcome + (f' ({annotations})' if annotations else '') + '\n')
    return result, progress + details + ''.join(summary).encode('utf-8')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in LANES:
        raise SystemExit('Expected one Windows Python producer lane.')
    raise SystemExit(run(sys.argv[1]))
