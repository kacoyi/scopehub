"""Measure one experimental branch policy read change outside the verification lane."""

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest
from unittest.mock import patch


SOURCE = '9877c170643b673c58a7d167fe38c2e7978ed83c'
CASES = (
    'test_same_day_branch_progress_follows_ancestry_and_binds_each_protection_revision',
    'test_due_retirement_transfers_the_missed_cycle_until_post_due_observation',
)
ORDER = ('before', 'after', 'after', 'before', 'after', 'before', 'before', 'after')
POLICY = 'project/branch-protection.json'
COMMIT_DATE = '2026-11-22T12:00:00+00:00'


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def run_arm(arm, result_path):
    sys.path.insert(0, str(Path.cwd()))
    from scripts import project
    from tests.project.test_delivery import DeliveryTests

    original_main = project.main
    original_git = project.feedback_git
    original_policy = project.periodic_policy
    original_setup = DeliveryTests.setUp
    invocations = []
    roots = []
    setup_times = []

    def setup(case):
        start = perf_counter()
        original_setup(case)
        roots.append(case.root)
        case.enterContext(patch.dict(os.environ, {
            'GIT_AUTHOR_DATE': COMMIT_DATE, 'GIT_COMMITTER_DATE': COMMIT_DATE,
        }))
        setup_times.append(perf_counter() - start)

    def main():
        reads = Counter()
        git_starts = 0
        policies = {}
        out, err, destination = StringIO(), StringIO(), sys.stdout
        exit_code = 0

        def traced_git(root, *args):
            nonlocal git_starts
            git_starts += 1
            if len(args) == 2 and args[0] == 'show' and args[1].endswith(':' + POLICY):
                reads[args[1]] += 1
            return original_git(root, *args)

        def experiment(root, source):
            if source is None:
                source = project.feedback_commit(root, 'HEAD')
                if source is None:
                    raise ValueError('The observation source has no protected repository policy.')
            key = (root, source)
            if not project.maintenance.git_commit(source):
                return original_policy(root, source)
            if key not in policies:
                policies[key] = original_policy(root, source)
            return policies[key]

        policy = experiment if arm == 'after' else original_policy
        start = perf_counter()
        with (patch.object(project, 'feedback_git', traced_git),
              patch.object(project, 'periodic_policy', policy), redirect_stdout(out), redirect_stderr(err)):
            try:
                original_main()
            except SystemExit as error:
                exit_code = error.code
                raise
            finally:
                elapsed = perf_counter() - start
                destination.write(out.getvalue())
                report = {
                    'argv': sys.argv[1:], 'exit_code': exit_code, 'stdout': out.getvalue(), 'stderr': err.getvalue(),
                    'seconds': elapsed, 'git_starts': git_starts, 'policy_reads': dict(reads),
                }
                invocations.append(report)

    def isolation_control():
        fixture = DeliveryTests()
        try:
            fixture.setUp()
            path, _ = fixture.periodic_fixture()
            policy_path = path.with_name('branch-protection.json')
            initial_bytes = policy_path.read_bytes()
            policy = json.loads(initial_bytes)
            policy['repository'] = 'fixture/changed-policy'
            old = fixture.commit_fixture()
            policy_path.write_bytes(project.json_bytes(policy))
            changed = fixture.commit_fixture()
            policy_path.unlink()
            missing = fixture.commit_fixture()
            raw = fixture.root / '.reports' / 'timing.json'
            raw.parent.mkdir(exist_ok=True)
            timing = fixture.timing_review('2026-11-24T08:00:00Z')
            args = ('observe-maintenance', 'verification-timing', '--report', str(raw))

            def observe(source):
                timing['recent_collection']['capture_source'] = source
                raw.write_bytes(project.json_bytes(timing))
                return fixture.project_command(*args)

            # Working policy differs from HEAD and from the retained old source.
            policy_path.write_bytes(initial_bytes)
            fixture.assertEqual(old, observe(old)['source_commit'])
            fixture.assertIn('repository', observe(changed)['error'])
            fixture.assertIn('no protected repository policy', observe(missing)['error'])
            fixture.assertIn('no protected repository policy', observe(None)['error'])
            fixture.commit_fixture()
            fixture.assertIsNone(observe(None)['source_commit'])
            fixture.assertEqual(old, observe(old)['source_commit'])
        finally:
            fixture.assertTrue(fixture.doCleanups())

    previous_env = dict(os.environ)
    start = perf_counter()
    with patch.object(DeliveryTests, 'setUp', setup), patch.object(project, 'main', main):
        suite = unittest.TestSuite(DeliveryTests(case) for case in CASES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        suite_seconds = perf_counter() - start
        case_command_count = len(invocations)
        if result.wasSuccessful():
            isolation_control()
    for index, root in enumerate(roots):
        for report in invocations:
            for key in ('argv', 'stdout', 'stderr'):
                target = f'<fixture-{index}>'
                if isinstance(report[key], list):
                    report[key] = [arg.replace(str(root), target) for arg in report[key]]
                else:
                    report[key] = report[key].replace(str(root), target)
                    report[key] = report[key].replace(str(root).replace('\\', '\\\\'), target)
    clean = all(not root.exists() for root in roots) and dict(os.environ) == previous_env
    write_json(result_path, {
        'arm': arm, 'cases': list(CASES), 'tests_run': result.testsRun, 'skipped': result.skipped,
        'suite_seconds': suite_seconds, 'fixture_setup_seconds': sum(setup_times[:len(CASES)]),
        'case_command_count': case_command_count, 'root_count': len(roots),
        'cleanup_complete': clean, 'invocations': invocations,
    })
    return 0 if result.wasSuccessful() and clean and len(roots) == len(CASES) + 1 else 1


def observations(result, process_seconds):
    interactions = result['invocations'][:result['case_command_count']]
    reads = sum(sum(command['policy_reads'].values()) for command in interactions)
    seconds = sum(command['seconds'] for command in interactions)
    return {
        'arm': result['arm'], 'suite_seconds': result['suite_seconds'], 'process_seconds': process_seconds,
        'command_seconds': seconds, 'fixture_setup_seconds': result['fixture_setup_seconds'],
        'outside_commands_seconds': result['suite_seconds'] - seconds,
        'policy_reads': reads, 'git_starts': sum(command['git_starts'] for command in interactions),
    }


def run_pairs(output, order):
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if head != SOURCE:
        raise SystemExit(f'Expected source {SOURCE}; found {head}')
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'scripts', 'tests', POLICY,
                    'contracts/schemas/project.schema.json', 'uv.lock'], check=True)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'identity.json', {
        'source': head, 'platform': platform.platform(), 'python': sys.version,
        'git': subprocess.check_output(['git', '--version'], text=True).strip(),
        'launcher_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    reference = None
    measured = []
    for index, arm in enumerate(order):
        label = f'{index}-{arm}'
        result_path = (output / f'{label}.json').resolve()
        with tempfile.TemporaryDirectory(prefix='m0-153-policy-') as scratch:
            environment = {**os.environ, **dict.fromkeys(('TEMP', 'TMP', 'TMPDIR'), scratch)}
            start = perf_counter()
            try:
                completed = subprocess.run([sys.executable, '-B', __file__, result_path, '--arm', arm],
                                           capture_output=True, env=environment, timeout=1200)
            except subprocess.TimeoutExpired as error:
                (output / f'{label}.log').write_bytes((error.stdout or b'') + (error.stderr or b''))
                raise
            elapsed = perf_counter() - start
            (output / f'{label}.log').write_bytes(completed.stdout + completed.stderr)
            completed.check_returncode()
            if list(Path(scratch).iterdir()):
                raise SystemExit(f'{label} left a fixture in its private temporary directory')
        result = json.loads(result_path.read_bytes())
        transcript = [{name: row[name] for name in ('argv', 'exit_code', 'stdout', 'stderr')}
                      for row in result['invocations']]
        if reference is None:
            reference = transcript
        elif transcript != reference:
            raise SystemExit(f'{label} changed a public command transcript; original outputs retained')
        measured.append(observations(result, elapsed))
        write_json(output / 'timing.json', {'observations': measured, 'medians': {
            name: {metric: median(row[metric] for row in measured if row['arm'] == name)
                   for metric in measured[0] if metric != 'arm'}
            for name in dict.fromkeys(row['arm'] for row in measured)
        }})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--arm', choices=('before', 'after'))
    parser.add_argument('--smoke', action='store_true')
    arguments = parser.parse_args()
    if arguments.arm:
        sys.exit(run_arm(arguments.arm, arguments.output))
    run_pairs(arguments.output, ORDER[:2] if arguments.smoke else ORDER)
