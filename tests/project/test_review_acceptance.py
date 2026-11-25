"""Exercise the independent decision at the actual project acceptance boundaries."""

from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
from unittest.mock import patch

from scripts import project
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

    def review(self, source, verdict='APPROVE', *, observation=OUTCOME, task=TASK, use_later_verdict=False, error=None):
        trace = self.root / '.reports/independent.txt'
        trace.parent.mkdir(exist_ok=True)
        earlier = f'• APPROVE — earlier review of {task} at {source}.\nHistorical outcome.\n' if use_later_verdict else ''
        trace.write_text(f'{earlier}› Report APPROVE or CHANGES_REQUESTED for {task} at {source}.\n'
                         f'• Ran git show historical-output\n  └ APPROVE — quoted {source}\n'
                         f'• {verdict} — independent decision for {task} at {source}.\n'
                         f'  {observation}\n  Worked for 1m • today\n',
                         encoding='utf-8')
        return self.command('review', TASK, '--source', source, '--transcript', str(trace),
                            '--reviewer', 'Synthetic independent reviewer', '--observation', observation, error=error)

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

        rejected = self.review(source, 'CHANGES_REQUESTED', use_later_verdict=True)
        self.assertEqual('changes_requested', rejected['decision']['verdict'])
        self.command('check', error='changes_requested')
        self.review(source, observation='The same independent reader preserved the value through reopen and restart.')
        self.assertEqual('passed', self.command('check')['status'])
        self.assertEqual('passed', self.command('history', '--base', base)['status'])
        self.assertEqual([TASK], delivery_scope(self.root, project.load_project(self.root), base)['completed_task_ids'])

    def test_other_task_or_only_an_assignment_cannot_supply_a_decision(self):
        _, source = self.open_review()
        self.review(source, task='M0-002', error='identifying the reviewed task')
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
        self.review(source, 'CHANGES_REQUESTED', observation='The later independent reading found a discrepancy after reopen.')
        self.review(source)  # Re-intaking the same original is idempotent.
        self.assertEqual('changes_requested', self.command('context', TASK)['independent_review']['status'])
        self.commit()
        current = self.git('rev-parse', 'HEAD')
        path = self.root / old['path']
        record = json.loads(path.read_bytes())
        record['decisions'].pop()
        path.write_text(json.dumps(record), encoding='utf-8')
        self.command('history', '--base', current, error='decision history changed')
