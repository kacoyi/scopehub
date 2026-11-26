"""Exercise the Herdr native boundary without opening a process or terminal."""

from contextlib import chdir, nullcontext, redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from scripts import herdr, herdr_lifecycle, herdr_native
from scripts.herdr_lifecycle import LifecycleHold


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
SOURCE = 'e365cb825bfb27d10104326340e5f4fc2a0aa129'
TURN = '01a21690-93ea-7912-a36b-ba7f5ac45d78'
ANSWER = 'The exact original is ready for handoff.'


class FakeCore:
    """Model native state transitions and record which turn a mutation would affect."""

    def __init__(self):
        self.terminals = {PARENT: {'address': PARENT, 'state': 'live', 'name': 'coordinator'},
                          't19': {'address': 't19', 'state': 'live', 'name': 'unrelated'}}
        self.mutations = []
        self.advance_on_read = None
        self.address_reads = 0

    def later_turn(self, state):
        self.terminals[ADDRESS].update(turn=2, state=state, status={'state': 'working'})

    def call(self, method, **params):
        if method == 'terminal.get':
            address = params['terminal']
            row = self.terminals.get(address)
            if row is None:
                row = next((row for row in self.terminals.values() if row['name'] == address), None)
            snapshot = deepcopy(row)
            if address == ADDRESS:
                self.address_reads += 1
                if self.address_reads == self.advance_on_read:
                    self.later_turn('live')
            return snapshot
        if method == 'events.subscribe' and 'children_of' in params:
            return {'terminals': []}
        if method in ('terminal.kill', 'terminal.rm'):
            address = params['terminal']
            self.mutations.append((method, address, self.terminals[address]['turn']))
            if method == 'terminal.kill':
                self.terminals[address]['state'] = 'finished'
            else:
                del self.terminals[address]
            return {}
        raise AssertionError((method, params))


