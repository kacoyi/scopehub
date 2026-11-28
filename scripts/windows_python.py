"""Run and combine the two ordered parts of ordinary Windows PR Python discovery."""

import atexit
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys
from time import perf_counter
import unittest

CHECK = 'python-contracts-workers'
LANES = ('python', 'recovery')
LEGACY_CUTOFF = 'tests.project.test_verification_scope'
REVIEW_ACCEPTANCE_CUTOFF = 'tests.project.test_review_acceptance'
SCHEDULING_CUTOFF = 'tests.project.test_verification_scheduling'
STORAGE_CUTOFF = 'tests.project.test_storage'
CUTOFF = 'tests.project.test_processes'
# The publisher reads the transfer from the tested commit.
RECOVERY_PR_CHECKS = set()
COMMAND = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
           '-p', 'test_*.py', '-v', '--durations', '0']
REPORT_VERSION = '2.1'
# Publication also reads this declaration from the tested commit.
CALLBACK_RECORD_VERSION = 1
CALLBACK_RECORD = re.compile(
    rb'\r?\nScopeHub unittest callbacks v1: ([0-9]{1,20}) ([a-f0-9]{64}) ([a-f0-9]{64})(?=\r?\n)',
)
CALLBACK_FIELDS = ('inventory', 'cases', 'counts', 'events', 'runner_seconds')
COUNTS = ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses')
FAILURES = ('failures', 'errors', 'unexpectedSuccesses')
# Process duration is rounded independently to milliseconds by the harness.
CLOCK_ROUNDING = .002
SEPARATOR = b'-' * 70
LEGACY_LOG_LIMIT = 8 * 1024 * 1024
LEGACY_LINE_LIMIT = 64 * 1024
LEGACY_LINE_COUNT_LIMIT = 100_000
RUNNER_TOTAL = re.compile(rb'Ran ([0-9]+) tests? in [0-9]+\.[0-9]{3}s')
RUNNER_DURATION_ROW = re.compile(rb'[0-9]+\.[0-9]{3}s +[^ \t].*')
RUNNER_SKIP_COUNT = re.compile(rb'[(, ]skipped=([0-9]+)')
RUNNER_VERDICT = re.compile(rb'(?:OK|FAILED|NO TESTS RAN)(?: \([^()]+\))?')
RUNNER_NAME = re.compile(rb' \(([^\s()]+)\)')
RUNNER_FIXTURE = re.compile(rb'(?:setUp|tearDown)(?:Class|Module) \(')
RUNNER_SKIP = re.compile(rb'''skipped ['"]''')
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


def partition(inventory, *, cutoff=None):
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
    if cutoff is None:
        cutoff = CUTOFF
    boundary = next((i for i, module in enumerate(modules) if module >= cutoff), len(modules))
    if any(module < cutoff for module in modules[boundary:]):
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


