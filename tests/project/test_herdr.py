"""Exercise the Herdr launch boundary without a running core or model."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from scripts import herdr


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/herdr.py'
IMPLEMENT = {'model': 'gpt-6.2-sol', 'reasoning_effort': 'ultra'}
SUPERVISE = {'model': 'gpt-6.2-astra', 'reasoning_effort': 'xhigh'}

MOCK_PROGRAM = """\
import json
import os
from pathlib import Path
import sys

program = Path(sys.argv[1]).name
argv = sys.argv[2:]
with open(os.environ['HERDR_TEST_LOG'], 'a', encoding='utf-8') as log:
    print(json.dumps({'program': program, 'argv': argv, 'cwd': str(Path.cwd())}), file=log)
if program == 'herdr-app':
    if os.environ.get('HERDR_TEST_APP_OFFLINE'):
        print('fixture desktop unavailable', file=sys.stderr)
        sys.exit(2)
    print(os.environ['HERDR_TEST_SPACES'])
elif program == 'hc':
    if os.environ.get('HERDR_TEST_HC_EXIT'):
        print('fixture refused launch', file=sys.stderr)
        sys.exit(int(os.environ['HERDR_TEST_HC_EXIT']))
    command = argv[argv.index('--') + 1:]
    os.execvp(command[0], command)
