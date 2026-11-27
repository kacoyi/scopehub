"""Exact-result controls for a single resolved Herdr-owned Codex rollout."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import herdr_returns
from scripts.herdr_returns import ReturnHold, probe_return


SESSION = '01a21690-93a0-79f3-b020-ab4c007f0531'
TURN = '01a21690-93ea-7912-a36b-ba7f5ac45d78'
SOURCE = '0a' * 20
ASSIGNMENT = 'ScopeHub task M0-162, role implement.\nPerform the bound isolated assignment.'
ANSWER = 'The original checks and their exact source are ready for handoff.'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def message(role, version, content, **payload):
    identifier = f'msg_{version}'
    return {
        'type': 'response_item',
        'payload': {'type': 'message', 'id': identifier, 'role': role, 'content': [content], **payload},
        'metadata': {'client_authored': False, 'retained_source': {
            'id': {'role': role, 'turn_id': TURN, 'message_id': identifier}, 'complete': role == 'assistant',
        }},
    }


class HerdrReturnProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.rollout = self.root / f'rollout-2026-11-25T22-35-03-{SESSION}.jsonl'
        self.terminal = {'session_id': SESSION, 'rollout_path': self.rollout}
        self.binding = {'task': 'M0-162', 'role': 'implement', 'checkout': str(self.root), 'source': SOURCE,
                        'prompt_sha256': digest(ASSIGNMENT.encode()), 'model': 'gpt-6.2-sol', 'effort': 'ultra'}

    def events(self, answer=ANSWER):
        return [
            {'type': 'session_meta', 'payload': {'id': SESSION, 'session_id': SESSION,
             'source': 'cli', 'originator': 'codex-tui', 'thread_source': 'user', 'cwd': str(self.root),
             'git': {'commit_hash': SOURCE}}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': TURN, 'root_turn_id': TURN,
             'turn_attribution': {'turn_id': TURN, 'root_turn_id': TURN, 'turn_trigger': 'user',
                                  'parent_turn_id': None, 'initiating_agent_path': None}}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Injected instructions are not the initial user assignment.'}]}},
            {'type': 'turn_context', 'payload': {'turn_id': TURN, 'root_turn_id': TURN, 'cwd': str(self.root),
             'model': self.binding['model'], 'effort': self.binding['effort']}},
            message('user', 'assignment', {'type': 'input_text', 'text': ASSIGNMENT}),
            message('user', 'steering', {'type': 'input_text', 'text': 'Use the bound checkout.'}),
            message('assistant', 'result', {'type': 'output_text', 'text': answer},
                    phase='final_answer', status='completed'),
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': TURN, 'root_turn_id': TURN,
             'last_agent_message': answer}},
        ]

    def write(self, events, ending=b'\n'):
        lines = []
        for index, event in enumerate(events):
            record = {'ordinal': index, 'timestamp': f'2026-11-26T06:42:{index:02d}.002Z', **event}
            lines.append(json.dumps(record).encode('utf-8') + ending)
        self.rollout.write_bytes(b''.join(lines))
        return lines

    def probe(self):
        return probe_return(self.binding, self.terminal)

    def test_accepts_exact_original_without_task_token_after_same_turn_steering(self):
        hashes = []
        for ending in (b'\n', b'\r\n'):
            with self.subTest(ending=ending):
                originals = self.write(self.events(), ending)
                proof = self.probe()

                self.assertNotIn(b'M0-162', proof.original)
                self.assertEqual(proof.original, originals[6])
                self.assertEqual(proof.completion, originals[7])
                self.assertEqual(proof.metadata, {
                    'original_sha256': digest(originals[6]), 'completion_sha256': digest(originals[7]),
                    'completed_at': '2026-11-26T06:42:07.002Z', 'response_timestamp': '2026-11-26T06:42:06.002Z',
                    'message_id': 'msg_result', 'turn_id': TURN, 'session_id': SESSION,
                    'rollout_path': str(self.rollout),
                })
                self.assertEqual(self.rollout.read_bytes(), b''.join(originals))
                hashes.append((proof.metadata['original_sha256'], proof.metadata['completion_sha256']))
        if len(hashes) == 2:
            self.assertNotEqual(hashes[0][0], hashes[1][0])
            self.assertNotEqual(hashes[0][1], hashes[1][1])

    def test_windows_path_creation_time_and_descriptor_change_time_are_distinct(self):
        originals = self.write(self.events())
        status = self.rollout.lstat()
        common = {field: getattr(status, field) for field in ('st_mode', 'st_uid', 'st_dev', 'st_ino',
                                                               'st_size', 'st_mtime_ns')}
        created = status.st_ctime_ns - 2_000_000_000
        # Model CPython 3.12 Windows, which exposes creation time in lstat().st_ctime_ns.
        pathname = SimpleNamespace(**common, st_birthtime_ns=created, st_ctime_ns=created)
        descriptor = SimpleNamespace(**common, st_birthtime_ns=created, st_ctime_ns=status.st_ctime_ns)
        changed = SimpleNamespace(**common, st_birthtime_ns=created, st_ctime_ns=status.st_ctime_ns + 2_000_000_000)

        with (patch.object(herdr_returns, 'sys', SimpleNamespace(platform='win32'), create=True),
              patch.object(herdr_returns.os, 'fstat', side_effect=(descriptor, descriptor)) as fstat,
              patch.object(Path, 'lstat', return_value=pathname) as lstat):
            proof = self.probe()
            self.assertEqual(proof.original, originals[6])
            self.assertEqual(proof.completion, originals[7])

            fstat.side_effect = (descriptor, changed)
            with self.assertRaisesRegex(ReturnHold, '^original_changed$'):
                self.probe()

            for field in ('st_ino', 'st_birthtime_ns'):
                with self.subTest(changed_path_field=field):
                    fstat.side_effect = (descriptor, descriptor)
                    lstat.return_value = SimpleNamespace(**{**vars(pathname), field: getattr(pathname, field) + 1})
                    with self.assertRaisesRegex(ReturnHold, '^original_changed$'):
                        self.probe()
        self.assertEqual(self.rollout.read_bytes(), b''.join(originals))

    def test_one_shot_requires_its_exact_exec_origin_and_retains_both_events(self):
        self.binding['mode'] = 'one-shot'
        events = self.events()
        del events[5]
        events[0]['payload'].update(source='exec', originator='codex_exec')
        events[1]['payload']['turn_attribution']['turn_trigger'] = 'exec'
        lines = self.write(events)

        proof = self.probe()

        self.assertEqual(proof.original, lines[-2])
        self.assertEqual(proof.completion, lines[-1])
        self.assertEqual(proof.metadata['turn_id'], TURN)
        self.assertEqual(proof.metadata['completion_sha256'], digest(lines[-1]))
        for path, value, reason in (
            ((0, 'payload', 'source'), 'cli', 'session_mismatch'),
            ((0, 'payload', 'originator'), 'codex-tui', 'session_mismatch'),
            ((0, 'payload', 'git', 'commit_hash'), 'b2' * 20, 'session_mismatch'),
            ((1, 'payload', 'turn_attribution', 'turn_trigger'), 'user', 'turn_changed'),
            ((-1, 'payload', 'turn_id'), 'unrelated-turn', 'turn_changed'),
        ):
            with self.subTest(path=path):
                changed = deepcopy(events)
                field = changed
                for key in path[:-1]:
                    field = field[key]
                field[path[-1]] = value
                self.write(changed)
                with self.assertRaisesRegex(ReturnHold, f'^{reason}$'):
                    self.probe()
        self.write(events)
        self.binding.pop('mode')
        with self.assertRaisesRegex(ReturnHold, '^session_mismatch$'):
            self.probe()

    def test_rejects_missing_incomplete_ambiguous_mismatched_or_interrupted_originals(self):
        with self.assertRaisesRegex(ReturnHold, '^original_missing$'):
            self.probe()
        events = self.events()
        interrupted = {'type': 'event_msg', 'payload': {'type': 'turn_aborted', 'turn_id': TURN}}
        changed_prompt = message('user', 'wrong', {'type': 'input_text', 'text': 'Different assignment.'})
        incomplete_final = {**events[6], 'payload': {**events[6]['payload'], 'status': 'incomplete'}}
        foreign_final = deepcopy(events[6])
        foreign_final['metadata']['retained_source']['id']['turn_id'] = 'different-turn'
        different_completion = {**events[7], 'payload': {**events[7]['payload'], 'last_agent_message': 'Wrong.'}}
        for scenario, reason in (
            (events[:-1], 'turn_incomplete'),
            (events[:6] + events[7:], 'original_missing'),
            (events[:7] + [events[6]] + events[7:], 'original_ambiguous'),
            (events[:-1] + [different_completion], 'original_mismatch'),
            (events[:4] + [changed_prompt] + events[4:], 'assignment_mismatch'),
            (events[:6] + [incomplete_final] + events[7:], 'original_invalid'),
            (events[:6] + [foreign_final] + events[7:], 'original_invalid'),
            (events[:6] + [interrupted] + events[6:], 'turn_interrupted'),
            (events + [events[1]], 'turn_changed'),
            (events + [events[5]], 'turn_changed'),
        ):
            with self.subTest(reason=reason):
                self.write(scenario)
                with self.assertRaisesRegex(ReturnHold, f'^{reason}$'):
                    self.probe()

        encoded = self.write(events)
        self.rollout.write_bytes(b''.join(encoded)[:-2])
        with self.assertRaisesRegex(ReturnHold, '^original_incomplete$'):
            self.probe()

    def test_rejects_foreign_session_checkout_or_source(self):
        self.write(self.events())
        wrong_session = {**self.terminal, 'session_id': '01a21690-93a0-79f3-b020-ab4c007f0532'}
        for binding, terminal in (
            (self.binding, wrong_session),
            ({**self.binding, 'checkout': str(self.root / 'different-checkout')}, self.terminal),
            ({**self.binding, 'source': 'bb' * 20}, self.terminal),
        ):
            with self.subTest(binding=binding, session=terminal['session_id']):
                with self.assertRaisesRegex(ReturnHold, '^session_mismatch$'):
                    probe_return(binding, terminal)

    def test_review_requires_its_exact_subject_and_keeps_the_raw_intake_original(self):
        self.binding.update(role='review', model='gpt-6.2-astra', effort='medium')
        wrong_source = 'bb' * 20
        for ending in (b'\n', b'\r\n'):
            for heading, verdict in ((f'APPROVE M0-162 {SOURCE}', 'approved'),
                                     (f'CHANGES_REQUESTED M0-162 {SOURCE}', 'changes_requested')):
                with self.subTest(ending=ending, heading=heading):
                    lines = self.write(self.events(f'{heading}\n\nAn open lane retains its original.'), ending)
                    proof = self.probe()
                    self.assertEqual(proof.original, lines[6])
                    self.assertEqual(proof.completion, lines[7])
                    self.assertEqual(proof.metadata['review_verdict'], verdict)

            for heading in (f'APPROVE M0-163 {SOURCE}', f'APPROVE M0-162 {wrong_source}',
                            f'APPROVE M0-162 {SOURCE[:12]}', f'APPROVE {SOURCE}'):
                with self.subTest(ending=ending, heading=heading):
                    self.write(self.events(f'{heading}\n\nThe response mentions M0-162 and {SOURCE}.'), ending)
                    with self.assertRaisesRegex(ReturnHold, '^review_verdict_mismatch$'):
                        self.probe()