def callback_bytes(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def write_callback_record(log, payload):
    sys.stdout.flush()
    sys.stderr.flush()
    with open(log, 'rb') as raw:
        prefix = raw.read(os.fstat(raw.fileno()).st_size)
    outcome = sha256(callback_bytes(payload)).hexdigest()
    record = (f'\nScopeHub unittest callbacks v{CALLBACK_RECORD_VERSION}: '
              f'{len(prefix)} {sha256(prefix).hexdigest()} {outcome}\n').encode('ascii')
    if os.write(2, record) != len(record):
        raise OSError('Could not write the complete unittest callback record.')


def write_manifest(destination, payload):
    if payload is None:
        return
    # Bind the output observed inside the interpreter. The harness separately seals
    # the original stream after the process exits and its redirection is closed.
    sys.stdout.flush()
    sys.stderr.flush()
    log_digest = sha256()
    log_size = 0
    with destination.with_suffix('.log').open('rb') as raw:
        while chunk := raw.read(256 * 1024):
            log_digest.update(chunk)
            log_size += len(chunk)
    manifest = {
        **payload,
        'callback_record_version': CALLBACK_RECORD_VERSION,
        'log_size': log_size,
        'log_sha256': log_digest.hexdigest(),
    }
    write_manifest_file(destination, manifest)


def write_manifest_file(destination, manifest):
    pending = destination.with_suffix('.tmp')
    pending.write_bytes((json.dumps(manifest, allow_nan=False) + '\n').encode('utf-8'))
    pending.replace(destination)


def seal_manifest(destination, log_sha256):
    manifest = json.loads(destination.read_bytes())
    if not isinstance(manifest, dict) or 'final_log_sha256' in manifest:
        raise ValueError('Invalid unsealed Windows Python runner manifest.')
    manifest['final_log_sha256'] = log_sha256
    write_manifest_file(destination, manifest)


def run(lane):
    payload = None
    destination = Path(os.environ['SCOPEHUB_RUN_DIR']) / (part_id(lane) + '.json')
    atexit.register(lambda: write_manifest(destination, payload))
    discovered = list(tests(unittest.defaultTestLoader.discover('tests', 'test_*.py', '.')))
    inventory = [test.id() for test in discovered]
    # These are the same owners unittest uses to invoke module and class fixtures.
    owners = {test.id(): [type(test).__module__, unittest.util.strclass(type(test))] for test in discovered}
    live = set(partition(inventory)[lane])
    suite = unittest.TestSuite(test for test in discovered if test.id() in live)
    warnings = None if sys.warnoptions else 'default'
    result = unittest.TextTestRunner(verbosity=2, durations=0, warnings=warnings, resultclass=PartResult).run(suite)
    outcomes = {
        'inventory': inventory,
        'owners': owners,
        'cases': result.cases,
        'counts': {key: len(getattr(result, key)) for key in COUNTS},
        'events': result.events,
        'runner_seconds': result.elapsed,
    }
    # Only this process receives the callbacks. Seal their payload alongside the
    # original transcript before fixture shutdown can append any later output.
    write_callback_record(destination.with_suffix('.log'), outcomes)
    payload = outcomes
    return 0 if result.wasSuccessful() else 1


def verify_manifest(manifest, lane, *, cutoff=None):
    required = {*CALLBACK_FIELDS, 'log_size', 'log_sha256'}
    if (not isinstance(manifest, dict) or required - manifest.keys()
            or manifest.keys() - required - {'owners', 'final_log_sha256', 'callback_record_version'}):
        raise ValueError('Incomplete Windows Python case manifest.')
    expected = partition(manifest['inventory'], cutoff=cutoff)[lane]
    if 'owners' in manifest:
        owners = manifest['owners']
        if (not isinstance(owners, dict) or owners.keys() != set(manifest['inventory'])
                or any(not isinstance(row, list) or len(row) != 2
                       or any(not isinstance(value, str) or not value for value in row)
                       or not row[1].startswith(row[0] + '.') for row in owners.values())):
            raise ValueError('Invalid Windows Python discovered fixture owners.')
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
    setup_blocks = set()
    # Older structured archives recorded the conventional unittest IDs only.
    owners = manifest.get('owners')
    by_case = {}
    positions = {case: index for index, case in enumerate(cases)}
    previous = -1
    progress, diagnostics, runner_progress = [], [], []
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
            if subtest and owner not in by_case:
                # A first subtest callback closes the unfinished parent header.
                runner_progress.append((display, None))
            by_case.setdefault(owner, []).append((outcome, subtest))
        else:
            hook, _, fixture = owner.partition(' (')
            if (subtest or hook not in FIXTURES or not fixture.endswith(')') or description != owner
                    or outcome not in {'ERROR', 'skipped'}):
                raise ValueError('Unittest callback has an unknown case or fixture owner.')
            name = fixture[:-1]
            class_fixture = hook.endswith('Class')
            if owners is not None:
                members = {case for case in expected if owners[case][int(class_fixture)] == name}
            elif class_fixture:
                members = {case for case in expected if case.rsplit('.', 1)[0] == name}
            else:
                # Legacy IDs share a dotted spelling for nested classes and submodules.
                # Keep conventional module matches and account for omitted nested cases
                # while preserving execution in continuing submodules.
                members = {case for case in expected if case.rsplit('.', 2)[0] == name
                           or (hook not in SETUP or case not in cases)
                           and case.rsplit('.', 1)[0].startswith(name + '.')}
            if not members:
                raise ValueError('Unittest callback has an unknown case or fixture owner.')
            if hook in SETUP:
                setup_blocks.update(members)
        if count := OUTCOMES[outcome]:
            counts[count] += 1
        status = f'skipped {detail!r}' if outcome == 'skipped' else outcome
        indent = '  ' if subtest else ''
        runner_progress.append((f'{indent}{description} ... ', status))
        progress.append(f'{indent}{description} ... {status}\n')
        if outcome in {'FAIL', 'ERROR'}:
            diagnostics.append('=' * 70 + f'\n{outcome}: {description}\n' + '-' * 70 + f'\n{detail}\n')
        elif outcome == 'unexpected success':
            diagnostics.append('=' * 70 + f'\nUNEXPECTED SUCCESS: {description}\n')

    unblocked = [case for case in expected if case not in setup_blocks]
    if list(by_case) != unblocked or list(cases) != unblocked or counts != manifest['counts']:
        raise ValueError('Unittest callback order, counts or fixture outcomes differ from discovery.')
    for case, outcomes in by_case.items():
        if (cases[case][1] != (('skipped', False) in outcomes)
                or ('ok', False) in outcomes and outcomes != [('ok', False)]):
            raise ValueError('Unittest callback outcomes contradict the case record.')
    return (''.join(progress).encode('utf-8', errors='backslashreplace'),
            ''.join(diagnostics).encode('utf-8', errors='backslashreplace'),
            runner_progress)


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


def legacy_footers(lines):
    """Find every possible outer footer under the historical verbose runner format."""
    max_digits = len(str(len(lines)))
    for total_line, line in enumerate(lines):
        # An emitted count cannot exceed the number of physical progress lines.
        # Bound decimal parsing even for a forged summary with thousands of digits.
        totals = (match for match in RUNNER_TOTAL.finditer(line) if len(match[1]) <= max_digits
                  and int(match[1]) <= len(lines))
        total = next(totals, None)
        if total is None:
            continue
        count = int(total[1])
        index = total_line - 1
        if (index < 1 or total_line + 3 >= len(lines) or total[0] != line
                or lines[index] != SEPARATOR or lines[total_line + 1]
                or not RUNNER_VERDICT.fullmatch(verdict := lines[total_line + 2])):
            # Partial or interleaved completion text cannot establish which
            # other summary, if any, belongs to the outer result.
            yield count, b'', index, index
            continue
        # An oversized skip total is a contender, never a usable verdict.
        skip_count = RUNNER_SKIP_COUNT.search(verdict)
        skipped = 0 if skip_count is None else (
            int(skip_count[1]) if len(skip_count[1]) <= max_digits else len(lines))
        duration = index
        if not lines[index - 1]:
            first_row = index - 1
            while first_row > 0 and RUNNER_DURATION_ROW.fullmatch(lines[first_row - 1]):
                first_row -= 1
            rows = index - 1 - first_row
            if (rows and first_row >= 2 and lines[first_row - 2] == b'Slowest test durations'
                    and lines[first_row - 1] == SEPARATOR and rows <= count <= rows + skipped):
                duration = first_row - 2
        # Ordinary cases always report a duration except decorator skips.
        # Fixture skips can increase the skipped total without starting a case.
        if duration != index or count <= skipped:
            yield count, verdict, duration, index


def legacy_progress(raw, progress):
    """Prove the ordered callback statuses without assigning any writes to nested runners."""
    # A case can print a fixture lookalike before its own status. A fixture
    # has no mandatory startTest callback to establish that boundary.
    if RUNNER_FIXTURE.search(raw):
        return False
    # Look for extra and duplicated direct case or subtest headers, including
    # after inline output, so they cannot be mistaken for another case's status.
    headers = (match for match in RUNNER_NAME.finditer(raw) if b'.' in match[1])
    regions = []
    for label, status in progress:
        if (status is None or '\n' in label or '\\' in label or not label.isascii() or not status.isascii()
                or status != 'ok' and (not status.startswith('skipped ') or '\\' in status)):
            return False
        rendered = label.encode('ascii')
        description = rendered.removesuffix(b' ... ')
        header = next(headers, None)
        if header is None:
            return False
        end = header.end()
        start = end - len(description)
        if start < 0 or not raw.startswith(rendered, start):
            return False
        regions.append((start, start + len(rendered), status.encode('ascii')))
    if next(headers, None) is not None:
        return False

    can_change_pass = can_change_skip = False
    for index, (_, start, status) in enumerate(regions):
        end = regions[index + 1][0] if index + 1 < len(regions) else len(raw)
        if start > end:
            return False
        segment = raw[start:end]
        if not any(line.endswith(status) for line in segment.split(b'\n')[:-1]):
            return False
        # A status is written separately from the header and newline. Even a
        # fragment of unrelated output can supply a competing status. Do not
        # erase any of these possibilities on the strength of printed syntax.
        skips = RUNNER_SKIP.finditer(segment)
        if status == b'ok':
            can_change_pass |= next(skips, None) is not None
        else:
            if any(not segment.startswith(status, match.start()) for match in skips):
                return False
            can_change_skip |= b'ok' in segment
        # The outer skip count is fixed. An alternative assignment must
        # exchange at least one pass and skip or change a skip reason.
        if can_change_pass and can_change_skip:
            return False
    return True


def verify_execution(raw, progress, count, verdict, durations=()):
    error = 'Original unittest progress, run count or verdict does not establish the case manifest.'
    if len(progress) != count or len(raw) > LEGACY_LOG_LIMIT or raw.count(b'\n') > LEGACY_LINE_COUNT_LIMIT:
        raise ValueError(error)
    lines = raw.replace(b'\r\n', b'\n').split(b'\n')
    if any(len(line) > LEGACY_LINE_LIMIT for line in lines):
        raise ValueError(error)
    candidates = legacy_footers(lines)
    footer = next(candidates, None)
    if footer is None or next(candidates, None) is not None:
        raise ValueError(error)
    actual_count, actual_verdict, section, summary = footer
    if (actual_count != count or actual_verdict != verdict
            or raw.count(b'Slowest test durations') != int(section != summary)
            or lines[section:summary] != duration_lines(durations)
            or not legacy_progress(b'\n'.join(lines[:section]) + b'\n', progress)):
        raise ValueError(error)


def callback_record_digests(raw):
    """Return callback payload digests bound to this original process log."""
    records = []
    for record in CALLBACK_RECORD.finditer(raw):
        boundary = int(record[1])
        if boundary <= record.start() and sha256(raw[:boundary]).hexdigest().encode('ascii') == record[2]:
            records.append(record[3])
    return records


def verify_callback_record(raw, manifest):
    payload = {field: manifest[field] for field in (*CALLBACK_FIELDS, 'owners')}
    expected = sha256(callback_bytes(payload)).hexdigest().encode('ascii')
    if callback_record_digests(raw) != [expected]:
        raise ValueError('Original unittest callback record differs from the case manifest or transcript.')


def verify_original(record, raw, manifest, lane, *, callback_records=None, cutoff=None):
    expected = verify_manifest(manifest, lane, cutoff=cutoff)
    version = manifest.get('callback_record_version')
    if ('callback_record_version' in manifest and (type(version) is not int or version != CALLBACK_RECORD_VERSION)
            or callback_records is not None and callback_records != (version is not None)):
        raise ValueError('Original unittest callback record version differs from the recorded source.')
    if version is not None and 'owners' not in manifest:
        raise ValueError('Original unittest callback record is missing discovered fixture owners.')
    size = manifest['log_size']
    if (type(size) is not int or not 0 < size <= len(raw)
            or sha256(raw[:size]).hexdigest() != manifest['log_sha256']):
        raise ValueError('Original unittest log differs from the runner-bound bytes.')
    trailer = raw[size:]
    reason = record.get('reason_code')
    if 'final_log_sha256' in manifest:
        if manifest['final_log_sha256'] != sha256(raw).hexdigest():
            raise ValueError('Original unittest log differs from the closed producer log.')
    elif trailer and not reason:
        raise ValueError('Unexpected output after the original unittest run.')
    verdict = unittest_verdict(manifest['counts'], len(manifest['cases']))
    exit_code = int(verdict.startswith(b'FAILED'))
    if not reason and (record.get('exit_code') != exit_code or (record['status'] == 'passed') != (exit_code == 0)):
        raise ValueError('Original unittest verdict does not match the process result.')
    runner_seconds = manifest['runner_seconds']
    start, end = process_interval(record)
    durations = case_times([manifest])
    if (type(runner_seconds) not in (int, float) or not math.isfinite(runner_seconds)
            or not 0 <= runner_seconds <= (end - start).total_seconds() + CLOCK_ROUNDING
            or sum(elapsed for _, elapsed in durations) > runner_seconds + CLOCK_ROUNDING):
        raise ValueError('Original unittest runner duration exceeds its process duration or understates case time.')
    progress, diagnostics, runner_progress = verify_events(manifest, expected)
    if version is None:
        # The older producer did not bind its callback boundary to the log.
        # A supplied prefix cannot exclude contenders in the closed original.
        verify_execution(raw, runner_progress, len(manifest['cases']), verdict, durations)
    else:
        verify_callback_record(raw[:size], manifest)
    return progress, diagnostics + (trailer if reason else b'')


def elapsed_seconds(parts):
    times = [process_interval(record) for record, _, _ in parts if 'started_at' in record]
    return round((max(end for _, end in times) - min(start for start, _ in times)).total_seconds(), 3) if times else 0


def combine(parts, *, cutoff=None):
    """Build the canonical check log and verdict. Original producer logs stay separate."""
    completed, outputs = [], []
    result = 'passed'
    for lane, (record, raw, manifest) in zip(LANES, parts, strict=True):
        passed = record['status'] == 'passed'
        if not passed:
            result = 'failed'
        if manifest is not None:
            outputs.append(verify_original(record, raw, manifest, lane, cutoff=cutoff))
            completed.append(manifest)
        elif passed:
            raise ValueError('Successful Windows Python part has no case manifest.')
    if len(completed) == 2 and any(completed[0].get(key) != completed[1].get(key) for key in ('inventory', 'owners')):
        raise ValueError('Windows Python parts have different ordered discovery inventories or fixture owners.')
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
