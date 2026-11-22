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
        steps = workflow['jobs']['verify']['steps']
        cls.step = next(
            step for step in steps
            if step.get('name') == 'Install and start the pinned chart-test browser'
        )
        cls.prerequisite = next(step for step in steps if step.get('id') == 'task_setup')

    def test_browser_setup_is_required_without_a_successful_task_opt_out(self):
        self.assertEqual("steps.task_setup.outputs.skip != 'true'", self.step['if'])
        self.assertEqual('inputs.task_id', self.prerequisite['if'])
        self.assertNotIn('continue-on-error', self.step)
        self.assertEqual('bash', self.step['shell'])

    def test_task_omits_setup_only_for_reviewed_browser_and_build_free_selection(self):
        project = load_project(ROOT)
        self.assertFalse(ci_requires_build_setup(project, 'M0-041'))
        self.assertTrue(ci_requires_build_setup(project, 'M1-005'))
        self.assertTrue(ci_requires_build_setup(project, 'missing-task'))
        self.assertTrue(ci_requires_build_setup(project, None))

        project['checks']['environment']['command'] = ['{python}', 'changed.py']
        self.assertTrue(ci_requires_build_setup(project, 'M0-041'))

    @unittest.skipUnless(sys.platform == 'linux', 'Run with the Linux GitHub Actions Bash shell')
    def test_prerequisite_error_leaves_chromium_setup_enabled(self):
        for answer, status in [('false', 0), ('true', 0), ('false', 1)]:
            with self.subTest(answer=answer, status=status), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'step output.txt'
                stub = f"uv() {{ printf '%s\\n' {answer}; return {status}; }}\n"
                subprocess.run(
                    ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', stub + self.prerequisite['run']],
                    env=dict(os.environ, SCOPEHUB_TASK_ID='M0-041', GITHUB_OUTPUT=str(output)),
                    timeout=10, check=True,
                )
                if answer == 'false' and status == 0:
                    self.assertEqual('skip=true\n', output.read_text(encoding='utf-8'))
                else:
                    self.assertFalse(output.exists())

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
