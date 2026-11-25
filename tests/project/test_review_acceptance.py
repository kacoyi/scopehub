"""Exercise the independent decision at the actual project acceptance boundaries."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
import json
import subprocess
from unittest.mock import patch

from scripts import project, review_acceptance
from scripts.integration import delivery_scope
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


TASK = 'M0-001'
OUTCOME = 'The independent reader observed the original value after reopening the selected fixture.'


class TaskReviewTests(ProjectFixture):
    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Review fixture')
        return self.git('rev-parse', 'HEAD')

    def command(self, *args, error=None):
        output = StringIO()
        with patch.object(project, 'ROOT', self.root), patch('sys.argv', ['project.py', *args]), redirect_stdout(output):
            if error is None:
                project.main()
            else:
                with self.assertRaises(SystemExit) as failure:
                    project.main()
                self.assertEqual(1, failure.exception.code)
        record = json.loads(output.getvalue())
        if error:
            self.assertIn(error, record['error'])
        return record

    def open_review(self):
        (self.root / 'project/branch-protection.json').write_text('{"branch":"main"}', encoding='utf-8')
        baseline = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', baseline)
        ledger = self.ledger()
        plan = task_by_id(ledger, TASK)['plan']
        plan['independent_review'] = True
        plan['evolution'] = {
            'kind': 'refactor', 'preserve': ['The existing reader retains its last durable value.'],
            'change': ['Move the source selection to the component that owns it.'],
            'data_plan': 'Resume the same saved data.', 'cleanup': 'Retire the old reader when the unchanged caller passes.',
        }
        self.save_ledger(ledger)
        return baseline, self.commit()

    def review(self, source, verdict='APPROVE', *, observation=OUTCOME, task=TASK,
               supersedes=None, event=None, suffix='', error=None):
        trace = self.root / '.reports/independent.txt'
        trace.parent.mkdir(exist_ok=True)
        if event:
            identity, time = event
            content = f'{verdict} {task} {source}\n\n{observation}'
            body = json.dumps({'type': 'response_item', 'timestamp': time, 'payload': {
                'type': 'message', 'phase': 'final_answer', 'role': 'assistant', 'status': 'completed',
                'id': identity, 'content': [{'type': 'output_text', 'text': content}],
            }})
        else:
            body = (f'› Report APPROVE or CHANGES_REQUESTED for {task} at {source}.\n'
                    f'• Ran git show historical-output\n  └ APPROVE — quoted {source}\n'
                    f'• {verdict} — independent decision for {task} at\n'
                    f'  {source}.\n\n  {observation}\n  Worked for 1m • today\n')
        trace.write_text(body + suffix, encoding='utf-8')
        args = ['review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer', '--observation', observation]
        if supersedes:
            args.extend(['--supersedes', supersedes])
        return self.command(*args, error=error)

    def finish(self):
        path = self.report()
        receipt = self.command('record', TASK, '--report', str(path))
        self.finish_first(receipt)
        return receipt

    def test_green_harness_cannot_finish_without_the_latest_independent_approval(self):
        base, source = self.open_review()
        pending = self.command('context', TASK)['independent_review']
        self.assertEqual('pending', pending['status'])
        self.assertEqual('Retire the old reader when the unchanged caller passes.', pending['evolution']['cleanup'])
        self.finish()
        self.command('check', error='Independent review for M0-001 is pending')
        self.command('history', '--base', base, error='Independent review for M0-001 is pending')
        # A local commit above protected main cannot transform its own closure into accepted history.
        self.commit()
        self.command('check', error='Independent review for M0-001 is pending')
        with self.assertRaisesRegex(project.ProjectError, 'Independent review for M0-001 is pending'):
            delivery_scope(self.root, project.load_project(self.root), base)

        rejected = self.review(source, 'CHANGES_REQUESTED', event=('msg_rejected', '2026-11-25T13:00:00Z'))
        self.assertEqual('changes_requested', rejected['decision']['verdict'])
        self.command('check', error='changes_requested')
        self.review(source, observation='The same independent reader preserved the value through reopen and restart.',
                    supersedes=rejected['decision']['response_id'],
                    event=('msg_approved', '2026-11-25T13:15:00Z'))
        self.assertEqual('passed', self.command('check')['status'])
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.assertEqual([TASK], delivery_scope(self.root, project.load_project(self.root), base)['completed_task_ids'])

    def test_other_task_or_only_an_assignment_cannot_supply_a_decision(self):
        _, source = self.open_review()
        self.review(source, task='M0-002', error='identify this task')
        trace = self.root / '.reports/independent.txt'
        trace.write_text(f'› Report APPROVE or CHANGES_REQUESTED for {TASK} at {source}.\n'
                         f'• Ran rg approval\n  └ APPROVE — {TASK} at {source}.\n',
                         encoding='utf-8')
        self.command('review', TASK, '--source', source, '--transcript', str(trace),
                     '--reviewer', 'Synthetic independent reviewer', '--observation', OUTCOME,
                     error='final independent verdict')

    def test_receipt_administration_cannot_reuse_approval_for_changed_inputs_or_scope(self):
        base, source = self.open_review()
        self.review(source)
        self.finish()
        accepted = self.ledger()
        implementation = self.root / 'implementation.py'
        original = implementation.read_bytes()
        implementation.write_bytes(original + b'new_behavior = True\n')
        self.command('history', '--base', base, error='is stale')
        implementation.write_bytes(original)
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.command('context', TASK)
        changed = self.ledger()
        task = task_by_id(changed, TASK)
        task.update(status='planned', next_action='Verify the newly scoped migration.', evidence=[])
        task['plan']['evolution']['change'].append('Change a new state owner.')
        self.save_ledger(changed)
        self.assertEqual('stale', self.command('context', TASK)['independent_review']['status'])
        self.save_ledger(accepted)

    def test_disjoint_protected_merge_reuses_review_but_overlap_needs_a_new_decision(self):
        base, plan_source = self.open_review()
        implementation = self.root / 'implementation.py'
        implementation.write_bytes(b'value = 2\n')
        source = self.commit()
        self.review(source)
        self.git('checkout', '-qb', 'protected-fixture', base)
        (self.root / 'unrelated.py').write_bytes(b'protected = True\n')
        protected = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', protected)
        self.git('checkout', '--detach', source)
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'merge', '--no-edit', protected)
        self.finish()
        self.assertEqual('passed', self.command('history', '--base', protected)['status'])

        implementation.write_bytes(b'value = 3\n')
        self.command('history', '--base', protected, error='is stale')
        self.assertNotEqual(source, plan_source)

    def test_long_comparison_preserves_legacy_acceptance_observed_on_protected_history(self):
        base, _ = self.open_review()
        self.finish()
        already_accepted = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', already_accepted)
        (self.root / 'implementation.py').write_bytes(b'new_unrelated_work = True\n')
        self.commit()
        self.assertEqual('passed', self.command('history', '--base', base)['status'])

    def test_an_arbitrary_checkpoint_cannot_license_changed_material(self):
        _, source = self.open_review()
        self.review(source)
        (self.root / 'new_behavior.py').write_bytes(b'outside_review = True\n')
        unreviewed = self.commit()
        self.finish()
        self.command('history', '--base', unreviewed, error='is stale')

    def test_withdrawal_and_ordinary_receipts_keep_their_original_rules(self):
        base, _ = self.open_review()
        ledger = self.ledger()
        task_by_id(ledger, TASK).update(status='cancelled', closure_reason='Synthetic experiment withdrawn.', next_action=None)
        ledger['focus_task_id'] = 'M0-002'
        self.save_ledger(ledger)
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        plan = dict(task_by_id(ledger, TASK)['plan'], independent_review=False)
        task_by_id(ledger, 'M0-002')['plan'] = plan
        self.save_ledger(ledger)
        recorded = self.command('record', 'M0-002', '--report', str(self.report(task_id='M0-002')))
        task_by_id(ledger, 'M0-002').update(status='done', evidence=[recorded], next_action=None)
        ledger['focus_task_id'] = 'M0-003'
        self.save_ledger(ledger)
        self.assertEqual('passed', self.command('check')['status'])
        self.assertEqual('passed', self.command('history', '--base', base)['status'])

    def test_recorded_rejection_cannot_be_removed_or_replayed_over_a_new_verdict(self):
        base, source = self.open_review()
        old = self.review(source)
        self.review(source, 'CHANGES_REQUESTED', observation='The later independent reading found a discrepancy after reopen.',
                    supersedes=old['decision']['response_id'])
        copied = self.review(source, suffix='\n')  # Same rendered response despite changed trailing bytes.
        self.assertEqual(old['decision'], copied['decision'])
        self.assertEqual('changes_requested', self.command('context', TASK)['independent_review']['status'])
        self.commit()
        current = self.git('rev-parse', 'HEAD')
        path = self.root / old['path']
        record = json.loads(path.read_bytes())
        record['events'].pop()
        path.write_text(json.dumps(record), encoding='utf-8')
        self.command('history', '--base', current, error='review history changed')

    def test_first_authority_boundary_and_adjacent_routine_activation(self):
        ledger = self.ledger()
        plan = task_by_id(ledger, TASK)['plan']
        plan['change_types'] = ['tooling', 'permissions']
        delivery = project.read_json(self.root / 'project/delivery.json')
        delivery['change_types'].append({'id': 'permissions', 'triggers': ['Changes to the authorized recipient.'],
                                         'checks': ['project-state']})
        (self.root / 'project/delivery.json').write_bytes(project.json_bytes(delivery))
        self.save_ledger(ledger)
        (self.root / 'project/branch-protection.json').write_text('{"branch":"main"}', encoding='utf-8')
        prior = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', prior)
        focus = task_by_id(ledger, TASK)
        focus['status'] = 'active'
        self.save_ledger(ledger)
        for phase in ('implement', 'verify', 'handoff'):
            view = self.command('context', TASK, '--phase', phase)['independent_review']
            self.assertEqual('pending', view['status'])
            self.assertIn('coordinator', view['reviewer'])
        self.assertNotIn('independent_review', self.command('context', 'M0-002'))
        self.finish()
        self.command('history', '--base', prior, error='Independent review for M0-001 is pending')

    def test_transcript_mentions_and_later_unresolved_reply_cannot_approve(self):
        _, source = self.open_review()
        self.review(source, task='M0-002', observation=f'{TASK} remains planned; no acceptance was requested.',
                    error='identify this task')
        self.review(source, suffix='\n› Reopen the result.\n\n• The original outcome now disagrees on reopen.\n',
                    error='The final independent verdict')

    def test_stale_predecessor_and_reordered_event_cannot_replace_rejection(self):
        _, source = self.open_review()
        first = self.review(source, event=('msg_first', '2026-11-25T11:00:00Z'))
        rejected = self.review(source, 'CHANGES_REQUESTED', event=('msg_second', '2026-11-25T13:00:00Z'),
                               supersedes=first['decision']['response_id'])
        self.review(source, event=('msg_concurrent', '2026-11-25T13:10:00Z'),
                    supersedes=first['decision']['response_id'], error='must supersede')
        self.review(source, event=('msg_reordered', '2026-11-25T12:59:00Z'),
                    supersedes=rejected['decision']['response_id'], error='predates or equals')
        self.review(source, event=('msg_first', '2026-11-25T11:00:00Z'), suffix='\n\n')
        self.assertEqual('changes_requested', self.command('context', TASK)['independent_review']['status'])

    def test_public_projection_is_verified_and_available_in_each_focused_phase(self):
        base, source = self.open_review()
        result = self.review(source)
        for phase in ('implement', 'verify', 'handoff'):
            view = self.command('context', TASK, '--phase', phase)['independent_review']
            self.assertEqual(OUTCOME, view['observation'])
            self.assertEqual(source, view['reviewed_source'])
            self.assertEqual('Synthetic independent reviewer', view['reviewer'])
            self.assertEqual(result['path'], view['source']['path'])
        self.finish()
        file = self.root / result['path']
        record = json.loads(file.read_bytes())
        record['events'][0]['observation'] = 'Changed local projection with unchanged original SHA.'
        file.write_bytes(project.json_bytes(record))
        self.command('check', error='public evidence digest mismatch')
        self.command('history', '--base', base, error='public evidence digest mismatch')

    def test_missing_or_shallow_history_cannot_accept_its_own_completion(self):
        base, source = self.open_review()
        self.review(source)
        self.finish()
        head = self.commit()
        self.assertEqual('passed', self.command('check')['status'])
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.command('check', error='is unavailable')
        self.command('history', '--base', head, error='is unavailable')
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.git('update-ref', 'refs/remotes/origin/main', base)
        shallow = self.root / '.git/shallow'
        shallow.write_text(head + '\n', encoding='utf-8')
        self.command('check', error='is unavailable')
        snapshot = self.command('context', TASK)['independent_review']
        self.assertIn('Restore the local protected', snapshot['review_next_action'])
        shallow.unlink()
        self.assertEqual('passed', self.command('check')['status'])

    def test_local_closure_checkpoint_cannot_substitute_for_protected_acceptance(self):
        self.open_review()
        self.finish()
        closed = self.commit()
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        (self.root / 'separate_input.py').write_bytes(b'outcome = True\n')
        self.commit()
        self.command('history', '--base', closed, error='Independent review for M0-001 is pending')

    def test_consequential_context_teaches_importable_reply_without_burdening_routine_work(self):
        _, _ = self.open_review()
        for args in (('context', TASK), ('context', TASK, '--phase', 'verify'),
                     ('context', TASK, '--phase', 'verify', '--detail')):
            reply = self.command(*args)['review_reply']
            self.assertEqual([f'APPROVE {TASK} <full-clean-reviewed-commit>',
                              f'CHANGES_REQUESTED {TASK} <full-clean-reviewed-commit>'], reply['first_line'])
            self.assertIn('standalone factual English sentence', reply['observation'])
            self.assertIn('verbatim', reply['observation'])
            self.assertIn('Chinese', reply['owner_communication'])
        self.assertNotIn('review_reply', self.command('context', 'M0-002', '--phase', 'verify'))

    def test_post_verdict_observation_rejects_heading_atoms_and_accepts_short_facts(self):
        base, source = self.open_review()
        short = 'Reopen: BUSY.'
        trace = self.root / '.reports/response-body.txt'
        trace.parent.mkdir()
        trace.write_text(f'APPROVE {TASK} {source}\n\n{short}\n', encoding='utf-8')
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer')
        for invalid in (source, TASK, 'APPROVE', 'BUSY'):
            self.command(*args, '--observation', invalid, error='post-verdict body')
        actual = self.command(*args, '--observation', short)
        self.assertEqual(short, actual['decision']['observation'])
        self.finish()
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        path = self.root / actual['path']
        record = json.loads(path.read_bytes())
        entry = record['events'][0]
        entry['observation'] = source
        entry['evidence_sha256'] = review_acceptance.evidence_digest(TASK, entry)
        path.write_bytes(project.json_bytes(record))
        self.command('check', error='Unusable independent decision or observation')
        self.command('history', '--base', base, error='Unusable independent decision or observation')

    def test_new_request_persists_across_replays_and_waits_for_a_later_same_source_reply(self):
        base, source = self.open_review()
        old = self.review(source)
        self.finish()
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        scope = 'Independently repeat the exact reopen and verify the original persisted value.'
        args = ('review', TASK, '--source', source, '--request', scope,
                '--supersedes', old['decision']['response_id'])
        opened = self.command(*args)
        pending = self.command(*args)
        self.assertTrue(opened['recorded'])
        self.assertFalse(pending['recorded'])
        self.assertEqual(opened['request'], pending['request'])
        request = opened['request']
        self.assertEqual('request', opened['current_entry_kind'])
        view = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual('pending', view['status'])
        self.assertEqual(scope, view['pending_request']['scope'])
        self.assertIn(request['request_id'], view['coordination_command'])
        self.assertEqual('approved', view['last_verdict'])
        self.command('check', error='is pending')
        self.command('history', '--base', base, error='is pending')
        with self.assertRaisesRegex(project.ProjectError, 'is pending'):
            delivery_scope(self.root, project.load_project(self.root), base)
        self.review(source, suffix='\n', supersedes=request['request_id'])
        self.command('check', error='is pending')
        self.review(source, observation='The reproduced reopen retained the original durable value.',
                    supersedes=request['request_id'], error='later final assistant export')
        previous = datetime.fromisoformat(request['requested_at'])
        self.review(source, observation='The older independent replay retained a durable value.',
                    supersedes=request['request_id'],
                    event=('msg_too_early', (previous - timedelta(seconds=1)).isoformat()), error='predates')
        self.review(base, observation='Another independent replay retained that other source value.',
                    supersedes=request['request_id'],
                    event=('msg_other_source', (previous + timedelta(seconds=1)).isoformat()),
                    error='exact source')
        new = self.review(source, observation='The later independent replay retained the exact durable value.',
                          supersedes=request['request_id'],
                          event=('msg_later', (previous + timedelta(seconds=2)).isoformat()))
        self.assertEqual('decision', new['current_entry_kind'])
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.assertFalse(self.command(*args)['recorded'])
        self.assertEqual('approved', self.command('context', TASK)['independent_review']['status'])

    def test_data_effect_distinguishes_destructive_unclassified_and_additive_changes(self):
        delivery = project.read_json(self.root / 'project/delivery.json')
        delivery['change_types'].append({'id': 'persistence', 'triggers': ['Durable data effects.'],
                                         'checks': ['project-state']})
        (self.root / 'project/delivery.json').write_bytes(project.json_bytes(delivery))
        self.commit()
        ledger = self.ledger()
        task = task_by_id(ledger, TASK)
        task['plan']['change_types'] = ['persistence']
        evolution = {'kind': 'additive', 'preserve': ['Existing rows outside the selected identity.'],
                     'change': ['Exercise the declared storage outcome.'],
                     'data_plan': 'Use bounded fixture records.', 'cleanup': 'Clear the isolated test data.'}
        for effect, required in ((None, True), ('destructive', True), ('preserved', False), ('additive', False)):
            with self.subTest(effect=effect):
                task['plan']['evolution'] = dict(evolution, **({'data_effect': effect} if effect else {}))
                self.save_ledger(ledger)
                result = self.command('context', TASK)
                self.assertEqual(required, 'independent_review' in result)
        task['plan']['change_types'] = ['tooling']
        task['plan']['evolution']['data_effect'] = 'destructive'
        self.save_ledger(ledger)
        self.assertEqual('pending', self.command('context', TASK)['independent_review']['status'])