@unittest.skipUnless(sys.platform == 'linux', 'Local Herdr lifecycle requires Linux.')
class HerdrCollectEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'state'
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()
        (self.root / '.git').mkdir()
        (self.root / '.git/HEAD').write_text(SOURCE + '\n', encoding='utf-8')
        self.fake = FakeCore()
        self.rollouts = {}
        self.enterContext(chdir(self.root))
        self.enterContext(patch.object(herdr_native, 'connect', side_effect=self.open_core))
        self.enterContext(patch.object(herdr_native, '_one_rollout', side_effect=self.discover_rollout))

    def open_core(self, path, expected=None):
        self.assertEqual(expected, CORE_ID)
        return nullcontext((self.fake, CORE_ID))

    def discover_rollout(self, pid, sessions):
        self.assertEqual(sessions, self.sessions)
        return self.rollouts[pid]

    def register(self, assignment, *, address=ADDRESS, session=SESSION, source=SOURCE,
                 status='idle', complete=True, one_shot=False):
        name = f'm0-162-implement-{assignment}'
        labels = {'task': 'M0-162', 'role': 'implement', 'scopehub.assignment': assignment}
        binding = {'id': assignment, 'task': 'M0-162', 'role': 'implement', 'checkout': str(self.root),
                   'source': source, 'prompt_sha256': sha256(PROMPT.encode()).hexdigest(),
                   'model': 'gpt-6.2-sol', 'effort': 'ultra', 'tier': 'priority', 'sessions': str(self.sessions),
                   'labels': labels, 'codex_argv': ARGV[:-1], 'name': name, 'core_id': CORE_ID, 'parent': PARENT}
        pid = 100 + len(self.rollouts)
        if one_shot:
            labels['herdr.space'] = 'space-a'
            binding.update(mode='one-shot', space='space-a', codex_argv=['codex', 'exec', '--json', *ARGV[2:-1]],
                           launch_argv=[sys.executable, '-B', '/scopehub/scripts/herdr.py', 'run-owned', '--'])
        argv = [*binding.get('launch_argv', binding['codex_argv']), PROMPT]
        self.fake.terminals[address] = {'address': address, 'name': name, 'labels': labels, 'parent': PARENT,
                                        'cwd': str(self.root), 'argv': argv, 'created_ms': pid,
                                        'state': 'live', 'turn': 1, 'reported': True, 'status': {'state': status},
                                        'status_ms': pid + 1, 'pid': pid,
                                        'foreground': {'pid': pid, 'argv': ['/codex/bin/codex', *ARGV[1:]],
                                                       'cwd': str(self.root)}}
        rollout = self.sessions / f'rollout-2026-11-25T22-35-03-{session}.jsonl'
        self.rollouts[pid] = str(rollout), session

        def message(role, identifier, item, **payload):
            return {'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'id': identifier,
                    'content': [item], **payload}, 'metadata': {'client_authored': False, 'retained_source': {
                        'id': {'role': role, 'turn_id': TURN, 'message_id': identifier},
                        'complete': role == 'assistant'}}}

        events = [
            {'type': 'session_meta', 'payload': {'id': session, 'session_id': session, 'source': 'cli',
             'originator': 'codex-tui', 'thread_source': 'user', 'cwd': str(self.root),
             'git': {'commit_hash': SOURCE}}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': TURN, 'root_turn_id': TURN,
             'turn_attribution': {'turn_id': TURN, 'root_turn_id': TURN, 'turn_trigger': 'user'}}},
            {'type': 'turn_context', 'payload': {'turn_id': TURN, 'root_turn_id': TURN, 'cwd': str(self.root),
             'model': binding['model'], 'effort': binding['effort']}},
            message('user', 'msg_prompt', {'type': 'input_text', 'text': PROMPT}),
            message('assistant', 'msg_result', {'type': 'output_text', 'text': ANSWER},
                    phase='final_answer', status='completed'),
        ]
        if one_shot:
            events[0]['payload'].update(source='exec', originator='codex_exec')
            events[1]['payload']['turn_attribution']['turn_trigger'] = 'exec'
        if complete:
            events.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': TURN,
                           'root_turn_id': TURN, 'last_agent_message': ANSWER}})
        lines = [(json.dumps({'ordinal': index, 'timestamp': f'2026-11-26T06:42:{index:02d}.002Z', **event})
                  + '\n').encode() for index, event in enumerate(events)]
        rollout.write_bytes(b''.join(lines))
        lifecycle, _ = herdr.collector(self.state, PARENT)
        lifecycle.register(binding)
        if one_shot:
            lifecycle.save_execution(binding, {
                'address': address, 'created_ms': pid, 'pid': pid, 'session_id': session,
                'rollout_path': str(rollout), 'tab': {'id': 'tab-a', 'pane': 'pane-a'},
            })
        return lines[-2:] if complete else None

    def lane(self, assignment, *, section='pending'):
        return self.state / PARENT / section / assignment

    def collect(self, assignment=None, *, watch=False):
        argv = ['herdr.py', 'collect', '--parent', PARENT, '--state-dir', str(self.state)]
        if assignment is not None:
            argv += ['--assignment-id', assignment]
        if watch:
            argv.append('--watch')
        output = StringIO()
        with patch.object(sys, 'argv', argv), redirect_stdout(output):
            self.assertEqual(herdr.main(), 0)
        rows = json.loads(output.getvalue())['assignments']
        return rows[0] if assignment is not None else {row['assignment']: row for row in rows}

    def assert_archive(self, result, evidence, *, legacy=False):
        original, completion = evidence
        original_path = Path(result['original'])
        self.assertEqual(original_path.name, 'original.jsonl')
        receipt = json.loads(Path(result['receipt']).read_bytes())
        self.assertEqual(receipt['binding']['source'], SOURCE)
        self.assertEqual(receipt['archive']['proof']['turn_id'], TURN)
        self.assertEqual(receipt['archive']['proof']['completion_sha256'], sha256(completion).hexdigest())
        self.assertEqual(result['original_sha256'], sha256(original).hexdigest())
        self.assertEqual(original_path.read_bytes(), original)
        if legacy:
            self.assertNotIn('completion', result)
        else:
            completion_path = Path(result['completion'])
            self.assertEqual(completion_path, original_path.with_name('completion.jsonl'))
            self.assertEqual(completion_path.read_bytes(), completion)
        self.assertNotIn(b'M0-162', original)

    def test_reusable_assignment_cannot_kill_a_later_turn_after_the_last_state_read(self):
        evidence = self.register('reusable')
        # The third address-based read was the legacy collector's final snapshot before kill.
        self.fake.advance_on_read = 3

        result = self.collect('reusable')

        self.assertEqual(self.fake.mutations, [])
        self.assertEqual(result['status'], 'archived')
        self.assertIn(result['reason'], {'reusable_terminal', 'terminal_turn_changed'})
        self.assert_archive(result, evidence)

        # Repeat at the first address read of a fresh inspection of the durable archive.
        self.fake.advance_on_read = self.fake.address_reads + 1
        self.assert_archive(self.collect('reusable'), evidence)
        self.assertEqual(self.fake.terminals[ADDRESS]['turn'], 2)
        changed = self.collect('reusable')
        self.assertEqual((changed['status'], changed['reason']), ('archived', 'terminal_turn_changed'))
        self.assertEqual(self.fake.mutations, [])
        self.assert_archive(changed, evidence)

    def test_interrupted_collection_never_removes_a_different_finished_turn(self):
        evidence = self.register('interrupted')
        write_record = herdr_lifecycle._write_record

        def crash_after_legacy_retirement_marker(lane, record):
            if record['phase'] == 'archived':
                return_path = lane / 'return'
                if return_path.exists():
                    # Store the Version 1 on-disk layout used before raw completion retention.
                    (return_path / 'original.jsonl').rename(lane / 'original.jsonl')
                    (return_path / 'completion.jsonl').unlink()
                    (return_path / 'archive.json').unlink()
                    return_path.rmdir()
                archive = {key: value for key, value in record['archive'].items() if key != 'binding_sha256'}
                write_record(lane, {**record, 'version': 1, 'phase': 'retiring', 'archive': archive})
                raise KeyboardInterrupt
            write_record(lane, record)

        with (patch.object(herdr_lifecycle, '_write_record', side_effect=crash_after_legacy_retirement_marker),
              self.assertRaises(KeyboardInterrupt)):
            self.collect('interrupted')
        self.fake.later_turn('finished')

        result = self.collect('interrupted')

        self.assertEqual(self.fake.mutations, [])
        self.assertEqual((result['status'], result['reason']), ('archived', 'terminal_turn_changed'))
        self.assertEqual(self.fake.terminals[ADDRESS]['turn'], 2)
        self.assertEqual(self.fake.terminals[ADDRESS]['state'], 'finished')
        self.assert_archive(result, evidence, legacy=True)
        self.assertEqual(json.loads(Path(result['receipt']).read_bytes())['version'], 2)

    def test_interrupted_atomic_archive_keeps_both_events_after_the_live_rollout_is_gone(self):
        evidence = self.register('atomic')
        with (patch.object(herdr_lifecycle, '_write_record', side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            self.collect('atomic')
        self.fake.later_turn('finished')
        for path, _ in self.rollouts.values():
            Path(path).unlink()

        result = self.collect('atomic', watch=True)

        self.assertEqual((result['status'], result['reason']), ('archived', 'terminal_turn_changed'))
        self.assertEqual(self.fake.mutations, [])
        self.assert_archive(result, evidence)

    def test_last_legitimate_assignment_archives_independently_of_closure_and_holds(self):
        self.register('a-wrong-source', address='t13', source='fe' * 20)
        self.register('m-incomplete', address='t14', session=OTHER_SESSION, complete=False)
        evidence = self.register('z-eligible', session='01a21690-93a0-79f3-b020-ab4c007f0533')

        results = self.collect()

        self.assertEqual(self.fake.mutations, [])
        self.assertEqual(results['a-wrong-source']['reason'], 'session_mismatch')
        self.assertEqual(results['m-incomplete']['reason'], 'turn_incomplete')
        self.assertEqual((results['z-eligible']['status'], results['z-eligible']['reason']),
                         ('archived', 'reusable_terminal'))
        for assignment in ('a-wrong-source', 'm-incomplete'):
            self.assertFalse((self.lane(assignment) / 'return').exists())
        self.assertEqual(set(self.fake.terminals), {PARENT, ADDRESS, 't13', 't14', 't19'})
        self.assert_archive(self.collect('z-eligible'), evidence)

    def test_corrupt_execution_does_not_stop_independent_collection(self):
        self.register('a-corrupt-execution', address='t16', one_shot=True)
        evidence = self.register('z-eligible', session=OTHER_SESSION)
        sidecar = self.lane('a-corrupt-execution') / 'execution.json'
        corrupt = b'{"nested":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}\n'
        sidecar.write_bytes(corrupt)

        results = self.collect()

        held = {'assignment': 'a-corrupt-execution', 'status': 'held', 'reason': 'assignment_execution_changed'}
        self.assertEqual(results['a-corrupt-execution'], held)
        self.assertEqual(self.collect('a-corrupt-execution'), held)
        self.assertEqual(sidecar.read_bytes(), corrupt)
        self.assertFalse((sidecar.parent / 'return').exists())
        self.assertEqual(results['z-eligible']['status'], 'archived')
        self.assert_archive(self.collect('z-eligible'), evidence)
        self.assertEqual(set(self.fake.terminals), {PARENT, ADDRESS, 't16', 't19'})
        self.assertEqual(self.fake.mutations, [])

    def test_one_shot_closure_requires_its_own_successful_exit_and_exact_tab_absence(self):
        evidence = self.register('one-shot', one_shot=True)
        terminal = self.fake.terminals[ADDRESS]
        space = {'id': 'space-a', 'machine': None}
        own_pane = {'terminal': ADDRESS, 'name': terminal['name'], 'pane': 'pane-a'}
        owned = {'id': 'tab-a', 'terminals': [own_pane]}
        sibling = {'id': 'tab-b', 'terminals': [{'terminal': 't19', 'name': 'unrelated', 'pane': 'pane-b'}]}
        desktop = self.enterContext(patch.object(herdr_native.subprocess, 'run'))

        def snapshot(*spaces):
            desktop.return_value = SimpleNamespace(stdout=json.dumps({'spaces': spaces}).encode())

        snapshot({**space, 'tabs': [sibling]})

        result = self.collect('one-shot')
        self.assertEqual((result['status'], result['reason']), ('archived', 'one_shot_awaiting_exit'))
        self.assert_archive(result, evidence)
        desktop.assert_not_called()

        pid = terminal['pid']
        terminal['pid'] += 1
        self.assertEqual(self.collect('one-shot')['reason'], 'terminal_program_changed')
        terminal.update(state='finished', reason='exited', exit_code=0)
        self.assertEqual(self.collect('one-shot')['reason'], 'terminal_program_changed')
        terminal['pid'] = pid
        for code in (1, False):
            with self.subTest(exit_code=code):
                terminal['exit_code'] = code
                self.assertEqual(self.collect('one-shot')['reason'], 'one_shot_exit_unverified')
        terminal['exit_code'] = 0
        terminal['created_ms'] += 1
        self.assertEqual(self.collect('one-shot')['reason'], 'assignment_execution_changed')
        desktop.assert_not_called()

        terminal['created_ms'] -= 1
        snapshot({**space, 'tabs': [owned, sibling]})
        self.assertEqual(self.collect('one-shot')['reason'], 'one_shot_exited')

        snapshot({**space, 'tabs': [{'id': 'tab-new', 'terminals': [own_pane]}, sibling]})
        self.assertEqual(self.collect('one-shot')['reason'], 'one_shot_exited')

        snapshot({**space, 'id': 'another-space', 'tabs': [sibling]})
        self.assertEqual(self.collect('one-shot')['reason'], 'one_shot_exited')

        snapshot({**space, 'tabs': [sibling]})
        closed = self.collect('one-shot')
        self.assertEqual((closed['status'], closed['reason']), ('archived', 'one_shot_tab_closed'))
        self.assert_archive(closed, evidence)
        self.assertEqual(desktop.call_args_list, [
            call(['herdr-app', 'ls'], cwd=str(self.root), capture_output=True, timeout=5, check=True)
        ] * 4)
        self.assertEqual(set(self.fake.terminals), {PARENT, ADDRESS, 't19'})
        self.assertEqual(self.fake.mutations, [])

    def test_one_shot_closure_requires_a_complete_desktop_snapshot(self):
        evidence = self.register('one-shot', one_shot=True)
        terminal = self.fake.terminals[ADDRESS]
        terminal.update(state='finished', reason='exited', exit_code=0)
        space = {'id': 'space-a', 'machine': None, 'tabs': []}
        other_terminal = {'terminal': 't19', 'pane': 'pane-b'}
        sibling = {'id': 'tab-b', 'terminals': [other_terminal]}
        other_space = {**space, 'id': 'space-b', 'tabs': [sibling]}
        desktop = self.enterContext(patch.object(herdr_native.subprocess, 'run'))

        cases = (
            ('complete empty space', [space, other_space], 'one_shot_tab_closed'),
            ('complete sibling tab', [{**space, 'tabs': [sibling]}], 'one_shot_tab_closed'),
            ('space absent', [other_space], 'one_shot_exited'),
            ('pane moved', [space, {**other_space, 'tabs': [
                {**sibling, 'terminals': [{**other_terminal, 'pane': 'pane-a'}]}
            ]}], 'one_shot_exited'),
            ('terminal moved', [space, {**other_space, 'tabs': [
                {**sibling, 'terminals': [{**other_terminal, 'terminal': ADDRESS}]}
            ]}], 'one_shot_exited'),
            ('tab moved', [space, {**other_space, 'tabs': [{**sibling, 'id': 'tab-a'}]}], 'one_shot_exited'),
            ('tab and pane identities missing', [{**space, 'tabs': [sibling, {'terminals': [{}]}]}],
             'desktop_snapshot_incomplete'),
            ('tab identity missing', [{**space, 'tabs': [sibling, {'terminals': [other_terminal]}]}],
             'desktop_snapshot_incomplete'),
            ('pane identity missing in another space', [space, {**other_space, 'tabs': [
                {**sibling, 'terminals': [{'terminal': 't19'}]}
            ]}], 'desktop_snapshot_incomplete'),
            ('terminal identity missing', [{**space, 'tabs': [{**sibling, 'terminals': [{'pane': 'pane-b'}]}]}],
             'desktop_snapshot_incomplete'),
            ('blank terminal identity', [{**space, 'tabs': [
                {**sibling, 'terminals': [{**other_terminal, 'terminal': ''}]}
            ]}], 'desktop_snapshot_incomplete'),
            ('tabs missing from space', [{'id': 'space-a', 'machine': None}], 'desktop_snapshot_incomplete'),
            ('terminals missing from tab', [{**space, 'tabs': [{'id': 'tab-b'}]}], 'desktop_snapshot_incomplete'),
        )
        for case, spaces, reason in cases:
            with self.subTest(case=case):
                desktop.return_value = SimpleNamespace(stdout=json.dumps({'spaces': spaces}).encode())
                result = self.collect('one-shot')
                self.assertEqual((result['status'], result['reason']), ('archived', reason))
                self.assert_archive(result, evidence)
        self.assertEqual(set(self.fake.terminals), {PARENT, ADDRESS, 't19'})
        self.assertEqual(self.fake.mutations, [])

    def test_one_shot_closure_requires_valid_stored_tab_and_pane_identities(self):
        evidence = self.register('one-shot', one_shot=True)
        self.fake.terminals[ADDRESS].update(state='finished', reason='exited', exit_code=0)
        execution = self.lane('one-shot') / 'execution.json'
        saved = json.loads(execution.read_bytes())
        saved['tab'] = {'id': '', 'pane': None}
        execution.write_text(json.dumps(saved), encoding='utf-8')
        desktop = self.enterContext(patch.object(herdr_native.subprocess, 'run'))
        desktop.return_value = SimpleNamespace(stdout=json.dumps({'spaces': [{
            'id': 'space-a', 'machine': None, 'tabs': [{
                'id': 'tab-b', 'terminals': [{'terminal': 't19', 'pane': 'pane-b'}],
            }],
        }]}).encode())

        result = self.collect('one-shot')

        self.assertEqual((result['status'], result['reason']), ('archived', 'terminal_tab_changed'))
        self.assert_archive(result, evidence)
        desktop.assert_not_called()
        self.assertEqual(self.fake.mutations, [])


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
        self.native = herdr_native.NativeLifecycle(self.socket)
        self.core = Mock()
        self.connect = self.enterContext(patch.object(herdr_native, 'connect', side_effect=self.open_core))
        self.rollout = self.enterContext(patch.object(herdr_native, '_one_rollout',
                                                     return_value=(str(ROLLOUT), SESSION)))

    def open_core(self, path, expected):
        self.assertEqual(path, self.socket)
        self.assertEqual(expected, CORE_ID)
        return nullcontext((self.core, CORE_ID))

    def test_exact_completed_launch_queries_only_its_children_and_foreground_process(self):
        self.row['labels']['where'] = 'native display location'
        self.core.call.side_effect = [self.row, {'terminals': [{'address': 't13', 'state': 'finished'}]}]

        observed = self.native.observe(self.binding, retiring=None)

        self.assertEqual(observed, self.terminal)
        self.connect.assert_called_once_with(self.socket, CORE_ID)
        self.rollout.assert_called_once_with(100, SESSIONS)
        self.assertEqual(self.core.call.call_args_list, [call('terminal.get', terminal=NAME),
                                                         call('events.subscribe', children_of=ADDRESS)])

    def test_ordinary_shell_wrong_owner_prompt_tier_or_sibling_never_reaches_private_probe(self):
        for label, changed in (
            ('shell', {'argv': ['bash', '-l']}),
            ('owner', {'parent': 't19'}),
            ('prompt', {'argv': [*ARGV[:-1], 'Unregistered prompt.']}),
            ('tier', {'argv': [*ARGV[:7], 'service_tier="default"', *ARGV[8:]]}),
            ('sibling', {'labels': {**self.binding['labels'], 'scopehub.assignment': 'assignment-b'}}),
            ('shared tab', {'labels': {**self.binding['labels'], 'herdr.tab': 'unrelated-tab'}}),
        ):
            with self.subTest(label=label):
                self.core.reset_mock()
                self.core.call.return_value = {**self.row, **changed}
                with self.assertRaisesRegex(LifecycleHold, '^foreign_terminal$'):
                    self.native.observe(self.binding, retiring=None)
                self.core.call.assert_called_once_with('terminal.get', terminal=NAME)
        self.rollout.assert_not_called()

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

    def test_observation_refuses_replaced_address_without_archive_probe(self):
        self.core.call.return_value = {**self.row, 'created_ms': 11}
        with self.assertRaisesRegex(LifecycleHold, '^foreign_terminal$'):
            self.native.observe(self.binding, retiring=self.terminal)
        self.core.call.assert_called_once_with('terminal.get', terminal=ADDRESS)
        self.rollout.assert_not_called()

    def test_finished_reusable_terminal_requires_positive_turn_identity(self):
        for changed, reason in (
            ({'turn': 2}, 'terminal_turn_changed'),
            ({'turn': None, 'reported': False}, 'terminal_finished_turn_unverified'),
        ):
            with self.subTest(reason=reason):
                self.core.reset_mock()
                self.core.call.return_value = {**self.row, 'state': 'finished', **changed}
                with self.assertRaisesRegex(LifecycleHold, f'^{reason}$'):
                    self.native.observe(self.binding, retiring=self.terminal)
                self.core.call.assert_called_once_with('terminal.get', terminal=ADDRESS)
        self.rollout.assert_not_called()


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
