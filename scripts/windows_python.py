"""Run and combine the two ordered parts of ordinary Windows PR Python discovery."""

import atexit
from datetime import datetime, timedelta
from hashlib import file_digest, sha256
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
import unittest

CHECK = 'python-contracts-workers'
LANES = ('python', 'recovery')
CUTOFF = 'tests.project.test_verification_scope'
COMMAND = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
           '-p', 'test_*.py', '-v', '--durations', '0']
REPORT_VERSION = '2.1'
COUNTS = ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses')
FAILURES = ('failures', 'errors', 'unexpectedSuccesses')
# Process duration is rounded independently to milliseconds by the harness.
CLOCK_ROUNDING = .002
SEPARATOR = b'-' * 70
COUNT_NAMES = {'expectedFailures': 'expected failures', 'unexpectedSuccesses': 'unexpected successes'}
OUTCOMES = {'ok': None, 'FAIL': 'failures', 'ERROR': 'errors', 'skipped': 'skipped',
            'expected failure': 'expectedFailures', 'unexpected success': 'unexpectedSuccesses'}
SETUP = ('setUpClass', 'setUpModule')
FIXTURES = (*SETUP, 'tearDownClass', 'tearDownModule')


def enabled(project, ci, selected):
    return (ci and ci['event'] == 'pull_request' and CHECK in selected
            and project['checks'][CHECK]['command'] == COMMAND)


def part_id(lane):
    return f'windows-python-{lane}'


def command(python, lane):
    return [python, 'scripts/windows_python.py', lane]


