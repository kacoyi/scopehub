"""Run and combine the two ordered parts of ordinary Windows PR Python discovery."""

from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
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
# The two clocks and the unittest total are rendered independently to milliseconds.
CLOCK_ROUNDING = .002
SEPARATOR = b'-' * 70
DISPLAY = re.compile(rb'^([^\s()]+ \([^\s()]+\))(?= \.\.\. |\r?\n[^\r\n]* \.\.\. )', re.M)
SUBTEST = re.compile(rb'^[ \t]+[^ \t].* \.\.\. (FAIL|ERROR|skipped .+)$')
SUMMARY = re.compile(rb'Ran (0|[1-9][0-9]*) test(s?) in ([0-9]+\.[0-9]{3})s')
RESULT_COUNTS = {b'FAIL': 'failures', b'ERROR': 'errors', b'expected failure': 'expectedFailures',
                 b'unexpected success': 'unexpectedSuccesses'}
COUNT_NAMES = {'expectedFailures': 'expected failures', 'unexpectedSuccesses': 'unexpected successes'}
FIXTURE_EVENTS = (b'setUpClass (', b'setUpModule (', b'tearDownClass (', b'tearDownModule (')


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
    return (b''.join(lines[:first[0]]), b''.join(lines[first[0] + 1:second[0]]),
            lines[second[0] + 1:])


def unittest_verdict(counts, tests_run):
    status = b'FAILED' if any(counts[key] for key in FAILURES) else b'OK'
    if status == b'OK' and not tests_run and not counts['skipped']:
        status = b'NO TESTS RAN'
    detail = ', '.join(f'{COUNT_NAMES.get(key, key)}={counts[key]}' for key in COUNTS if counts[key])
    return status + (f' ({detail})'.encode('ascii') if detail else b'')


def verify_progress(progress, manifest):
    """Bind the raw verbose test results, including synthetic unittest fixture events."""
    rows = list(DISPLAY.finditer(progress))
    cases, fixture_skips = [], []
    counts = dict.fromkeys(COUNTS, 0)
    for i, row in enumerate(rows):
        label = row[1]
        stop = rows[i + 1].start() if i + 1 < len(rows) else len(progress)
        _, _, output = progress[row.end():stop].partition(b' ... ')
        lines = output.splitlines()
        main = [line for line in lines if line == b'ok' or line in RESULT_COUNTS or line.startswith(b'skipped ')]
        subtests = [match[1] for line in lines if (match := SUBTEST.fullmatch(line))]
        if len(main) > 1 or not (main or subtests):
            raise ValueError('Original unittest output has an incomplete or ambiguous test outcome.')
        skipped = bool(main and main[0].startswith(b'skipped '))
        for outcome in main + subtests:
            if outcome.startswith(b'skipped '):
                counts['skipped'] += 1
            elif outcome in RESULT_COUNTS:
                counts[RESULT_COUNTS[outcome]] += 1
        if label.startswith(FIXTURE_EVENTS):
            if skipped and label.startswith(FIXTURE_EVENTS[:2]):
                fixture_skips.append(label)
        else:
            cases.append((label, skipped))
    reported = [(display.encode('utf-8'), skipped) for _, display, _, skipped, _ in manifest['cases']]
    reported_skips = [fixture.encode('utf-8') for fixture in manifest['fixture_skips']]
    if cases != reported or fixture_skips != reported_skips or counts != manifest['counts']:
        raise ValueError('Original unittest ordered outcomes or skips differ from the case manifest.')


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
    passed = record['status'] == 'passed'
    verify_manifest(manifest, lane, passed)
    progress, details, tail = log_sections(raw)
    verify_progress(progress, manifest)
    counts = manifest['counts']
    for heading, key in ((b'FAIL: ', 'failures'), (b'ERROR: ', 'errors'),
                         (b'UNEXPECTED SUCCESS: ', 'unexpectedSuccesses')):
        if sum(line.startswith(heading) for line in details.splitlines()) != counts[key]:
            raise ValueError('Original unittest failure diagnostics differ from the case manifest.')
    durations = case_times([manifest])
    lines = [line.rstrip(b'\r\n') for line in tail]
    before_summary = [*duration_lines(durations), SEPARATOR]
    offset = len(before_summary)
    if lines[:offset] != before_summary:
        raise ValueError('Original unittest case durations differ from the case manifest.')
    summary = SUMMARY.fullmatch(lines[offset]) if len(lines) >= offset + 3 else None
    count = len(manifest['cases'])
    verdict = unittest_verdict(counts, count)
    if (not summary or int(summary[1]) != count or bool(summary[2]) != (count != 1)
            or lines[offset + 1:offset + 3] != [b'', verdict]):
        raise ValueError('Original unittest run count or terminal verdict differs from the case manifest.')
    runner_seconds = float(summary[3])
    start, end = process_interval(record)
    if (not math.isfinite(runner_seconds)
            or runner_seconds > (end - start).total_seconds() + CLOCK_ROUNDING
            or sum(elapsed for _, elapsed in durations) > runner_seconds + CLOCK_ROUNDING):
        raise ValueError('Original unittest runner duration exceeds its process duration or understates case time.')
    trailer = b''.join(tail[offset + 3:])
    if (trailer.strip() and not record.get('reason_code')
            or not record.get('reason_code') and record.get('exit_code') != int(verdict.startswith(b'FAILED'))):
        raise ValueError('Original unittest verdict does not match the process result.')
    return progress, details + trailer


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
