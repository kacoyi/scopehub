"""Exercise the Herdr launch boundary without a running core or model."""

from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import BytesIO, StringIO, TextIOWrapper
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from scripts import herdr, herdr_lifecycle
from scripts.herdr_returns import Proof
from scripts.worktree import exclusive_git_lock


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/herdr.py'
IMPLEMENT = {'model': 'fixture-implementation-model', 'reasoning_effort': 'ultra'}
SUPERVISE = {'model': 'fixture-supervision-model', 'reasoning_effort': 'xhigh'}
EFFORTS = ['medium', 'high', 'xhigh', 'max', 'ultra']


def catalog_model(slug, *efforts, visibility='list'):
    return {'slug': slug, 'visibility': visibility,
            'supported_reasoning_levels': [{'effort': effort} for effort in efforts]}


FUTURE_CATALOG = {'models': [
    catalog_model('gpt-10.9-sol', 'medium', 'high', 'ultra'),
    catalog_model('gpt-9.99-sol', 'medium', 'ultra'),
    catalog_model('gpt-10.12-sol', 'medium', 'high'),
    catalog_model('gpt-12-sol', 'high'),
    catalog_model('gpt-90-sol', *EFFORTS, visibility='hide'),
    catalog_model('gpt-91-sol', *EFFORTS, visibility='unlisted'),
    catalog_model('gpt-92-sol-preview', *EFFORTS),
    catalog_model('gpt-93-preview-sol', *EFFORTS),
    catalog_model('gpt-94x1-sol', *EFFORTS),
    catalog_model('gpt-95-sol\n', *EFFORTS),
    catalog_model('gpt-30.1-astra', *EFFORTS),
]}


class RunnerHeld(Exception):
    """Stop at the public owned runner's inspection wait."""


