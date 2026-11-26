"""Exercise the Herdr native boundary without opening a process or terminal."""

from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from scripts import herdr_native
from scripts.herdr_lifecycle import LifecycleHold
from scripts.herdr_returns import Proof


CORE_ID = '3a' * 16
PARENT = 't11'
ADDRESS = 't12'
NAME = 'm0-162-implement-aabbccddeeff'
SESSION = '01a21690-93a0-79f3-b020-ab4c007f0531'
OTHER_SESSION = '01a21690-93a0-79f3-b020-ab4c007f0532'
CHECKOUT = '/scopehub-worktree'
SESSIONS = Path(Path.cwd().anchor) / 'private-codex' / 'sessions'
ROLLOUT = SESSIONS / f'2026/11/25/rollout-2026-11-25T22-35-03-{SESSION}.jsonl'
PROMPT = 'Owned assignment text.'
ARGV = ['codex', '--no-daemon', '--model', 'gpt-6.2-sol', '-c', 'model_reasoning_effort="ultra"',
        '-c', 'service_tier="priority"', '--', PROMPT]
ORIGINAL = b'one exact original event\n'
DIGEST = sha256(ORIGINAL).hexdigest()


class HerdrNativeTests(unittest.TestCase):
    def setUp(self):
        self.socket = Path('/runtime/herdr-core/default.sock')
        self.binding = {'task': 'M0-162', 'role': 'implement', 'checkout': CHECKOUT, 'source': 'b2' * 20,
                        'prompt_sha256': sha256(PROMPT.encode()).hexdigest(), 'model': 'gpt-6.2-sol',
                        'effort': 'ultra', 'tier': 'priority', 'sessions': str(SESSIONS),
                        'labels': {'task': 'M0-162', 'role': 'implement', 'scopehub.assignment': 'assignment-a'},
                        'codex_argv': ARGV[:-1], 'name': NAME, 'core_id': CORE_ID, 'parent': PARENT}
        self.row = {'address': ADDRESS, 'name': NAME, 'labels': self.binding['labels'].copy(), 'parent': PARENT,
                    'cwd': CHECKOUT, 'argv': ARGV.copy(), 'created_ms': 10, 'state': 'live', 'turn': 1,
                    'reported': True, 'status': {'state': 'idle'}, 'status_ms': 20, 'pid': 100,
                    'foreground': {'pid': 100, 'argv': ['/codex/bin/codex', *ARGV[1:]], 'cwd': CHECKOUT}}
        self.terminal = {'address': ADDRESS, 'created_ms': 10, 'turn': 1, 'pid': 100,
                         'rollout_path': str(ROLLOUT), 'session_id': SESSION, 'tab': None}
        self.proof = Proof(ORIGINAL, {'original_sha256': DIGEST})
        self.probe = Mock(return_value=self.proof)
        self.native = herdr_native.NativeLifecycle(self.socket, self.probe)
        self.core = Mock()
        self.connect = self.enterContext(patch.object(herdr_native, 'connect', side_effect=self.open_core))
        self.rollout = self.enterContext(patch.object(herdr_native, '_one_rollout',
                                                     return_value=(str(ROLLOUT), SESSION)))

    def open_core(self, path, expected):
        self.assertEqual(path, self.socket)
        self.assertEqual(expected, CORE_ID)
        return nullcontext((self.core, CORE_ID))

    def test_exact_completed_launch_queries_only_its_children_and_foreground_process(self):
        self.core.call.side_effect = [self.row, {'terminals': [{'address': 't13', 'state': 'finished'}]}]

        observed = self.native.observe(self.binding, retiring=None)

        self.assertEqual(observed, self.terminal)
        self.connect.assert_called_once_with(self.socket, CORE_ID)
        self.rollout.assert_called_once_with(100, SESSIONS)
        self.assertEqual(self.core.call.call_args_list, [call('terminal.get', terminal=NAME),
                                                         call('events.subscribe', children_of=ADDRESS)])
        self.probe.assert_not_called()

    def test_ordinary_shell_wrong_owner_prompt_tier_or_sibling_never_reaches_private_probe(self):
        for label, changed in (
            ('shell', {'argv': ['bash', '-l']}),
            ('owner', {'parent': 't19'}),
            ('prompt', {'argv': [*ARGV[:-1], 'Unregistered prompt.']}),
            ('tier', {'argv': [*ARGV[:7], 'service_tier="default"', *ARGV[8:]]}),
            ('sibling', {'labels': {**self.binding['labels'], 'scopehub.assignment': 'assignment-b'}}),
        ):
            with self.subTest(label=label):
                self.core.reset_mock()
                self.core.call.return_value = {**self.row, **changed}
                with self.assertRaisesRegex(LifecycleHold, '^foreign_terminal$'):
                    self.native.observe(self.binding, retiring=None)
                self.core.call.assert_called_once_with('terminal.get', terminal=NAME)
        self.rollout.assert_not_called()
        self.probe.assert_not_called()

    def test_working_changed_turn_or_replaced_process_never_reaches_private_probe(self):
        for changed, reason in (
            ({'status': {'state': 'working'}}, 'terminal_not_ready'),
            ({'turn': 2}, 'terminal_turn_changed'),
            ({'turn': None, 'reported': False}, 'terminal_turn_unconfirmed'),
            ({'foreground': {**self.row['foreground'], 'pid': 101}}, 'terminal_program_changed'),
        ):
            with self.subTest(reason=reason):
                self.core.reset_mock()
                self.core.call.return_value = {**self.row, **changed}
                with self.assertRaisesRegex(LifecycleHold, f'^{reason}$'):
                    self.native.observe(self.binding, retiring=None)
                self.core.call.assert_called_once_with('terminal.get', terminal=NAME)
        self.rollout.assert_not_called()
        self.probe.assert_not_called()

    def test_live_or_unverifiable_children_hold_only_the_exact_owned_terminal(self):
        for snapshot, reason in (({'terminals': [{'address': 't13', 'state': 'live'}]},
                                  'terminal_has_live_children'), (None, 'terminal_children_unverified')):
            with self.subTest(reason=reason):
                self.core.reset_mock()
                self.core.call.side_effect = [self.row, snapshot]
                with self.assertRaisesRegex(LifecycleHold, f'^{reason}$'):
                    self.native.observe(self.binding, retiring=None)
                self.assertEqual(self.core.call.call_args_list, [call('terminal.get', terminal=NAME),
                                                                 call('events.subscribe', children_of=ADDRESS)])
        self.probe.assert_not_called()

    def test_retirement_only_mutates_the_exact_owned_address_after_revalidation(self):
        self.core.call.side_effect = [self.row, self.row, {'terminals': []}, self.row, {},
                                      {**self.row, 'state': 'finished'}, {}, None]

        self.native.retire(self.binding, self.terminal, original_sha256=DIGEST)

        self.probe.assert_called_once_with(self.binding, self.terminal)
        self.assertEqual(self.core.call.call_args_list, [
            call('terminal.get', terminal=ADDRESS), call('terminal.get', terminal=ADDRESS),
            call('events.subscribe', children_of=ADDRESS), call('terminal.get', terminal=ADDRESS),
            call('terminal.kill', terminal=ADDRESS), call('terminal.get', terminal=ADDRESS),
            call('terminal.rm', terminal=ADDRESS), call('terminal.get', terminal=ADDRESS),
        ])

    def test_new_turn_between_revalidation_and_mutation_holds_without_a_close(self):
        self.core.call.side_effect = [self.row, self.row, {'terminals': []}, {**self.row, 'turn': 2}]

        with self.assertRaisesRegex(LifecycleHold, '^terminal_changed_before_retirement$'):
            self.native.retire(self.binding, self.terminal, original_sha256=DIGEST)
        self.assertTrue(all(entry.args[0] in ('terminal.get', 'events.subscribe')
                            for entry in self.core.call.call_args_list))

    def test_retirement_refuses_changed_archived_response_without_a_close(self):
        stale = sha256(b'different archived response').hexdigest()
        self.core.call.return_value = self.row

        with self.assertRaisesRegex(LifecycleHold, '^original_changed$'):
            self.native.retire(self.binding, self.terminal, original_sha256=stale)
        self.core.call.assert_called_once_with('terminal.get', terminal=ADDRESS)

    def test_retirement_refuses_replaced_exact_address_without_archive_probe(self):
        self.core.call.return_value = {**self.row, 'created_ms': 11}
        with self.assertRaisesRegex(LifecycleHold, '^foreign_terminal$'):
            self.native.retire(self.binding, self.terminal, original_sha256=DIGEST)
        self.core.call.assert_called_once_with('terminal.get', terminal=ADDRESS)
        self.probe.assert_not_called()

    def test_visible_retry_holds_until_its_exact_tab_has_disappeared(self):
        binding = {**self.binding, 'space': 'space-a'}
        terminal = {**self.terminal, 'tab': {'id': 'tab-a', 'pane': 'pane-a'}}
        expected_tab = {'id': 'tab-a', 'terminals': [{'terminal': ADDRESS, 'name': NAME, 'pane': 'pane-a'}]}
        sibling_tab = {'id': 'tab-b', 'terminals': [{'terminal': 't19', 'name': 'sibling', 'pane': 'pane-b'}]}
        local = {'id': 'space-a', 'machine': None}
        self.core.call.return_value = None
        with patch.object(herdr_native, '_desktop_tabs', side_effect=[[(local, expected_tab), (local, sibling_tab)],
                                                                     [(local, sibling_tab)]]):
            with self.assertRaisesRegex(LifecycleHold, '^desktop_retirement_pending$'):
                self.native.retire(binding, terminal, original_sha256=DIGEST)
            self.native.retire(binding, terminal, original_sha256=DIGEST)
        self.assertEqual(self.core.call.call_args_list, [call('terminal.get', terminal=ADDRESS)] * 2)
        self.probe.assert_not_called()