"""


class WorktreeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.primary = self.root / 'primary'
        self.worktree = self.root / 'linked checkout'
        self.nested = self.worktree / 'nested'
        self.nested.mkdir(parents=True)
        git_dir = self.primary / '.git'
        linked_git_dir = git_dir / 'worktrees' / 'linked'
        linked_git_dir.mkdir(parents=True)
        for git_metadata in (git_dir, linked_git_dir):
            (git_metadata / 'HEAD').write_text('ref: refs/heads/fixture\n', encoding='utf-8')
        (self.worktree / '.git').write_text(
            f'gitdir: {os.path.relpath(linked_git_dir, self.worktree)}\n', encoding='utf-8',
        )
        self.write_records(self.primary, 'M0-024')
        self.write_records(self.worktree, 'M0-025')

        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.env = {
            **os.environ, 'PATH': str(self.bin), 'HERDR_TEST_LOG': str(self.log),
            'SCOPEHUB_STORAGE': 'off',
            'HERDR_TEST_SPACES': json.dumps({
                'spaces': [{'id': 's-local', 'machine': None}, {'id': 'remote/s-remote', 'machine': 'remote'}],
                'recent': [{'id': 's-recent', 'machine': None}],
            }),
        }

    def write_records(self, root, task_id, *, implement=IMPLEMENT, supervise=SUPERVISE):
        project = root / 'project'
        project.mkdir(exist_ok=True)
        (project / 'tasks.json').write_text(json.dumps({'tasks': [{'id': task_id}]}), encoding='utf-8')
        (project / 'development.json').write_text(json.dumps({
            'herdr': {'host': 'Herdr', 'profiles': {'implement': implement, 'supervise': supervise},
                      'parallel': [], 'tooling': []},
        }), encoding='utf-8')

    def cli(self, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), *arguments], cwd=cwd or self.nested,
            env=self.env, capture_output=True, encoding='utf-8', timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]


class DryRunTests(WorktreeFixture):
    def test_reads_linked_worktree_records_and_needs_no_executables(self):
        self.write_records(self.worktree, 'M0-025', implement={'model': 'fixture-model', 'reasoning_effort': 'high'})
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--space', 's-local', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['cwd'], str(self.worktree))
        self.assertEqual(plan['argv'][:5], ['hc', 'run', '--bg', '--cwd', str(self.worktree)])
        self.assertIn('herdr.space=s-local', plan['argv'])
        self.assertEqual(plan['argv'][-9:-1], [
            'codex', '--no-daemon', '--model', 'fixture-model', '-c',
            'model_reasoning_effort="high"', '--approve-for-me', '--',
        ])
        context, assignment = plan['argv'][-1].split('\n\n', 1)
        self.assertIn('M0-025, role implement', context)
        self.assertIn('pnpm project:context M0-025', context)
        self.assertEqual(assignment, 'Inspect one file')
        self.assertFalse(self.log.exists())

    def test_accepts_a_primary_worktree_and_uses_a_fresh_valid_name(self):
        names = []
        for _ in range(2):
            result = self.cli('M0-024', 'coordinate', '--assignment', 'Review handoff', '--dry-run', cwd=self.primary)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['cwd'], str(self.primary))
            self.assertEqual(plan['argv'][-3:-1], ['--approve-for-me', '--'])
            names.append(plan['argv'][plan['argv'].index('--name') + 1])
        self.assertRegex(names[0], r'^m0-024-coordinate-[a-z0-9_-]+$')
        self.assertLessEqual(len(names[0]), 32)
        self.assertNotEqual(*names)

    def test_review_returns_sandbox_denials_without_interactive_approval(self):
        result = self.cli('M0-025', 'review', '--assignment', 'Inspect one file', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)['argv']
        self.assertEqual(argv[-6:-1], ['--sandbox', 'read-only', '--ask-for-approval', 'never', '--'])

    def test_rejects_outside_worktree_or_unknown_task_in_current_worktree(self):
        for cwd, task, message in (
            (self.root, 'M0-025', 'ScopeHub Git worktree'),
            (self.nested, 'M0-024', 'Unknown ScopeHub task ID'),
        ):
            with self.subTest(cwd=cwd, task=task):
                result = self.cli(task, 'review', '--assignment', 'Inspect one file', '--dry-run', cwd=cwd)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_invalid_role_assignment_space_and_profile_fail_before_launch(self):
        for role, options, message in (
            ('exec', ['--assignment', 'Inspect one file'], 'invalid choice'),
            ('review', [], '--assignment'),
            ('review', ['--assignment', ' \n '], '--assignment must contain task text'),
            ('review', ['--assignment', 'Inspect one file', '--space', 'remote/s-remote'], 'local Herdr space ID'),
        ):
            with self.subTest(role=role, options=options):
                result = self.cli('M0-025', role, *options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.write_records(self.worktree, 'M0-025', supervise={})
        result = self.cli('M0-025', 'review', '--assignment', 'Inspect one file')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile 'supervise'", result.stderr)
        self.assertFalse(self.log.exists())


class StorageMaintenanceTests(WorktreeFixture):
    def launch(self, *options):
        with (patch.object(sys, 'argv', [str(SCRIPT), 'M0-025', 'review', '--assignment', 'Inspect one file', *options]),
              patch.object(herdr.Path, 'cwd', return_value=self.nested),
              redirect_stdout(StringIO()), redirect_stderr(StringIO())):
            return herdr.main()

    def test_storage_maintenance_follows_successful_launch_in_resolved_worktree(self):
        calls = Mock()
        with (patch.object(herdr.subprocess, 'run', calls.launch),
              patch.object(herdr, 'maintain_automatically', calls.maintain)):
            self.assertEqual(0, self.launch())
        command = calls.launch.call_args.args[0]
        self.assertEqual('hc', command[0])
        self.assertEqual([call.launch(command, cwd=self.worktree, check=True), call.maintain(self.worktree)],
                         calls.mock_calls)

    def test_dry_runs_and_failed_launches_skip_storage_maintenance(self):
        with (patch.object(herdr.subprocess, 'run') as launch,
              patch.object(herdr, 'maintain_automatically') as maintain):
            self.assertEqual(0, self.launch('--dry-run'))
            launch.assert_not_called()
            maintain.assert_not_called()
            launch.side_effect = subprocess.CalledProcessError(13, ['hc', 'run'])
            self.assertEqual(13, self.launch())
            launch.assert_called_once()
            maintain.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'Mock executables use POSIX shell launchers.')
class LaunchTests(WorktreeFixture):
    def setUp(self):
        super().setUp()
        helper = self.root / 'mock.py'
        helper.write_text(MOCK_PROGRAM, encoding='utf-8')
        self.env.update(HERDR_TEST_PYTHON=sys.executable, HERDR_TEST_HELPER=str(helper))
        for program in ('hc', 'codex', 'herdr-app'):
            path = self.bin / program
            path.write_text(
                '#!/bin/sh\nexec "$HERDR_TEST_PYTHON" "$HERDR_TEST_HELPER" "$0" "$@"\n', encoding='utf-8',
            )
            path.chmod(0o700)

    def test_models_and_permissions_follow_each_role(self):
        for role, model, effort in (
            ('implement', 'gpt-6.2-sol', 'ultra'),
            ('review', 'gpt-6.2-astra', 'xhigh'),
            ('coordinate', 'gpt-6.2-astra', 'xhigh'),
        ):
            with self.subTest(role=role):
                result = self.cli('M0-025', role, '--assignment', 'Inspect one file')
                self.assertEqual(result.returncode, 0, result.stderr)
                hc, codex = self.calls()[-2:]
                self.assertEqual(hc['program'], 'hc')
                self.assertEqual(hc['cwd'], str(self.worktree))
                self.assertEqual(hc['argv'][:4], ['run', '--bg', '--cwd', str(self.worktree)])
                self.assertEqual(hc['argv'][6:11], ['--label', 'task=M0-025', '--label', f'role={role}', '--'])
                expected = ['--no-daemon', '--model', model, '-c', f'model_reasoning_effort="{effort}"']
                if role == 'review':
                    expected.extend(['--sandbox', 'read-only', '--ask-for-approval', 'never'])
                else:
                    expected.append('--approve-for-me')
                self.assertEqual((codex['program'], codex['cwd']), ('codex', str(self.worktree)))
                self.assertEqual(codex['argv'][:-1], [*expected, '--'])
                context, assignment = codex['argv'][-1].split('\n\n', 1)
                self.assertIn(f'M0-025, role {role}', context)
                if role == 'review':
                    self.assertIn('.venv/bin/python -B scripts/project.py context M0-025', context)
                else:
                    self.assertIn('pnpm project:context M0-025', context)
                self.assertEqual(assignment, 'Inspect one file')
        self.assertEqual([call['program'] for call in self.calls()], ['hc', 'codex'] * 3)

    def test_assignment_reaches_codex_verbatim_in_an_explicit_space(self):
        marker = self.root / 'injected'
        command = f'printf unsafe > {shlex.quote(str(marker))}'
        assignment = f'--literal ; {command}\n$({command}) `{command}` "quoted" \u03bb '
        result = self.cli('M0-025', 'review', f'--assignment={assignment}', '--space', 's-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        desktop, hc, codex = self.calls()
        self.assertEqual((desktop['program'], desktop['argv']), ('herdr-app', ['ls']))
        self.assertEqual(hc['argv'][10:13], ['--label', 'herdr.space=s-local', '--'])
        self.assertEqual(codex['argv'][-2], '--')
        self.assertEqual(codex['argv'][-1].split('\n\n', 1)[1], assignment)
        self.assertFalse(marker.exists())

    def test_unknown_or_closed_space_never_creates_a_terminal(self):
        for space in ('s-unknown', 's-recent'):
            with self.subTest(space=space):
                result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--space', space)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('No existing local Herdr space', result.stderr)
        self.assertEqual([call['program'] for call in self.calls()], ['herdr-app', 'herdr-app'])

    def test_unavailable_desktop_stops_an_explicit_space_launch(self):
        self.env['HERDR_TEST_APP_OFFLINE'] = '1'
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--space', 's-local')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fixture desktop unavailable', result.stderr)
        self.assertIn('herdr-app ls failed', result.stderr)
        self.assertEqual([call['program'] for call in self.calls()], ['herdr-app'])

    def test_missing_hc_reports_an_error(self):
        (self.bin / 'hc').unlink()
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hc', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_host_failure_exits_nonzero_and_keeps_diagnostics(self):
        self.env['HERDR_TEST_HC_EXIT'] = '13'
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file')
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn('fixture refused launch', result.stderr)
        self.assertIn('hc run failed with exit status 13', result.stderr)
        self.assertEqual([call['program'] for call in self.calls()], ['hc'])


if __name__ == '__main__':
    unittest.main()