@unittest.skipUnless(sys.platform == 'linux', 'Local Herdr lifecycle requires Linux.')
class HerdrOwnedEntryTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory).resolve()
        self.state = self.root / 'state'
        self.parent, self.assignment, self.prompt = 't42', 'one-shot', 'Synthetic assignment.'
        self.args = ['--state-dir', str(self.state), '--parent', self.parent, '--assignment-id', self.assignment]
        self.binding = {'id': self.assignment, 'parent': self.parent, 'mode': 'one-shot',
                        'task': 'M0-162', 'role': 'implement', 'checkout': str(self.root),
                        'source': '1' * 40, 'prompt_sha256': sha256(self.prompt.encode()).hexdigest(),
                        'model': 'gpt-fixture-sol', 'effort': 'medium', 'sessions': str(self.root / 'sessions'),
                        'codex_home': str(self.root), 'codex_argv': ['codex', 'exec', '--json']}
        self.terminal = {'address': 't43', 'created_ms': 345, 'pid': os.getpid(),
                         'tab': {'id': 'tab-43', 'pane': 'pane-43'}}
        self.session = '01a21690-93a0-79f3-b020-ab4c007f0531'
        self.rollout = str(self.root / 'sessions' / f'rollout-fixture-{self.session}.jsonl')
        self.execution = dict(self.terminal, session_id=self.session, rollout_path=self.rollout)
        self.native = Mock()
        self.native.own_process.return_value = self.terminal
        self.native.observe.side_effect = self.observe
        self.original = b'{"text":"The synthetic return is complete."}\n'
        self.completion = b'{"turn_id":"bound","type":"task_complete"}\n'
        self.probe = Mock(return_value=Proof(self.original, {
            'session_id': self.session, 'rollout_path': self.rollout,
            'completion_sha256': sha256(self.completion).hexdigest(),
        }, self.completion))
        self.lifecycle = self.new_lifecycle()
        self.lifecycle.register(self.binding)
        self.pending = self.lifecycle.pending / self.assignment
        self.archived = self.lifecycle.archived / self.assignment
        self.child = Mock(pid=123)
        self.child.wait.return_value = 0
        self.thread_started = (json.dumps({'type': 'thread.started', 'thread_id': self.session}) + '\n').encode()
        self.stderr = StringIO()
        self.stdout = BytesIO()
        self.enterContext(patch.object(herdr, 'collector', side_effect=lambda *_: (self.new_lifecycle(), self.native)))
        self.enterContext(patch.object(herdr, 'find_worktree', return_value=self.root))
        self.enterContext(patch.object(herdr, 'inherited_parent', return_value=self.terminal['address']))
        self.enterContext(patch.object(herdr, 'check_review_source'))
        self.enterContext(patch.object(herdr, 'exec_rollout', return_value=self.rollout))
        self.enterContext(patch.object(herdr.signal, 'pause', side_effect=RunnerHeld))

    def new_lifecycle(self):
        return herdr_lifecycle.Lifecycle(self.state, self.parent, observe=self.native.observe, probe=self.probe)

    def observe(self, binding, *, retiring):
        self.assertEqual(binding, self.binding)
        execution = self.lifecycle.execution(self.assignment)
        if retiring is not None:
            self.assertEqual(retiring, execution)
            raise herdr_lifecycle.LifecycleHold('one_shot_awaiting_exit')
        return execution

    def run_owned(self, output=None):
        self.child.stdout = BytesIO(self.thread_started if output is None else output)
        argv = ['herdr.py', 'run-owned', *self.args, '--', self.prompt]
        with (patch.object(sys, 'argv', argv), patch.object(herdr.subprocess, 'Popen', return_value=nullcontext(self.child)),
              redirect_stdout(TextIOWrapper(self.stdout, write_through=True)) as out, redirect_stderr(self.stderr)):
            try:
                return herdr.main()
            finally:
                out.detach()

    def collect(self):
        with (patch.object(sys, 'argv', ['herdr.py', 'collect', *self.args]), redirect_stdout(StringIO()) as out):
            self.assertEqual(herdr.main(), 0)
        return json.loads(out.getvalue())['assignments'][0]

    def assert_saved_pair(self):
        receipt = self.collect()
        self.assertEqual(receipt['reason'], 'one_shot_awaiting_exit')
        self.assertEqual(Path(receipt['original']).read_bytes(), self.original)
        self.assertEqual(Path(receipt['completion']).read_bytes(), self.completion)

    def compete(self, *, after_lock=None, interrupt=None):
        lock = self.enterContext(ExitStack())

        def finished_child():
            lock.enter_context(exclusive_git_lock(self.pending / 'operation.lock'))
            return 0

        self.child.wait.side_effect = finished_child

        def publish(_):
            self.assertEqual(self.collect()['reason'], 'assignment_busy')
            lock.close()
            if interrupt is not None:
                with patch.object(herdr_lifecycle, '_write_record', side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.collect()
            else:
                self.assertEqual(self.collect()['status'], 'archived')
            if after_lock:
                after_lock()

        return patch.object(herdr.time, 'sleep', side_effect=publish)

    def test_run_owned_without_competition_recovers_its_exact_pair(self):
        self.assertEqual(self.run_owned(), 0)
        self.assert_saved_pair()
        self.assertIn(b'Verified assignment return archived', self.stdout.getvalue())

    def test_run_owned_waits_for_competing_public_collector_then_exits(self):
        with self.compete() as retry:
            self.assertEqual(self.run_owned(), 0)
        retry.assert_called_once()
        self.child.wait.assert_called_once()
        self.probe.assert_called_once_with(self.binding, self.execution)
        self.assert_saved_pair()

    def test_run_owned_recovers_pair_after_competing_collector_interrupts(self):
        with self.compete(interrupt=KeyboardInterrupt) as retry:
            self.assertEqual(self.run_owned(), 0)
        retry.assert_called_once()
        self.probe.assert_called_once_with(self.binding, self.execution)
        self.assert_saved_pair()

    def test_run_owned_recovers_when_competitor_moves_the_lane_before_lock_acquisition(self):
        # Run a separate public collect between finding the pending lane and opening its lock.
        def acquire(path):
            if self.pending.exists() and (self.pending / 'execution.json').exists():
                with patch.object(herdr_lifecycle, 'exclusive_git_lock', exclusive_git_lock):
                    self.assertEqual(self.collect()['status'], 'archived')
            return exclusive_git_lock(path)

        with (patch.object(herdr_lifecycle, 'exclusive_git_lock', side_effect=acquire),
              patch.object(herdr.time, 'sleep') as retry):
            self.assertEqual(self.run_owned(), 0)
        retry.assert_called_once()
        self.probe.assert_called_once_with(self.binding, self.execution)
        self.assert_saved_pair()

    def test_run_owned_preserves_corrupt_competing_completion(self):
        completion = self.archived / 'return/completion.jsonl'
        with self.compete(after_lock=lambda: completion.write_bytes(b'changed')), self.assertRaises(RunnerHeld):
            self.run_owned()
        self.assertIn('archive_changed', self.stderr.getvalue())
        self.assertEqual(completion.read_bytes(), b'changed')
        self.assertEqual((self.archived / 'return/original.jsonl').read_bytes(), self.original)
        self.assertNotIn(b'Verified assignment return archived', self.stdout.getvalue())

    def test_run_owned_rejects_competing_archive_for_a_different_execution(self):
        def change_execution():
            (self.archived / 'execution.json').write_text(json.dumps(dict(self.execution, pid=1)), encoding='utf-8')

        with self.compete(after_lock=change_execution), self.assertRaises(RunnerHeld):
            self.run_owned()
        self.assertIn('assignment_execution_changed', self.stderr.getvalue())
        self.assertEqual((self.archived / 'return/completion.jsonl').read_bytes(), self.completion)
        self.assertNotIn(b'Verified assignment return archived', self.stdout.getvalue())

    def test_run_owned_corrupt_output_never_unwinds_the_inspection_hold(self):
        for output in (b'{}', b'{\n', b'[]\n', b'\xff\n', b'{"deep":' + b'[' * 10000 + b']' * 10000 + b'}\n'):
            with self.subTest(length=len(output)), self.assertRaises(RunnerHeld):
                self.run_owned(output)
        self.assertNotIn(b'Verified assignment return archived', self.stdout.getvalue())
        self.assertEqual(self.stderr.getvalue().count('invalid_one_shot_output'), 5)
        self.assertFalse((self.pending / 'return').exists())
        self.assertFalse(self.archived.exists())
        self.probe.assert_not_called()

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
elif program == 'codex' and argv == ['debug', 'models']:
    print(os.environ.get('HERDR_TEST_CATALOG_STDERR', ''), file=sys.stderr)
    print(os.environ['HERDR_TEST_CATALOG'])
    sys.exit(int(os.environ.get('HERDR_TEST_CATALOG_EXIT', '0')))
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
        self.codex_home = self.root / 'codex home'
        self.codex_home.mkdir()
        self.env = {
            **os.environ, 'PATH': str(self.bin), 'HERDR_TEST_LOG': str(self.log),
            'SCOPEHUB_STORAGE': 'off', 'CODEX_HOME': str(self.codex_home),
            'HERDR_TEST_CATALOG': json.dumps(FUTURE_CATALOG),
            'HERDR_TEST_SPACES': json.dumps({
                'spaces': [{'id': 's-local', 'machine': None}, {'id': 'remote/s-remote', 'machine': 'remote'}],
                'recent': [{'id': 's-recent', 'machine': None}],
            }),
        }
        for key in ('HC_TERMINAL_ID', 'HC_TERMINAL_SOCKET'):
            self.env.pop(key, None)

    def write_records(self, root, task_id, *, implement=IMPLEMENT, supervise=SUPERVISE, efforts=None):
        project = root / 'project'
        project.mkdir(exist_ok=True)
        (project / 'tasks.json').write_text(json.dumps({'tasks': [{'id': task_id}]}), encoding='utf-8')
        settings = {'host': 'Herdr', 'profiles': {'implement': implement, 'supervise': supervise},
                    'parallel': [], 'tooling': []}
        if efforts is not None:
            settings['reasoning_efforts'] = efforts
        (project / 'development.json').write_text(json.dumps({'herdr': settings}), encoding='utf-8')

    def use_family_profiles(self, efforts=EFFORTS):
        self.write_records(self.worktree, 'M0-025', implement={'family': 'sol'},
                           supervise={'family': 'astra'}, efforts=efforts)

    def cli(self, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), *arguments], cwd=cwd or self.nested,
            env=self.env, capture_output=True, encoding='utf-8', timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]


