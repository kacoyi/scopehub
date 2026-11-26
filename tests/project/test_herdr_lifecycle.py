"""Exercise recoverable assignment archives without real terminals."""

from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import herdr_lifecycle
from scripts.herdr_lifecycle import Lifecycle, LifecycleHold
from scripts.herdr_returns import Proof
from scripts.worktree import exclusive_git_lock


@unittest.skipUnless(sys.platform == 'linux', 'Local Herdr lifecycle requires Linux.')
class HerdrLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name).resolve() / 'state'
        self.parent = 't42'
        self.original = b'{"text":"The valid control ran."}\n'
        self.completion = b'{"turn_id":"bound","type":"task_complete"}\n'
        self.terminal = {'id': 't43', 'session_id': 'fixture-session', 'turn': 1}
        self.observe = Mock(return_value=self.terminal.copy())
        self.probe = Mock(return_value=Proof(self.original, {'turn_id': 'bound',
                          'completion_sha256': sha256(self.completion).hexdigest()}, self.completion))
        self.lifecycle = self.open_lifecycle()

    def open_lifecycle(self):
        return Lifecycle(self.state, self.parent, observe=self.observe, probe=self.probe)

    def register(self, assignment='owned-1', **overrides):
        binding = {
            'id': assignment, 'parent': self.parent, 'role': 'implement', 'task': 'M0-162',
            'checkout': str(self.state.parent), 'source': 'a' * 40, 'prompt_sha256': 'b' * 64,
            'model': 'gpt-fixture-sol', 'effort': 'ultra',
        }
        binding.update(overrides)
        self.lifecycle.register(binding)
        return binding

    def collect(self, assignment='owned-1', *, dry_run=False, lifecycle=None):
        return (lifecycle or self.lifecycle).collect(assignment, dry_run=dry_run)['assignments'][0]

    def lane(self, assignment='owned-1', *, section='pending'):
        return self.state / self.parent / section / assignment

    def state_record(self, *, section='pending'):
        return json.loads((self.lane(section=section) / 'assignment.json').read_bytes())

    def test_empty_and_foreign_dry_runs_have_no_side_effects(self):
        result = self.collect(dry_run=True)
        self.assertEqual(result['reason'], 'unregistered_assignment')
        self.assertFalse(self.state.exists())
        self.observe.assert_not_called()

        self.register()
        other = Lifecycle(self.state, 't45', observe=self.observe, probe=self.probe)
        self.assertEqual(self.collect(lifecycle=other)['reason'], 'unregistered_assignment')
        self.assertFalse((self.state / 't45').exists())
        self.observe.assert_not_called()

    def test_eligible_dry_run_writes_nothing_even_if_lock_is_absent(self):
        self.register()
        (self.lane() / 'operation.lock').unlink()
        before = {p.relative_to(self.state): p.stat().st_mtime_ns for p in self.state.rglob('*')}
        self.assertEqual(self.collect(dry_run=True)['status'], 'eligible')
        self.assertEqual({p.relative_to(self.state): p.stat().st_mtime_ns for p in self.state.rglob('*')}, before)
        self.assertEqual(self.state_record()['phase'], 'registered')

    def test_last_assignment_keeps_exact_private_archive_when_terminal_is_reusable(self):
        binding = self.register()
        result = self.collect(lifecycle=self.open_lifecycle())

        self.assertEqual((result['status'], result['reason']), ('archived', 'reusable_terminal'))
        receipt = self.state_record(section='archived')
        self.assertEqual(receipt['binding'], binding)
        self.assertEqual(receipt['phase'], 'archived')
        self.assertEqual(receipt['archive']['sha256'], sha256(self.original).hexdigest())
        self.assertEqual(receipt['archive']['sha256'], result['original_sha256'])
        archive = self.lane(section='archived') / 'return'
        self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
        for filename, contents in (('original.jsonl', self.original), ('completion.jsonl', self.completion)):
            with self.subTest(filename=filename):
                path = archive / filename
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.read_bytes(), contents)

        self.assertEqual(self.collect()['status'], 'archived')
        self.probe.assert_called_once_with(binding, self.terminal)
        self.assertEqual(self.lifecycle.collect()['assignments'], [])

    def test_busy_corrupt_working_and_failed_lanes_leave_eligible_lane_independent(self):
        busy = self.register('busy')
        self.register('corrupt')
        self.register('working')
        self.register('failed')
        self.register('malformed')
        self.register('eligible')
        (self.lane('corrupt') / 'assignment.json').write_bytes(b'{"version":75}')

        def observe(binding, *, retiring):
            if binding['id'] == 'working':
                raise LifecycleHold('terminal_working')
            if binding['id'] == 'failed':
                raise OSError('A private path is never printed.')
            if binding['id'] == 'malformed':
                raise KeyError('Invalid private native input')
            return self.terminal.copy()

        self.observe.side_effect = observe
        with exclusive_git_lock(self.lane(busy['id']) / 'operation.lock'):
            results = self.lifecycle.collect()['assignments']
        self.assertEqual({row['assignment']: row.get('reason', row['status']) for row in results},
                         {'busy': 'assignment_busy', 'corrupt': 'unknown_registration_version',
                          'eligible': 'reusable_terminal', 'working': 'terminal_working',
                          'failed': 'assignment_io_unavailable', 'malformed': 'invalid_assignment_input'})
        self.assertEqual([row['status'] for row in results if row['assignment'] == 'eligible'], ['archived'])

    def test_missing_or_ambiguous_result_keeps_terminal(self):
        self.register()
        for reason in ('original_missing', 'original_ambiguous', 'turn_interrupted'):
            self.probe.side_effect = LifecycleHold(reason)
            self.assertEqual(self.collect()['reason'], reason)
        self.assertFalse((self.lane() / 'return').exists())

    def test_turn_change_after_archive_holds_original_and_leaves_terminal(self):
        self.register()
        changed = dict(self.terminal, turn=2)
        self.observe.side_effect = [self.terminal, changed, changed, None]
        result = self.collect()
        self.assertEqual((result['status'], result['reason']), ('archived', 'terminal_changed'))
        self.assertEqual(self.state_record(section='archived')['phase'], 'archived')
        self.assertEqual(self.collect()['reason'], 'terminal_changed')
        self.assertEqual(self.collect()['reason'], 'terminal_missing')
        self.assertEqual((self.lane(section='archived') / 'return/original.jsonl').read_bytes(), self.original)

    def test_interruption_after_pair_commit_recovers_original_and_completion_without_a_live_turn(self):
        self.register()
        with (patch.object(herdr_lifecycle, '_write_record', side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            self.collect()
        self.assertEqual(self.state_record()['phase'], 'registered')
        archive = self.lane() / 'return'
        expected_events = {'original.jsonl': self.original, 'completion.jsonl': self.completion}
        self.assertEqual({name: (archive / name).read_bytes() for name in expected_events}, expected_events)
        self.probe.reset_mock()
        self.observe.return_value = None

        result = self.collect(lifecycle=self.open_lifecycle())

        self.assertEqual((result['status'], result['reason']), ('archived', 'terminal_missing'))
        archive = self.lane(section='archived') / 'return'
        self.assertEqual({name: (archive / name).read_bytes() for name in expected_events}, expected_events)
        self.probe.assert_not_called()

    def test_corrupt_retained_event_never_discloses_or_replaces_archive(self):
        self.register()
        self.assertEqual(self.collect()['status'], 'archived')
        self.observe.reset_mock()
        self.probe.reset_mock()
        archive = self.lane(section='archived') / 'return'
        for filename, contents in (('original.jsonl', self.original), ('completion.jsonl', self.completion)):
            with self.subTest(filename=filename):
                path = archive / filename
                path.write_bytes(b'changed')
                result = self.collect()
                self.assertEqual((result['status'], result['reason']), ('held', 'archive_changed'))
                self.assertEqual(path.read_bytes(), b'changed')
                path.write_bytes(contents)
        self.observe.assert_not_called()
        self.probe.assert_not_called()

    def test_orphaned_original_is_checked_by_dry_run_and_never_overwritten(self):
        self.register()
        original = self.lane() / 'original.jsonl'
        original.touch(mode=0o600)
        original.write_bytes(b'interrupted write')
        self.assertEqual(self.collect(dry_run=True)['reason'], 'archive_changed')
        self.assertEqual(self.collect()['reason'], 'archive_changed')
        self.assertEqual(original.read_bytes(), b'interrupted write')

    def test_linked_state_parent_is_rejected_before_registration_creates_files(self):
        real_parent = self.state.parent / 'actual'
        real_parent.mkdir()
        linked_parent = self.state.parent / 'link'
        linked_parent.symlink_to(real_parent)
        self.state = linked_parent / 'state'
        self.lifecycle = self.open_lifecycle()
        with self.assertRaisesRegex(LifecycleHold, 'unsafe_private_directory'):
            self.register()
        self.assertEqual(list(real_parent.iterdir()), [])
        self.observe.assert_not_called()

    def test_registration_cannot_be_rebound_or_authorize_a_coordinator(self):
        self.register()
        self.register()
        with self.assertRaisesRegex(LifecycleHold, 'registration_conflict'):
            self.register(source='c' * 40)
        with self.assertRaisesRegex(LifecycleHold, 'invalid_registration'):
            self.register('coord', role='coordinate')
        self.observe.assert_not_called()
