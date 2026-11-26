"""Exercise archived-original and exact retirement behavior without real terminals."""

from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.herdr_lifecycle import Lifecycle, LifecycleHold
from scripts.worktree import exclusive_git_lock


@unittest.skipUnless(sys.platform == 'linux', 'Local Herdr lifecycle requires Linux.')
class HerdrLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name).resolve() / 'state'
        self.parent = 't42'
        self.original = b'{"text":"The valid control ran."}\n'
        self.terminal = {'id': 't43', 'session_id': 'fixture-session', 'turn': 1}
        self.observe = Mock(return_value=self.terminal.copy())
        self.retire = Mock()
        self.probe = Mock(return_value=SimpleNamespace(original=self.original, metadata={'turn': 1}))
        self.lifecycle = self.open_lifecycle()

    def open_lifecycle(self):
        return Lifecycle(self.state, self.parent, observe=self.observe, retire=self.retire, probe=self.probe)

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

    def lane(self, assignment='owned-1', *, retired=False):
        return self.state / self.parent / ('retired' if retired else 'pending') / assignment

    def state_record(self, *, retired=False):
        return json.loads((self.lane(retired=retired) / 'assignment.json').read_bytes())

    def test_empty_and_foreign_dry_runs_have_no_side_effects(self):
        result = self.collect(dry_run=True)
        self.assertEqual(result['reason'], 'unregistered_assignment')
        self.assertFalse(self.state.exists())
        self.observe.assert_not_called()
        self.retire.assert_not_called()

        self.register()
        other = Lifecycle(self.state, 't45', observe=self.observe, retire=self.retire, probe=self.probe)
        self.assertEqual(self.collect(lifecycle=other)['reason'], 'unregistered_assignment')
        self.assertFalse((self.state / 't45').exists())
        self.observe.assert_not_called()

    def test_eligible_dry_run_writes_nothing_even_if_lock_is_absent(self):
        self.register()
        (self.lane() / 'operation.lock').unlink()
        before = {p.relative_to(self.state): p.stat().st_mtime_ns for p in self.state.rglob('*')}
        self.assertEqual(self.collect(dry_run=True)['status'], 'eligible')
        self.assertEqual({p.relative_to(self.state): p.stat().st_mtime_ns for p in self.state.rglob('*')}, before)
        self.retire.assert_not_called()
        self.assertEqual(self.state_record()['phase'], 'registered')

    def test_only_exact_private_archive_is_available_before_last_assignment_retires(self):
        binding = self.register()

        def verify_archive_before_close(received, terminal, *, original_sha256):
            self.assertEqual(received, binding)
            self.assertEqual(terminal, self.terminal)
            receipt = self.state_record()
            self.assertEqual(receipt['phase'], 'retiring')
            self.assertEqual(receipt['archive']['sha256'], sha256(self.original).hexdigest())
            self.assertEqual(receipt['archive']['sha256'], original_sha256)
            self.assertEqual(self.lane().stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.lane() / 'original.jsonl').stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.lane() / 'original.jsonl').read_bytes(), self.original)

        self.retire.side_effect = verify_archive_before_close
        result = self.collect(lifecycle=self.open_lifecycle())
        self.assertEqual(result['status'], 'retired')
        self.assertEqual(self.state_record(retired=True)['binding'], binding)
        self.assertEqual(self.collect()['status'], 'retired')
        self.retire.assert_called_once()
        self.assertEqual(self.lifecycle.collect()['assignments'], [])

    def test_busy_corrupt_working_and_failed_lanes_leave_eligible_lane_independent(self):
        busy = self.register('busy')
        self.register('corrupt')
        self.register('working')
        self.register('failed')
        self.register('malformed')
        eligible = self.register('eligible')
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
                          'eligible': 'retired', 'working': 'terminal_working',
                          'failed': 'assignment_io_unavailable', 'malformed': 'invalid_assignment_input'})
        self.retire.assert_called_once_with(eligible, self.terminal, original_sha256=sha256(self.original).hexdigest())

    def test_missing_or_ambiguous_result_keeps_terminal(self):
        self.register()
        for reason in ('original_missing', 'original_ambiguous', 'turn_interrupted'):
            self.probe.side_effect = LifecycleHold(reason)
            self.assertEqual(self.collect()['reason'], reason)
        self.assertFalse((self.lane() / 'original.jsonl').exists())
        self.retire.assert_not_called()

    def test_turn_change_after_archive_holds_original_and_leaves_terminal(self):
        self.register()
        changed = dict(self.terminal, turn=2)
        self.observe.side_effect = [self.terminal, changed, changed, None]
        self.assertEqual(self.collect()['reason'], 'terminal_changed')
        self.assertEqual(self.state_record()['phase'], 'archived')
        self.assertEqual(self.collect()['reason'], 'terminal_changed')
        self.assertEqual(self.collect()['reason'], 'terminal_missing')
        self.assertEqual((self.lane() / 'original.jsonl').read_bytes(), self.original)
        self.retire.assert_not_called()

    def test_interruption_after_archive_retries_exact_original_and_close_once(self):
        self.register()
        self.observe.side_effect = [self.terminal, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assertEqual(self.state_record()['phase'], 'archived')
        self.retire.assert_not_called()
        self.observe.side_effect = None
        self.assertEqual(self.collect(lifecycle=self.open_lifecycle())['status'], 'retired')
        self.retire.assert_called_once()

    def test_interruption_after_native_close_recovers_without_closing_again(self):
        self.register()

        def interrupted_close(binding, terminal, *, original_sha256):
            self.observe.return_value = None
            raise KeyboardInterrupt()

        self.retire.side_effect = interrupted_close
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assertEqual(self.collect(dry_run=True)['status'], 'recoverable_retirement')
        self.assertEqual(self.state_record()['phase'], 'retiring')
        self.assertEqual(self.collect(lifecycle=self.open_lifecycle())['status'], 'retired')
        self.retire.assert_called_once()

    def test_finished_interruption_uses_retained_original_without_the_live_cli(self):
        binding = self.register()
        self.retire.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assertEqual(self.state_record()['phase'], 'retiring')
        self.probe.reset_mock()
        self.retire.reset_mock(side_effect=True)
        self.observe.return_value = dict(self.terminal, finished=True)
        self.assertEqual(self.collect(dry_run=True)['status'], 'recoverable_retirement')
        self.assertEqual(self.collect(lifecycle=self.open_lifecycle())['status'], 'retired')
        self.probe.assert_not_called()
        self.retire.assert_called_once_with(binding, self.terminal, original_sha256=sha256(self.original).hexdigest())

    def test_corrupt_retained_original_never_authorizes_retry(self):
        self.register()
        self.retire.side_effect = LifecycleHold('native_unavailable')
        self.assertEqual(self.collect()['reason'], 'native_unavailable')
        (self.lane() / 'original.jsonl').write_bytes(b'changed')
        self.assertEqual(self.collect()['reason'], 'archive_changed')
        self.retire.assert_called_once()

    def test_orphaned_original_is_checked_by_dry_run_and_never_overwritten(self):
        self.register()
        original = self.lane() / 'original.jsonl'
        original.touch(mode=0o600)
        original.write_bytes(b'interrupted write')
        self.assertEqual(self.collect(dry_run=True)['reason'], 'archive_changed')
        self.assertEqual(self.collect()['reason'], 'archive_changed')
        self.assertEqual(original.read_bytes(), b'interrupted write')
        self.retire.assert_not_called()

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