def partition(inventory):
    if (not isinstance(inventory, list) or not inventory or not all(isinstance(case, str) and len(case) > 2 for case in inventory)
            or len(inventory) != len(set(inventory))):
        raise ValueError('Python discovery has invalid or duplicate test IDs.')
    # Unittest represents discovery errors and module skips as test cases of its own.
    # Give them the original module's position and keep the original IDs in evidence.
    modules = []
    for case in inventory:
        for prefix in ('unittest.loader._FailedTest.', 'unittest.loader.ModuleSkipped.'):
            if case.startswith(prefix):
                modules.append(case.removeprefix(prefix))
                break
        else:
            modules.append(case.rsplit('.', 2)[0])
    boundary = next((i for i, module in enumerate(modules) if module >= CUTOFF), len(modules))
    if any(module < CUTOFF for module in modules[boundary:]):
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
        self.events = []
        self.active = None

    def startTestRun(self):
        self.started = perf_counter()

    def stopTestRun(self):
        self.elapsed = perf_counter() - self.started

    def startTest(self, test):
        self.active = test
        self.cases.append([test.id(), str(test), None, False, False])
        super().startTest(test)

    def addDuration(self, test, elapsed):
        self.cases[-1][2] = elapsed
        super().addDuration(test, elapsed)

    def stopTest(self, test):
        self.cases[-1][4] = True
        self.active = None
        super().stopTest(test)

    def event(self, test, outcome, detail=None, *, owner=None):
        owner_id = owner.id() if owner is not None else test.id()
        self.events.append([owner_id, self.getDescription(test), outcome, detail, owner is not None])

    def addSuccess(self, test):
        super().addSuccess(test)
        self.event(test, 'ok')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.event(test, 'FAIL', self.failures[-1][1])

    def addError(self, test, err):
        super().addError(test, err)
        self.event(test, 'ERROR', self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        owner = None
        if test is not self.active and getattr(test, 'test_case', None) is self.active:
            owner = self.active
        if test is self.active:
            self.cases[-1][3] = True
        self.event(test, 'skipped', reason, owner=owner)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.event(test, 'expected failure', self.expectedFailures[-1][1])

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.event(test, 'unexpected success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            if issubclass(err[0], subtest.failureException):
                self.event(subtest, 'FAIL', self.failures[-1][1], owner=test)
            else:
                self.event(subtest, 'ERROR', self.errors[-1][1], owner=test)


def write_manifest(label, result, inventory):
    if result is None:
        return
    # The harness redirects both standard streams to this original from process launch.
    # Register before discovery so ordinary test shutdown output is included as well.
    sys.stdout.flush()
    sys.stderr.flush()
    destination = Path(os.environ['SCOPEHUB_RUN_DIR']) / (label + '.json')
    with destination.with_suffix('.log').open('rb') as raw:
        log_digest = file_digest(raw, 'sha256').hexdigest()
        log_size = os.fstat(raw.fileno()).st_size
    manifest = {
        'inventory': inventory,
        'cases': result.cases,
        'counts': {key: len(getattr(result, key)) for key in COUNTS},
        'events': result.events,
        'runner_seconds': result.elapsed,
        'log_size': log_size,
        'log_sha256': log_digest,
    }
    pending = destination.with_suffix('.tmp')
    pending.write_bytes((json.dumps(manifest, allow_nan=False) + '\n').encode('utf-8'))
    pending.replace(destination)


def run(lane):
    result = None
    inventory = []
    atexit.register(lambda: write_manifest(part_id(lane), result, inventory))
    discovered = list(tests(unittest.defaultTestLoader.discover('tests', 'test_*.py', '.')))
    inventory = [test.id() for test in discovered]
    live = set(partition(inventory)[lane])
    suite = unittest.TestSuite(test for test in discovered if test.id() in live)
    warnings = None if sys.warnoptions else 'default'
    result = unittest.TextTestRunner(verbosity=2, durations=0, warnings=warnings, resultclass=PartResult).run(suite)
    return 0 if result.wasSuccessful() else 1


def verify_manifest(manifest, lane):
    if not isinstance(manifest, dict) or set(manifest) != {
            'inventory', 'cases', 'counts', 'events', 'runner_seconds', 'log_size', 'log_sha256'}:
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
        if not case[1].endswith(f' ({case[0]})'):
            raise ValueError('Windows Python unittest display differs from its discovered test ID.')
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
    return expected


def unittest_verdict(counts, tests_run):
    status = b'FAILED' if any(counts[key] for key in FAILURES) else b'OK'
    if status == b'OK' and not tests_run and not counts['skipped']:
        status = b'NO TESTS RAN'
    detail = ', '.join(f'{COUNT_NAMES.get(key, key)}={counts[key]}' for key in COUNTS if counts[key])
    return status + (f' ({detail})'.encode('ascii') if detail else b'')


def verify_events(manifest, expected):
    cases = {identity: (display, skipped) for identity, display, _, skipped, _ in manifest['cases']}
    events = manifest['events']
    if not isinstance(events, list):
        raise ValueError('Invalid unittest callback events.')
    counts = dict.fromkeys(COUNTS, 0)
    setup_blocks = []
    by_case = {}
    positions = {case: index for index, case in enumerate(cases)}
    previous = -1
    progress, diagnostics = [], []
    for event in events:
        if (not isinstance(event, list) or len(event) != 5
                or not all(isinstance(value, str) for value in event[:3]) or type(event[4]) is not bool):
            raise ValueError('Invalid unittest callback event.')
        owner, description, outcome, detail, subtest = event
        detailed = outcome in {'FAIL', 'ERROR', 'skipped', 'expected failure'}
        if outcome not in OUTCOMES or (not isinstance(detail, str) if detailed else detail is not None):
            raise ValueError('Invalid unittest callback outcome or detail.')
        if owner in cases:
            display, _ = cases[owner]
            valid = (description.startswith(display + ' ') and outcome in {'FAIL', 'ERROR', 'skipped'}
                     if subtest else description.partition('\n')[0] == display)
            if not valid:
                raise ValueError('Unittest callback diagnostic belongs to another case.')
            if positions[owner] < previous:
                raise ValueError('Unittest callback outcomes are out of case order.')
            previous = positions[owner]
            by_case.setdefault(owner, []).append((outcome, subtest))
        else:
            hook, _, fixture = owner.partition(' (')
            prefix = fixture.removesuffix(')') + '.'
            if (subtest or hook not in FIXTURES or not fixture.endswith(')') or description != owner
                    or outcome not in {'ERROR', 'skipped'} or not any(case.startswith(prefix) for case in expected)):
                raise ValueError('Unittest callback has an unknown case or fixture owner.')
            if hook in SETUP:
                setup_blocks.append(prefix)
        if count := OUTCOMES[outcome]:
            counts[count] += 1
        status = f'skipped {detail!r}' if outcome == 'skipped' else outcome
        indent = '  ' if subtest else ''
        progress.append(f'{indent}{description} ... {status}\n')
        if outcome in {'FAIL', 'ERROR'}:
            diagnostics.append('=' * 70 + f'\n{outcome}: {description}\n' + '-' * 70 + f'\n{detail}\n')
        elif outcome == 'unexpected success':
            diagnostics.append('=' * 70 + f'\nUNEXPECTED SUCCESS: {description}\n')

    unblocked = [case for case in expected if not case.startswith(tuple(setup_blocks))]
    if list(by_case) != unblocked or list(cases) != unblocked or counts != manifest['counts']:
        raise ValueError('Unittest callback order, counts or fixture outcomes differ from discovery.')
    for case, outcomes in by_case.items():
        if (cases[case][1] != (('skipped', False) in outcomes)
                or ('ok', False) in outcomes and outcomes != [('ok', False)]):
            raise ValueError('Unittest callback outcomes contradict the case record.')
    return (''.join(progress).encode('utf-8', errors='backslashreplace'),
            ''.join(diagnostics).encode('utf-8', errors='backslashreplace'))


def case_times(manifests):
    return sorted(((name, elapsed) for manifest in manifests
                   for _, name, elapsed, _, _ in manifest['cases'] if elapsed is not None),
                  key=lambda row: row[1], reverse=True)


def duration_lines(durations):
    if not durations:
        return []
    return [b'Slowest test durations', SEPARATOR,
            *(f'{elapsed:.3f}s'.ljust(10).encode('ascii') + b' ' + name.encode('utf-8')
              for name, elapsed in durations), b'']


def process_interval(record):
    clock = datetime.fromisoformat(record['started_at'])
    duration = record['duration_seconds']
    if (clock.utcoffset() is None or type(duration) not in (int, float) or not math.isfinite(duration)
            or not 0 <= duration <= timedelta.max.total_seconds()):
        raise ValueError('Invalid Windows Python process clock or duration.')
    try:
        return clock, clock + timedelta(seconds=duration)
    except (OverflowError, ValueError) as error:
        raise ValueError('Invalid Windows Python process clock interval.') from error


def verify_original(record, raw, manifest, lane):
    expected = verify_manifest(manifest, lane)
    size = manifest['log_size']
    if (type(size) is not int or not 0 < size <= len(raw)
            or sha256(raw[:size]).hexdigest() != manifest['log_sha256']):
        raise ValueError('Original unittest log differs from the runner-bound bytes.')
    trailer = raw[size:]
    reason = record.get('reason_code')
    if trailer and not reason:
        raise ValueError('Unexpected output after the original unittest run.')
    verdict = unittest_verdict(manifest['counts'], len(manifest['cases']))
    exit_code = int(verdict.startswith(b'FAILED'))
    if not reason and (record.get('exit_code') != exit_code or (record['status'] == 'passed') != (exit_code == 0)):
        raise ValueError('Original unittest verdict does not match the process result.')
    runner_seconds = manifest['runner_seconds']
    start, end = process_interval(record)
    if (type(runner_seconds) not in (int, float) or not math.isfinite(runner_seconds)
            or not 0 <= runner_seconds <= (end - start).total_seconds() + CLOCK_ROUNDING
            or sum(elapsed for _, elapsed in case_times([manifest])) > runner_seconds + CLOCK_ROUNDING):
        raise ValueError('Original unittest runner duration exceeds its process duration or understates case time.')
    progress, diagnostics = verify_events(manifest, expected)
    return progress, diagnostics + trailer


def elapsed_seconds(parts):
    times = [process_interval(record) for record, _, _ in parts if 'started_at' in record]
    return round((max(end for _, end in times) - min(start for start, _ in times)).total_seconds(), 3) if times else 0


def combine(parts):
    """Build the canonical check log and verdict. Original producer logs stay separate."""
    completed, outputs = [], []
    result = 'passed'
    for lane, (record, raw, manifest) in zip(LANES, parts, strict=True):
        passed = record['status'] == 'passed'
        if not passed:
            result = 'failed'
        if manifest is not None:
            outputs.append(verify_original(record, raw, manifest, lane))
            completed.append(manifest)
        elif passed:
            raise ValueError('Successful Windows Python part has no case manifest.')
    if len(completed) == 2 and completed[0]['inventory'] != completed[1]['inventory']:
        raise ValueError('Windows Python parts have different ordered discovery inventories.')
    if len(completed) < 2:
        log = b''.join(f'\n=== Windows Python {lane} part ===\n'.encode() + raw
                       for lane, (_, raw, _) in zip(LANES, parts, strict=True))
        return result, log

    progress = b''.join(lines for lines, _ in outputs)
    details = b''.join(lines for _, lines in outputs)
    count = sum(len(manifest['cases']) for manifest in completed)
    elapsed = elapsed_seconds(parts)
    totals = {key: sum(manifest['counts'][key] for manifest in completed) for key in COUNTS}
    verdict = unittest_verdict(totals, count)
    if result == 'failed' and verdict.startswith(b'OK'):
        verdict = b'FAILED (part process did not complete successfully)'
    times = duration_lines(case_times(completed))
    summary = [*times, b'', SEPARATOR,
               f'Ran {count} test{"s" if count != 1 else ""} in {elapsed:.3f}s'.encode('ascii'), b'', verdict]
    return result, progress + details + b'\n'.join(summary) + b'\n'



if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in LANES:
        raise SystemExit('Expected one Windows Python producer lane.')
    raise SystemExit(run(sys.argv[1]))
