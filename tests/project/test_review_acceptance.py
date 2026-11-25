"""Exercise the independent decision at the actual project acceptance boundaries."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
import json
import subprocess
from unittest.mock import patch

from scripts import project, review_acceptance, review_observation
from scripts.integration import delivery_scope
from scripts.review_tree import CommittedPath
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

    def test_protected_tip_preserves_old_acceptance_when_compared_to_parent(self):
        base, _ = self.open_review()
        self.finish()
        protected = self.commit()
        self.command('history', '--base', base, error='is pending')
        self.git('update-ref', 'refs/remotes/origin/main', protected)
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        opened = self.command('review', TASK, '--source', protected, '--request', 'Recheck retrieval after reopening.')
        handoff = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual('pending', handoff['status'])
        self.assertEqual(opened['request']['request_id'], handoff['current_entry_id'])
        self.command('history', '--base', base, error='is pending')

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

    def test_plain_review_uses_its_own_verdict_and_observation_with_terminal_examples(self):
        base, source = self.open_review()
        trace = self.root / '.reports/format.txt'
        trace.parent.mkdir()
        selected = trace.with_name('observation.txt')
        heading = f'CHANGES_REQUESTED {TASK} {source}'
        observed = 'I reopened `四月\u3000\t收盘.csv` and counted 11 rows.'
        quoted = 'I reopened `四月\u3000\t收盘.csv` and counted 12 rows.'
        example = (f'```text\n› Reopen the old result.\n• APPROVE {TASK} {source}\n\n'
                   f'  {quoted}\n  Worked for 1m • today\n```\n')
        plain = f'{heading}\n\n{observed}\n\n{example}'
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer', '--observation-file', str(selected))

        selected.write_bytes((quoted + '\r\n').encode('utf-8-sig'))
        for opening in ('An earlier review looked like this:\n\n', ''):
            with self.subTest(opening=opening):
                # An indented Markdown fence can still contain top-level terminal examples.
                trace.write_text(opening + '  ' + example, encoding='utf-8')
                self.command(*args, error='identify this task')
        raw = ('\r\n  \r\n' + plain.replace('\n', '\r\n')).encode('utf-8')
        trace.write_bytes(raw)
        self.command(*args, error='post-verdict body')
        selected.write_bytes((observed + '\r\n').encode('utf-8-sig'))
        receipt = self.command(*args)
        decision = receipt['decision']
        self.assertEqual(heading, decision['decision'])
        self.assertEqual(observed, decision['observation'])
        self.assertEqual(sha256(raw).hexdigest(), decision['original_sha256'])
        ledger = self.root / receipt['path']
        before = ledger.read_bytes()

        trace.write_text(json.dumps({
            'type': 'response_item', 'timestamp': '2026-11-25T13:00:00Z',
            'payload': {'type': 'message', 'phase': 'final_answer', 'role': 'assistant',
                        'status': 'completed', 'id': 'msg_same_markdown',
                        'content': [{'type': 'output_text', 'text': plain}]},
        }), encoding='utf-8')
        replay = self.command(*args)
        self.assertFalse(replay['recorded'])
        self.assertEqual(decision, replay['decision'])
        self.assertEqual(before, ledger.read_bytes())
        handoff = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual('changes_requested', handoff['status'])
        self.assertEqual(observed, handoff['observation'])
        self.finish()
        self.command('check', error='changes_requested')
        self.command('history', '--base', base, error='changes_requested')
        with self.assertRaisesRegex(project.ProjectError, 'changes_requested'):
            delivery_scope(self.root, project.load_project(self.root), base)

    def test_terminal_review_can_begin_with_clipped_indented_output(self):
        _, source = self.open_review()
        trace = self.root / '.reports/format.txt'
        trace.parent.mkdir()
        heading = f'APPROVE {TASK} {source}'
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer', '--observation', OUTCOME)
        transcript = ('    + Show details\n\n• Ran the previous check\n  └ Process exited normally.\n'
                      '› Reopen the fixture.\n\n'
                      f'• {heading}\n\n  {OUTCOME}\n  Worked for 1m • today\n')
        trace.write_text(transcript, encoding='utf-8')
        result = self.command(*args)
        self.assertEqual(heading, result['decision']['decision'])
        self.assertEqual(OUTCOME, result['decision']['observation'])
        trace.write_text(transcript + '\n› Verify the fixture again.\n', encoding='utf-8')
        self.command(*args, error='last terminal exchange')
        view = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual(result['decision']['response_id'], view['response_id'])

    def test_review_examples_never_supply_an_exchange_or_standalone_verdict(self):
        base, source = self.open_review()
        trace = self.root / '.reports/quoted-review.md'
        trace.parent.mkdir()
        claim = 'Both saved exports contained 12 rows.'
        heading = f'APPROVE {TASK} {source}'
        exchange = f'› Inspect the examples.\n• {heading}\n\n  {claim}\n  Worked for 1m • today\n'
        docs = '• Examples of archived review output\n\n'
        fenced = f'```text\n{exchange}```\n'
        samples = (
            docs + fenced,
            docs + '- A nested example.\n  > ~~~text\n' +
            ''.join(f'  > {line}\n' for line in exchange.splitlines()) + '  > ~~~\n',
            docs + '> A quoted exchange.\n' + exchange,
            docs + '1. A listed exchange.\n' + exchange,
            '• `Copied syntax\n' + exchange.replace('\n\n', '\n') + '`\n',
            docs + '<pre>\n' + exchange + '</pre>\n',
            f'    {heading}\n\n{claim}\n',
            f'APPROVE {TASK}\n  ```text\n  {source}\n  ```\n\n{claim}\n',
        )
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer', '--observation', claim)
        for index, example in enumerate(samples):
            with self.subTest(index=index):
                trace.write_text(example, encoding='utf-8')
                self.command(*args, error='identify this task')
        self.assertFalse((self.root / f'{review_acceptance.DIRECTORY}/{TASK}.json').exists())
        self.finish()
        self.command('check', error='is pending')
        self.command('history', '--base', base, error='is pending')
        with self.assertRaisesRegex(project.ProjectError, 'is pending'):
            delivery_scope(self.root, project.load_project(self.root), base)

    def test_real_terminal_final_reply_keeps_examples_out_of_exchange_boundaries(self):
        _, source = self.open_review()
        trace = self.root / '.reports/quoted-review.txt'
        trace.parent.mkdir()
        observed = 'I reopened `四月\u3000\t收盘.csv` and counted 11 rows.'
        quoted = 'Both saved exports contained 12 rows.'
        reject = f'CHANGES_REQUESTED {TASK} {source}'
        example = (f'```text\n› Prior request.\n• APPROVE {TASK} {source}\n\n'
                   f'{quoted}\n  Worked for 1m • example\n```')
        plain = f'{reject}\n\n{observed}\n\n{example}\n'
        terminal = (f'› Inspect the current fixture.\n\n• {reject}\n\n  {observed}\n\n'
                    + ''.join('  ' + line + '\n' for line in example.splitlines())
                    + '\n  Worked for 1m • today\n')
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer', '--observation')
        trace.write_text(terminal, encoding='utf-8')
        self.command(*args, quoted, error='post-verdict body')
        receipt = self.command(*args, observed)
        self.assertEqual(reject, receipt['decision']['decision'])
        self.assertEqual(observed, receipt['decision']['observation'])
        ledger = self.root / receipt['path']
        before = ledger.read_bytes()

        trace.write_text(plain, encoding='utf-8')
        replay = self.command(*args, observed)
        self.assertFalse(replay['recorded'])
        self.assertEqual(receipt['decision'], replay['decision'])
        self.assertEqual(before, ledger.read_bytes())
        trace.write_text(terminal + '\n› Recheck the exports.\n\n' + example + '\n', encoding='utf-8')
        self.command(*args, observed, error='last terminal exchange')
        self.assertEqual(before, ledger.read_bytes())
        self.assertEqual('changes_requested', self.command('context', TASK)['independent_review']['status'])

    def test_stale_predecessor_and_reordered_event_cannot_replace_rejection(self):
        _, source = self.open_review()
        first = self.review(source, event=('msg_first', '2026-11-25T11:00:00Z'))
        rejected = self.review(source, 'CHANGES_REQUESTED', event=('msg_second', '2026-11-25T13:00:00Z'),
                               supersedes=first['decision']['response_id'])
        self.review(source, observation='The concurrent reply kept its selected result.',
                    event=('msg_concurrent', '2026-11-25T13:10:00Z'),
                    supersedes=first['decision']['response_id'], error='must supersede')
        self.review(source, observation='The earlier reply kept a separate saved result.',
                    event=('msg_reordered', '2026-11-25T12:59:00Z'),
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

    def test_bound_review_navigation_keeps_current_requests_separate(self):
        _, source = self.open_review()
        verified = ('context', TASK, '--phase', 'verify', '--source', source)
        clean = self.command(*verified)
        self.assertEqual(source, clean['review_source'])
        self.assertNotIn('independent_review', clean)
        self.assertEqual(f'APPROVE {TASK} {source}', clean['review_reply']['first_line'][0])
        self.assertTrue(clean['review_reply']['current_checkout_review_command'].endswith(
            f'context {TASK} --phase handoff'))

        opened = self.command('review', TASK, '--source', source,
                              '--request', 'Verify the recorded read and reopening result.')
        live = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual('current_checkout', live['record_basis'])
        self.assertEqual(opened['request']['request_id'], live['pending_request']['request_id'])
        self.command(*verified, error='requires a clean checkout')
        self.assertEqual(source, self.command('source', 'project/tasks.json', '/tasks/0',
                                              '--source', source)['source_commit'])

        # Once a bound view is opened, mutable request records never become
        # part of the reviewed tree, including during repeated navigation.
        immutable = project.task_context(CommittedPath.at(self.root, source), TASK, phase='verify')
        self.assertNotIn('independent_review', immutable)
        self.assertEqual(clean['review_reply'], immutable['review_reply'])

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

    def test_review_markup_cannot_supply_an_observation(self):
        base, source = self.open_review()
        trace = self.root / '.reports/review-markup.txt'
        trace.parent.mkdir()
        short = 'Reopen: `BUSY`.'
        markdown = (
            '### A heading.\n\n'
            'A setext heading.\n---\n\n'
            '- **A list title.** The saved value reopened at version two.\n\n'
            '[A citation.](https://example.test/review(v2))\n\n'
            '> A quoted claim.\n\n'
            '~~~text\nA reported command output.\n\nStill inside the same command output.\n~~~\n\n'
        )
        trace.write_text(f'APPROVE {TASK} {source}\n\n{markdown}{short}\n', encoding='utf-8')
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Synthetic independent reviewer')
        for observed in ('A heading.', 'A setext heading.', 'A list title.', 'A citation.',
                         '[A citation.](https://example.test/review(v2))', 'A quoted claim.',
                         'A reported command output.', 'Still inside the same command output.'):
            with self.subTest(observed=observed):
                self.command(*args, '--observation', observed, error='post-verdict body')
        real = self.command(*args, '--observation', short)
        self.finish()
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        file = self.root / real['path']
        receipt = json.loads(file.read_bytes())
        for non_prose in ('Citation caption', '### A heading.', '[A citation.]', '> A quote.'):
            with self.subTest(non_prose=non_prose):
                retained = receipt['events'][0]
                retained['observation'] = non_prose
                retained['evidence_sha256'] = review_acceptance.evidence_digest(TASK, retained)
                file.write_bytes(project.json_bytes(receipt))
                self.command('check', error='Unusable independent decision or observation')

    def test_body_blocks_keep_full_quotes_and_formatted_prose_without_accepting_titles(self):
        _, source = self.open_review()
        trace = self.root / '.reports/reply.txt'
        trace.parent.mkdir()
        facts = (
            'The reopen reported "BUSY."',
            '**A fresh session** observed the stored value.',
            '**`replay`** consumed the final position.',
            'The second reply was **READY.**',
            '再次打开时返回“已恢复。”',
            'The same read preserved the exact value after restart.',
        )
        body = (
            f'**Review findings.** {facts[0]}\n\n'
            f'**Review details**\n{facts[1]}\n\n'
            f'{facts[2]}\n\n{facts[3]}\n\n{facts[4]}\n\n'
            'The same read preserved the exact\nvalue after restart.\n\n'
            '[An unrelated citation.](https://example.test/review)\n\n'
            '> A quoted block is context.\n\n'
            '**Review footer.**\n'
        )
        trace.write_text(f'APPROVE {TASK} {source}\n\n{body}', encoding='utf-8')
        for fact in facts:
            with self.subTest(fact=fact):
                result = review_acceptance.create_decision(
                    self.root, TASK, source, trace, 'Independent reviewer', None, fact)
                self.assertEqual(fact, result['observation'])
        for non_prose in ('**Review findings.', '**Review findings.**', 'Review details',
                          'An unrelated citation.', 'A quoted block is context.', '**Review footer.**'):
            with self.subTest(non_prose=non_prose), self.assertRaisesRegex(
                    review_acceptance.ReviewError, 'post-verdict body'):
                review_acceptance.create_decision(
                    self.root, TASK, source, trace, 'Independent reviewer', None, non_prose)
        heading = f'APPROVE {TASK} {source}'
        self.assertFalse(review_acceptance.observed_statement('**Review findings.', heading, heading))
        self.assertFalse(review_acceptance.observed_statement('**Review findings.**', heading, heading))

    def test_markdown_context_controls_observation_eligibility(self):
        base, source = self.open_review()
        trace = self.root / '.reports/independent-markdown.txt'
        trace.parent.mkdir()
        invisible = '"Windows directory snapshots."'
        setext = 'The Windows jobs restored the documented cache.'
        quoted = 'The Windows jobs produced complete snapshots.'
        authored = '**Both Windows jobs** emitted **two snapshots.**'
        body = (
            f'[log]: https://example.test/windows\n  {invisible}\n\n'
            f'{setext}\n--\n\n'
            f'> Notes copied from a prior review.\n{quoted}\n\n'
            f'{authored} [Evidence][log]\n'
        )
        trace.write_text(f'APPROVE {TASK} {source}\n\n{body}', encoding='utf-8')
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--reviewer', 'Independent reviewer')
        for non_prose in (invisible, setext, quoted, 'Evidence', '**two snapshots.**'):
            with self.subTest(non_prose=non_prose):
                self.command(*args, '--observation', non_prose, error='post-verdict body')
        self.finish()
        self.command('check', error='is pending')
        self.command('history', '--base', base, error='is pending')
        with self.assertRaisesRegex(project.ProjectError, 'is pending'):
            delivery_scope(self.root, project.load_project(self.root), base)
        accepted = self.command(*args, '--observation', authored)['decision']
        self.assertEqual(authored, accepted['observation'])
        self.assertEqual('passed', self.command('check')['status'])
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.assertEqual([TASK], delivery_scope(self.root, project.load_project(self.root), base)['completed_task_ids'])

    def test_observation_projection_keeps_code_spelling_and_folds_only_prose(self):
        _, source = self.open_review()
        trace = self.root / '.reports/reply.txt'
        trace.parent.mkdir()
        cases = (
            ('The legacy  report\r\nremains readable.', 'The legacy report remains readable.',
             'The legacy\r\nreport remains readable.'),
            ('- A second read confirmed `April \r\n  close.csv` in the output.',
             'A second read confirmed `April  close.csv` in the output.',
             'A second read\r\nconfirmed `April  close.csv` in the output.'),
            ('The output saved **`` April  close`.csv ``**\r\nwithout changes.',
             'The output saved **`` April  close`.csv ``** without changes.',
             'The output saved **`` April  close`.csv ``**\r\nwithout changes.'),
            ('The exact UTF-8 filename was `四月\u3000\t收盘.csv`.',
             'The exact UTF-8 filename was `四月\u3000\t收盘.csv`.',
             'The exact UTF-8 filename was `四月\u3000\t收盘.csv`.'),
            ('The reported name was `  April \r\nclose.csv  `.',
             'The reported name was `  April  close.csv  `.',
             'The reported name was `  April  close.csv  `.'),
        )
        for body, expected, selection in cases:
            with self.subTest(body=body):
                trace.write_bytes(f'APPROVE {TASK} {source}\r\n\r\n{body}\r\n'.encode('utf-8'))
                self.assertEqual([expected], list(review_observation.body_passages(body, rendered=False)))
                receipt = review_acceptance.create_decision(
                    self.root, TASK, source, trace, 'Independent reviewer', None, selection)
                self.assertEqual(expected, receipt['observation'])
                self.assertTrue(review_acceptance.observed_statement(
                    receipt['observation'], receipt['decision'], receipt['scope']))

    def test_observation_file_preserves_unicode_markup_and_accepts_windows_line_endings(self):
        base, source = self.open_review()
        opened = self.command('review', TASK, '--source', source, '--request',
                              'Repeat the independent recovery control.')
        pending = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertIn('--observation-file "review-observation.txt"', pending['coordination_command'])
        self.assertNotIn('--observation "', pending['coordination_command'])
        exact = '再次打开时返回 `April  close.csv`，输出为“已恢复。”'
        capture = self.root / '.reports'
        capture.mkdir()
        trace, observation = capture / 'final response.jsonl', capture / 'selected observation.txt'
        event_time = datetime.fromisoformat(opened['request']['requested_at']) + timedelta(seconds=1)
        trace.write_text(json.dumps({
            'type': 'response_item',
            'timestamp': event_time.isoformat(),
            'payload': {'type': 'message', 'phase': 'final_answer', 'role': 'assistant', 'status': 'completed',
                        'id': 'msg_observation_file', 'content': [{'type': 'output_text',
                            'text': f'APPROVE {TASK} {source}\n\n{exact}'}]},
        }, ensure_ascii=False), encoding='utf-8')
        args = ('review', TASK, '--source', source, '--transcript', str(trace),
                '--observation-file', str(observation), '--reviewer', 'Independent reviewer',
                '--supersedes', opened['request']['request_id'])
        observation.write_bytes(b'\xff\xfe')
        self.command(*args, error='Save the observation file as UTF-8.')
        observation.write_bytes(b'a' * (review_acceptance.OBSERVATION_FILE_LIMIT + 1))
        self.command(*args, error='exceeds the one-sentence input limit')
        ledger = self.root / opened['path']
        before = ledger.read_bytes()
        wrong = exact.replace('April  close.csv', 'April close.csv')
        observation.write_bytes((wrong + '\r\n').encode('utf-8-sig'))
        self.command(*args, error='post-verdict body')
        self.assertEqual(before, ledger.read_bytes())
        self.assertEqual('pending', self.command('context', TASK)['independent_review']['status'])
        observation.write_bytes((exact + '\r\n').encode('utf-8-sig'))
        published = self.command(*args)['decision']
        self.assertEqual(exact, published['observation'])
        self.assertEqual('approved', self.command('context', TASK)['independent_review']['status'])
        self.finish()
        self.assertEqual('passed', self.command('history', '--base', base)['status'])

    def test_response_identity_survives_plain_and_structured_representation_changes(self):
        base, source = self.open_review()
        trace = self.root / '.reports/identity-reply.txt'
        trace.parent.mkdir()
        ledger = self.root / f'{review_acceptance.DIRECTORY}/{TASK}.json'
        first = 'The reply retained `四月\u3000收盘.csv` through the read.'
        changed = 'The reply retained `四月\u3000\t收盘.csv` through the read.'
        other = 'An independent second read kept the existing records.'
        heading = f'APPROVE {TASK} {source}'

        def submit(observation, chosen, message, *, predecessor=None, wrapped=False, error=None):
            text = f'{heading}\n\n{observation}\n\n{other}\n'
            if wrapped:
                text = text.replace('through the read.', 'through\r\nthe read.') + '\r\n'
            when = ('2026-11-25T11:00:00Z' if message == 'msg_first'
                    else '2026-11-25T11:10:00Z')
            payload = {'type': 'message', 'phase': 'final_answer', 'role': 'assistant', 'status': 'completed',
                       'id': message, 'content': [{'type': 'output_text', 'text': text}]}
            trace.write_bytes((json.dumps({'type': 'response_item', 'timestamp': when, 'payload': payload},
                                          ensure_ascii=False) if message else text).encode('utf-8'))
            args = ['review', TASK, '--source', source, '--transcript', str(trace),
                    '--reviewer', 'Independent reviewer', '--observation', chosen]
            if predecessor:
                args.extend(['--supersedes', predecessor])
            return self.command(*args, error=error)

        for initial, subsequent in ((None, 'msg_later'), ('msg_first', 'msg_later'),
                                    (None, None), ('msg_first', None)):
            with self.subTest(initial=initial, subsequent=subsequent):
                old = submit(first, first, initial)['decision']
                if subsequent is None:
                    later = submit(changed, changed, None, predecessor=old['response_id'])
                    self.assertTrue(later['recorded'])
                    self.assertEqual(changed, later['decision']['observation'])
                    last = later['decision']['response_id']
                else:
                    last = old['response_id']
                with patch.object(review_acceptance, 'datetime', wraps=datetime) as clock:
                    clock.now.return_value = datetime.fromisoformat('2026-11-25T11:05:00+00:00')
                    request = self.command('review', TASK, '--source', source, '--supersedes', last,
                                           '--request', 'Independently recheck the selected filename.')['request']
                before = ledger.read_bytes()
                replay = submit(first, other, subsequent, predecessor=request['request_id'], wrapped=True)
                self.assertFalse(replay['recorded'])
                self.assertEqual(old, replay['decision'])
                self.assertEqual(before, ledger.read_bytes())
                self.assertEqual('pending', self.command('context', TASK)['independent_review']['status'])
                if subsequent:
                    if initial:
                        submit(changed, changed, initial, predecessor=request['request_id'],
                               error='response identity has different content')
                    submit(changed, first, subsequent, predecessor=request['request_id'], error='post-verdict body')
                    reply = submit(changed, changed, subsequent, predecessor=request['request_id'])
                    new = reply['decision']
                    self.assertEqual(changed, new['observation'])
                    self.assertNotEqual(old['response_sha256'], new['response_sha256'])
                    self.assertEqual(old['legacy_response_sha256'], new['legacy_response_sha256'])
                    self.assertEqual('approved', self.command('context', TASK)['independent_review']['status'])
                else:
                    fact = 'The independent reader confirmed a separate later result.'
                    submit(fact, fact, None, predecessor=request['request_id'], error='later final assistant export')
                    self.assertEqual(before, ledger.read_bytes())
                self.assertEqual(old, json.loads(ledger.read_bytes())['events'][0])
                self.assertEqual('passed', self.command('history', '--base', base)['status'])
                ledger.unlink()

        older = submit(first, first, None)['decision']
        older['response_sha256'] = older.pop('legacy_response_sha256')
        older['response_id'] = older['response_sha256']
        old_body = f'{first} {other}'.replace('\u3000', ' ').encode('utf-8')
        older['observation_body_sha256'] = sha256(old_body).hexdigest()
        older['evidence_sha256'] = review_acceptance.evidence_digest(TASK, older)
        ledger.write_bytes(project.json_bytes(dict(version=3, task_id=TASK, events=[older])))
        with patch.object(review_acceptance, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat('2026-11-25T11:05:00+00:00')
            request = self.command('review', TASK, '--source', source, '--supersedes', older['response_id'],
                                   '--request', 'Verify the historically folded filename.')['request']
        before = ledger.read_bytes()
        ambiguous = submit(changed, changed, 'msg_later', predecessor=request['request_id'])
        self.assertFalse(ambiguous['recorded'])
        self.assertEqual(older, ambiguous['decision'])
        self.assertEqual(before, ledger.read_bytes())
        self.assertEqual('pending', self.command('context', TASK)['independent_review']['status'])

    def test_replacing_closed_request_on_sibling_requires_a_fresh_approval(self):
        base, original = self.open_review()
        scope = 'Verify the persisted value and its selection in the original worktree.'
        old = self.command('review', TASK, '--source', original, '--request', scope)['request']
        time = datetime.fromisoformat(old['requested_at'])
        initial = self.review(original, 'CHANGES_REQUESTED', supersedes=old['request_id'],
                              event=('msg_first_scope', (time + timedelta(seconds=1)).isoformat()))['decision']
        ledger = self.ledger()
        self.git('checkout', '--detach', base)
        plan = task_by_id(ledger, TASK)['plan']
        plan['evolution']['change'].append('Cover restoration after selecting a different source.')
        self.save_ledger(ledger)
        # Commit only the replacement task definition, then carry the original review record.
        path = self.root / f'{review_acceptance.DIRECTORY}/{TASK}.json'
        history = path.read_bytes()
        path.unlink()
        replacement = self.commit()
        path.write_bytes(history)
        self.assertEqual(base, self.git('merge-base', original, replacement))
        view = self.command('context', TASK, '--phase', 'handoff')['independent_review']
        self.assertEqual('stale', view['status'])
        self.assertEqual(original, view['current_entry_source'])
        self.assertEqual(replacement, view['candidate_source'])
        self.assertIn(f'--supersedes {initial["response_id"]}', view['coordination_command'])
        self.review(replacement, supersedes=initial['response_id'],
                    event=('msg_wrong_order', (time + timedelta(seconds=2)).isoformat()),
                    error='Open a new independent review request')
        with patch.object(review_acceptance, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = time + timedelta(seconds=2)
            next_request = self.command('review', TASK, '--source', replacement, '--request',
                                        'Verify selection and restoration on the replacement worktree.',
                                        '--supersedes', initial['response_id'])['request']
        self.assertEqual('pending', self.command('context', TASK)['independent_review']['status'])
        self.finish()
        self.command('history', '--base', base, error='is pending')
        self.review(replacement, supersedes=next_request['request_id'],
                    event=('msg_replacement', (time + timedelta(seconds=3)).isoformat()))
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        events = json.loads(path.read_bytes())['events']
        self.assertEqual([original, original, replacement, replacement],
                         [row['source_commit'] for row in events])


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