class DryRunTests(WorktreeFixture):
    def local_native_context(self):
        socket = str(self.root / 'native.sock')
        self.env.update(HC_SOCKET=socket, HC_TERMINAL_ID='t25', HC_TERMINAL_SOCKET=socket)

    @unittest.skipUnless(sys.platform == 'linux', 'Managed sockets are local Linux only')
    def test_ordinary_visible_launch_adopts_its_native_owner_without_side_effects(self):
        self.local_native_context()
        self.use_family_profiles()
        (self.codex_home / 'models_cache.json').write_text(json.dumps(FUTURE_CATALOG), encoding='utf-8')
        state = self.root / 'absent private state'
        result = self.cli('M0-025', 'implement', '--assignment', 'One bounded return', '--space', 's-local',
                          '--effort', 'ultra', '--state-dir', str(state), '--dry-run')

        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertNotIn('--parent', plan['argv'])
        self.assertFalse(any(item.startswith('scopehub.assignment=') for item in plan['argv']))
        argv = plan['managed_argv']
        self.assertEqual(plan['lifecycle']['parent'], 't25')
        self.assertEqual(plan['lifecycle']['tier'], 'priority')
        self.assertEqual(argv[argv.index('--parent') + 1], 't25')
        self.assertIn('herdr.space=s-local', argv)
        self.assertIn('model_reasoning_effort="ultra"', argv)
        self.assertIn('service_tier="priority"', argv)
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-10.9-sol')
        self.assertFalse(state.exists())
        self.assertFalse(Path(self.env['HC_SOCKET']).exists())
        self.assertFalse(self.log.exists())

    def test_explicit_standalone_preserves_ordinary_visible_launch_inside_native_owner(self):
        self.local_native_context()
        result = self.cli('M0-025', 'implement', '--assignment', 'An interactive terminal', '--space', 's-local',
                          '--standalone', '--dry-run')

        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertNotIn('lifecycle', plan)
        self.assertNotIn('managed_argv', plan)
        self.assertNotIn('--parent', plan['argv'])
        self.assertFalse(any(item.startswith('scopehub.assignment=') for item in plan['argv']))
        self.assertIn('herdr.space=s-local', plan['argv'])
        self.assertEqual(plan['argv'][plan['argv'].index('--') + 1:][:2], ['codex', '--no-daemon'])
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Managed sockets are local Linux only')
    def test_one_shot_dry_run_binds_an_owned_visible_implementation_to_codex_exec(self):
        self.local_native_context()
        state = self.root / 'absent private state'
        result = self.cli('M0-025', 'implement', '--assignment', 'Complete one bounded turn', '--space', 's-local',
                          '--one-shot', '--state-dir', str(state), '--dry-run')

        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['lifecycle'], {'version': 1, 'managed_entry_required': True, 'parent': 't25',
                                             'tier': 'priority', 'host': 'linux', 'mode': 'one-shot'})
        argv = plan['argv']
        self.assertIn('herdr.space=s-local', argv)
        self.assertEqual(argv[argv.index('--parent') + 1], 't25')
        self.assertEqual(argv[argv.index('--') + 1:][:3], ['codex', 'exec', '--json'])
        self.assertNotIn('--no-daemon', argv)
        self.assertIn('--approve-for-me', argv)
        self.assertTrue(argv[-1].endswith('\nComplete one bounded turn'))
        self.assertFalse(state.exists())
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Managed sockets are local Linux only')
    def test_one_shot_rejects_an_unowned_hidden_standalone_or_nonimplementation_launch(self):
        arguments = ('--assignment', 'Complete one bounded turn', '--one-shot', '--dry-run')
        for role, options, owned in (
            ('implement', ('--space', 's-local'), False),
            ('implement', ('--managed', '--parent', 't25'), True),
            ('implement', ('--space', 's-local', '--standalone'), True),
            ('coordinate', ('--space', 's-local'), True),
            ('review', ('--space', 's-local'), True),
        ):
            with self.subTest(role=role, options=options, owned=owned):
                if owned:
                    self.local_native_context()
                result = self.cli('M0-025', role, *arguments, *options)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--one-shot requires a managed implementation with a visible local space', result.stderr)
                self.assertEqual(result.stdout, '')
        self.assertFalse(self.log.exists())

    def test_absent_incomplete_or_other_core_context_does_not_adopt_a_native_owner(self):
        arguments = ('M0-025', 'implement', '--assignment', 'An unowned terminal', '--space', 's-local', '--dry-run')
        for scenario in ('absent', 'unowned', 'incomplete', 'other-core'):
            with self.subTest(context=scenario):
                self.local_native_context()
                if scenario == 'absent':
                    self.env.pop('HC_TERMINAL_ID')
                    self.env.pop('HC_TERMINAL_SOCKET')
                elif scenario == 'unowned':
                    self.env['HC_TERMINAL_ID'] = 'virtual-terminal'
                elif scenario == 'incomplete':
                    self.env.pop('HC_TERMINAL_SOCKET')
                else:
                    self.env['HC_TERMINAL_SOCKET'] = str(self.root / 'other-core.sock')
                result = self.cli(*arguments)
                self.assertEqual(0, result.returncode, result.stderr)
                plan = json.loads(result.stdout)
                self.assertNotIn('lifecycle', plan)
                self.assertNotIn('managed_argv', plan)
                self.assertNotIn('--parent', plan['argv'])
        self.assertFalse(self.log.exists())

    def test_coordination_and_review_without_bound_source_stay_usable_in_a_native_owner(self):
        self.local_native_context()
        for role in ('coordinate', 'review'):
            with self.subTest(role=role):
                result = self.cli('M0-025', role, '--assignment', 'Inspect one file', '--space', 's-local', '--dry-run')
                self.assertEqual(0, result.returncode, result.stderr)
                plan = json.loads(result.stdout)
                self.assertNotIn('lifecycle', plan)
                self.assertNotIn('managed_argv', plan)
                self.assertNotIn('--parent', plan['argv'])
        self.assertFalse(self.log.exists())

    def test_managed_plan_serves_the_explicit_native_coordinator_launch(self):
        self.use_family_profiles()
        (self.codex_home / 'models_cache.json').write_text(json.dumps(FUTURE_CATALOG), encoding='utf-8')
        state = self.root / 'absent private state'
        result = self.cli('M0-025', 'implement', '--assignment', 'One bounded return', '--space', 's-local',
                          '--parent', 't25', '--no-notify', '--tier', 'priority', '--effort', 'ultra',
                          '--state-dir', str(state), '--managed', '--dry-run')
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        argv = plan['argv']
        self.assertEqual(argv[:3], ['hc', 'run', '--bg'])
        self.assertEqual(argv[argv.index('--parent') + 1], 't25')
        self.assertIn('--no-notify', argv)
        self.assertIn('herdr.space=s-local', argv)
        self.assertIn('model_reasoning_effort="ultra"', argv)
        self.assertIn('service_tier="priority"', argv)
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-10.9-sol')
        self.assertTrue(plan['lifecycle']['managed_entry_required'])
        self.assertFalse(state.exists())
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Managed sockets are local Linux only')
    def test_collection_dry_run_neither_starts_a_core_nor_creates_state(self):
        state = self.root / 'absent private state'
        self.env['HC_SOCKET'] = str(self.root / 'absent-core.sock')
        result = self.cli('collect', '--parent', 't25', '--state-dir', str(state), '--dry-run')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'version': 1, 'parent': 't25', 'dry_run': True, 'assignments': []},
                         json.loads(result.stdout))
        self.assertFalse(state.exists())
        self.assertFalse(self.log.exists())

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

    def test_launch_delivers_a_bounded_causal_excerpt_with_the_lookup(self):
        project = self.worktree / 'project'
        task = {
            'id': 'M0-025', 'status': 'active', 'title': 'Observe admission',
            'plan': {'outcome': 'Attribute admission to the owning operation.'},
            'next_action': 'Verification owner captures the next refusal before retry.',
        }
        (project / 'tasks.json').write_text(json.dumps({'tasks': [task]}), encoding='utf-8')
        known = {
            'task_ids': [task['id']], 'status': 'addressed', 'severity': 'critical',
            'source': 'Retained original fixture SHA 123.',
            'observation': 'Parsed A and published B.',
            'resolution': {'cause': 'The stages independently opened the same path.'},
        }
        pending = {
            'task_ids': [task['id']], 'status': 'open', 'severity': 'major',
            'observation': 'TIMEOUT.\nUnknown holder.' + ' More evidence.' * 100,
            'diagnostic_reports': [{'run_id': 'first-refusal', 'source_commit': '1' * 40,
                                    'report_sha256': '2' * 64}],
            'resolution': None,
        }
        feedback = project / 'feedback.json'
        feedback.write_text(json.dumps({'items': [known, dict(pending, severity='minor'), pending]}),
                            encoding='utf-8')

        def excerpt(role):
            result = self.cli(task['id'], role, '--assignment', 'Inspect task', '--dry-run')
            self.assertEqual(0, result.returncode, result.stderr)
            prompt = json.loads(result.stdout)['argv'][-1]
            self.assertLess(len(prompt.encode('utf-8')), 1600)
            return json.JSONDecoder().raw_decode(prompt.split('Task records\n', 1)[1])[0]

        preview = excerpt('implement')
        self.assertEqual(task['plan']['outcome'], preview['outcome'])
        self.assertEqual(task['next_action'], preview['next_action'])
        self.assertEqual(2, preview['additional_findings_in_task_context'])
        finding = preview['finding']
        self.assertEqual('open', finding['status'])
        self.assertTrue(finding['observation'].startswith('TIMEOUT. Unknown holder.'))
        self.assertTrue(finding['observation'].endswith('…'))
        self.assertNotIn('attribution', finding)
        self.assertNotIn('recorded_cause', finding)
        self.assertIn('resolve first-refusal', finding['original_diagnostic'])
        self.assertIn('scopehub.archiveRoot', finding['archive_configuration'])
        self.assertTrue(finding['full_record'].endswith('/items/2'))

        feedback.write_text(json.dumps({'items': [known]}), encoding='utf-8')
        finding = excerpt('review')['finding']
        self.assertEqual('addressed', finding['status'])
        self.assertEqual(known['observation'], finding['observation'])
        self.assertEqual(known['resolution']['cause'], finding['recorded_cause'])
        self.assertEqual(known['source'], finding['original_source'])
        self.assertNotIn('attribution', finding)

        # Closed workflow state and formal resolution are distinct from what the observation already shows.
        open_with_mechanism = dict(known, status='open', resolution=None)
        task['diagnostic_reports'] = pending['diagnostic_reports']
        (project / 'tasks.json').write_text(json.dumps({'tasks': [task]}), encoding='utf-8')
        feedback.write_text(json.dumps({'items': [dict(pending, task_ids=None), open_with_mechanism]}), encoding='utf-8')
        preview = excerpt('implement')
        self.assertIn('Malformed rows were skipped', preview['feedback_diagnostic'])
        self.assertEqual('Parsed A and published B.', preview['finding']['observation'])
        self.assertEqual(known['source'], preview['finding']['original_source'])
        self.assertNotIn('original_diagnostic', preview['finding'])
        self.assertNotIn('attribution', preview['finding'])
        self.assertTrue(preview['finding']['full_record'].endswith('/items/1'))

        # A truncated observation cannot imply the unchecked half of a long sentence.
        qualified = dict(known, observation='Evidence remained disputed because ' + 'more detail ' * 60 + 'scope was unknown.')
        feedback.write_text(json.dumps({'items': [qualified]}), encoding='utf-8')
        self.assertEqual('Read the exact observation in the full record.', excerpt('review')['finding']['observation'])

        feedback.write_text('unparseable feedback', encoding='utf-8')
        preview = excerpt('implement')
        self.assertIn('could not be read', preview['feedback_diagnostic'])
        self.assertNotIn('finding', preview)
        feedback.unlink()
        self.assertNotIn('finding', excerpt('implement'))

    def test_previews_cached_family_models_without_executables(self):
        self.use_family_profiles()
        cache = self.codex_home / 'models_cache.json'
        cache.write_text(json.dumps(FUTURE_CATALOG), encoding='utf-8')
        for role, options, model, effort in (
            ('implement', [], 'gpt-10.12-sol', 'medium'),
            ('implement', ['--effort', 'high'], 'gpt-12-sol', 'high'),
            ('implement', ['--effort', 'ultra'], 'gpt-10.9-sol', 'ultra'),
            ('implement', ['--escalate-model'], 'gpt-30.1-astra', 'medium'),
            ('review', [], 'gpt-30.1-astra', 'medium'),
            ('coordinate', [], 'gpt-30.1-astra', 'medium'),
        ):
            with self.subTest(role=role, options=options):
                result = self.cli('M0-025', role, '--assignment', 'Inspect one file', '--dry-run', *options)
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads(result.stdout)['argv']
                self.assertEqual(argv[argv.index('--model') + 1], model)
                self.assertEqual(argv[argv.index('-c') + 1], f'model_reasoning_effort="{effort}"')
        self.assertFalse(self.log.exists())

    def test_cache_defaults_to_dot_codex_in_home_directory(self):
        self.use_family_profiles(['medium'])
        cache = self.root / '.codex' / 'models_cache.json'
        cache.parent.mkdir()
        cache.write_text(json.dumps(FUTURE_CATALOG), encoding='utf-8')
        output = StringIO()
        with (patch.object(sys, 'argv', [str(SCRIPT), 'M0-025', 'implement',
                                         '--assignment', 'Inspect one file', '--dry-run']),
              patch.object(herdr.Path, 'cwd', return_value=self.nested),
              patch.object(herdr.Path, 'home', return_value=self.root),
              patch.dict(os.environ, clear=True), patch.object(herdr.subprocess, 'run') as run,
              redirect_stdout(output)):
            self.assertEqual(herdr.main(), 0)
        run.assert_not_called()
        self.assertIn('gpt-10.12-sol', json.loads(output.getvalue())['argv'])

    def test_repository_policy_routes_each_role_with_a_future_catalog(self):
        destination = self.worktree / 'project/development.json'
        destination.write_bytes((SCRIPT.parents[1] / 'project/development.json').read_bytes())
        (self.codex_home / 'models_cache.json').write_text(json.dumps(FUTURE_CATALOG), encoding='utf-8')
        for role, options, model, effort in (
            ('implement', [], 'gpt-10.12-sol', 'medium'),
            ('review', [], 'gpt-30.1-astra', 'medium'),
            ('coordinate', [], 'gpt-30.1-astra', 'medium'),
            ('implement', ['--escalate-model', '--effort', 'ultra'], 'gpt-30.1-astra', 'ultra'),
        ):
            with self.subTest(role=role, options=options):
                result = self.cli('M0-025', role, '--assignment', 'Inspect one file', '--dry-run', *options)
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads(result.stdout)['argv']
                self.assertEqual(argv[argv.index('--model') + 1], model)
                self.assertEqual(argv[argv.index('-c') + 1], f'model_reasoning_effort="{effort}"')

    def test_accepts_family_names_with_hyphens_and_digits(self):
        self.write_records(self.worktree, 'M0-025', implement={'family': 'new-sol2'},
                           supervise={'family': 'astra'}, efforts=EFFORTS)
        (self.codex_home / 'models_cache.json').write_text(json.dumps({
            'models': [catalog_model('gpt-11.1-new-sol2', 'medium')],
        }), encoding='utf-8')
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gpt-11.1-new-sol2', json.loads(result.stdout)['argv'])

    def test_missing_or_invalid_cache_reports_recovery_without_contents(self):
        self.use_family_profiles()
        cache = self.codex_home / 'models_cache.json'
        for payload in (None, 'private catalog diagnostic', '{"models":{}}'):
            with self.subTest(payload=payload):
                if payload is not None:
                    cache.write_text(payload, encoding='utf-8')
                result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--dry-run')
                self.assertEqual(result.returncode, 1)
                self.assertIn(str(cache), result.stderr)
                self.assertIn('Start Codex to populate the cache, or retry without --dry-run.', result.stderr)
                self.assertNotIn('private catalog diagnostic', result.stderr)
                self.assertEqual(result.stdout, '')
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

    def test_rejects_invalid_overrides_before_catalog_lookup(self):
        for role, options, message in (
            ('implement', ['--effort', 'high'], 'require a family-based Herdr profile'),
            ('implement', ['--escalate-model'], 'require a family-based Herdr profile'),
            ('review', ['--escalate-model'], 'requires the implement role'),
            ('coordinate', ['--escalate-model'], 'requires the implement role'),
        ):
            with self.subTest(role=role, options=options):
                result = self.cli('M0-025', role, '--assignment', 'Inspect one file', *options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.use_family_profiles()
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--effort', 'low')
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported Herdr effort 'low'", result.stderr)
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

    def test_catalog_timeout_fails_before_herdr_and_storage_maintenance(self):
        self.use_family_profiles()
        debug = ['codex', 'debug', 'models']
        with (patch.object(herdr.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                debug, herdr.CATALOG_TIMEOUT_SECONDS, output=b'private catalog')) as run,
              patch.object(herdr, 'maintain_automatically') as maintain):
            self.assertEqual(1, self.launch())
        run.assert_called_once_with(debug, cwd=self.worktree, capture_output=True,
                                    timeout=herdr.CATALOG_TIMEOUT_SECONDS, check=True)
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

    def test_names_clean_review_source_and_rejects_dirty_or_moved_checkout(self):
        git = shutil.which('git')
        self.assertIsNotNone(git)
        self.env['PATH'] += os.pathsep + str(Path(git).parent)
        (self.worktree / '.git').unlink()

        def run_git(*args):
            return subprocess.check_output([git, *args], cwd=self.worktree, text=True).strip()

        run_git('init', '--quiet')
        records = self.worktree / 'project'
        finding = {'task_ids': ['M0-025'], 'status': 'open', 'severity': 'major',
                   'observation': 'The committed observation.', 'source': 'Original fixture.'}
        feedback_path = records / 'feedback.json'
        feedback_path.write_text(json.dumps({'items': [finding]}), encoding='utf-8')
        run_git('add', 'project')
        run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                'commit', '--quiet', '-m', 'Review fixture')
        reviewed = run_git('rev-parse', 'HEAD')
        arguments = ('M0-025', 'review', '--source', reviewed, '--assignment', 'Check reproducible reopen')
        result = self.cli(*arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['hc', 'codex'], [event['program'] for event in self.calls()])
        self.assertIn(f'context M0-025 --phase verify --source {reviewed}', self.calls()[-1]['argv'][-1])
        self.assertIn('Run this context command before inspecting', self.calls()[-1]['argv'][-1])

        read_text = Path.read_text

        def transient_view(path, *args, **kwargs):
            if path == feedback_path:
                return '{"items": []}'
            if path == records / 'development.json':
                return '{}'  # A worktree settings read would refuse to launch.
            return read_text(path, *args, **kwargs)

        output, error = StringIO(), StringIO()
        with (patch.object(sys, 'argv', [str(SCRIPT), *arguments, '--dry-run']),
              patch.object(herdr.Path, 'cwd', return_value=self.worktree),
              patch.object(Path, 'read_text', transient_view),
              redirect_stdout(output), redirect_stderr(error)):
            self.assertEqual(0, herdr.main(), error.getvalue())
        prompt = json.loads(output.getvalue())['argv'][-1]
        self.assertIn(f'/items/0 --source {reviewed}', prompt)
        self.assertIn(finding['observation'], prompt)

        changed = self.worktree / 'independent-input.txt'
        changed.write_text('Changed input.\n', encoding='utf-8')
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    run_git('add', changed.name)
                self.assertIn('requires a clean checkout', self.cli(*arguments).stderr)
        run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                'commit', '--quiet', '-m', 'Change reviewed source')
        self.assertIn('does not match checkout HEAD', self.cli(*arguments).stderr)
        self.assertEqual(2, len(self.calls()))

    def test_models_and_permissions_follow_each_role(self):
        for role, model, effort in (
            ('implement', IMPLEMENT['model'], IMPLEMENT['reasoning_effort']),
            ('review', SUPERVISE['model'], SUPERVISE['reasoning_effort']),
            ('coordinate', SUPERVISE['model'], SUPERVISE['reasoning_effort']),
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
                    self.assertIn('.venv/bin/python -B scripts/project.py context M0-025 --phase verify', context)
                else:
                    phase = 'implement' if role == 'implement' else 'handoff'
                    self.assertIn(f'pnpm project:context M0-025 --phase {phase}', context)
                self.assertEqual(assignment, 'Inspect one file')
        self.assertEqual([call['program'] for call in self.calls()], ['hc', 'codex'] * 3)

    def test_family_launch_resolves_fresh_catalog_and_keeps_role_permissions(self):
        self.use_family_profiles()
        assignment = 'Inspect "one" file\nKeep $HOME literal.'
        for role, options, model, effort, permissions in (
            ('implement', ['--escalate-model', '--effort', 'max'], 'gpt-30.1-astra', 'max', ['--approve-for-me']),
            ('implement', [], 'gpt-10.12-sol', 'medium', ['--approve-for-me']),
            ('review', ['--effort', 'xhigh'], 'gpt-30.1-astra', 'xhigh',
             ['--sandbox', 'read-only', '--ask-for-approval', 'never']),
            ('coordinate', [], 'gpt-30.1-astra', 'medium', ['--approve-for-me']),
        ):
            with self.subTest(role=role, options=options):
                result = self.cli('M0-025', role, '--assignment', assignment, '--space', 's-local', *options)
                self.assertEqual(result.returncode, 0, result.stderr)
                catalog, desktop, hc, codex = self.calls()[-4:]
                self.assertEqual((catalog['program'], catalog['argv'], catalog['cwd']),
                                 ('codex', ['debug', 'models'], str(self.worktree)))
                self.assertEqual((desktop['program'], desktop['argv']), ('herdr-app', ['ls']))
                self.assertEqual(hc['argv'][6:13], [
                    '--label', 'task=M0-025', '--label', f'role={role}', '--label', 'herdr.space=s-local', '--',
                ])
                self.assertEqual((codex['program'], codex['cwd']), ('codex', str(self.worktree)))
                self.assertEqual(codex['argv'][:-1], [
                    '--no-daemon', '--model', model, '-c', f'model_reasoning_effort="{effort}"', *permissions, '--',
                ])
                context, actual_assignment = codex['argv'][-1].split('\n\n', 1)
                self.assertIn(f'M0-025, role {role}', context)
                self.assertEqual(actual_assignment, assignment)
        self.assertEqual([call['program'] for call in self.calls()], ['codex', 'herdr-app', 'hc', 'codex'] * 4)

    def test_real_launch_never_falls_back_to_cache_family_or_effort(self):
        self.use_family_profiles()
        (self.codex_home / 'models_cache.json').write_text(json.dumps({
            'models': [catalog_model('gpt-13-sol', 'max')],
        }), encoding='utf-8')
        self.env['HERDR_TEST_CATALOG'] = json.dumps({'models': [
            catalog_model('gpt-12-sol', 'high', 'ultra'),
            catalog_model('gpt-30.1-astra', 'max'),
        ]})
        result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file', '--effort', 'max')
        self.assertEqual(result.returncode, 1)
        self.assertIn("No listed stable Codex model in family 'sol' supports effort 'max'", result.stderr)
        self.assertEqual([entry['argv'] for entry in self.calls()], [['debug', 'models']])

    def test_catalog_failures_do_not_log_catalog_payloads_or_start_herdr(self):
        self.use_family_profiles()
        self.env['HERDR_TEST_CATALOG_STDERR'] = 'private diagnostic on stderr'
        self.env['HERDR_TEST_CATALOG'] = 'private catalog on stdout'
        for code, message in (('0', 'Invalid catalog'), ('19', 'failed with exit status 19')):
            with self.subTest(code=code):
                self.env['HERDR_TEST_CATALOG_EXIT'] = code
                result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file')
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn('private', result.stderr)
                self.assertEqual(result.stdout, '')
        self.assertEqual([entry['argv'] for entry in self.calls()], [['debug', 'models']] * 2)

    def test_invalid_family_policy_stops_before_catalog_or_herdr(self):
        for efforts in (None, [], ['low', 'medium'], ['high'], ['medium', 'low'], ['medium', 'high', 'high']):
            with self.subTest(efforts=efforts):
                self.use_family_profiles(efforts)
                result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file')
                self.assertEqual(result.returncode, 1)
                self.assertIn('reasoning_efforts must start with medium', result.stderr)
        for supervise in ({}, {'family': '1astra'}, {'family': 'astra\n'}, SUPERVISE):
            with self.subTest(supervise=supervise):
                self.write_records(self.worktree, 'M0-025', implement={'family': 'sol'},
                                   supervise=supervise, efforts=EFFORTS)
                result = self.cli('M0-025', 'implement', '--assignment', 'Inspect one file')
                self.assertEqual(result.returncode, 1)
                self.assertIn("profile 'supervise'", result.stderr)
        self.assertFalse(self.log.exists())

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