class HerdrNativeDiscoveryTests(unittest.TestCase):
    def test_handshake_rejects_restarted_core_before_scoped_operations(self):
        path = MagicMock()
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFSOCK, st_uid=1000)
        path.__str__ = Mock(return_value='/fake-core.sock')
        connection, core = MagicMock(), Mock()
        connection.__enter__.return_value = connection
        core.call.return_value = {'generation': 1, 'features': ['events'], 'core': {'id': '4b' * 16}}
        with (patch.object(herdr_native.os, 'getuid', return_value=1000, create=True),
              patch.object(herdr_native.socket, 'socket', return_value=connection),
              patch.object(herdr_native, 'Core', return_value=core)):
            with self.assertRaisesRegex(LifecycleHold, '^wrong_core$'):
                with herdr_native.connect(path, CORE_ID):
                    self.fail('A restarted core must reject the stored binding.')
        core.call.assert_called_once_with('hello')

    def test_descriptor_lookup_scopes_to_one_pid_and_one_session_tree(self):
        other = SESSIONS / f'rollout-another-{OTHER_SESSION}.jsonl'
        unrelated = SESSIONS.parent / 'other' / ROLLOUT.name
        for targets, expected in (([unrelated], 'original_missing'), ([ROLLOUT, other], 'original_ambiguous'),
                                  ([unrelated, ROLLOUT, ROLLOUT], (str(ROLLOUT), SESSION))):
            with self.subTest(expected=expected):
                directory = Path('/proc/100/fd')
                handles = iter(SimpleNamespace(path=str(directory / str(index))) for index in range(len(targets)))
                with (patch.object(herdr_native.os, 'scandir', return_value=nullcontext(handles)) as scan,
                      patch.object(herdr_native.os, 'readlink', side_effect=map(str, targets)) as read):
                    if isinstance(expected, str):
                        with self.assertRaisesRegex(LifecycleHold, f'^{expected}$'):
                            herdr_native._one_rollout(100, SESSIONS)
                    else:
                        self.assertEqual(herdr_native._one_rollout(100, SESSIONS), expected)
                scan.assert_called_once_with(directory)
                self.assertEqual(read.call_args_list, [call(str(directory / str(index)))
                                                       for index in range(len(targets))])
