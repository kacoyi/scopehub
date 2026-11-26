"""Read retained Windows serial Python evidence for a protected handoff."""

import hashlib
import json
from pathlib import Path
import re

if __package__:
    from .diagnostic_archive import resolve_diagnostic
    from .project import windows_x64
else:
    from diagnostic_archive import resolve_diagnostic
    from project import windows_x64


PYTHON_CHECK = 'python-contracts-workers'
WINDOWS_DEADLINE_SECONDS = 1200
SERIAL_COMMAND = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
                  '-p', 'test_*.py', '-v', '--durations', '0']
CASE = re.compile(r'(test\w* \([\w.]+\)) \.\.\.(?: (.*))?')
OUTCOME = re.compile(r'''ok|skipped (?:'.*'|".*")''')
FAILED_OUTCOMES = {'FAIL', 'ERROR', 'expected failure', 'unexpected success'}
TEST_COUNT = re.compile(r'Ran ([0-9]+) tests? in [0-9]+(?:\.[0-9]+)?s')
SUITE_RESULT = re.compile(r'OK(?: \(skipped=([0-9]+)\))?')


def serial_inventory(data):
    """Return ordered verbose unittest outcomes, including exact skip reasons."""
    lines = data.decode('utf-8').splitlines()
    if len(lines) < 5 or lines[-4] != '-' * 70 or lines[-2]:
        raise ValueError('The serial Python suite has no terminal summary.')
    count = TEST_COUNT.fullmatch(lines[-3])
    result = SUITE_RESULT.fullmatch(lines[-1])
    if not count or not result:
        raise ValueError('The serial Python suite has no passing terminal summary.')

    inventory, seen = [], set()
    pending = None
    for line in lines[:-4]:
        case = CASE.fullmatch(line)
        if case:
            identity, outcome = case.groups()
            if pending is not None or identity in seen:
                raise ValueError('The serial Python inventory has an incomplete or duplicate case.')
            seen.add(identity)
            if outcome in FAILED_OUTCOMES:
                raise ValueError('A serial Python case reported a failing outcome.')
            if OUTCOME.fullmatch(outcome or ''):
                inventory.append((identity, outcome))
            else:
                # Some cases print application output before unittest reports their outcome.
                pending = identity
        elif line in FAILED_OUTCOMES:
            raise ValueError('A serial Python case reported a failing outcome.')
        elif OUTCOME.fullmatch(line):
            if pending is None:
                raise ValueError('A serial Python case outcome has no matching start.')
            inventory.append((pending, line))
            pending = None
    skipped = sum(outcome.startswith('skipped ') for _, outcome in inventory)
    if pending is not None or not inventory or len(inventory) != int(count[1]) or skipped != int(result[1] or 0):
        raise ValueError('The serial Python suite summary differs from its case inventory.')
    return tuple(inventory)


def python_row(report):
    rows = report.get('checks')
    if not isinstance(rows, list):
        raise ValueError('Expected a harness check list.')
    python = [row for row in rows if isinstance(row, dict) and row.get('id') == PYTHON_CHECK]
    if len(python) != 1:
        raise ValueError('Expected exactly one retained Python check result.')
    return python[0]


def read_native(archive_root, locator):
    """Resolve a diagnostic original and return its report and bound Python log bytes."""
    original = resolve_diagnostic(locator, archive_root)
    if original['report_kind'] != 'harness' or PYTHON_CHECK not in original['verified_logs']:
        raise ValueError('The original lacks a verified Python check log.')
    path = Path(original['report_path'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != locator['report_sha256']:
        raise ValueError('The native report changed after diagnostic resolution.')
    report = json.loads(raw)
    log = path.with_name(PYTHON_CHECK + '.log').read_bytes()
    if hashlib.sha256(log).hexdigest() != python_row(report)['log_sha256']:
        raise ValueError('The Python log changed after diagnostic resolution.')
    return report, log


def serial_result(report, log, check):
    """Verify a clean Windows x64 result of the unchanged serial discovery check."""
    native = report.get('native_requirement')
    environment = report.get('environment')
    source = report.get('source')
    selection = report.get('selection')
    if (not all(isinstance(section, dict) for section in (native, environment, source, selection))
            or not isinstance(environment.get('machine'), str) or not windows_x64(environment)
            or native.get('satisfied') is not True
            or source.get('working_tree_dirty') is not False or report.get('source_end') != source
            or report.get('source_unchanged') is not True or report.get('project_unchanged') is not True):
        raise ValueError('The Python check did not observe a clean, stable Windows x64 checkout.')
    selected = selection.get('checks')
    if not isinstance(selected, list) or selected.count(PYTHON_CHECK) != 1:
        raise ValueError('The native report did not select the serial Python check.')
    if (check.get('id') != PYTHON_CHECK or check.get('status') != 'implemented'
            or check.get('command') != SERIAL_COMMAND or check.get('windows_timeout_seconds') != WINDOWS_DEADLINE_SECONDS):
        raise ValueError('The protected Python check command or Windows deadline changed.')
    python = environment.get('python_executable')
    row = python_row(report)
    elapsed = row.get('duration_seconds')
    exit_code = row.get('exit_code')
    if (not isinstance(python, str) or not python or row.get('command') != [python, *SERIAL_COMMAND[1:]]
            or row.get('status') != 'passed' or type(exit_code) is not int or exit_code != 0 or row.get('reason_code')
            or type(elapsed) not in (int, float) or not 0 < elapsed <= WINDOWS_DEADLINE_SECONDS
            or hashlib.sha256(log).hexdigest() != row.get('log_sha256')):
        raise ValueError('The original Python check did not record a passing serial run within the Windows deadline.')
    return serial_inventory(log)
