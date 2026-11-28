import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from scripts.check import ci_requires_build_setup, load_project


ROOT = Path(__file__).resolve().parents[2]


class VerifyBrowserSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load((ROOT / '.github/workflows/verify.yml').read_text(encoding='utf-8'))
        cls.events = workflow[True]  # PyYAML parses the YAML 1.1 boolean spelling "on".
        cls.run_name = workflow['run-name']
        cls.concurrency = workflow['concurrency']
        cls.jobs = workflow['jobs']
        cls.job = workflow['jobs']['verify']
        cls.steps = steps = cls.job['steps']
        cls.step = next(
            step for step in steps
            if step.get('name') == 'Install and start the pinned chart-test browser'
        )
        cls.prerequisite = next(step for step in steps if step.get('id') == 'build_setup')
        cls.selector = next(step for step in steps if step.get('id') == 'scope')
        cls.narrow = next(step for step in steps if step.get('name', '').endswith(' changes'))

    def test_scheduled_timing_checks_out_publishing_history(self):
        path = ROOT / '.github/workflows/review-verification-timing.yml'
        jobs = yaml.safe_load(path.read_bytes())['jobs']
        checkout, = [step for step in jobs['review']['steps'] if step.get('uses', '').startswith('actions/checkout@')]
        self.assertEqual(0, checkout['with']['fetch-depth'])
        self.assertFalse(checkout['with']['persist-credentials'])
        review, = [step for step in jobs['review']['steps'] if step.get('name') == 'Review recent hosted verification']
        self.assertIn('python3 scripts/timing.py --recent 12 --github-actions', review['run'])

    def test_browser_setup_is_required_without_a_successful_prerequisite_opt_out(self):
        self.assertEqual("steps.build_setup.outputs.skip != 'true' && steps.lane.outputs.skip != 'true' && "
                         "steps.lane.outputs.python_only != 'true' && "
                         "(matrix.lane != 'core' || steps.lane.outputs.split != 'true')",
                         self.step['if'])
        self.assertEqual("steps.lane.outputs.skip != 'true' && steps.lane.outputs.python_only != 'true'",
                         self.prerequisite['if'])
        self.assertNotIn('continue-on-error', self.step)
        self.assertEqual('bash', self.step['shell'])

    def test_protected_jobs_check_the_default_checkout_and_keep_actual_evidence_steps(self):
        protection = json.loads((ROOT / 'project/branch-protection.json').read_text(encoding='utf-8'))
        required = {check['context'] for check in protection['configuration']['required_status_checks']['checks']}
        self.assertEqual("${{ inputs.task_id && 'verify task' || 'verify' }} (${{ matrix.os }})", self.job['name'])
        self.assertEqual({'verify (ubuntu-latest)', 'verify (windows-latest)'}, required)
        routine = "(github.event_name == 'pull_request' || github.event_name == 'push')"
        self.assertIn(f"{routine} && '[\"ubuntu-latest\"]'", self.job['strategy']['matrix']['os'])
        aggregate = self.jobs['windows_change']
        self.assertIn(f"{routine} && 'verify (windows-latest)'", aggregate['name'])
        checkout = next(step for step in self.steps if step.get('uses', '').startswith('actions/checkout@'))
        self.assertEqual({'fetch-depth': 0, 'persist-credentials': False,
                          'ref': "${{ inputs.expected_head || '' }}"}, checkout['with'])
        self.assertNotIn('if', checkout)
        self.assertEqual(checkout, aggregate['steps'][0])
        self.assertEqual('${{ inputs.expected_head || github.event.pull_request.head.sha || github.sha }}',
                         self.job['env']['SCOPEHUB_SOURCE_HEAD'])
        for name in ('Preserve accepted history', 'Initialize CI verification reports'):
            step = next(step for step in self.steps if step.get('name') == name)
            self.assertLess(self.steps.index(step), self.steps.index(self.narrow))
        self.assertNotIn('Fetch the reviewed public publisher advisory', {step.get('name') for step in self.steps})
        self.assertNotIn('SCOPEHUB_PUBLISHER_ADVISORY_TOKEN', self.job['env'])
        for step in self.steps:
            self.assertNotIn('SCOPEHUB_PUBLISHER_ADVISORY_TOKEN', step.get('env', {}))
        stage = next(step for step in self.steps if step.get('id') == 'artifacts')
        self.assertEqual("always() && steps.reports.outcome == 'success'", stage['if'])
        upload = next(step for step in self.steps if "format('verification-{0}', matrix.os)" in step.get('with', {}).get('name', ''))
        self.assertEqual('${{ steps.artifacts.outputs.path }}', upload['with']['path'])
        self.assertNotIn('continue-on-error', upload)

    def test_workflow_routes_only_pr_and_main_push_into_scope_selection(self):
        self.assertIsNone(self.events['pull_request'])
        self.assertEqual({'branches': ['main']}, self.events['push'])
        self.assertEqual("github.event_name == 'pull_request' || github.event_name == 'push'", self.selector['if'])
        self.assertEqual('uv run --no-project python scripts/verification_scope.py --ci', self.selector['run'])
        self.assertTrue(self.selector['continue-on-error'])
        self.assertEqual("${{ steps.scope.outcome == 'success' && steps.scope.outputs.profile || 'full' }}",
                         self.prerequisite['env']['SCOPEHUB_SCOPE'])
        self.assertEqual("${{ steps.scope.outputs.decision != '' && 'true' || 'false' }}",
                         self.prerequisite['env']['SCOPEHUB_SCOPE_HAS_DECISION'])
        self.assertEqual('Verify ${{ steps.build_setup.outputs.profile }} changes', self.narrow['name'])
        self.assertEqual("steps.build_setup.outputs.profile != '' && !inputs.task_id && steps.lane.outputs.skip != 'true'", self.narrow['if'])
        self.assertEqual('${{ steps.build_setup.outputs.profile }}', self.narrow['env']['SCOPEHUB_SCOPE'])
        self.assertNotIn('continue-on-error', self.narrow)
        stage = next(step for step in self.steps if step.get('id') == 'artifacts')
        self.assertEqual('${{ steps.scope.outputs.decision }}', stage['env']['SCOPEHUB_SCOPE_DECISION'])
        self.assertEqual('${{ steps.scope.outcome }}', stage['env']['SCOPEHUB_SCOPE_OUTCOME'])

    def test_workflow_selector_emits_full_when_its_hosted_event_is_incomplete(self):
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'step output.txt'
            result = subprocess.run(
                self.selector['run'].split(), cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
                env=dict(os.environ, GITHUB_EVENT_NAME='push', GITHUB_EVENT_PATH='', GITHUB_SHA=head,
                         GITHUB_OUTPUT=str(output)),
            )
            decision = json.loads(result.stdout)
            self.assertEqual('full', decision['profile'])
            outputs = dict(line.split('=', 1) for line in output.read_text(encoding='utf-8').splitlines())
            self.assertEqual('full', outputs['profile'])
            self.assertEqual({'profile': 'full', 'reason_code': 'comparison_unavailable',
                              'checkout': head, 'base': None, 'change_count': None}, json.loads(outputs['decision']))

    def test_full_weekly_and_manual_routes_preserve_existing_harnesses(self):
        self.assertEqual([{'cron': '31 3 * * 1'}], self.events['schedule'])
        self.assertIn("(github.event_name != 'schedule' || github.ref == 'refs/heads/main')", self.job['if'])
        self.assertIn("github.event_name == 'schedule' && format('verify-weekly-{0}-{1}'", self.concurrency['group'])
        self.assertIn("inputs.profile == 'release' && format('verify-release-{0}-{1}'", self.concurrency['group'])
        self.assertEqual('full', self.events['workflow_dispatch']['inputs']['profile']['default'])
        self.assertEqual(['full', 'release'], self.events['workflow_dispatch']['inputs']['profile']['options'])
        commands = {step.get('run'): step for step in self.steps}
        fallback = "!inputs.task_id && steps.build_setup.outputs.profile == ''"
        conditions = {
            'pnpm check': f"runner.os != 'Windows' && {fallback}",
            'uv run python scripts/check.py --require-windows':
                f"runner.os == 'Windows' && matrix.lane == '' && inputs.profile != 'release' && {fallback}",
            'uv run python scripts/check.py --profile release --require-windows':
                "runner.os == 'Windows' && inputs.profile == 'release' && !inputs.task_id",
        }
        for command, condition in conditions.items():
            self.assertEqual(condition, commands[command]['if'])
            self.assertNotIn('name', commands[command])
            self.assertNotIn('continue-on-error', commands[command])
        task = next(step for step in self.steps if step.get('name') == 'Verify the selected task')
        self.assertEqual('inputs.task_id', task['if'])
        self.assertEqual('uv run python scripts/check.py --task="$SCOPEHUB_TASK_ID"', task['run'])
        reject = next(step for step in self.steps if step.get('name') == 'Reject a task ID with the release profile')
        self.assertEqual("inputs.task_id && inputs.profile == 'release'", reject['if'])
        self.assertNotIn('continue-on-error', reject)
        self.assertLess(self.steps.index(reject), self.steps.index(task))

    def test_weekly_summary_write_failure_keeps_product_result_independent(self):
        if os.name == 'nt':
            self.skipTest('The weekly summary uses Bash in the Linux job.')
        summary = next(step for step in self.steps if step.get('name') == 'Summarize weekly maintenance follow-through')
        product = next(step for step in self.steps if step.get('run') == 'pnpm check')
        self.assertEqual("${{ !cancelled() && github.event_name == 'schedule' && runner.os == 'Linux' }}", summary['if'])
        self.assertGreater(self.steps.index(summary), self.steps.index(product))
        self.assertEqual(self.steps[-1], summary)
        self.assertNotIn('continue-on-error', product)
        self.assertNotIn('continue-on-error', summary)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for destination, outcome in ((target / 'summary.md', 0), (target, 1)):
                result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', summary['run']],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                                        env=dict(os.environ, GITHUB_SHA='fixture', GITHUB_STEP_SUMMARY=str(destination)))
                self.assertEqual(outcome, result.returncode, result.stderr)
                self.assertIn('Weekly maintenance follow-through', result.stdout)
                if outcome:
                    self.assertIn('::error::', result.stdout)
                else:
                    self.assertIn('Source `fixture`', destination.read_text())

    def test_guarded_dispatch_gates_both_matrix_jobs_and_keeps_ordinary_concurrency(self):
        guarded = '(inputs.expected_head || inputs.expected_base || inputs.dispatch_id)'
        gate = self.jobs['reviewed_dispatch']
        self.assertEqual(f"github.event_name == 'workflow_dispatch' && {guarded}", gate['if'])
        self.assertEqual(('ubuntu-latest', 2), (gate['runs-on'], gate['timeout-minutes']))
        self.assertNotIn('uses', gate['steps'][0])
        self.assertEqual('reviewed_dispatch', self.job['needs'])
        self.assertIn(f"{guarded} && needs.reviewed_dispatch.result == 'success'", self.job['if'])
        self.assertIn(f"!{guarded} && needs.reviewed_dispatch.result == 'skipped'", self.job['if'])
        self.assertIn('!cancelled()', self.job['if'])
        self.assertIn('["ubuntu-latest","windows-latest"]', self.job['strategy']['matrix']['os'])
        for name, job in self.jobs.items():
            if name == 'windows_workspace':
                self.assertEqual("github.event_name == 'pull_request'", job['if'])
            elif name not in {'reviewed_dispatch', 'verify'}:
                self.assertIn("github.event_name == 'pull_request' || github.event_name == 'push'", job['if'])
                self.assertNotIn('workflow_dispatch', job['if'])
        group = self.concurrency['group']
        self.assertTrue(group.startswith(f"${{{{ {guarded} && format('verify-dispatch-{{0}}-{{1}}', github.workflow, github.run_id) ||"))
        self.assertIn("inputs.task_id && format('verify-task-{0}-{1}:{2}', github.workflow, github.ref, inputs.task_id)", group)
        self.assertIn("format('verify-{0}-{1}', github.workflow, github.ref)", group)
        self.assertTrue(self.concurrency['cancel-in-progress'])
        self.assertEqual("${{ inputs.dispatch_id && format('Verify task {0} [{1}]', inputs.task_id, inputs.dispatch_id) || '' }}",
                         self.run_name)
        for key in ('expected_head', 'expected_base', 'dispatch_id'):
            self.assertEqual('string', self.events['workflow_dispatch']['inputs'][key]['type'])
            self.assertFalse(self.events['workflow_dispatch']['inputs'][key]['required'])

    @unittest.skipUnless(sys.platform == 'linux', 'Run the hosted admission step with Bash')
    def test_guard_rejects_moved_refs_and_stale_or_unverifiable_bases(self):
        step, = self.jobs['reviewed_dispatch']['steps']
        head, base, moved = 'a' * 40, 'b' * 40, 'c' * 40
        valid = dict(EXPECTED_HEAD=head, EXPECTED_BASE=base, DISPATCH_ID='d' * 32, TASK_ID='M0-106',
                     VERIFY_PROFILE='full', GITHUB_SHA=head, GITHUB_REF_TYPE='branch',
                     GITHUB_REF='refs/heads/task', GITHUB_REPOSITORY='fixture/scopehub', OBSERVED_BASE=base,
                     GH_EXIT='0')
        cases = [
            ({}, 0, True, ''),
            ({'GITHUB_SHA': moved}, 1, False, 'reviewed head'),
            ({'OBSERVED_BASE': moved}, 1, True, 'reviewed base'),
            ({'OBSERVED_BASE': ''}, 1, True, 'reviewed base'),
            ({'GH_EXIT': '1'}, 1, True, ''),
            ({'EXPECTED_HEAD': ''}, 1, False, 'guarded task dispatch'),
            ({'DISPATCH_ID': ''}, 1, False, 'guarded task dispatch'),
            ({'TASK_ID': ''}, 1, False, 'guarded task dispatch'),
            ({'VERIFY_PROFILE': 'release'}, 1, False, 'guarded task dispatch'),
            ({'GITHUB_REF_TYPE': 'tag'}, 1, False, 'reviewed head'),
        ]
        for override, code, reads_base, message in cases:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as folder:
                gh = Path(folder) / 'gh'
                gh.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$GH_CALL"\n'
                              'printf "%s\\n" "$OBSERVED_BASE"\nexit "$GH_EXIT"\n', encoding='ascii')
                gh.chmod(0o700)
                call = Path(folder) / 'call'
                environment = dict(os.environ, PATH=f'{folder}{os.pathsep}{os.environ["PATH"]}', GH_CALL=str(call))
                environment.update(valid | override)
                result = subprocess.run([step['shell'], '-e', '-o', 'pipefail', '-c', step['run']],
                                        env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(code, result.returncode, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(reads_base, call.exists())
                if reads_base:
                    self.assertIn('repos/fixture/scopehub/git/ref/heads/main', call.read_text())

    def test_routine_windows_changes_use_bounded_native_lanes_and_attempt_bound_aggregation(self):
        aggregate = self.jobs['windows_change']
        self.assertEqual('windows-latest', aggregate['runs-on'])
        lanes = ['core', 'browser', 'python', 'recovery', 'workspace']
        self.assertEqual([f'windows_{lane}' for lane in lanes], aggregate['needs'])
        for lane in lanes:
            producer = self.jobs[f'windows_{lane}']
            self.assertEqual('windows-latest', producer['runs-on'])
            self.assertEqual([lane], producer['strategy']['matrix']['lane'])
            self.assertEqual(self.steps, producer['steps'])
            if lane == 'workspace':
                self.assertEqual("github.event_name == 'pull_request'", producer['if'])
            else:
                self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", producer['if'])
            self.assertIn(f"needs.windows_{lane}.result == 'success'", aggregate['env']['SCOPEHUB_WINDOWS_LANE_RESULT'])
        self.assertEqual("${{ always() && (github.event_name == 'pull_request' || github.event_name == 'push' && github.ref == 'refs/heads/main') }}",
                         aggregate['if'])
        steps = self.jobs['windows_python']['steps']
        for step in steps:
            if step.get('run') == 'pnpm install --frozen-lockfile' or step.get('uses', '').startswith(
                    ('pnpm/action-setup@', 'actions/setup-node@')):
                self.assertEqual("steps.lane.outputs.skip != 'true' && steps.lane.outputs.python_only != 'true'",
                                 step['if'])
        rust = next(step for step in steps if step.get('uses', '').startswith('dtolnay/rust-toolchain@'))
        locked_python = next(step for step in steps if step.get('run') == 'uv sync --locked')
        for step in (rust, locked_python):
            self.assertEqual("steps.lane.outputs.skip != 'true'", step['if'])
            self.assertNotIn('continue-on-error', step)
        self.assertEqual('1.99.0', rust['with']['toolchain'])
        producer_stage = next(step for step in steps if step.get('id') == 'artifacts')
        self.assertEqual("uv run --no-sync python scripts/ci_artifacts.py "
                         "${{ steps.lane.outputs.split == 'true' && 'stage-windows-lane' || 'stage' }}",
                         producer_stage['run'])
        aggregate_stage = next(step for step in aggregate['steps'] if step.get('id') == 'artifacts')
        self.assertEqual('uv run --no-sync python scripts/ci_artifacts.py stage', aggregate_stage['run'])
        downloads = [step for step in aggregate['steps'] if step.get('uses', '').startswith('actions/download-artifact@')]
        self.assertEqual(2, len(downloads))
        for step in downloads:
            self.assertNotIn('github-token', step['with'])
            self.assertIn('github.run_attempt', str(step['with']))
        full = next(step for step in aggregate['steps'] if step.get('id') == 'full')
        self.assertEqual('Run uv run python scripts/check.py --require-windows', full['name'])
        self.assertNotIn('continue-on-error', full)

    @unittest.skipUnless(sys.platform == 'linux', 'Run the workflow lane selector with Bash')
    def test_lane_setup_remains_limited_to_full_for_sibling_producers(self):
        route = next(step for step in self.steps if step.get('id') == 'lane')
        self.assertEqual("${{ steps.scope.outcome == 'success' && steps.scope.outputs.decision != '' && "
                         "steps.scope.outputs.profile || 'full' }}", route['env']['SCOPEHUB_SCOPE'])
        for event in ('pull_request', 'push'):
            lanes = ('core', 'browser', 'python', 'recovery')
            if event == 'pull_request':
                lanes = (*lanes, 'workspace')
            for lane, profile in ((lane, profile) for lane in lanes
                                  for profile in ('full', 'localization', 'records', 'tooling')):
                with self.subTest(event=event, lane=lane, profile=profile), tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'outputs.txt'
                    subprocess.run(
                        [route['shell'], '-c', route['run']], timeout=10, capture_output=True, check=True,
                        env=dict(os.environ, GITHUB_EVENT_NAME=event, SCOPEHUB_WINDOWS_LANE=lane,
                                 SCOPEHUB_SCOPE=profile, GITHUB_OUTPUT=str(output)),
                    )
                    expected = []
                    if profile == 'full':
                        expected.append('split=true')
                        if lane == 'python' or (event == 'pull_request' and lane == 'recovery'):
                            expected.append('python_only=true')
                    elif lane != 'core':
                        expected.append('skip=true')
                    actual = output.read_text(encoding='utf-8').splitlines() if output.exists() else []
                    self.assertEqual(expected, actual)

    @unittest.skipUnless(sys.platform == 'linux', 'Run the workflow failure guard with Bash')
    def test_protected_windows_job_requires_success_even_after_a_cancelled_dependency(self):
        aggregate = self.jobs['windows_change']
        guard = aggregate['steps'][-1]
        self.assertEqual('Require successful Windows lane jobs (PR workspace producer)', guard['name'])
        self.assertEqual('${{ always() }}', guard['if'])
        self.assertNotIn('continue-on-error', guard)
        condition = ' '.join(aggregate['env']['SCOPEHUB_WINDOWS_LANE_RESULT'].split())
        original_producers = ' && '.join(f"needs.windows_{lane}.result == 'success'"
                                         for lane in ('core', 'browser', 'python', 'recovery'))
        self.assertEqual('${{ ' + original_producers +
                         " && (github.event_name == 'pull_request' && needs.windows_workspace.result == 'success' || "
                         "github.event_name == 'push' && needs.windows_workspace.result == 'skipped') && "
                         "'success' || 'failure' }}", condition)
        self.assertEqual("${{ job.status == 'success' && (steps.full.outcome == 'success' || steps.scoped.outcome == 'success') }}",
                         guard['env']['SCOPEHUB_AGGREGATE_PASSED'])
        for result, completed_aggregate, expected_exit in (
            ('success', 'true', 0), ('success', 'false', 1), ('failure', 'true', 1),
            ('cancelled', 'false', 1), ('skipped', 'false', 1), ('', '', 1),
        ):
            with self.subTest(result=result, completed_aggregate=completed_aggregate):
                completed = subprocess.run(
                    [guard['shell'], '-c', guard['run']],
                    env=dict(os.environ, SCOPEHUB_WINDOWS_LANE_RESULT=result, SCOPEHUB_AGGREGATE_PASSED=completed_aggregate),
                    capture_output=True, timeout=10,
                )
                self.assertEqual(expected_exit, completed.returncode)

    def test_task_omits_setup_only_for_reviewed_browser_and_build_free_selection(self):
        project = load_project(ROOT)
        self.assertFalse(ci_requires_build_setup(project, 'M0-041'))
        self.assertTrue(ci_requires_build_setup(project, 'M1-005'))
        self.assertTrue(ci_requires_build_setup(project, 'missing-task'))
        self.assertTrue(ci_requires_build_setup(project, None))

        project['checks']['environment']['command'] = ['{python}', 'changed.py']
        self.assertTrue(ci_requires_build_setup(project, 'M0-041'))

    def test_scoped_prerequisites_review_the_commands_and_keep_localization_setup(self):
        project = load_project(ROOT)
        for profile in ('records', 'tooling'):
            with self.subTest(profile=profile):
                self.assertFalse(ci_requires_build_setup(project, profile=profile))
        for profile in ('localization', 'full', 'release', 'fast', 'unknown'):
            with self.subTest(profile=profile):
                self.assertTrue(ci_requires_build_setup(project, profile=profile))

        project['checks']['dependency-review']['command'] += ['--offline']
        self.assertTrue(ci_requires_build_setup(project, profile='records'))
        self.assertTrue(ci_requires_build_setup(project, profile='tooling'))
        del project['catalog']['profiles']['records']
        self.assertTrue(ci_requires_build_setup(project, profile='records'))

    @unittest.skipUnless(sys.platform == 'linux', 'Run with the Linux GitHub Actions Bash shell')
    def test_prerequisite_script_accepts_only_fixed_profiles_and_keeps_setup_on_uncertainty(self):
        cases = (
            ('records', '', 'false', 0, 'profile=records\nskip=true\n', '--profile=records'),
            ('tooling', '', 'false', 0, 'profile=tooling\nskip=true\n', '--profile=tooling'),
            ('tooling', '', 'true', 0, 'profile=tooling\n', '--profile=tooling'),
            ('records', '', 'false', 1, 'profile=records\n', '--profile=records'),
            ('localization', '', 'false', 0, 'profile=localization\n', None),
            ('full', '', 'false', 0, '', None),
            ('', '', 'false', 0, '', None),
            ('unknown', '', 'false', 0, '', None),
            ('records\nskip=true', '', 'false', 0, '', None),
            ('full', 'M0-041', 'false', 0, 'skip=true\n', '--task=M0-041'),
            ('full', 'missing task', 'false', 1, '', '--task=missing task'),
        )
        for profile, task, answer, status, expected_output, selection in cases:
            with self.subTest(profile=profile, task=task, status=status), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'step output.txt'
                arguments = Path(directory) / 'arguments.txt'
                stub = f'''uv() {{
  printf '%s\\n' "$@" > "$STUB_ARGUMENTS"
  printf '%s\\n' {answer}
  return {status}
}}
'''
                result = subprocess.run(
                    ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', stub + self.prerequisite['run']],
                    env=dict(os.environ, SCOPEHUB_TASK_ID=task, SCOPEHUB_SCOPE=profile,
                             SCOPEHUB_SCOPE_HAS_DECISION='true',
                             GITHUB_OUTPUT=str(output), STUB_ARGUMENTS=str(arguments)),
                    timeout=10, capture_output=True, text=True, check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected_output, output.read_text(encoding='utf-8') if output.exists() else '')
                if selection:
                    self.assertEqual(['run', '--no-sync', 'python', 'scripts/check.py', selection,
                                      '--ci-build-setup-required'], arguments.read_text(encoding='utf-8').splitlines())
                else:
                    self.assertFalse(arguments.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Run with the Linux GitHub Actions Bash shell')
    def test_successful_narrow_profile_without_decision_keeps_the_full_route(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'step output.txt'
            subprocess.run(
                ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', self.prerequisite['run']],
                env=dict(os.environ, SCOPEHUB_TASK_ID='', SCOPEHUB_SCOPE='records',
                         SCOPEHUB_SCOPE_HAS_DECISION='false', GITHUB_OUTPUT=str(output)),
                timeout=10, capture_output=True, text=True, check=True,
            )
            self.assertFalse(output.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Run with the Linux GitHub Actions Bash shell')
    def test_narrow_harness_passes_the_profile_and_propagates_failure(self):
        for profile in ('records', 'tooling', 'localization'):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c',
                     'uv() { printf "%s\\n" "$@"; return 9; }\n' + self.narrow['run']],
                    env=dict(os.environ, SCOPEHUB_SCOPE=profile), timeout=10,
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(9, result.returncode, result.stderr)
                self.assertEqual(['run', 'python', 'scripts/check.py', '--profile', profile], result.stdout.splitlines())

    @unittest.skipUnless(sys.platform == 'linux', 'Run with the Linux GitHub Actions Bash shell')
    def test_launch_failure_repairs_dependencies_and_must_pass_a_second_probe(self):
        stub = '''
RUNNER_TEMP='runner temp'
install_status=$1
first_probe_status=$2
dependency_status=$3
second_probe_status=$4
pnpm() {
  printf '<%s>' "$@"
  printf '\\n'
  case "$3" in
    install) return "$install_status" ;;
    install-deps) return "$dependency_status" ;;
    screenshot)
      local result=$first_probe_status
      first_probe_status=$second_probe_status
      return "$result"
      ;;
    *) return 99 ;;
  esac
}
'''
        install = '<exec><playwright><install><chromium>'
        deps = '<exec><playwright><install-deps><chromium>'
        probe = ('<exec><playwright><screenshot><--browser><chromium><about:blank>'
                 '<runner temp/scopehub-chromium-probe.png>')
        cases = (
            ((0, 0, 0, 0), 0, [install, probe]),
            ((0, 7, 0, 0), 0, [install, probe, deps, probe]),
            ((8, 0, 0, 0), 8, [install]),
            ((0, 7, 9, 0), 9, [install, probe, deps]),
            ((0, 7, 0, 10), 10, [install, probe, deps, probe]),
        )
        for statuses, expected_status, expected_calls in cases:
            with self.subTest(statuses=statuses):
                result = subprocess.run(
                    ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', stub + self.step['run'],
                     'chromium-setup', *(str(status) for status in statuses)],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(expected_status, result.returncode, result.stderr)
                self.assertEqual(expected_calls, result.stdout.splitlines())
