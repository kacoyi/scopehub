"""Exercise false completion, stale decisions and delivery feedback with isolated records."""

from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import shlex
import base64
import subprocess
import sys
import tempfile
from unittest.mock import patch

from scripts import check as harness
from scripts import ci
from scripts import project as project_cli
from scripts import timing as timing_cli
from scripts.project import (
    ProjectError, archive_evidence, digest, json_bytes, load_project, product_progress, readiness,
    periodic_reviews, read_json, recover_hosted_checkpoint, review_history, task_context, workfront,
    verified_main, source_identity, validate_model,
)
from tests.project.test_continuity import ProjectFixture
from tests.project.fixtures import owner_workbench_fixture, task_by_id, ubuntu_test_host
from scripts.verification_evidence import release_artifacts, collect_session, performance_verdict, field_observation
from scripts.verification_report import integration_validation


class DeliveryTests(ProjectFixture):
    def periodic_fixture(self):
        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, self.review_day, self.review_hour, tzinfo=tz)

        self.review_day, self.review_hour = 25, 10
        self.enterContext(patch.object(project_cli, 'datetime', ReviewClock))
        policy = self.root / 'project/branch-protection.json'
        shutil.copyfile(project_cli.ROOT / 'project/branch-protection.json', policy)
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['scaffold_maintenance']['periodic_reviews'] = [
            {'id': key, 'reviewed_on': day, 'interval_days': 7, 'actions': ['Review the existing observation.']}
            for key, (day, _) in project_cli.maintenance.REVIEW_SOURCES.items()]
        path.write_bytes(json_bytes(delivery))
        return path, delivery

    def project_command(self, *args):
        out = StringIO()
        with (patch.object(project_cli, 'ROOT', self.root), patch.object(sys, 'argv', ['project.py', *args]),
              redirect_stdout(out)):
            try:
                project_cli.main()
            except SystemExit as error:
                self.assertEqual(1, error.code)
        return json.loads(out.getvalue())

    def timing_review(self, clock, *, source=None):
        scopes = []
        for platform in ('Linux', 'Windows'):
            for event in ('pull_request', 'push'):
                runs = [str(45556251399 + 3 * len(scopes) + i) for i in range(3)]
                scopes.append({'repository': 'kacoyi/scopehub', 'event': event, 'profile': 'full',
                               'platform': platform, 'sample_count': 3, 'recent_runs': runs,
                               'recent_objective_breaches': 0})
        if source is None:
            source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        report = {'recent_collection': {'collected_at': clock, 'capture_source': source},
                  'objective_reviews': scopes, 'input_issues': [],
                  'budget_follow_up': {'repeated_breaches': [], 'sustained_growth': []}}
        self.timing_received(report)
        return report

    def timing_received(self, report):
        collection = report['recent_collection']
        source = collection['capture_source']
        samples = []
        for scope in report['objective_reviews']:
            for run in scope['recent_runs']:
                ci = {key: scope[key] for key in ('repository', 'event')}
                ci.update(run_id=run, run_attempt='1', head_sha=source, checkout_sha=source)
                sample = timing_cli.report_sample({
                    'run_id': f'synthetic-report-{run}', 'ci': ci, 'generated_at': collection['collected_at'],
                    'source': {'git_head': source, 'working_tree_dirty': False}, 'status': 'passed',
                    'source_unchanged': True, 'project_unchanged': True,
                    'environment': {'system': scope['platform'], 'machine': 'x86_64'},
                    'selection': {'profile': 'full', 'checks': ['environment']},
                    'checks': [{'id': 'environment', 'status': 'passed', 'exit_code': 0, 'duration_seconds': 1}],
                })
                sample['report_inputs_sha256'] = [timing_cli.report_input_sha256(sample)]
                sample['review_exclusion'] = None
                sample['seconds']['total'] = 42
                samples.append(sample)
        report['samples'] = samples
        collection['collected_samples'] = list(timing_cli.platform_report_receipts(samples))

    def protect_observation(self, record, *reports):
        verified = self.project_command('verify-maintenance', str(record),
                                        *(arg for report in reports for arg in ('--report', str(report))))
        self.assertEqual('originals_verified', verified.get('status'), verified)
        relative = record.relative_to(self.root).as_posix()
        subprocess.run(['git', '-c', 'user.name=Independent Fixture', '-c', 'user.email=fixture@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--only',
                        '-m', 'Accept the independently checked observation', '--', relative],
                       cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'],
                       cwd=self.root, check=True, capture_output=True)

    def add_periodic_record(self, item, raw, *findings):
        input_path = self.root / '.reports' / 'observation.json'
        input_path.parent.mkdir(exist_ok=True)
        input_path.write_bytes(json_bytes(raw))
        args = [arg for finding in findings for arg in ('--finding', finding)]
        generated = self.project_command('observe-maintenance', item['id'], '--report', str(input_path), *args)
        self.assertEqual(item['id'], generated.get('review_id'), generated)
        relative = f'contracts/reviews/{item["id"]}-{generated["observed_at"][:10]}.json'
        if item.get('review_record') and item['review_record'] != relative:
            item.setdefault('review_history', []).append(item['review_record'])
        item.update(reviewed_on=generated['observed_at'][:10], review_record=relative)
        record = self.root / item['review_record']
        record.parent.mkdir(exist_ok=True)
        record.write_bytes(json_bytes(generated))
        subprocess.run(['git', 'add', '--', str(record)], cwd=self.root, check=True, capture_output=True)
        self.protect_observation(record, input_path)
        return record

    def test_startup_and_both_history_routes_reject_date_only_for_all_five_reviews(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        clean = self.project_command('status', '--overview')
        self.assertLess(len(json_bytes(clean)), 2000)
        self.assertEqual('unobserved', clean['verification']['local'])
        for item in delivery['scaffold_maintenance']['periodic_reviews']:
            with self.subTest(review=item['id']):
                previous = item['reviewed_on']
                item['reviewed_on'] = '2026-11-25'
                path.write_bytes(json_bytes(delivery))
                next_action = self.project_command('status', '--overview')['next']
                self.assertEqual(item['id'], next_action['id'])
                self.assertEqual('evidence_missing', next_action['state'])
                self.assertEqual('coordinator', next_action['owner'])
                for command in [('check',), ('history', '--base', base)]:
                    self.assertIn(item['id'], self.project_command(*command)['error'])
                item['reviewed_on'] = previous
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('check')['status'])

    def test_fresh_periodic_observation_cannot_be_removed_or_reused(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        item, other = delivery['scaffold_maintenance']['periodic_reviews'][1:3]
        record = self.add_periodic_record(item, self.timing_review('2026-11-24T00:00:00Z'))
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        self.assertEqual('checks_needed', self.project_command('status')['periodic_reviews'][1]['state'])
        first = self.commit_fixture()
        previous = record.read_bytes()
        record.unlink()
        self.assertEqual('evidence_missing', self.project_command('status', '--overview')['next']['state'])
        self.assertIn('verification-timing', self.project_command('check')['error'])
        record.write_bytes(previous)
        other.update(reviewed_on=item['reviewed_on'], review_record=item['review_record'])
        path.write_bytes(json_bytes(delivery))
        self.assertIn('category', self.project_command('history', '--base', first)['error'])
        other.update(reviewed_on='2026-11-21')
        del other['review_record']
        self.add_periodic_record(item, self.timing_review('2026-11-25T00:00:00Z'))
        path.write_bytes(json_bytes(delivery))
        self.assertIn('fresh comparable observation', self.project_command('check')['error'])

    def test_future_cadence_and_review_retirement_preserve_due_or_open_obligations(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        item = delivery['scaffold_maintenance']['periodic_reviews'][1]
        item['interval_days'] = 14
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        item['retired_reason'] = 'Review at the independently verified successor cadence.'
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('retired', self.project_command('status')['periodic_reviews'][1]['state'])
        self.assertEqual('passed', self.project_command('check')['status'])
        del item['retired_reason']
        item['interval_days'] = 1
        path.write_bytes(json_bytes(delivery))
        overdue = self.commit_fixture()
        item['interval_days'] = 14
        path.write_bytes(json_bytes(delivery))
        self.assertIn('postpone', self.project_command('history', '--base', overdue)['error'])

    def test_due_retirement_transfers_the_missed_cycle_until_post_due_observation(self):
        path, delivery = self.periodic_fixture()
        tasks = self.ledger()
        owner = task_by_id(tasks, 'M0-001')
        owner['scope'].append('project/delivery.json')
        self.save_ledger(tasks)
        base = self.commit_fixture()
        item = delivery['scaffold_maintenance']['periodic_reviews'][2]
        item['interval_days'] = 1
        observed = {'observed_at': '2026-11-24T08:00:00Z', 'repository': 'kacoyi/scopehub',
                    'default_branch': {'name': 'main', 'sha': base}, 'delete_branch_on_merge': True,
                    'errors': [], 'branches': [{'name': 'main', 'sha': base, 'status': 'default', 'protected': True}],
                    'open_pull_requests': []}
        settings = read_json(self.root / 'project/branch-protection.json')['configuration']
        for key in ('enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution'):
            settings[key] = {'enabled': settings[key]}
        raw, protection = (self.root / '.reports' / name for name in ('branches.json', 'protection.json'))
        raw.parent.mkdir(exist_ok=True)
        args = ('observe-maintenance', item['id'], '--report', str(raw), '--report', str(protection))

        def capture(day):
            observed['observed_at'] = f'2026-11-{day}T08:00:00Z'
            raw.write_bytes(json_bytes(observed))
            protection.write_bytes(json_bytes({'repository': 'kacoyi/scopehub', 'branch': 'main', 'branch_sha': base,
                                               'observed_at': observed['observed_at'], 'observed': settings}))
            record = self.project_command(*args)
            self.assertEqual([], record['evidence']['pending'])
            relative = f'contracts/reviews/branch-{day}.json'
            written = self.root / relative
            written.parent.mkdir(exist_ok=True)
            written.write_bytes(json_bytes(record))
            subprocess.run(['git', 'add', '--', relative], cwd=self.root, check=True)
            self.protect_observation(written, raw, protection)
            if item.get('review_record'):
                item['review_history'] = [item['review_record']]
            item.update(reviewed_on=f'2026-11-{day}', review_record=relative)
            path.write_bytes(json_bytes(delivery))
            return written

        original = capture(24)
        unchanged = original.read_bytes()
        active = self.commit_fixture()
        self.assertEqual('due', self.project_command('status')['periodic_reviews'][2]['state'])
        item.update(retired_reason='Move overdue work to its scoped owner.', follow_through_task=owner['id'])
        path.write_bytes(json_bytes(delivery))
        self.assertIn('retired_due_on set to 2026-11-25', self.project_command('history', '--base', active)['error'])
        item['retired_due_on'] = '2026-11-25'
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('check')['status'])
        retired = self.commit_fixture()
        for day in (25, 26):
            self.review_day = day
            status = self.project_command('status')['periodic_reviews'][2]
            self.assertEqual(('owned_action', '2026-11-25'), (status['state'], status['next_review_by']))
            context = self.project_command('context', owner['id'], '--phase', 'verify')
            self.assertIn('scheduled observation due 2026-11-25', context['transferred_reviews'][0]['obligations'])
        item['retired_due_on'] = '2026-11-24'
        path.write_bytes(json_bytes(delivery))
        self.assertIn('accepted retirement calendar cutoff', self.project_command('history', '--base', retired)['error'])
        item['retired_due_on'] = '2026-11-25'
        capture(25)
        for compared in (active, retired):
            self.assertEqual('passed', self.project_command('history', '--base', compared)['status'])
        self.assertEqual('retired', self.project_command('status')['periodic_reviews'][2]['state'])
        self.assertFalse(self.project_command('context', owner['id'], '--phase', 'verify')['transferred_reviews'])
        self.assertEqual(unchanged, original.read_bytes())

    def test_standalone_local_timing_progress_uses_run_and_source_not_hosted_scopes(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        row = delivery['scaffold_maintenance']['periodic_reviews'][1]
        report = {'samples': [], 'objective_reviews': [], 'input_issues': [],
                  'budget_follow_up': {'repeated_breaches': [], 'sustained_growth': []}}

        def sample(source, run, day, seconds):
            return {'run_id': run, 'source_commit': source, 'recorded_at': f'2026-11-{day}T08:00:00Z',
                    'ci': None, 'profile': 'fast', 'report_status': 'passed', 'review_exclusion': None,
                    'working_tree_dirty': False, 'seconds': {'harness': seconds}}

        first = sample(base, 'local-first', 24, 80.812)
        report['samples'] = [first]
        self.add_periodic_record(row, report)
        path.write_bytes(json_bytes(delivery))
        accepted = self.commit_fixture()
        second = sample(accepted, 'local-next', 25, 105.207)
        report['samples'] = [second]
        latest = self.add_periodic_record(row, report)
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('check')['status'])
        self.assertEqual('passed', self.project_command('history', '--base', accepted)['status'])
        evidence = read_json(latest)['evidence']
        self.assertEqual(second['run_id'], evidence['local_fast']['run_id'])
        self.assertEqual({'Linux pull_request', 'Linux push', 'Windows pull_request', 'Windows push',
                          project_cli.maintenance.PRODUCT_TIMING}, set(evidence['pending']))
        accepted = self.commit_fixture()
        self.review_day = 26
        # A later hosted collection clock does not make a repeated or older local run new.
        for replay in (second, sample(base, 'uncaptured-older-run', 24, 81.0)):
            with self.subTest(run=replay['run_id']):
                report.update(samples=[replay], recent_collection={'collected_at': '2026-11-26T08:00:00Z',
                                                                    'collected_samples': []})
                self.add_periodic_record(row, report)
                path.write_bytes(json_bytes(delivery))
                self.assertIn('fresh comparable observation', self.project_command('history', '--base', accepted)['error'])
                row['review_record'] = row['review_history'].pop()
                row['reviewed_on'] = '2026-11-25'

    def test_same_day_branch_progress_preserves_exact_outstanding_tips_and_adverse_review(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        key = 'branches-and-pull-requests'
        item = delivery['scaffold_maintenance']['periodic_reviews'][2]
        tips = [f'{index:040x}' for index in range(1, 10)]
        branches = [{'name': 'main', 'sha': base, 'status': 'default', 'protected': True}]
        branches.extend({'name': f'topic-{index}', 'sha': tip, 'status': 'unique_history'}
                        for index, tip in enumerate(tips))
        raw = {'observed_at': '2026-11-25T06:00:00Z', 'repository': 'kacoyi/scopehub',
               'default_branch': {'name': 'main', 'sha': base}, 'delete_branch_on_merge': True,
               'errors': [], 'branches': branches, 'open_pull_requests': []}
        original = self.add_periodic_record(item, raw)
        before = original.read_bytes()
        path.write_bytes(json_bytes(delivery))
        accepted = self.commit_fixture()
        raw_path = self.root / '.reports/observation.json'

        def propose(label, hour, rows, *, deletes=True, finding=None):
            raw_path.write_bytes(json_bytes({**raw, 'observed_at': f'2026-11-25T{hour:02}:00:00Z',
                                            'branches': rows, 'delete_branch_on_merge': deletes}))
            extra = ('--finding', finding) if finding else ()
            record = self.project_command('observe-maintenance', key, '--report', str(raw_path), *extra)
            if 'error' in record:
                return record
            relative = f'contracts/reviews/branch-{label}.json'
            supplement = self.root / relative
            supplement.write_bytes(json_bytes(record))
            subprocess.run(['git', 'add', '--', relative], cwd=self.root, check=True)
            self.protect_observation(supplement, raw_path)
            item.update(review_history=[original.relative_to(self.root).as_posix()], review_record=relative)
            path.write_bytes(json_bytes(delivery))
            return record

        traced = [{**branches[1], 'status': 'in_use', 'open_pr_heads': [12]}, *branches[2:]]
        unchanged = propose('unchanged', 7, branches[:1] + branches[2:])
        self.assertEqual(8, len(unchanged['evidence']['untraced_tips']))
        self.assertIn('later same-day', self.project_command('history', '--base', accepted)['error'])
        propose('old-clock', 6, [branches[0], *traced])
        self.assertIn('later same-day', self.project_command('check')['error'])
        replacement = {**branches[2], 'sha': 'f' * 40}
        propose('rewritten', 7, [branches[0], traced[0], replacement, *branches[3:]])
        self.assertIn('later same-day', self.project_command('check')['error'])
        progress = propose('one-traced', 7, [branches[0], *traced])
        expected = sorted(project_cli.maintenance.tip_identity('kacoyi/scopehub', tip) for tip in tips[1:])
        self.assertEqual(expected, progress['evidence']['untraced_tips'])
        self.assertEqual('passed', self.project_command('history', '--base', accepted)['status'])
        self.assertIn(project_cli.maintenance.BRANCH_TRACE, self.project_command('status')['periodic_reviews'][2]['problem'])
        self.assertEqual(before, original.read_bytes())
        adverse = propose('simultaneous-adverse', 8, [branches[0], *traced], deletes=False)
        self.assertIn('canonical feedback', adverse['error'])

    def test_timing_review_freshness_uses_all_admitted_scopes_and_accepts_new_overlap(self):
        path, delivery = self.periodic_fixture()
        baseline = self.commit_fixture()
        row = delivery['scaffold_maintenance']['periodic_reviews'][1]
        self.review_day = 29

        def attach(day, event, runs=None):
            raw = self.timing_review(f'2026-11-{day}T08:00:00Z')
            cohorts = [item for item in raw['objective_reviews'] if (item['platform'], item['event']) == ('Linux', event)]
            identity = cohorts[0]['recent_runs']
            if runs is not None:
                identity[:] = runs
            raw['objective_reviews'] = cohorts
            self.timing_received(raw)
            self.add_periodic_record(row, raw)
            path.write_bytes(json_bytes(delivery))
            return identity

        pr_runs = attach(24, 'pull_request')
        self.commit_fixture()
        push_runs = attach(25, 'push')
        self.assertEqual('passed', self.project_command('check')['status'])
        accepted = self.commit_fixture()
        saved = read_json(path)['scaffold_maintenance']['periodic_reviews'][1]
        attach(26, 'pull_request', list(reversed(pr_runs)))
        for args in (('check',), ('history', '--base', accepted), ('history', '--base', baseline)):
            self.assertIn('fresh comparable observation', self.project_command(*args)['error'])
        self.assertEqual('evidence_invalid', self.project_command('status')['periodic_reviews'][1]['state'])

        row.clear()
        row.update(saved)
        attach(27, 'pull_request', [pr_runs[1], pr_runs[0], '45556290000'])
        for args in (('check',), ('history', '--base', accepted), ('history', '--base', baseline)):
            self.assertEqual('passed', self.project_command(*args)['status'])
        accepted = self.commit_fixture()
        attach(28, 'push', list(reversed(push_runs)))
        for args in (('check',), ('history', '--base', accepted), ('history', '--base', baseline)):
            self.assertIn('fresh comparable observation', self.project_command(*args)['error'])

    def test_same_day_branch_progress_follows_ancestry_and_binds_each_protection_revision(self):
        path, delivery = self.periodic_fixture()
        initial = self.commit_fixture()
        tree = project_cli.feedback_git(self.root, 'rev-parse', f'{initial}^{{tree}}').stdout.decode().strip()

        def checkpoint(message, *parents):
            args = [arg for parent in parents for arg in ('-p', parent)]
            return subprocess.check_output(
                ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit-tree', tree, *args, '-m', message],
                cwd=self.root, text=True).strip()

        old = checkpoint('Original default tip', initial)
        advanced = checkpoint('Normal default-branch integration', old)
        unrelated = checkpoint('Unrelated lineage from the common base', initial)
        # A review checkout can contain valid observations from both lines. The transition still needs ancestry.
        joined = checkpoint('Fixture holding both revisions', advanced, unrelated)
        subprocess.run(['git', 'checkout', '--detach', joined], cwd=self.root, check=True, capture_output=True)

        row = delivery['scaffold_maintenance']['periodic_reviews'][2]
        raw, protection = (self.root / '.reports' / name for name in ('branches.json', 'protection.json'))
        raw.parent.mkdir(exist_ok=True)
        settings = read_json(self.root / 'project/branch-protection.json')['configuration']
        for key in ('enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution'):
            settings[key] = {'enabled': settings[key]}
        names = ('alpha', 'beta', 'gamma')

        def attach(label, hour, source, traced, protection_source):
            clock = f'2026-11-25T{hour:02}:00:00Z'
            branches = [{'name': 'main', 'sha': source, 'status': 'default', 'protected': True}]
            for index, name in enumerate(names, 1):
                entry = {'name': name, 'sha': f'{index:040x}', 'status': 'unique_history'}
                if index <= traced:
                    entry.update(status='in_use', open_pr_heads=[index])
                branches.append(entry)
            capture = {'observed_at': clock, 'repository': 'kacoyi/scopehub',
                       'default_branch': {'name': 'main', 'sha': source}, 'delete_branch_on_merge': True,
                       'errors': [], 'branches': branches, 'open_pull_requests': []}
            raw.write_bytes(json_bytes(capture))
            protection.write_bytes(json_bytes({'repository': capture['repository'], 'branch': 'main',
                                               'branch_sha': protection_source, 'observed_at': clock,
                                               'observed': settings}))
            record = self.project_command('observe-maintenance', row['id'], '--report', str(raw),
                                          '--report', str(protection))
            self.assertEqual(source, record['source_commit'])
            relative = f'contracts/reviews/branch-progress-{label}.json'
            proposed = self.root / relative
            proposed.parent.mkdir(exist_ok=True)
            proposed.write_bytes(json_bytes(record))
            subprocess.run(['git', 'add', '--', relative], cwd=self.root, check=True)
            self.protect_observation(proposed, raw, protection)
            row.update(reviewed_on='2026-11-25', review_record=relative)
            path.write_bytes(json_bytes(delivery))
            return record

        first = attach('first', 6, old, 0, old)
        self.assertTrue(first['evidence']['protection_observed'])
        self.assertEqual(3, len(first['evidence']['untraced_tips']))
        original_path = self.root / row['review_record']
        original_bytes = original_path.read_bytes()
        accepted = self.commit_fixture()
        row['review_history'] = [row['review_record']]

        wrong = attach('misbound', 7, advanced, 1, old)
        self.assertFalse(wrong['evidence']['protection_observed'])
        self.assertIn('later same-day', self.project_command('check')['error'])
        attach('unrelated', 7, unrelated, 1, unrelated)
        self.assertIn('same-day', self.project_command('history', '--base', accepted)['error'])

        progress = attach('descendant', 8, advanced, 1, advanced)
        self.assertTrue(progress['evidence']['protection_observed'])
        self.assertEqual(2, len(progress['evidence']['untraced_tips']))
        self.assertLess(set(progress['evidence']['untraced_tips']), set(first['evidence']['untraced_tips']))
        for args in (('check',), ('history', '--base', accepted), ('history', '--base', initial)):
            self.assertEqual('passed', self.project_command(*args)['status'])
        self.assertEqual('checks_needed', self.project_command('status')['periodic_reviews'][2]['state'])
        self.assertEqual(original_bytes, original_path.read_bytes())

        accepted = self.commit_fixture()
        row['review_history'].append(row['review_record'])
        attach('reversal', 9, old, 2, old)
        for args in (('check',), ('history', '--base', accepted), ('history', '--base', initial)):
            self.assertIn('same-day', self.project_command(*args)['error'])
        self.assertEqual('evidence_invalid', self.project_command('status')['periodic_reviews'][2]['state'])

    def test_periodic_observation_assigns_real_followup_and_reopens_on_changed_occurrence(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        item = delivery['scaffold_maintenance']['periodic_reviews'][1]
        review = self.timing_review('2026-11-24T00:00:00Z')
        review['objective_reviews'][0]['recent_objective_breaches'] = 2
        sha = digest(json_bytes(review))
        feedback_path = self.root / 'project/feedback.json'
        feedback = read_json(feedback_path)
        entry = {'id': 'timing-observed-regression', 'source': f'Original timing observation {sha}.',
                 'observation': 'Two verified ordinary runs exceeded the objective.', 'severity': 'minor',
                 'status': 'open', 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                 'resolution': None, 'deferral_reason': None}
        feedback['items'].append(entry)
        feedback_path.write_bytes(json_bytes(feedback))
        self.add_periodic_record(item, review, entry['id'])
        path.write_bytes(json_bytes(delivery))
        next_action = self.project_command('status', '--overview')['next']
        self.assertEqual('owned_action', next_action['state'])
        self.assertEqual('pnpm project:context M0-001 --phase verify', next_action['command'])
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        linked = self.commit_fixture()
        item['interval_days'] = 15
        path.write_bytes(json_bytes(delivery))
        self.assertIn('postpone', self.project_command('history', '--base', linked)['error'])
        item['interval_days'] = 7
        path.write_bytes(json_bytes(delivery))
        entry.update(status='deferred', deferral_reason='Wait for the next ordinary full PR cohort.')
        feedback_path.write_bytes(json_bytes(feedback))
        self.assertEqual('checks_needed', self.project_command('status')['periodic_reviews'][1]['state'])
        entry.update(status='open', deferral_reason=None, occurrence=2)
        feedback_path.write_bytes(json_bytes(feedback))
        self.assertEqual('recheck_needed', self.project_command('status', '--overview')['next']['state'])

    def test_clean_checkout_ages_into_due_and_keeps_owned_critical_feedback_visible(self):
        self.periodic_fixture()
        base = self.commit_fixture()
        quiet = self.project_command('status', '--overview')
        self.assertEqual(0, quiet['maintenance']['periodic_attention'])
        self.assertIsNone(quiet['maintenance']['follow_through'])
        self.review_day = 29
        late = self.project_command('status', '--overview')
        self.assertEqual('due', late['next']['state'])
        self.assertEqual('coordinator', late['next']['owner'])
        self.assertEqual('pnpm project:timing --recent 12', late['next']['command'])
        self.assertEqual('passed', self.project_command('check')['status'])
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=self.root, check=True)

        # The view consumes the same active work and unresolved feedback already
        # selected by workfront. The calendar signal still has an actionable slot.
        from scripts.development import status_view
        state = workfront(self.root)
        state['active_task_ids'] = ['M0-001']
        state['unresolved_feedback'].append({'severity': 'critical', 'status': 'open',
                                             'task_ids': state['active_task_ids'],
                                             'id': 'synthetic-critical-followup'})
        overview = status_view(state, overview=True)
        self.assertEqual('synthetic-critical-followup', overview['next']['id'])
        self.assertEqual('verification-timing', overview['maintenance']['follow_through']['id'])

    def test_compatibility_intake_distinguishes_unchanged_changed_and_unavailable(self):
        delivery_path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        historical = self.root / project_cli.maintenance.COMPATIBILITY_BASELINE
        historical.parent.mkdir(exist_ok=True)
        historical.write_bytes(json_bytes({'baseline': {'main': base}}))
        untouched = self.commit_fixture()
        key = 'compatibility-and-recovery'
        args = ('observe-maintenance', key)
        quiet = self.project_command(*args)
        self.assertEqual(key, quiet['review_id'])
        self.assertEqual([], quiet['evidence']['observations'])
        self.assertEqual({}, quiet['evidence']['unverified_boundaries'])
        item = delivery['scaffold_maintenance']['periodic_reviews'][-1]
        item.update(reviewed_on=quiet['observed_at'][:10], review_record='contracts/reviews/compatibility.json')
        record = self.root / item['review_record']
        record.write_bytes(json_bytes(quiet))
        delivery_path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', untouched)['status'])
        baseline = self.commit_fixture()
        load = project_cli.load_project
        def with_owner(root):
            project = load(root)
            project['development'] = {'modules': [{'id': 'persistence', 'paths': ['crates/persistence']}]}
            return project

        self.enterContext(patch.object(project_cli, 'load_project', side_effect=with_owner))
        consumer = self.root / 'crates/persistence/src/lib.rs'
        consumer.parent.mkdir(parents=True)
        consumer.write_text('pub const VERSION: u8 = 9;\n')
        self.commit_fixture()
        changed = self.project_command(*args)
        self.assertEqual({'workspace-migrations': {'paths': ['crates/persistence/src/lib.rs'],
                                                   'checks': ['state-migrations']}}, changed['changed_boundaries'])
        self.assertEqual('checks_needed', self.project_command('status')['periodic_reviews'][-1]['state'])
        delivery = read_json(delivery_path)
        item = delivery['scaffold_maintenance']['periodic_reviews'][-1]
        del item['review_record']
        delivery_path.write_bytes(json_bytes(delivery))
        historical.write_bytes(json_bytes({'baseline': {'main': 'a' * 40}}))
        self.commit_fixture()
        self.assertIn('comparable accepted', self.project_command(*args)['error'])
        self.assertIn(key, self.project_command('history', '--base', baseline)['error'])

    def test_quiet_timing_does_not_prove_recovery_from_a_task_receipt(self):
        path, delivery = self.periodic_fixture()
        self.commit_fixture()
        feedback_path = self.root / 'project/feedback.json'
        feedback = read_json(feedback_path)
        row = {'id': 'timing-regression-repaired', 'source': 'Retained timing observation.',
               'observation': 'A retained timing window exceeded the objective.', 'severity': 'minor',
               'status': 'open', 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
               'resolution': None, 'deferral_reason': None}
        feedback['items'].append(row)
        feedback_path.write_bytes(json_bytes(feedback))
        report = self.report()
        report_data = read_json(report)
        report_data['observed_feedback'] = [project_cli.feedback_fingerprint(row)]
        report.write_bytes(json_bytes(report_data))
        receipt = archive_evidence(self.root, 'M0-001', report)
        self.finish_first(receipt)
        row.update(status='addressed', resolution={'cause': 'Repeated work.', 'correction': 'Reused work.',
                                                  'prevention': 'Verify the selected check.'})
        feedback_path.write_bytes(json_bytes(feedback))
        self.review_hour = 13
        item = delivery['scaffold_maintenance']['periodic_reviews'][1]
        quiet = self.timing_review('2026-11-25T11:00:00Z')
        self.add_periodic_record(item, quiet, row['id'])
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('checks_needed', self.project_command('status')['periodic_reviews'][1]['state'])
        adverse = self.timing_review('2026-11-25T12:00:00Z', source=quiet['recent_collection']['capture_source'])
        adverse['objective_reviews'][0]['recent_objective_breaches'] = 2
        again = self.root / '.reports/adverse.json'
        again.write_bytes(json_bytes(adverse))
        row['source'] += ' New comparable observation ' + digest(again.read_bytes())
        feedback_path.write_bytes(json_bytes(feedback))
        args = ('observe-maintenance', item['id'], '--report', str(again), '--finding', row['id'])
        new_record = self.project_command(*args)
        self.assertEqual(item['id'], new_record['review_id'])
        recurrence = self.root / 'contracts/reviews/timing-recurrence.json'
        recurrence.write_bytes(json_bytes(new_record))
        subprocess.run(['git', 'add', '--', str(recurrence)], cwd=self.root, check=True)
        self.protect_observation(recurrence, again)
        item.setdefault('review_history', []).append(item['review_record'])
        item['review_record'] = recurrence.relative_to(self.root).as_posix()
        path.write_bytes(json_bytes(delivery))
        self.assertIn('bound adverse finding', self.project_command('check')['error'])

    def test_branch_projection_and_date_are_bound_to_minimized_input(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        item = delivery['scaffold_maintenance']['periodic_reviews'][2]
        raw = {'observed_at': '2026-11-25T09:00:00Z', 'repository': 'kacoyi/scopehub',
               'default_branch': {'name': 'main', 'sha': base}, 'delete_branch_on_merge': True, 'errors': [],
               'branches': [{'name': 'main', 'sha': base, 'status': 'default', 'protected': True},
                            {'name': 'private-retained-name', 'sha': '7' * 40, 'status': 'unique_history'}],
               'open_pull_requests': []}
        record = self.add_periodic_record(item, raw)
        path.write_bytes(json_bytes(delivery))
        self.assertNotIn('private-retained-name', record.read_text())
        (self.root / '.reports/observation.json').unlink()
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        view = self.project_command('status')['periodic_reviews'][2]
        self.assertEqual('checks_needed', view['state'])
        self.assertEqual('pnpm project:protection', view['command'])

        row = read_json(record)
        row['observed_at'] = '2026-11-24T09:00:00+00:00'
        row['evidence']['summary']['branches']['unique_history'] = 0
        item['reviewed_on'] = '2026-11-24'
        path.write_bytes(json_bytes(delivery))
        record.write_bytes(json_bytes(row))
        self.assertIn('hash-bound', self.project_command('history', '--base', base)['error'])
        self.assertIn('hash-bound', self.project_command('check')['error'])
        row['facts']['branch_statuses'].remove('unique_history')
        row['facts']['unique_tips'] = []
        row['evidence']['untraced_tips'] = []
        row['evidence']['pending'].remove(project_cli.maintenance.BRANCH_TRACE)
        row['facts']['observed_at'] = row['observed_at']
        del row['evidence']['summary']['branches']['unique_history']
        row['report_sha256'] = digest(json_bytes(row['facts']))
        record.write_bytes(json_bytes(row))
        self.assertIn('protected intake', self.project_command('history', '--base', base)['error'])
        self.assertIn('protected intake', self.project_command('check')['error'])
        self.assertEqual('intake_needed', self.project_command('status')['periodic_reviews'][2]['state'])

    def test_weekly_ref_and_incomplete_worktree_and_timing_are_scoped(self):
        path, delivery = self.periodic_fixture()
        base = self.commit_fixture()
        report = self.root / '.reports/observation.json'
        report.parent.mkdir()
        key = 'weekly-regression-continuity'
        raw = {'repository': 'alternate/repo', 'head_sha': base, 'observed_at': '2026-11-25T09:00:00Z',
               'weekly_regression': {'status': 'passed', 'run': {
                   'id': 1, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
                   'head_sha': base, 'head_branch': 'elsewhere', 'event': 'schedule',
                   'created_at': '2026-11-24T00:00:00Z'}}}
        for repository, branch in [('alternate/repo', 'main'), ('kacoyi/scopehub', 'elsewhere')]:
            raw['repository'], raw['weekly_regression']['run']['head_branch'] = repository, branch
            report.write_bytes(json_bytes(raw))
            self.assertIn('another repository or branch',
                          self.project_command('observe-maintenance', key, '--report', str(report))['error'])
        raw['weekly_regression']['run']['head_branch'] = 'main'
        self.add_periodic_record(delivery['scaffold_maintenance']['periodic_reviews'][0], raw)
        worktree = {'integration_tip': base, 'integration_ref': 'refs/remotes/origin/main',
                    'observed_at': '2026-11-25T09:00:00Z', 'activity_issues': [], 'archive_error': None,
                    'archive_root': '/private/archive/root', 'worktrees': [
                        {'path': '/private/worktree/path', 'state': 'held', 'holds': ['checkout_observation_incomplete']}]}
        record = self.add_periodic_record(delivery['scaffold_maintenance']['periodic_reviews'][3], worktree)
        self.assertNotIn('/private', record.read_text())
        pending = read_json(record)['evidence']['pending']
        self.assertIn('complete worktree, process and checkout observations', pending)
        self.assertIn('project-owned staging outside registered worktrees', pending)
        raw_timing = self.timing_review('2026-11-25T09:00:00Z')
        scopes = raw_timing['objective_reviews']
        scopes[2]['recent_runs'] = scopes[0]['recent_runs']
        raw_timing['recent_collection']['collected_samples'] = [
            sample for sample in raw_timing['recent_collection']['collected_samples'] if sample['platform'] == 'Linux']
        timing = self.add_periodic_record(delivery['scaffold_maintenance']['periodic_reviews'][1], raw_timing)
        self.assertIn('Windows pull_request', read_json(timing)['evidence']['pending'])
        path.write_bytes(json_bytes(delivery))
        reviews = self.project_command('status')['periodic_reviews']
        self.assertEqual(['observed', 'checks_needed', 'checks_needed'], [reviews[i]['state'] for i in (0, 1, 3)])

    def test_uncatalogued_due_rule_and_retained_urgent_work_remain_visible(self):
        path, delivery = self.periodic_fixture()
        self.commit_fixture()
        self.review_day = 29
        delivery['scaffold_maintenance']['periodic_reviews'].append({
            'id': 'replacement-rule', 'interval_days': 7, 'reviewed_on': '2026-11-21',
            'actions': ['Agree on the source observation.'],
        })
        path.write_bytes(json_bytes(delivery))
        from scripts.development import status_view
        quiet = workfront(self.root)
        self.assertEqual('policy_needed', quiet['periodic_reviews'][-1]['state'])
        self.assertEqual('replacement-rule', status_view(quiet, overview=True)['next']['id'])
        for tasks, expected in [([], 'assignment_needed'), (['M0-999'], 'planned_follow_through')]:
            with self.subTest(expected=expected):
                state = {**quiet, 'unresolved_feedback': [{
                    'severity': 'critical', 'status': 'open', 'task_ids': tasks, 'id': 'synthetic-critical'}]}
                result = status_view(state, overview=True)
                self.assertEqual(expected, result['next']['state'])
                self.assertEqual('replacement-rule', result['maintenance']['follow_through']['id'])
        retained = {'matching_inputs': {'count': 1, 'items': [{
            'check_id': 'project-state', 'report': '.reports/runs/20261125T005000.000000Z-abcdef12/test-report.json'}]}}
        result = status_view({**quiet, 'retained_failures': retained}, overview=True)
        self.assertEqual('retained_failure', result['next']['state'])
        self.assertIn('project:diagnostic identify', result['next']['command'])
        self.assertEqual('replacement-rule', result['maintenance']['follow_through']['id'])
        self.assertEqual('pnpm project:source project/development.json /modules', result['details']['modules'])

    def test_timing_recovery_uses_the_matching_producer_finding_and_scope(self):
        from scripts.maintenance import producer_recovery
        scope = {'repository': 'kacoyi/scopehub', 'event': 'pull_request', 'profile': 'full', 'platform': 'Windows'}
        repair = 'a' * 40
        finding = {'id': 'timing-pr-windows', 'verification_budget': {**scope, 'repair_commit': repair}}
        sample = {'run_id': 'windows-change-45563183277-1', 'source_commit': 'b' * 40,
                  'recorded_at': '2026-11-25T12:00:00Z'}
        verified = {'status': 'recovered', 'repair_commit': repair, 'comparisons': [{'recovered': sample}]}
        row = {'feedback_id': finding['id'], 'scope': scope, 'recovery': verified}
        self.assertEqual(sample['run_id'], producer_recovery(finding, [row])['run_id'])
        for bad in [
            {**row, 'feedback_id': 'another-finding'},
            {**row, 'scope': {**scope, 'event': 'push'}},
            {**row, 'scope': {**scope, 'platform': 'Linux'}},
            {**row, 'recovery': {**verified, 'status': 'pending'}},
            {**row, 'recovery': {**verified, 'repair_commit': 'c' * 40}},
            {**row, 'recovery': {**verified, 'comparisons': []}},
        ]:
            with self.subTest(bad=bad):
                self.assertIsNone(producer_recovery(finding, [bad]))

    def test_verified_branch_copy_covers_only_its_exact_unique_tip(self):
        path, delivery = self.periodic_fixture()
        first = self.commit_fixture()
        tree = subprocess.check_output(['git', 'rev-parse', f'{first}^{{tree}}'], cwd=self.root, text=True).strip()
        tip = subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                       '-c', 'commit.gpgsign=false', 'commit-tree', tree, '-m', 'Retained tip'],
                                      cwd=self.root, text=True).strip()
        subprocess.run(['git', 'update-ref', 'refs/tags/retained-tip', tip], cwd=self.root, check=True)
        dispositions = self.root / 'contracts/reviews/recovery-disposition.json'
        dispositions.parent.mkdir(exist_ok=True)
        dispositions.write_bytes(json_bytes({'version': 1, 'dispositions': [{
            'head': tip, 'disposition': 'superseded', 'reason': 'Exact bytes retained independently.',
            'recoverable_ref': 'refs/tags/retained-tip'}]}))
        source = self.commit_fixture()
        key = 'branches-and-pull-requests'
        branch = {'observed_at': '2026-11-24T08:00:00Z', 'repository': 'kacoyi/scopehub',
                  'default_branch': {'name': 'main', 'sha': source}, 'delete_branch_on_merge': True,
                  'errors': [], 'branches': [{'name': 'main', 'sha': source, 'status': 'default', 'protected': True},
                                             {'name': 'private-tip', 'sha': tip, 'status': 'unique_history'}],
                  'open_pull_requests': []}
        row = delivery['scaffold_maintenance']['periodic_reviews'][2]
        first_observation = self.add_periodic_record(row, branch)
        self.assertIn(project_cli.maintenance.BRANCH_TRACE, read_json(first_observation)['evidence']['pending'])
        path.write_bytes(json_bytes(delivery))
        before = self.commit_fixture()
        # Merely disappearing from a subsequent inventory does not establish recovery.
        branch['branches'].pop()
        branch['observed_at'] = '2026-11-25T08:00:00Z'
        latest = self.add_periodic_record(row, branch)
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', before)['status'])
        view = self.project_command('status')['periodic_reviews'][2]
        self.assertEqual('checks_needed', view['state'])
        self.assertIn(project_cli.maintenance.BRANCH_TRACE, view['problem'])
        raw = self.root / '.reports/observation.json'
        recovered = self.project_command('observe-maintenance', key, '--report', str(raw), '--dispositions',
                                         f'{source}:contracts/reviews/recovery-disposition.json')
        self.assertEqual([project_cli.maintenance.tip_identity('kacoyi/scopehub', tip)], recovered['facts']['traced_tips'])
        self.assertNotIn('private-tip', json.dumps(recovered))
        self.assertIn('effective protection', recovered['evidence']['pending'][0])
        self.assertIn(latest.relative_to(self.root).as_posix(), row['review_record'])

    def test_independent_local_and_product_timing_evidence_can_cover_each_scope(self):
        self.periodic_fixture()
        base = self.commit_fixture()
        project = load_project(self.root)
        slices = ('M0-001', 'M0-002')
        project['delivery']['scenarios'].append({'id': 'synthetic-comparable-scenario',
                                                 'task_ids': list(slices), 'checks': ['project-state']})
        evidence_directory = self.root / 'project/evidence'
        evidence_directory.mkdir(exist_ok=True)
        product = {'kind': 'scopehub-product-delivery-timing-v1', 'scenario': 'synthetic-comparable-scenario',
                   'slices': []}
        for task_id in slices:
            task = project['tasks'][task_id]
            usable = f'project/evidence/{task_id}-usable.json'
            main = f'project/evidence/{task_id}-main.json'
            receipt = {'recorded_at': '2026-11-25T08:30:00Z',
                       'source': {'git_head': base, 'working_tree_dirty': False},
                       'checks': [{'id': 'project-state', 'status': 'passed'}]}
            (self.root / usable).write_bytes(json_bytes(receipt))
            (self.root / main).write_bytes(json_bytes({**receipt, 'ci': {'repository': 'kacoyi/scopehub',
                           'event': 'push', 'head_sha': base}}))
            task['evidence'] = [{'path': usable}, {'path': main}]
            product['slices'].append({'task_id': task_id, 'usable_receipt': usable, 'verified_main_receipt': main,
                   'work_started_at': '2026-11-25T08:00:00Z', 'usable_at': '2026-11-25T08:08:00Z',
                   'pr_created_at': '2026-11-25T08:10:00Z', 'verified_main_at': '2026-11-25T08:13:00Z'})
        raw = self.root / '.reports/timing-input.json'
        raw.parent.mkdir()
        timing = self.timing_review('2026-11-25T09:00:00Z')
        timing['samples'].append({'run_id': 'synthetic-local-fast', 'source_commit': base,
                                  'recorded_at': '2026-11-25T08:00:00Z', 'profile': 'fast',
                                  'report_status': 'passed', 'review_exclusion': None, 'working_tree_dirty': False,
                                  'seconds': {'harness': 13.0}, 'ci': None})
        raw.write_bytes(json_bytes(timing))
        supplement = self.root / '.reports/product-timing.json'
        supplement.write_bytes(json_bytes(product))
        args = ('observe-maintenance', 'verification-timing', '--report', str(raw))
        with patch.object(project_cli, 'load_project', return_value=project):
            local = self.project_command(*args)
            self.assertEqual([project_cli.maintenance.PRODUCT_TIMING], local['evidence']['pending'])
            both = self.project_command(*args, '--report', str(supplement))
            self.assertEqual([], both['evidence']['pending'])
            self.assertEqual(480, both['evidence']['product_delivery']['work_to_usable_median_seconds'])
            self.assertEqual(180, both['evidence']['product_delivery']['pr_to_verified_main_median_seconds'])
            local_only = {**timing, 'recent_collection': None, 'objective_reviews': []}
            raw.write_bytes(json_bytes(local_only))
            local_capture = self.project_command(*args)
            self.assertIsNone(local_capture['source_commit'])
            self.assertEqual('synthetic-local-fast', local_capture['evidence']['local_fast']['run_id'])
            self.assertIn('Windows push', local_capture['evidence']['pending'])
            raw.write_bytes(json_bytes(timing))
            product['slices'][0]['verified_main_receipt'] = product['slices'][1]['verified_main_receipt']
            supplement.write_bytes(json_bytes(product))
            self.assertIn('same slice', self.project_command(*args, '--report', str(supplement))['error'])

    def test_maintenance_rechecks_exact_received_platform_report_identity(self):
        self.periodic_fixture()
        self.commit_fixture()
        inputs = self.timing_review('2026-11-25T07:00:00Z')
        observed = inputs['recent_collection']['collected_samples']
        target = next(row for row in observed if row['platform'] == 'Windows' and row['event'] == 'pull_request')
        raw = self.root / '.reports/timing-input.json'
        raw.parent.mkdir()
        proposed = raw.with_name('proposed.json')
        cases = (
            (target, False),
            (dict(target, run_attempt='2'), True),
            (dict(target, checkout_sha='f' * 40), True),
            (dict(target, report_id='different-harness-report'), True),
            (dict(target, report_input_sha256='f' * 64), True),
            (dict({key: value for key, value in target.items() if key != 'report_input_sha256'},
                  report_identity_sha256=target['report_input_sha256']), True),
            ({key: target[key] for key in ('run_id', 'event', 'platform')}, True),
        )
        for collected, pending in cases:
            with self.subTest(collected=collected):
                inputs['recent_collection']['collected_samples'] = [collected]
                raw.write_bytes(json_bytes(inputs))
                outcome = self.project_command('observe-maintenance', 'verification-timing', '--report', str(raw))
                self.assertIs(pending, 'Windows pull_request' in outcome['evidence']['pending'])
                self.assertEqual(4, len(outcome['evidence']['objective_reviews']))
                proposed.write_bytes(json_bytes(outcome))
                verified = self.project_command('verify-maintenance', str(proposed), '--report', str(raw))
                self.assertEqual('originals_verified', verified['status'])

    def test_timing_original_does_not_inherit_the_review_checkout_source(self):
        self.periodic_fixture()
        base = self.commit_fixture()
        key = 'verification-timing'
        raw = self.root / '.reports/timing.json'
        raw.parent.mkdir(exist_ok=True)
        inputs = self.timing_review('2026-11-25T07:00:00Z')
        del inputs['recent_collection']['capture_source']
        raw.write_bytes(json_bytes(inputs))
        args = ('observe-maintenance', key, '--report', str(raw))
        pending = self.project_command(*args)
        self.assertIsNone(pending['source_commit'])
        self.assertIn(project_cli.maintenance.LOCAL_FAST, pending['evidence']['pending'])
        self.assertIn(project_cli.maintenance.PRODUCT_TIMING, pending['evidence']['pending'])
        marker = self.root / 'reviewer.txt'
        marker.write_text('A later independent review checkout.\n')
        later = self.commit_fixture()
        self.assertNotEqual(base, later)
        self.assertEqual(pending, self.project_command(*args))
        file = self.root / '.reports/proposed-timing.json'
        verify = ('verify-maintenance', str(file), '--report', str(raw))
        file.write_bytes(json_bytes(pending))
        self.assertIsNone(self.project_command(*verify)['source_commit'])
        for substitute in (base, later):
            pending['source_commit'] = pending['facts']['source_commit'] = substitute
            pending['report_sha256'] = digest(json_bytes(pending['facts']))
            file.write_bytes(json_bytes(pending))
            self.assertIn('differs from', self.project_command(*verify)['error'])
        inputs['recent_collection']['capture_source'] = base
        raw.write_bytes(json_bytes(inputs))
        positive = self.project_command(*args)
        self.assertEqual(base, positive['source_commit'])
        file.write_bytes(json_bytes(positive))
        self.assertEqual(base, self.project_command(*verify)['source_commit'])

    def test_adverse_supplement_and_next_day_preserve_every_admitted_predecessor(self):
        path, delivery = self.periodic_fixture()
        source = self.commit_fixture()
        key = 'branches-and-pull-requests'
        row = delivery['scaffold_maintenance']['periodic_reviews'][2]
        branch = {'observed_at': '2026-11-24T06:00:00Z', 'repository': 'kacoyi/scopehub',
                  'default_branch': {'name': 'main', 'sha': source}, 'delete_branch_on_merge': True,
                  'errors': [], 'branches': [{'name': 'main', 'sha': source, 'status': 'default', 'protected': True}],
                  'open_pull_requests': []}
        original = self.add_periodic_record(row, branch)
        original_bytes = original.read_bytes()
        path.write_bytes(json_bytes(delivery))
        initial = self.commit_fixture()
        feedback_path = self.root / 'project/feedback.json'
        feedback = read_json(feedback_path)
        raw = self.root / '.reports/observation.json'
        policy = read_json(self.root / 'project/branch-protection.json')['configuration']
        for setting in ('enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution'):
            policy[setting] = {'enabled': policy[setting]}
        protection = self.root / '.reports/protection.json'
        settings = {'repository': 'kacoyi/scopehub', 'branch': 'main', 'branch_sha': source,
                    'observed_at': '2026-11-24T07:00:00Z', 'observed': policy}
        policy['allow_deletions']['enabled'] = True
        protection.write_bytes(json_bytes(settings))
        args = ('observe-maintenance', key, '--report', str(raw), '--report', str(protection))
        self.assertIn('canonical feedback', self.project_command(*args)['error'])
        feedback['items'].append({'id': 'branch-deletion-observed', 'severity': 'minor', 'status': 'open',
                                  'source': 'Original digest ' + digest(protection.read_bytes()),
                                  'observation': 'Protected branch permitted deletion.', 'task_ids': ['M0-001'],
                                  'regression_checks': ['project-state'], 'resolution': None, 'deferral_reason': None})
        feedback_path.write_bytes(json_bytes(feedback))
        adverse = self.project_command(*args, '--finding', 'branch-deletion-observed')
        second = self.root / 'contracts/reviews/accepted-adverse.json'
        second.write_bytes(json_bytes(adverse))
        subprocess.run(['git', 'add', '--', str(second)], cwd=self.root, check=True)
        self.protect_observation(second, raw, protection)
        row['review_history'] = [row['review_record']]
        row['review_record'] = second.relative_to(self.root).as_posix()
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', initial)['status'])
        second_base = self.commit_fixture()
        self.assertEqual('owned_action', self.project_command('status')['periodic_reviews'][2]['state'])
        branch['observed_at'] = '2026-11-25T06:00:00Z'
        settings['observed_at'] = '2026-11-25T07:00:00Z'
        policy['allow_deletions']['enabled'] = False
        raw.write_bytes(json_bytes(branch))
        protection.write_bytes(json_bytes(settings))
        third = self.root / 'contracts/reviews/accepted-next-day.json'
        third.write_bytes(json_bytes(self.project_command(*args, '--finding', 'branch-deletion-observed')))
        subprocess.run(['git', 'add', '--', str(third)], cwd=self.root, check=True)
        self.protect_observation(third, raw, protection)
        row['review_history'].append(row['review_record'])
        row.update(reviewed_on='2026-11-25', review_record=third.relative_to(self.root).as_posix())
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', second_base)['status'])
        third_base = self.commit_fixture()
        self.assertEqual('passed', self.project_command('check')['status'])
        original.write_bytes(original_bytes + b' ')
        self.assertIn('predecessor', self.project_command('history', '--base', third_base)['error'])
        self.assertIn('predecessor', self.project_command('check')['error'])
        original.write_bytes(original_bytes)
        original.unlink()
        self.assertTrue(self.project_command('status')['periodic_reviews'][2]['review_due'])
        original.write_bytes(original_bytes)
        old = row['review_history'].pop(0)
        path.write_bytes(json_bytes(delivery))
        self.assertIn('admitted predecessor', self.project_command('history', '--base', third_base)['error'])
        row['review_history'].insert(0, old)

    def test_same_day_protection_supplement_preserves_approved_predecessor(self):
        path, delivery = self.periodic_fixture()
        ledger = self.ledger()
        successor = task_by_id(ledger, 'M0-001')
        successor['scope'].append('project/delivery.json')
        self.save_ledger(ledger)
        base = self.commit_fixture()
        branch = 'branches-and-pull-requests'
        item = delivery['scaffold_maintenance']['periodic_reviews'][2]
        branch_input = {'observed_at': '2026-11-25T08:00:00Z', 'repository': 'kacoyi/scopehub',
                        'default_branch': {'name': 'main', 'sha': base}, 'delete_branch_on_merge': True,
                        'errors': [], 'branches': [{'name': 'main', 'sha': base, 'status': 'default', 'protected': True}],
                        'open_pull_requests': []}
        previous = self.add_periodic_record(item, branch_input)
        original = previous.read_bytes()
        path.write_bytes(json_bytes(delivery))
        item.update(retired_reason='Transfer exact missing protection observation.', follow_through_task=successor['id'])
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('owned_action', self.project_command('status')['periodic_reviews'][2]['state'])
        before = self.commit_fixture()
        raw = self.root / '.reports/observation.json'
        settings = read_json(self.root / 'project/branch-protection.json')['configuration']
        for key in ('enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution'):
            settings[key] = {'enabled': settings[key]}
        protection = self.root / '.reports/protection.json'
        protection.write_bytes(json_bytes({'repository': 'kacoyi/scopehub', 'branch': 'main', 'branch_sha': base,
                                            'observed_at': '2026-11-25T09:00:00Z', 'observed': settings}))
        args = ('observe-maintenance', branch, '--report', str(raw), '--report', str(protection))
        observation = self.project_command(*args)
        self.assertEqual(observation, self.project_command(*args))
        self.assertEqual([], observation['evidence']['pending'])
        supplement = self.root / 'contracts/reviews/branch-supplement.json'
        supplement.write_bytes(json_bytes(observation))
        subprocess.run(['git', 'add', '--', str(supplement)], cwd=self.root, check=True)
        self.protect_observation(supplement, raw, protection)
        item['review_history'] = [item['review_record']]
        item['review_record'] = supplement.relative_to(self.root).as_posix()
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', before)['status'])
        closed = self.project_command('status')['periodic_reviews'][2]
        self.assertEqual('retired', closed['state'])
        self.assertEqual(successor['id'], closed['follow_through_task'])
        self.assertFalse(closed['review_due'])
        self.review_day = 30
        self.assertEqual('retired', self.project_command('status')['periodic_reviews'][2]['state'])
        self.assertEqual(original, previous.read_bytes())
        previous.unlink()
        self.assertIn('observation file', self.project_command('status')['periodic_reviews'][2]['problem'].lower())

    def test_legacy_worktree_retirement_must_retain_owned_external_stage_scope(self):
        path, delivery = self.periodic_fixture()
        ledger = self.ledger()
        owner = task_by_id(ledger, 'M0-001')
        owner['scope'].append('project/delivery.json')
        self.save_ledger(ledger)
        base = self.commit_fixture()
        item = delivery['scaffold_maintenance']['periodic_reviews'][3]
        item.update(retired_reason='Use the scoped successor task.')
        path.write_bytes(json_bytes(delivery))
        self.assertIn('external staging', self.project_command('history', '--base', base)['error'].lower())
        item['follow_through_task'] = owner['id']
        path.write_bytes(json_bytes(delivery))
        self.assertEqual('passed', self.project_command('history', '--base', base)['status'])
        status = self.project_command('status')['periodic_reviews'][3]
        self.assertEqual('owned_action', status['state'])
        self.assertIn('staging outside registered worktrees', status['problem'])
        self.assertIn(owner['id'], status['command'])

    def test_cold_critical_lookup_is_stable_and_read_only(self):
        self.periodic_fixture()
        feedback_file = self.root / 'project/feedback.json'
        original = read_json(feedback_file)
        item = {'id': 'critical-from-a-new-session', 'source': 'Synthetic isolated report.',
                'observation': 'Restoring shows an earlier selection.', 'severity': 'critical',
                'status': 'open', 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                'resolution': None, 'deferral_reason': None}
        other = {**item, 'id': 'unassigned-minor', 'severity': 'minor'}
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.commit_fixture()
        for items in ([item, other], [other, item]):
            feedback_file.write_bytes(json_bytes({**original, 'items': items}))
            overview = self.project_command('status', '--overview', '--read-only')
            command = shlex.split(overview['next']['command'])
            executable = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
            self.assertEqual([executable, '-B', 'scripts/project.py'], command[:3])
            focused = self.project_command(*command[3:])
            self.assertEqual(item, focused['value'])
            self.assertLess(len(json_bytes(focused)), 1500)
            self.assertIn('--id critical-from-a-new-session', overview['next']['command'])
            self.assertIn('scripts/project.py source project/development.json /modules', overview['details']['modules'])
        task_id = 'M0-002'
        owned = {**item, 'id': 'another-critical-with-owner', 'task_ids': [task_id]}
        updated = self.ledger()
        task = task_by_id(updated, task_id)
        task.update(status='active', plan=task_by_id(updated, 'M0-001')['plan'])
        self.save_ledger(updated)
        for items in ([owned, item], [item, owned]):
            feedback_file.write_bytes(json_bytes({**original, 'items': items}))
            for read_only in (False, True):
                overview = self.project_command('status', '--overview', *(['--read-only'] if read_only else []))
                self.assertEqual(owned['id'], overview['next']['id'])
                self.assertEqual(1, overview['backlog']['unassigned_findings'])
                command = shlex.split(overview['backlog']['unassigned_lookup'])
                query = self.project_command(*command[3:] if read_only else ['source', *command[2:]])
                self.assertEqual([item['id']], [row['id'] for row in query['items']])
                self.assertLess(len(json_bytes(query)), 1000)
                detail = shlex.split(query['items'][0]['command'])
                self.assertEqual(item, self.project_command(*detail[3:] if read_only else ['source', *detail[2:]])['value'])
        from scripts.development import read_only_project_command
        with patch('scripts.development.os.name', 'nt'):
            self.assertEqual('./.venv/Scripts/python.exe -B scripts/project.py source project/feedback.json --id '
                             'critical-from-a-new-session',
                             read_only_project_command('source', 'project/feedback.json', '--id', item['id']))

    def test_bound_feedback_lookup_and_paged_queue_read_the_same_committed_tree(self):
        self.periodic_fixture()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        path = self.root / 'project/feedback.json'
        feedback = read_json(path)
        rows = [{'id': f'fixture-unassigned-{index}', 'source': 'Synthetic review input.',
                 'observation': f'Observed state {index}.', 'severity': 'critical', 'status': 'open',
                 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                 'resolution': None, 'deferral_reason': None} for index in range(7)]
        path.write_bytes(json_bytes({**feedback, 'items': list(reversed(rows))}))
        delivery_path = self.root / 'project/delivery.json'
        current_delivery = delivery_path.read_bytes()
        historical_delivery = json.loads(current_delivery)
        del historical_delivery['scaffold_maintenance']
        delivery_path.write_bytes(json_bytes(historical_delivery))
        reviewed = self.commit_fixture()
        delivery_path.write_bytes(current_delivery)
        args = ('source', 'project/feedback.json')
        live_args = (*args, '--unassigned')
        bound_args = (*live_args, '--source', reviewed)
        self.assertEqual(7, self.project_command(*live_args)['total'])
        initial = self.project_command(*bound_args, '--read-only')
        self.assertEqual(reviewed, initial['source_commit'])
        self.assertEqual([row['id'] for row in rows[:5]], [row['id'] for row in initial['items']])
        self.assertIn(f'--source {reviewed}', initial['more'])
        # Existing scoped review commands reopen the named tree even when the working data differs.
        changed = {**rows[0], 'observation': 'Changed and assigned in the working tree.', 'task_ids': ['M0-002']}
        path.write_bytes(json_bytes({**feedback, 'items': [changed, *rows[1:]]}))
        self.assertEqual(6, self.project_command(*live_args)['total'])
        self.assertEqual(changed, self.project_command(*args, '--id', changed['id'])['value'])
        self.assertEqual(7, self.project_command(*bound_args)['total'])
        for mode in ((), ('--read-only',)):
            queue = self.project_command(*bound_args, *mode)
            continued = self.project_command(*shlex.split(queue['more'])[3:])
            self.assertEqual(reviewed, continued['source_commit'])
            self.assertEqual([row['id'] for row in rows[5:]], [row['id'] for row in continued['items']])
            detail = self.project_command(*shlex.split(queue['items'][0]['command'])[3:])
            self.assertEqual(reviewed, detail['source_commit'])
            self.assertEqual(rows[0], detail['value'])
            self.assertEqual('Link this existing finding to an unfinished corrective task and verify its assigned checks.',
                             detail['follow_through'])
        dirty = self.project_command('context', 'M0-002', '--phase', 'verify', '--source', reviewed)
        self.assertIn('requires a clean checkout', dirty['error'])
        self.commit_fixture()
        self.assertEqual(7, self.project_command(*bound_args)['total'])
        moved = self.project_command('context', 'M0-002', '--phase', 'verify', '--source', reviewed)
        self.assertIn('does not match checkout HEAD', moved['error'])
        self.assertIn('no JSON pointer', self.project_command(*args, '/items', '--unassigned', '--source', reviewed)['error'])

    def test_bound_context_routes_transferred_scope_to_immutable_policy(self):
        path, delivery = self.periodic_fixture()
        ledger = self.ledger()
        owner = task_by_id(ledger, 'M0-001')
        owner['scope'].append('project/delivery.json')
        self.save_ledger(ledger)
        row = delivery['scaffold_maintenance']['periodic_reviews'][3]
        row.update(retired_reason='Keep the staging scope assigned to the successor.', follow_through_task=owner['id'])
        path.write_bytes(json_bytes(delivery))
        reviewed = self.commit_fixture()
        args = ('context', owner['id'], '--phase', 'verify')
        ordinary = self.project_command(*args)
        self.assertIn('staging outside registered worktrees', ordinary['transferred_reviews'][0]['obligations'])
        result = self.project_command(*args, '--source', reviewed)
        transfer, = result['transferred_reviews']
        self.assertEqual('recorded transfer policy', transfer['scope'])
        policy = self.project_command(*shlex.split(transfer['policy_command'])[3:])
        self.assertEqual(row, policy['value'])
        self.assertEqual(reviewed, policy['source_commit'])

    def test_chart_and_prototype_passes_cannot_satisfy_the_v1_workbench_gate(self):
        project = load_project(project_cli.ROOT)
        gate = 'chart-free-workbench'
        self.assertIn(gate, harness.select_checks(project, profile='release'))
        self.assertIn(gate, project['delivery']['release_policy']['required_checks'])
        self.assertIn(gate, harness.select_checks(project, task_id='M2-013'))
        self.assertEqual('current-contract', project['tasks']['M2-013']['delivery'])

        # Give every other check a passing result, including the chart and renderer probe.
        results = [{'id': key, 'status': 'passed'} for key in project['checks'] if key != gate]
        for result, expected in (([], 'blocked'), ([{'id': gate, 'status': 'blocked'}], 'blocked'),
                                 ([{'id': gate, 'status': 'failed'}], 'failed'),
                                 ([{'id': gate, 'status': 'passed'}], 'passed')):
            with self.subTest(result=result):
                milestones, acceptance = product_progress(project, results + result)
                workbench, = (row for row in acceptance if gate in row['checks'])
                self.assertEqual(expected, workbench['status'])
                milestone = next(row for row in milestones if row['id'] == 'M2')
                self.assertEqual(expected, milestone['verification'])

    def test_recurring_review_becomes_due_and_exposes_the_action(self):
        delivery = load_project(self.root)['delivery']
        before, = periodic_reviews(delivery, date(2026, 11, 27))
        due, = periodic_reviews(delivery, date(2026, 11, 28))
        self.assertFalse(before['review_due'])
        self.assertTrue(due['review_due'])
        self.assertEqual(due['next_review_by'], '2026-11-28')
        self.assertEqual(workfront(self.root)['periodic_reviews'][0]['actions'], due['actions'])

    def test_hosted_report_rejects_private_fields_in_evidence_metadata(self):
        for target in ('root', 'check', 'environment'):
            report = read_json(self.report())
            record = {'root': report, 'check': report['checks'][0], 'environment': report['environment']}[target]
            record['conversation_text'] = 'Synthetic private fixture.'
            validate_model(self.root, 'HarnessReport', report)
            with self.subTest(target=target), self.assertRaises(ProjectError):
                validate_model(self.root, 'HostedHarnessReport', report)

    def edit_decision(self):
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['decision'] += ' Changed after validation.'
        path.write_bytes(json_bytes(decision))

    def run_harness(self, args=(), before_check=None, system='Linux'):
        def execute(name, command, env, run_dir, timeout):
            if before_check:
                before_check(name, env)
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic harness fixture, not product acceptance.\n')
            return {'id': name, 'status': 'passed', 'command': command, 'exit_code': 0,
                    'log': log.relative_to(self.root).as_posix(), 'log_sha256': digest(log.read_bytes())}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=execute),
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(harness.platform, 'system', return_value=system),
              patch.object(harness.platform, 'machine', return_value='x86_64'),
              patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        return stopped.exception.code, read_json(self.root / '.reports/test-report.json')

    def commit_fixture(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, capture_output=True, check=True)
        subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve fixture history'],
                       cwd=self.root, capture_output=True, check=True)
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root,
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_decision_change_invalidates_status_and_archival(self):
        report = self.report()
        self.edit_decision()
        state = workfront(self.root)
        self.assertTrue(state['verification']['matches_current_execution_inputs'])
        self.assertFalse(state['verification']['matches_current_project_definitions'])
        self.assertIsNone(state['milestones'])
        with self.assertRaisesRegex(ProjectError, 'definitions or decisions changed'):
            archive_evidence(self.root, 'M0-001', report)

    def test_task_change_hides_stale_milestone_results(self):
        self.report()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['acceptance'].append('New observable outcome.')
        self.save_ledger(ledger)
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNone(state['milestones'])

    def test_malformed_report_cannot_appear_current_or_support_completion(self):
        path = self.report()
        report = read_json(path)
        report['source_unchanged'] = 'true'
        path.write_bytes(json_bytes(report))
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNotNone(state['verification']['report_error'])
        with self.assertRaisesRegex(ProjectError, 'HarnessReport'):
            archive_evidence(self.root, 'M0-001', path)

    def test_commit_after_dirty_verification_does_not_create_release_proof(self):
        self.report()
        self.commit_fixture()
        result = readiness(self.root)
        self.assertTrue(result['verification']['current'])
        self.assertIn('Release verification must name the same clean committed revision.', result['blockers'])

    def completed_release_fixture(self):
        # An isolated all-implemented project exercises the eventual release path.
        # Its synthetic logs and outcomes are never production acceptance evidence.
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        for check in catalog['checks']:
            check.update(status='implemented', command=['{python}', '-c', "print('synthetic acceptance')"])
        catalog['required_change_checks'] = [check['id'] for check in catalog['checks'] if check['cadence'] == 'change']
        path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        plan = task_by_id(ledger, 'M0-001')['plan']
        for task in ledger['tasks']:
            task['checks'] = [check['id'] for check in catalog['checks']]
            task['plan'] = plan
        self.save_ledger(ledger)
        report_path = self.report(system='Windows')
        completed = set()
        while len(completed) < len(ledger['tasks']):
            task = next(task for task in ledger['tasks'] if task['id'] not in completed
                        and set(task['depends_on']).issubset(completed))
            ref = archive_evidence(self.root, task['id'], report_path)
            task.update(status='done', evidence=[ref], next_action=None)
            completed.add(task['id'])
            ledger['focus_task_id'] = next((item['id'] for item in ledger['tasks'] if item['id'] not in completed), None)
            self.save_ledger(ledger)
        self.commit_fixture()
        report_path = self.report(system='Windows')
        report = read_json(report_path)
        report.update(
            selection={'profile': 'release', 'task_id': None, 'checks': [c['id'] for c in report['checks']]},
            native_requirement={'requested': True, 'satisfied': True},
            integration_validation=integration_validation(report['checks']),
        )
        run_dir = self.root / '.reports/runs/fixture'
        folder = run_dir / 'candidate'
        folder.mkdir()
        identity = {'commit': report['source']['git_head'], 'inputs_sha256': report['source']['sha256']}
        data = b'Synthetic installer data for tests.'
        (folder / 'fixture.exe').write_bytes(data)
        installer = {'path': 'fixture.exe', 'sha256': digest(data)}

        def save(name, value):
            content = value if isinstance(value, bytes) else json_bytes(value)
            (folder / name).write_bytes(content)
            return {'path': name, 'sha256': digest(content)}

        observations = []
        for name, build in [('Windows 10', 19045), ('Windows 11', 22631)]:
            host = {'family': name, 'product_type': 1, 'arch': 'x64', 'build': build, 'webview2': 'fixture'}
            observation = dict(
                identity,
                installer_sha256=installer['sha256'],
                host=host,
                operator='synthetic',
                observed_at='2026-01-01T12:00:00Z',
                clean_install='passed',
                upgrade='passed',
            )
            observations.append(save(str(build) + '.json', observation))
        host.update(cpu='fixture', ram='fixture', display_scale='fixture', refresh_rate='fixture')
        performance = save(
            'performance.json',
            dict(
                identity,
                host=host,
                installer_sha256=installer['sha256'],
                cold_first_frame_ms=[1000] * 20,
                warm_first_frame_ms=[1000] * 20,
                update_to_present_ms=[200] * 72000,
                private_bytes_per_minute=[1024] * 120,
                final_bar_presented=True,
            ),
        )
        notice = save('THIRD_PARTY_NOTICES', b'Synthetic third-party notices and license text.\n')
        owner = save('owner-workbench.json', owner_workbench_fixture(identity, installer['sha256']))
        save(
            'build-manifest.json',
            dict(identity, installer=installer, windows_observations=observations, performance=performance, owner_workbench=owner,
                 third_party_licenses=[notice]),
        )
        report['candidate_artifacts'] = release_artifacts(
            run_dir, report['source'], load_project(self.root)['catalog']['desktop_performance']
        )
        report_path.write_bytes(json_bytes(report))
        return report_path, report

    def test_release_technical_pass_does_not_authorize_publication(self):
        self.completed_release_fixture()
        with patch('scripts.project.verified_main', return_value={'id': 'synthetic observed main run'}):
            result = readiness(self.root)
        self.assertEqual([], result['blockers'])
        self.assertTrue(result['technical_ready'])
        self.assertEqual('not_evaluated', result['authorization'])
        self.assertEqual('present_release_proposal', result['recommendation'])

    def test_release_readiness_rejects_install_and_performance_only_ownerless_candidate(self):
        path, report = self.completed_release_fixture()
        manifest_path = self.root / '.reports/runs/fixture/candidate/build-manifest.json'
        manifest = read_json(manifest_path)
        del manifest['owner_workbench']
        contents = json_bytes(manifest)
        manifest_path.write_bytes(contents)
        report['candidate_artifacts']['manifest_sha256'] = digest(contents)
        path.write_bytes(json_bytes(report))
        result = readiness(self.root)
        self.assertFalse(result['technical_ready'])
        self.assertIn('owner workbench', ' '.join(result['blockers']))
        self.assertEqual('not_evaluated', result['authorization'])

    def test_bound_hosted_release_and_main_provenance(self):
        latest, report = self.completed_release_fixture()
        head = report['source']['git_head']
        base = self.root / '.reports/ci/402/windows'
        base.mkdir(parents=True)
        shutil.copytree(self.root / '.reports/runs', base / 'runs')
        report['ci'] = {
            'provider': 'github-actions',
            'repository': 'owner/repo',
            'run_id': '402',
            'run_attempt': '1',
            'event': 'workflow_dispatch',
            'head_sha': head,
            'checkout_sha': head,
            'run_url': 'https://github.com/owner/repo/actions/runs/402',
        }
        hosted = base / 'test-report.json'
        hosted.write_bytes(json_bytes(report))
        latest.write_bytes(json_bytes(dict(report, selection={'profile': 'fast', 'task_id': None})))
        with patch('scripts.project.verified_main', return_value={'id': 'main run'}):
            self.assertFalse(readiness(self.root)['technical_ready'])
            self.assertTrue(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate=head)[
                    'technical_ready'
                ]
            )
            self.assertFalse(readiness(self.root, report_path=hosted, candidate=head)['technical_ready'])
            self.assertFalse(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate='a' * 40)[
                    'technical_ready'
                ]
            )
            payload = base / 'runs/fixture/candidate/fixture.exe'
            payload.write_bytes(b'changed')
            self.assertFalse(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate=head)[
                    'technical_ready'
                ]
            )
        self.assertEqual('fast', read_json(latest)['selection']['profile'])

    def test_release_requires_both_windows_families_and_a_measured_installer(self):
        path, report = self.completed_release_fixture()
        manifest_path = self.root / '.reports/runs/fixture/candidate/build-manifest.json'
        manifest = read_json(manifest_path)
        manifest['windows_observations'] = [manifest['windows_observations'][1]] * 2
        manifest_path.write_bytes(json_bytes(manifest))
        with patch('scripts.project.verified_main', return_value={'id': 'main run'}):
            self.assertFalse(readiness(self.root, report_path=path)['technical_ready'])

    def test_owner_rule_hash_includes_authority_and_excludes_supplementary_observations(self):
        path = self.root / 'contracts/reviews/example.json'
        path.parent.mkdir()
        path.write_bytes(json_bytes({'owner_direction': 'use A', 'screenshot': 1}))
        spec_path = self.root / 'engineering-contract-v1.json'
        spec = read_json(spec_path)
        spec['repository'] = {
            'owner_decision_inputs': [
                {'path': 'contracts/reviews/example.json', 'pointer': '/owner_direction'}
            ]
        }
        spec_path.write_bytes(json_bytes(spec))
        self.report()
        path.write_bytes(json_bytes({'owner_direction': 'use A', 'screenshot': 2}))
        self.assertTrue(workfront(self.root)['verification']['current'])
        path.write_bytes(json_bytes({'owner_direction': 'use B', 'screenshot': 2}))
        self.assertFalse(workfront(self.root)['verification']['current'])

    def test_modality_evidence_is_independent_of_mock_results_and_default_settings(self):
        directory = self.root / 'observations'
        directory.mkdir()
        source = {'git_head': 'b' * 40, 'sha256': 'a' * 64}
        from tests.contracts.test_qualification import observed_case

        original = {
            'qualification': observed_case(),
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'provider': 'fixture',
            'route': 'route',
            'data_revision': 'revision',
            'authorized_scope': 'synthetic-model-qualification',
            'observed_at': '2026-11-18T12:00:00Z',
            'model': 'fixture',
            'deployment': 'cloud',
        }
        results = []
        self.assertEqual('not_evaluated', integration_validation(results)['cloud_models']['status'])
        results.append({'id': 'ai-replay-evaluations', 'status': 'passed'})
        mock = integration_validation(results)
        self.assertTrue(mock['mock_ai_evaluated'])
        self.assertEqual('not_evaluated', mock['cloud_models']['status'])
        for key, modality in [('configured-model-session', 'text'), ('configured-voice-session', 'voice')]:
            (directory / (key + '.observation.json')).write_bytes(
                json_bytes(dict(original, check=key, modality=modality, authorized_scope=(
                    'synthetic-voice-qualification' if modality == 'voice' else 'synthetic-model-qualification')))
            )
            observed = collect_session(directory, key, source)
            results.append({'id': key, 'status': 'passed', 'observation': observed})
            self.assertEqual(
                [r['observation'] for r in results if 'observation' in r],
                integration_validation(results)['cloud_models']['observations'],
            )
            with self.assertRaisesRegex(ProjectError, 'another revision'):
                collect_session(directory, key, dict(source, git_head='c' * 40))
        self.assertEqual('not_evaluated', integration_validation(results)['real_market_data']['status'])
        key = 'configured-model-session'
        filename = directory / (key + '.observation.json')
        filename.write_bytes(json_bytes(dict(original, check=key, modality='text', deployment='local',
                                             qualification=observed_case('local'))))
        local = integration_validation(
            [{'id': key, 'status': 'passed', 'observation': collect_session(directory, key, source)}]
        )
        self.assertEqual('observed', local['configured_models']['status'])
        self.assertEqual('not_evaluated', local['cloud_models']['status'])
        self.assertEqual('local', local['configured_models']['observations'][0]['deployment'])
        filename.write_bytes(json_bytes(dict(original, check=key, modality='text', deployment='unknown')))
        with self.assertRaisesRegex(ProjectError, 'local or cloud'):
            collect_session(directory, key, source)
        for field, value in [('route', 'https://example.invalid/private?token=synthetic'),
                             ('model', 'synthetic private body'), ('authorized_scope', 'synthetic private body')]:
            filename.write_bytes(json_bytes(dict(original, check=key, modality='text', **{field: value})))
            with self.subTest(field=field), self.assertRaisesRegex(ProjectError, 'redacted'):
                collect_session(directory, key, source)

    def test_client_family_name_does_not_qualify_a_server_build(self):
        from scripts.verification_evidence import windows_client
        host = {'family': 'Windows 11', 'build': 26100, 'arch': 'x64', 'webview2': 'fixture'}
        for wrong in (None, True, 2, 3):
            with self.subTest(product_type=wrong), self.assertRaises(ProjectError):
                windows_client(dict(host, product_type=wrong))
        self.assertEqual('Windows 11', windows_client(dict(host, product_type=1)))

    def test_primary_cloud_task_requires_the_retained_qualified_cloud_session(self):
        from scripts.check import checked_session
        from tests.contracts.test_qualification import observed_case

        key = 'configured-model-session'
        task_id = 'M0-001'
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        base = next(c for c in catalog['checks'] if c['id'] == 'project-state')
        catalog['checks'].append(dict(base, id=key, cadence='release'))
        catalog_path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        task_by_id(ledger, task_id)['checks'].append(key)
        self.save_ledger(ledger)
        policy = self.root / 'project/delivery.json'
        delivery = read_json(policy)
        delivery['release_policy']['required_checks'].append(key)
        policy.write_bytes(json_bytes(delivery))
        contract_path = self.root / 'engineering-contract-v1.json'
        spec = read_json(contract_path)
        spec['ai'] = {'model_connections': {'primary_cloud_route': {'task': task_id, 'check': key}}}
        contract_path.write_bytes(json_bytes(spec))
        self.commit_fixture()
        path = self.report()
        report = read_json(path)
        checks = {c['id']: c for c in report['checks']}
        folder = self.root / '.reports/runs/fixture'
        observation = folder / (key + '.observation.json')
        raw = dict(check=key, modality='text', provider='fixture', route='fixture', model='fixture',
                   data_revision='test', authorized_scope='synthetic-model-qualification', observed_at='2026-11-19T12:00:00Z',
                   commit=report['source']['git_head'], inputs_sha256=report['source']['sha256'])
        for route in ('local', 'cloud'):
            raw.update(deployment=route, qualification=observed_case(route))
            observation.write_bytes(json_bytes(raw))
            checks[key]['observation'] = collect_session(folder, key, report['source'])
            path.write_bytes(json_bytes(report))
            if route == 'local':
                with self.assertRaisesRegex(ProjectError, 'observed cloud text'):
                    checked_session(load_project(self.root), folder, key, report['source'])
                with self.assertRaisesRegex(ProjectError, 'observed cloud text'):
                    archive_evidence(self.root, task_id, path)
            else:
                self.assertEqual(checks[key]['observation'], checked_session(load_project(self.root), folder, key, report['source']))
        self.assertIn('path', archive_evidence(self.root, task_id, path))
        observation.write_bytes(json_bytes(dict(raw, raw_prompt='synthetic private text')))
        with self.assertRaisesRegex(ProjectError, 'Unexpected fields'):
            archive_evidence(self.root, task_id, path)

    def test_memory_and_latency_verdicts(self):
        profile = {'initial_bars_to_chart_p95_ms': 1000, 'chart_update_p95_ms': 200}
        data = {
            'cold_first_frame_ms': [1000] * 20,
            'warm_first_frame_ms': [999] * 20,
            'update_to_present_ms': [200] * 72000,
            'private_bytes_per_minute': [40 * 1024**2] * 120,
            'final_bar_presented': True,
        }
        self.assertEqual('passed', performance_verdict(data, profile)['status'])
        data['private_bytes_per_minute'] = [i * 1024**2 for i in range(120)]
        self.assertEqual('failed', performance_verdict(data, profile)['status'])
        data['private_bytes_per_minute'] = [1024**2] * 110 + [100 * 1024**2] * 10
        self.assertEqual('inconclusive', performance_verdict(data, profile)['status'])
        data['cold_first_frame_ms'][-2:] = [1001, 1002]
        self.assertEqual('failed', performance_verdict(data, profile)['status'])

    def test_main_requires_current_official_main_push_and_candidate_release_run(self):
        path = self.root / 'project/branch-protection.json'
        path.write_bytes(json_bytes({'repository': 'owner/repo', 'branch': 'main'}))
        head = 'b' * 40
        run = {
            'head_branch': 'main',
            'head_sha': head,
            'id': 4,
            'run_attempt': 1,
            'event': 'push',
            'path': '.github/workflows/verify.yml',
            'status': 'completed',
            'conclusion': 'success',
        }

        def command(argv, root):
            if 'git/ref' in ' '.join(argv):
                return head
            if any(x.endswith('/attempts/1') for x in argv):
                return json.dumps(dict(run, id=5, event='workflow_dispatch'))
            return json.dumps({'workflow_runs': [run]})

        with (
            patch('scripts.ci.github_identity', return_value='owner/repo'),
            patch('scripts.ci.command', side_effect=command),
        ):
            self.assertEqual(
                4,
                verified_main(
                    self.root, head, {'repository': 'owner/repo', 'run_id': '5', 'run_attempt': '1'}
                )['id'],
            )
            with self.assertRaisesRegex(ProjectError, 'not the current main'):
                verified_main(self.root, 'c' * 40, None)
            run['event'] = 'pull_request'
            with self.assertRaisesRegex(ProjectError, 'push verification'):
                verified_main(self.root, head, None)

    def test_graphical_ubuntu_requires_retained_screenshots_and_reports_pending_modalities(self):
        self.commit_fixture()
        source = source_identity(self.root)
        key = 'ubuntu-workspace-observation'
        directory = self.root / '.reports/observations' / key
        directory.mkdir(parents=True)
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XxGCPgAAAABJRU5ErkJggg=='
        )
        (directory / 'fixture.png').write_bytes(image)
        names = [
            'history',
            'live',
            'reconnect',
            'folder-refresh',
            'undo-and-restore',
            'layout',
            'indicator',
            'mock-ai',
            'evidence-selection',
            'decision-review',
            'local-bridge',
        ]
        obs = {
            'kind': key,
            'observation_version': 2,
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'operator': 'synthetic operator',
            'observed_at': '2026-01-01T12:00:00Z',
            'surface': 'native-ubuntu-tauri-webkitgtk', 'host': ubuntu_test_host(),
            'screenshot_scope': 'synthetic', 'sharing_scope': 'redacted',
            'actions': dict.fromkeys(names, {'status': 'passed', 'observed': 'Observed synthetic test outcome.'}),
            'external_sessions': dict.fromkeys(['text', 'voice', 'cli', 'desktop'], 'pending'),
            'screenshots': [{'path': 'fixture.png', 'sha256': digest(image)}],
        }
        observation_path = directory / 'observation.json'
        def check_variation(record, message):
            observation_path.write_bytes(json_bytes(record))
            for hosted in (False, True):
                with self.subTest(message=message, hosted=hosted), self.assertRaisesRegex(ProjectError, message):
                    field_observation(self.root, key, hosted=hosted)

        legacy = {key: value for key, value in obs.items() if key not in ('observation_version', 'host')}
        check_variation(dict(legacy, surface='ubuntu-graphical-workspace'), 'observation_version')
        check_variation(obs, 'lacks required actions')
        chart_free = {'workbench-from-blank', 'workbench-edit', 'workbench-ai-reshape', 'workbench-manual-detail',
                      'workbench-refresh', 'workbench-source-selection', 'workbench-undo', 'workbench-recover',
                      'workbench-reopen'}
        obs['actions'].update(dict.fromkeys(chart_free, {'status': 'passed', 'observed': 'Visible fixture outcome.'}))
        for timestamp, error in (('2999-01-01T02:00:00+02:00', 'future observation time'),
                                 ('2026-01-01T12:00:00', 'timezone-aware')):
            check_variation(dict(obs, observed_at=timestamp), error)
        for surface in ('ubuntu-graphical-workspace', 'chromium-app-mode', 'standalone-vite'):
            check_variation(dict(obs, surface=surface), 'native Ubuntu')
        for field in ('os', 'ubuntu_version', 'webkitgtk_version', 'process_name', 'process_id', 'window_id', 'window_process_id'):
            check_variation(dict(obs, host={k: v for k, v in obs['host'].items() if k != field}),
                            'Ubuntu|ubuntu_version|webkitgtk_version|process|window_id')
        for field, value in (('os', 'Debian'), ('ubuntu_version', 'latest'), ('webkitgtk_version', 'unknown'),
                             ('process_name', 'chromium'), ('window_process_id', 456), ('process_id', True)):
            check_variation(dict(obs, host={**obs['host'], field: value}), 'Ubuntu|ubuntu_version|webkitgtk_version|process')
        observation_path.write_bytes(json_bytes(obs))
        result = field_observation(self.root, key)
        self.assertEqual(2, result['observation_version'])
        offset = timezone(timedelta(hours=-7))
        near = (datetime.now(timezone.utc) + timedelta(minutes=4)).astimezone(offset).isoformat()
        observation_path.write_bytes(json_bytes(dict(obs, observed_at=near)))
        for hosted in (False, True):
            self.assertEqual(2, field_observation(self.root, key, hosted=hosted)['observation_version'])
        late = (datetime.now(timezone.utc) + timedelta(minutes=6)).astimezone(offset).isoformat()
        check_variation(dict(obs, observed_at=late), 'future observation time')
        self.assertEqual(obs['external_sessions'], result['external_sessions'])
        failed = dict(obs, actions={**obs['actions'], 'reconnect': {'status': 'failed', 'observed': 'No chart'}})
        (directory / 'observation.json').write_bytes(json_bytes(failed))
        with self.assertRaisesRegex(ProjectError, 'Field action did not pass: reconnect'):
            field_observation(self.root, key)
        (directory / 'observation.json').write_bytes(json_bytes(obs))
        (directory / 'fixture.png').unlink()
        with self.assertRaisesRegex(ProjectError, 'Missing or changed'):
            field_observation(self.root, key)

    def test_physical_trial_requires_matching_ubuntu_receipt_and_native_surface(self):
        self.commit_fixture()
        source = source_identity(self.root)
        key = 'physical-windows-workspace'
        directory = self.root / '.reports/observations' / key
        directory.mkdir(parents=True)
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XxGCPgAAAABJRU5ErkJggg=='
        )
        (directory / 'fixture.png').write_bytes(image)
        actions = [
            'unconfigured-entry',
            'sample',
            'instrument',
            'timeframe',
            'style',
            'resize',
            'crosshair',
            'data-inspection',
            'exit',
        ]
        win = {
            'kind': key,
            'observation_version': 2, 'screenshot_scope': 'synthetic', 'sharing_scope': 'redacted',
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'operator': 'test operator',
            'observed_at': '2026-11-18T12:01:00Z',
            'host': {'family': 'Windows 11', 'product_type': 1, 'build': 22631, 'arch': 'x64', 'webview2': 'fixture'},
            'surface': 'physical-dual-boot-native-webview2',
            'screenshots': [{'path': 'fixture.png', 'sha256': digest(image)}],
            'actions': dict.fromkeys(actions, {'status': 'passed', 'observed': 'Synthetic fixture outcome.'}),
            'ubuntu_reviewed_commit': 'c' * 40,
        }
        obs_path = directory / 'observation.json'
        obs_path.write_bytes(json_bytes(win))
        ubuntu_path = self.root / '.reports/ubuntu-receipt.json'
        receipt = {
            'source': dict(source, git_head='c' * 40),
            'field_observations': {'ubuntu-workspace-observation': {
                'kind': 'ubuntu-workspace-observation', 'commit': 'c' * 40,
                'surface': 'native-ubuntu-tauri-webkitgtk', 'observation_version': 2}},
        }
        ubuntu_path.write_bytes(json_bytes(receipt))
        ledger = {
            'tasks': {'M1-012': {'status': 'done', 'evidence': [{'path': '.reports/ubuntu-receipt.json'}]}}
        }
        with patch('scripts.verification_evidence.load_project', return_value=ledger):
            for hosted in (False, True):
                with self.subTest(hosted=hosted), self.assertRaisesRegex(ProjectError, 'lacks required actions'):
                    field_observation(self.root, key, hosted=hosted)
            for name in ('workbench-edit', 'workbench-selection', 'workbench-display-scaling',
                         'workbench-reopen', 'workbench-recover'):
                win['actions'][name] = {'status': 'passed', 'observed': 'Visible fixture result.'}
            for name in ('workbench-edit', 'workbench-selection', 'workbench-display-scaling',
                         'workbench-reopen', 'workbench-recover'):
                incomplete = {k: v for k, v in win['actions'].items() if k != name}
                obs_path.write_bytes(json_bytes(dict(win, actions=incomplete)))
                for hosted in (False, True):
                    with self.subTest(name=name, hosted=hosted), self.assertRaisesRegex(ProjectError, name):
                        field_observation(self.root, key, hosted=hosted)
            obs_path.write_bytes(json_bytes(win))
            self.assertEqual(key, field_observation(self.root, key)['kind'])
            obs_path.write_bytes(json_bytes(dict(win, observed_at='2999-01-01T01:00:00-05:00')))
            for hosted in (False, True):
                with self.subTest(hosted=hosted), self.assertRaisesRegex(ProjectError, 'future observation time'):
                    field_observation(self.root, key, hosted=hosted)
            obs_path.write_bytes(json_bytes(win))
            historical = {**receipt, 'field_observations': {'ubuntu-workspace-observation': {
                'kind': 'ubuntu-workspace-observation', 'surface': 'ubuntu-graphical-workspace'}}}
            ubuntu_path.write_bytes(json_bytes(historical))
            with self.assertRaisesRegex(ProjectError, 'matching Ubuntu'):
                field_observation(self.root, key)
            ubuntu_path.write_bytes(json_bytes(receipt))
            obs_path.write_bytes(json_bytes(dict(win, surface='hosted-build-only')))
            with self.assertRaisesRegex(ProjectError, 'physical native WebView2'):
                field_observation(self.root, key)
            obs_path.write_bytes(json_bytes(win))
            receipt['source']['sha256'] = 'd' * 64
            ubuntu_path.write_bytes(json_bytes(receipt))
            with self.assertRaisesRegex(ProjectError, 'matching Ubuntu'):
                field_observation(self.root, key)

    def test_release_profile_covers_product_obligations_without_selecting_task_only_work(self):
        project = load_project(project_cli.ROOT)
        obligations = {
            'decision-note-lifecycle', 'indicator-workflow', 'local-folder-import', 'model-connection-replay',
            'model-connection-workflow', 'private-record-lifecycle', 'state-migrations', 'workspace-layout',
            'workspace-widget-bindings',
        }
        release = harness.select_checks(project, profile='release')
        self.assertLessEqual(obligations, release)
        changes = {key for key, check in project['checks'].items()
                   if check['status'] != 'retired' and check.get('cadence', 'change') == 'change'}
        release_cadence = {key for key, check in project['checks'].items()
                           if check['status'] != 'retired' and check.get('cadence') == 'release'}
        self.assertLessEqual(changes | release_cadence, release)
        self.assertEqual(changes, harness.select_checks(project))
        for name, expected in project['catalog']['profiles'].items():
            with self.subTest(profile=name):
                self.assertEqual(set(expected), harness.select_checks(project, profile=name))

    def test_new_task_cadence_obligations_enter_release_with_platform_and_implementation_limits(self):
        catalog_path = self.root / 'project/checks.json'
        delivery_path = self.root / 'project/delivery.json'
        catalog = read_json(catalog_path)
        delivery = read_json(delivery_path)
        base = next(check for check in catalog['checks'] if check['id'] == 'project-state')
        bindings = {
            'milestone-only': catalog['milestones'][0]['checks'],
            'acceptance-only': catalog['acceptance'][0]['checks'],
            'scenario-only': delivery['scenarios'][0]['checks'],
            'release-policy-only': delivery['release_policy']['required_checks'],
        }
        ledger = self.ledger()
        scenario_task = task_by_id(ledger, delivery['scenarios'][0]['task_ids'][0])
        scenario_task['checks'].append('scenario-only')
        self.save_ledger(ledger)
        for key, checks in bindings.items():
            check = dict(base, id=key, cadence='task')
            if key == 'acceptance-only':
                check.update(status='planned', command=None)
            if key == 'scenario-only':
                check['platforms'] = 'windows-x64'
            catalog['checks'].append(check)
            checks.append(key)
        catalog['checks'].append(dict(base, id='retired-check', status='retired', command=None))
        catalog_path.write_bytes(json_bytes(catalog))
        delivery_path.write_bytes(json_bytes(delivery))

        for system in ('Linux', 'Windows'):
            with self.subTest(system=system):
                code, report = self.run_harness(['--profile', 'release'], system=system)
                self.assertEqual(1, code)
                self.assertEqual('blocked', report['status'])
                self.assertLessEqual(set(bindings), set(report['selection']['checks']))
                rows = {row['id']: row for row in report['checks']}
                self.assertEqual('passed', rows['milestone-only']['status'])
                self.assertEqual('passed', rows['release-policy-only']['status'])
                self.assertEqual('not_implemented', rows['acceptance-only']['reason_code'])
                self.assertEqual('not_selected', rows['retired-check']['reason_code'])
                if system == 'Windows':
                    self.assertEqual('passed', rows['scenario-only']['status'])
                else:
                    self.assertEqual('unsupported_platform', rows['scenario-only']['reason_code'])

    def test_retired_check_cannot_remain_an_explicit_release_obligation(self):
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        retired = dict(catalog['checks'][0], id='former-release-check', status='retired', command=None)
        catalog['checks'].append(retired)
        catalog_path.write_bytes(json_bytes(catalog))
        delivery_path = self.root / 'project/delivery.json'
        delivery = read_json(delivery_path)
        delivery['release_policy']['required_checks'].append(retired['id'])
        delivery_path.write_bytes(json_bytes(delivery))
        with self.assertRaisesRegex(ProjectError, 'Release policy depends on a retired check'):
            load_project(self.root)

    def test_task_only_observations_do_not_enter_change_or_release_selection(self):
        project = load_project(self.root)
        key = 'one-observed-session'
        project['checks'][key] = {'id': key, 'status': 'implemented', 'cadence': 'task'}
        task = project['tasks']['M0-001']
        task['checks'].append(key)
        for delivery in ('current-contract', 'later'):
            with self.subTest(delivery=delivery):
                task['delivery'] = delivery
                self.assertNotIn(key, harness.select_checks(project))
                self.assertNotIn(key, harness.select_checks(project, profile='release'))
                self.assertIn(key, harness.select_checks(project, task_id=task['id']))

    def test_single_stream_and_quality_checks_cannot_pass_full_market_acceptance(self):
        project = load_project(self.root)
        project['catalog']['acceptance']=[{'requirement':'all instruments and timeframes','checks':['market-workflow']}]
        partial=[{'id':name,'status':'passed'} for name in ['market-live-stream','market-quality-states']]
        self.assertEqual('blocked',product_progress(project,partial)[1][0]['status'])
        self.assertEqual('passed',product_progress(project,partial+[{'id':'market-workflow','status':'passed'}])[1][0]['status'])

    def test_ordinary_or_non_native_report_cannot_become_release_acceptance(self):
        path, report = self.completed_release_fixture()
        for update in [
            {'selection': {'profile': 'full', 'task_id': None}},
            {'selection': {'profile': 'fast', 'task_id': None}},
            {'selection': {'profile': 'task', 'task_id': 'M5-001'}},
            {'selection': None},
            {'selection': ['release']},
            {'native_requirement': {'requested': False, 'satisfied': True}},
            {'native_requirement': {'requested': True, 'satisfied': False}},
            {'native_requirement': {'requested': 1, 'satisfied': 1}},
            {'environment': dict(report['environment'], system='Linux')},
            {'environment': dict(report['environment'], machine='aarch64')},
            {'status': 'blocked'},
        ]:
            with self.subTest(update=update):
                path.write_bytes(json_bytes(dict(report, **update)))
                self.assertFalse(readiness(self.root)['release_ready'])

    def test_decision_change_during_run_fails_the_report(self):
        code, report = self.run_harness(before_check=lambda name, _: self.edit_decision() if name == 'project-state' else None)
        self.assertEqual(1, code)
        self.assertTrue(report['source_unchanged'])
        self.assertFalse(report['project_unchanged'])
        self.assertEqual('failed', report['status'])
        self.assertFalse(report['product_complete'])

    def test_invalid_state_at_end_is_recorded_as_failure(self):
        def invalidate(name, _):
            if name == 'project-state':
                ledger = self.ledger()
                ledger['focus_task_id'] = None
                self.save_ledger(ledger)

        code, report = self.run_harness(before_check=invalidate)
        self.assertEqual(1, code)
        self.assertIn('focus task', report['project_state_error'])
        self.assertFalse(report['project_unchanged'])

    def test_fast_checks_cannot_complete_unexecuted_task_checks(self):
        code, report = self.run_harness(['--profile', 'fast'])
        self.assertEqual(0, code)
        self.assertEqual('passed', report['status'])
        results = {item['id']: item for item in report['checks']}
        self.assertEqual('not_selected', results['rust-lints']['reason_code'])
        self.assertFalse(report['product_complete'])
        with self.assertRaisesRegex(ProjectError, 'Required check did not pass'):
            archive_evidence(self.root, 'M0-001', self.root / '.reports/test-report.json')

    def test_storage_maintenance_precedes_task_and_fast_verification(self):
        for selection in (['--task', 'M0-001'], ['--profile', 'fast']):
            with self.subTest(selection=selection), patch.object(harness, 'maintain_automatically') as maintain:
                code, report = self.run_harness(selection, before_check=lambda *_: maintain.assert_called_once_with(self.root))
                self.assertEqual(0, code)
                self.assertEqual('passed', report['status'])
                maintain.assert_called_once_with(self.root)

    def test_cargo_incremental_defaults_only_in_local_verification_children(self):
        base_env = {key: value for key, value in os.environ.items()
                    if key not in {'CARGO_INCREMENTAL', 'CI', 'GITHUB_ACTIONS'}}
        cases = [({}, '0'), ({'CARGO_INCREMENTAL': '1'}, '1'), ({'CARGO_INCREMENTAL': ''}, ''),
                 ({'CI': 'true'}, None), ({'GITHUB_ACTIONS': 'true'}, None)]
        for overrides, expected in cases:
            child_values = []
            with (self.subTest(overrides=overrides), patch.dict(os.environ, base_env | overrides, clear=True),
                  patch.object(harness, 'ci_provenance', return_value=None),
                  patch.object(harness, 'maintain_automatically')):
                code, _ = self.run_harness(
                    ['--profile', 'fast'], before_check=lambda _, env: child_values.append(env.get('CARGO_INCREMENTAL')))
                self.assertEqual(0, code)
                self.assertTrue(child_values)
                self.assertEqual([expected] * len(child_values), child_values)
                self.assertEqual(overrides.get('CARGO_INCREMENTAL'), os.environ.get('CARGO_INCREMENTAL'))

    def test_invalid_verification_selection_skips_storage_maintenance(self):
        with patch.object(harness, 'maintain_automatically') as maintain:
            with self.assertRaisesRegex(ProjectError, 'Unknown task'):
                self.run_harness(['--task', 'M0-999'])
            maintain.assert_not_called()

    def test_selecting_planned_work_returns_blocked_and_nonzero(self):
        code, report = self.run_harness(['--task', 'M1-002'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertEqual({'environment', 'project-state', 'market-fixtures'}, set(report['selection']['checks']))

    def test_change_runs_exclude_expensive_release_checks_but_task_selection_can_include_them(self):
        project = load_project(self.root)
        self.assertNotIn('runtime-stability', harness.select_checks(project))
        self.assertIn('runtime-stability', harness.select_checks(project, profile='release'))
        self.assertIn('runtime-stability', harness.select_checks(project, task_id='M5-001'))
        code, report = self.run_harness(['--profile', 'release'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])

    def test_fast_run_stops_after_failure_and_preserves_the_omissions(self):
        def failed(name, command, env, run_dir, timeout):
            return {'id': name, 'status': 'failed', 'command': command, 'exit_code': 2}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=failed) as executed,
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(sys, 'argv', ['check.py', '--profile', 'fast']), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        self.assertEqual(1, stopped.exception.code)
        self.assertEqual(1, executed.call_count)
        report = read_json(self.root / '.reports/test-report.json')
        self.assertEqual('failed', report['status'])
        self.assertEqual('earlier_failure', next(item for item in report['checks'] if item['id'] == 'project-state')['reason_code'])

    def test_require_windows_cannot_be_satisfied_by_a_fast_linux_run(self):
        code, report = self.run_harness(['--profile', 'fast', '--require-windows'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertFalse(report['native_requirement']['satisfied'])
        self.assertFalse(report['product_complete'])

    def test_required_check_cannot_be_demoted_deleted_or_moved_to_release(self):
        path = self.root / 'project/checks.json'
        original = path.read_bytes()
        for mutation in ['demote', 'delete', 'release']:
            with self.subTest(mutation=mutation):
                path.write_bytes(original)
                catalog = read_json(path)
                check = next(item for item in catalog['checks'] if item['id'] == 'rust-lints')
                if mutation == 'demote':
                    check.update(status='planned', command=None)
                elif mutation == 'delete':
                    catalog['checks'].remove(check)
                else:
                    check['cadence'] = 'release'
                path.write_bytes(json_bytes(catalog))
                with self.assertRaisesRegex(ProjectError, 'Required change checks'):
                    self.run_harness(['--require-windows'], system='Windows')

    def test_integration_rejects_selection_omission_on_both_platforms(self):
        project = load_project(self.root)
        selected = harness.select_checks(project) - {'rust-lints'}
        for system, args in [('Linux', []), ('Windows', ['--require-windows'])]:
            with self.subTest(system=system), patch.object(harness, 'select_checks', return_value=selected):
                code, report = self.run_harness(args, system=system)
                self.assertEqual(1, code)
                self.assertEqual('blocked', report['status'])
                self.assertIn('rust-lints', report['integration_gates']['unverified'])

    def test_future_planned_checks_do_not_block_native_change_integration(self):
        code, report = self.run_harness(['--require-windows'], system='Windows')
        self.assertEqual(0, code)
        self.assertEqual('passed', report['integration_gates']['status'])
        self.assertFalse(report['product_complete'])
        code, report = self.run_harness(['--profile', 'fast'], system='Windows')
        self.assertEqual(0, code)
        self.assertEqual('not_requested', report['integration_gates']['status'])
        code, report = self.run_harness(['--profile', 'fast', '--require-windows'], system='Windows')
        self.assertEqual(1, code)

    def test_narrow_profiles_on_windows_report_native_and_release_checks_as_omitted(self):
        path = self.root / 'project/checks.json'
        fixture = read_json(path)
        production = load_project(harness.ROOT)
        profiles = {name: production['catalog']['profiles'][name]
                    for name in ('records', 'tooling', 'localization')}
        added = set().union(*profiles.values()) - {check['id'] for check in fixture['checks']}
        fixture['checks'].extend(production['checks'][key] for key in sorted(added))
        fixture['required_change_checks'].extend(sorted(added))
        fixture['profiles'].update(profiles)
        path.write_bytes(json_bytes(fixture))

        for profile, selected in profiles.items():
            with self.subTest(profile=profile):
                code, report = self.run_harness(['--profile', profile], system='Windows')
                self.assertEqual(0, code)
                self.assertEqual('passed', report['status'])
                self.assertEqual('Windows', report['environment']['system'])
                self.assertEqual({'profile': profile, 'task_id': None, 'checks': sorted(selected)}, report['selection'])
                self.assertFalse(report['native_requirement']['requested'])
                self.assertEqual('not_requested', report['integration_gates']['status'])
                self.assertIn('windows-native-build', report['integration_gates']['unverified'])
                self.assertFalse(report['product_complete'])
                self.assertIsNone(report['candidate_artifacts'])
                observed = {item['id']: item for item in report['checks']}
                self.assertEqual(set(selected), {key for key, item in observed.items() if item['status'] == 'passed'})
                self.assertEqual({key for key in observed if key not in selected},
                                 {key for key, item in observed.items() if item.get('reason_code') == 'not_selected'})
                self.assertEqual([sys.executable, 'scripts/dependencies.py'], observed['dependency-review']['command'])
                for key in ('windows-native-build', 'release-artifacts'):
                    self.assertEqual('blocked', observed[key]['status'])
                    self.assertNotIn('exit_code', observed[key])

        code, report = self.run_harness(['--profile', 'localization', '--require-windows'], system='Windows')
        self.assertEqual(1, code)
        self.assertFalse(report['native_requirement']['satisfied'])
        self.assertEqual('blocked', report['integration_gates']['status'])

    def replace_gate(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        old = next(item for item in catalog['checks'] if item['id'] == 'rust-lints')
        replacement = dict(old, id='replacement-lints')
        old.update(status='retired', command=None)
        catalog['checks'].append(replacement)
        for ids in [catalog['required_change_checks'], *[gate['checks'] for gate in catalog['milestones']]]:
            ids[:] = ['replacement-lints' if key == 'rust-lints' else key for key in ids]
        path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task.update(status='active', checks=['project-state', 'replacement-lints'])
        self.save_ledger(ledger)
        return path, catalog

    def test_history_prevents_removing_both_a_gate_and_its_required_binding(self):
        base = self.commit_fixture()
        self.replace_gate()
        with self.assertRaisesRegex(ProjectError, 'Established integration gate removed'):
            review_history(self.root, base)

    def test_history_allows_task_bound_replacement_and_preserves_its_rationale(self):
        base = self.commit_fixture()
        path, catalog = self.replace_gate()
        catalog['gate_changes'].append({'removed_check': 'rust-lints', 'replacement_checks': ['replacement-lints'],
                                        'task_id': 'M0-001', 'reason': 'Synthetic replacement exercises the same failure boundary.'})
        path.write_bytes(json_bytes(catalog))
        self.assertEqual(1, len(review_history(self.root, base)['gate_replacements']))
        replaced = self.commit_fixture()
        catalog['gate_changes'][0]['reason'] = 'Rewrite the accepted rationale.'
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'Accepted gate replacement changed'):
            review_history(self.root, replaced)

    def test_split_gate_can_be_replaced_again_from_either_review_base(self):
        original = self.commit_fixture()
        path, catalog = self.replace_gate()
        first = {'removed_check': 'rust-lints', 'replacement_checks': ['replacement-lints', 'project-state'],
                 'task_id': 'M0-001', 'reason': 'Check source and project consistency separately.'}
        catalog['gate_changes'].append(first)
        path.write_bytes(json_bytes(catalog))
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        intermediate = self.commit_fixture()

        check = next(item for item in catalog['checks'] if item['id'] == 'replacement-lints')
        catalog['checks'].append(dict(check, id='final-lints'))
        check.update(status='retired', command=None)
        milestone = next(item for item in catalog['milestones'] if item['id'] == 'M0')
        for ids in [catalog['required_change_checks'], milestone['checks']]:
            ids[ids.index('replacement-lints')] = 'final-lints'
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-002')
        task.update(status='active', checks=['project-state', 'final-lints'],
                    plan=task_by_id(ledger, 'M0-001')['plan'])
        self.save_ledger(ledger)
        second = {'removed_check': 'replacement-lints', 'replacement_checks': ['final-lints'],
                  'task_id': 'M0-002', 'reason': 'Preserve the source checks through the next cutover.'}
        catalog['gate_changes'].append(second)
        path.write_bytes(json_bytes(catalog))
        self.assertEqual([first, second], review_history(self.root, original)['gate_replacements'])
        self.assertEqual([second], review_history(self.root, intermediate)['gate_replacements'])

        # A surviving branch of the split cannot cover a missing or unreviewed branch.
        task['status'] = 'planned'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'without a reviewed replacement'):
            review_history(self.root, original)
        catalog['gate_changes'].remove(second)
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'without a reviewed replacement'):
            review_history(self.root, original)

    def test_circular_gate_substitutions_fail_even_with_a_required_leaf(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        catalog['gate_changes'] = [
            {'removed_check': 'rust-lints', 'replacement_checks': ['project-state'],
             'task_id': 'M0-001', 'reason': 'Synthetic first step.'},
            {'removed_check': 'project-state', 'replacement_checks': ['rust-lints', 'environment'],
             'task_id': 'M0-001', 'reason': 'Synthetic return to an earlier gate.'},
        ]
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['checks'].append('environment')
        self.save_ledger(ledger)
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'cycle'):
            load_project(self.root)

    def test_retiring_a_work_classification_preserves_closed_plans_and_checks_open_ones(self):
        path = self.root / 'project/delivery.json'
        policy = read_json(path)
        legacy = dict(policy['change_types'][0], id='old-maintenance')
        policy['change_types'].append(legacy)
        path.write_bytes(json_bytes(policy))
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task['plan']['change_types'] = ['old-maintenance']
        task_by_id(ledger, 'M0-004').update(status='cancelled', closure_reason='Superseded.',
                                             plan=task['plan'])
        self.save_ledger(ledger)
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        accepted = self.commit_fixture()

        policy['change_types'].remove(legacy)
        path.write_bytes(json_bytes(policy))
        self.assertEqual('passed', review_history(self.root, accepted)['status'])
        ledger = self.ledger()
        task_by_id(ledger, 'M0-002')['plan'] = task_by_id(ledger, 'M0-001')['plan']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unknown references.*old-maintenance'):
            load_project(self.root)

    def test_cancelled_history_does_not_prevent_a_delivered_replacement_from_passing(self):
        project = load_project(self.root)
        # Synthetic states test aggregation; real task completion is validated separately.
        for task in project['tasks'].values():
            if task['milestone'] == 'M0':
                task['status'] = 'done' if task['id'] == 'M0-004' else 'cancelled'
        results = [{'id': key, 'status': 'passed'} for key in project['checks']]
        milestone = product_progress(project, results)[0][0]
        self.assertEqual('passed', milestone['status'])
        self.assertIn('M0-001', milestone['cancelled_task_ids'])
        project['tasks']['M0-004']['status'] = 'cancelled'
        self.assertEqual('blocked', product_progress(project, results)[0][0]['status'])

    def test_failed_run_cannot_archive_even_when_task_checks_pass(self):
        path = self.report()
        report = read_json(path)
        report['checks'].append({'id': 'environment', 'status': 'failed', 'command': [], 'exit_code': 1})
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'failed verification run'):
            archive_evidence(self.root, 'M0-001', path)

    def test_active_work_needs_a_plan_and_completed_dependencies(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['status'] = 'active'
        del task_by_id(ledger, 'M0-001')['plan']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'needs a delivery plan'):
            load_project(self.root)
        task_by_id(ledger, 'M0-001')['status'] = 'planned'
        task_by_id(ledger, 'M0-003')['status'] = 'active'
        ledger['focus_task_id'] = 'M0-003'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unfinished dependencies'):
            load_project(self.root)

    def test_proposed_decision_cannot_authorize_active_work(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['status'] = 'active'
        self.save_ledger(ledger)
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['status'] = 'proposed'
        path.write_bytes(json_bytes(decision))
        with self.assertRaisesRegex(ProjectError, 'unaccepted decision'):
            load_project(self.root)

    def test_uncovered_risks_cannot_be_hidden_in_a_task_plan(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['plan']['risks'].append({'failure': 'Unverified installation.', 'checks': ['windows-installation']})
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'uncovered risks'):
            load_project(self.root)

    def test_context_and_scenarios_identify_native_and_mock_boundaries(self):
        context = task_context(self.root, 'M0-003')
        self.assertTrue(any(check['platforms'] == 'windows-x64' for check in context['checks']))
        context = task_context(self.root, 'M4-001')
        self.assertTrue(any('mock evaluation' in scenario['evidence_boundary'] for scenario in context['scenarios']))

    def test_scenario_cannot_name_checks_without_a_delivery_task(self):
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['scenarios'][0]['checks'].append('release-artifacts')
        path.write_bytes(json_bytes(delivery))
        with self.assertRaisesRegex(ProjectError, 'without a delivery task'):
            load_project(self.root)

    def feedback(self, status='open'):
        item = {'id': 'fixture-defect', 'source': 'Synthetic test observation.', 'observation': 'A previous check missed a defect.',
                'severity': 'major', 'status': status, 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                'resolution': None, 'deferral_reason': None}
        if status == 'addressed':
            item['resolution'] = {'cause': 'Missing invariant.', 'correction': 'Validate the invariant.', 'prevention': 'Add regression evidence.'}
        return item

    def save_feedback(self, item):
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': [item]}))

    def test_feedback_cannot_be_closed_before_corrective_work(self):
        self.save_feedback(self.feedback('addressed'))
        with self.assertRaisesRegex(ProjectError, 'completed corrective work'):
            load_project(self.root)

    def test_resolved_feedback_has_regression_proof_without_invalidating_receipt(self):
        self.save_feedback(self.feedback())
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        item = self.feedback('addressed')
        self.save_feedback(item)
        self.assertTrue(workfront(self.root)['verification']['current'])
        item['regression_checks'] = ['environment']
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'lacks regression evidence'):
            load_project(self.root)

    def test_deferred_major_feedback_remains_a_release_blocker(self):
        item = self.feedback('deferred')
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'explicit reason'):
            load_project(self.root)
        item['deferral_reason'] = 'Needs a native target to investigate.'
        self.save_feedback(item)
        result = readiness(self.root)
        self.assertFalse(result['release_ready'])
        self.assertTrue(any('fixture-defect' in blocker for blocker in result['blockers']))

    def test_feedback_intake_and_active_plan_assign_each_regression_to_linked_work(self):
        item = self.feedback()
        item['regression_checks'] = ['environment']
        for severity, status in [('minor', 'open'), ('critical', 'deferred')]:
            item.update(severity=severity, status=status, deferral_reason='Needs a native target.' if status == 'deferred' else None)
            self.save_feedback(item)
            with self.subTest(severity=severity), self.assertRaisesRegex(ProjectError, 'fixture-defect.*without a linked task.*environment'):
                load_project(self.root)

        item.update(task_ids=['M0-001', 'M4-001'], regression_checks=['rust-lints', 'ai-workflow'])
        self.save_feedback(item)
        load_project(self.root)
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task['status'] = 'active'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, r"M0-001.*fixture-defect.*plan risks: \['rust-lints'\]"):
            load_project(self.root)
        item['severity'] = 'minor'
        self.save_feedback(item)
        load_project(self.root)
        item['severity'] = 'major'
        self.save_feedback(item)
        task['plan']['risks'].append({'failure': 'The reported fixture defect recurs.', 'checks': ['rust-lints']})
        self.save_ledger(ledger)
        load_project(self.root)

    def test_retiring_last_open_feedback_owner_requires_a_handoff(self):
        item = self.feedback()
        self.save_feedback(item)
        observed = self.commit_fixture()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))

        with self.assertRaisesRegex(ProjectError, 'fixture-defect.*unfinished corrective task'):
            project_cli.local_feedback_history(self.root, load_project(self.root))
        with self.assertRaisesRegex(ProjectError, 'fixture-defect.*unfinished corrective task'):
            review_history(self.root, observed)

        # The original finding survives a handoff and its historical task stays in the record.
        self.save_feedback({**item, 'task_ids': ['M0-001', 'M0-002']})
        self.assertEqual('passed', review_history(self.root, observed)['status'])

        transferred = self.commit_fixture()
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'fixture-defect.*unfinished corrective task'):
            review_history(self.root, transferred)

        # A deliberate deferral stays visible with its reason and release consequences.
        self.save_feedback({**item, 'status': 'deferred', 'deferral_reason': 'Await a physical host.'})
        self.assertEqual('passed', review_history(self.root, observed)['status'])
        self.assertTrue(any(item['id'] in reason for reason in readiness(self.root)['blockers']))

    def test_working_feedback_checkpoint_is_checked_before_any_verification_commands(self):
        shutil.copytree(Path(harness.__file__).parent, self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        env = {**os.environ, 'SCOPEHUB_STORAGE': 'off', 'SCOPEHUB_REVIEW_BASE': '', 'SCOPEHUB_CHECKPOINT_BASE': ''}

        def invoke(script, *args, expected=0):
            result = subprocess.run([sys.executable, str(self.root / 'scripts' / script), *args], cwd=self.root,
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(expected, result.returncode, (result.stdout, result.stderr))
            return result.stdout

        # An old archived receipt predates the finding and must not qualify the closure.
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.feedback())
        observed = self.commit_fixture()
        self.save_feedback(self.feedback('addressed'))
        runs = self.root / '.reports' / 'runs'
        original_runs = set(runs.iterdir())
        for script, args in [('project.py', ('check',)), ('check.py', ('--task', 'M0-001'))]:
            with self.subTest(script=script):
                result = json.loads(invoke(script, *args, expected=1))
                self.assertIn('Closing feedback fixture-defect requires new regression evidence', result['error'])
                if script == 'check.py':
                    self.assertTrue((self.root / result['recovery_snapshot']).is_file())
                self.assertEqual(original_runs, set(runs.iterdir()))

        # A known review base also detects an unsupported closure already committed on the branch.
        self.commit_fixture()
        env['SCOPEHUB_REVIEW_BASE'] = observed
        self.assertIn('new regression evidence', json.loads(invoke('project.py', 'check', expected=1))['error'])
        item = self.feedback()
        self.save_feedback(item)
        self.assertIn('requires a new occurrence', json.loads(invoke('project.py', 'check', expected=1))['error'])
        item['occurrence'] = 2
        self.save_feedback(item)
        invoke('check.py', '--task', 'M0-001')
        receipt = archive_evidence(self.root, 'M0-001', self.root / '.reports' / 'test-report.json')
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['evidence'].append(receipt)
        self.save_ledger(ledger)
        self.save_feedback(dict(self.feedback('addressed'), occurrence=2))
        verified = json.loads(invoke('project.py', 'check'))
        self.assertEqual(2, len(verified['feedback_history']['compared_commits']))
        invoke('check.py', '--task', 'M0-001')
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': []}))
        self.assertIn('Feedback disappeared: fixture-defect', json.loads(invoke('project.py', 'check', expected=1))['error'])

    def test_feedback_checkpoint_preserves_legacy_receipts_and_identifies_missing_local_history(self):
        project = load_project(self.root)
        with patch.dict(os.environ, {'SCOPEHUB_REVIEW_BASE': '', 'SCOPEHUB_CHECKPOINT_BASE': ''}):
            self.assertEqual('not_applicable', project_cli.local_feedback_history(self.root, project)['status'])
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.feedback('addressed'))
        legacy = self.commit_fixture()
        project = load_project(self.root)
        with patch.dict(os.environ, {'SCOPEHUB_REVIEW_BASE': legacy, 'SCOPEHUB_CHECKPOINT_BASE': legacy}):
            self.assertEqual([legacy], project_cli.local_feedback_history(self.root, project)['compared_commits'])
        with patch.dict(os.environ, {'SCOPEHUB_REVIEW_BASE': 'missing-review-base'}):
            with self.assertRaisesRegex(ProjectError, 'unavailable locally: missing-review-base'):
                project_cli.local_feedback_history(self.root, project)
        with tempfile.TemporaryDirectory() as outside:
            self.assertEqual('not_applicable', project_cli.local_feedback_history(Path(outside), project)['status'])
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'items': {}}))
        self.commit_fixture()
        self.save_feedback(self.feedback())
        with self.assertRaisesRegex(ProjectError, 'Invalid project/feedback.json at local feedback checkpoint'):
            project_cli.local_feedback_history(self.root, load_project(self.root))

    def test_two_parent_feedback_import_is_confined_to_exact_reviewed_dispositions(self):
        def git(*args):
            return subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                                   '-c', 'commit.gpgsign=false', *args], cwd=self.root, check=True, capture_output=True).stdout

        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        fork = self.commit_fixture()
        git('checkout', '-qb', 'fixture-feature')
        (self.root / 'feature.txt').write_text('Independent branch.\n')
        absent = self.commit_fixture()
        self.save_feedback(self.feedback())
        open_checkpoint = self.commit_fixture()
        git('checkout', '-qb', 'fixture-reviewed', fork)
        accepted = self.feedback('addressed')
        self.save_feedback(accepted)
        reviewed = self.commit_fixture()
        git('checkout', '-qb', 'fixture-merge', absent)
        git('merge', '--no-edit', '--no-ff', reviewed)
        self.assertEqual([absent, reviewed], git('show', '-s', '--format=%P', 'HEAD').decode().split())

        def checks(base=reviewed, checkpoint=absent, *, error=None):
            env = {'SCOPEHUB_REVIEW_BASE': base, 'SCOPEHUB_CHECKPOINT_BASE': checkpoint}
            with patch.dict(os.environ, env):
                for command in (lambda: project_cli.local_feedback_history(self.root, load_project(self.root)),
                                lambda: review_history(self.root, base, checkpoint_base=checkpoint)):
                    if error:
                        with self.assertRaisesRegex(ProjectError, error):
                            command()
                    else:
                        self.assertEqual('passed', command()['status'])

        checks()
        checks(checkpoint=open_checkpoint, error='new regression evidence')
        checks(base='HEAD', error='new regression evidence')
        checks(base='', error='new regression evidence')
        with patch.dict(os.environ, {'SCOPEHUB_SOURCE_HEAD': reviewed}):
            checks(error='new regression evidence')
        # A source-label edit also defeats the import exception although the regression fingerprint is unchanged.
        self.save_feedback(dict(accepted, source='Edited on the candidate branch.'))
        checks(error='new regression evidence')
        self.save_feedback(self.feedback())
        checks(error='requires a new occurrence')
        self.save_feedback(dict(accepted, occurrence=2))
        checks(error='new regression evidence')
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': []}))
        checks(error='Feedback disappeared')

    def legacy_feedback(self, status='open'):
        item = self.feedback(status)
        item['source'] = 'Original check at .reports/runs/field-1/test-report.json.'
        item['diagnostic_reports'] = [{'run_id': 'field-1', 'source_commit': '1' * 40, 'report_sha256': '2' * 64}]
        return item

    def feedback_rule_cutover(self):
        """Build two protected merges around an independently selected receipt-format introduction."""
        def git(*args):
            return subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                                   '-c', 'commit.gpgsign=false', *args], cwd=self.root, capture_output=True,
                                  check=True, text=True).stdout.strip()

        before = self.commit_fixture()
        git('checkout', '-qb', 'receipt-rule')
        (self.root / 'rule.txt').write_text('Synthetic verification rule.\n')
        self.commit_fixture()
        git('checkout', '-qb', 'protected', before)
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.legacy_feedback())
        observed = self.commit_fixture()
        self.save_feedback(self.legacy_feedback('addressed'))
        last_legacy = self.commit_fixture()
        git('merge', '--quiet', '--no-ff', '--no-edit', 'receipt-rule')
        introduction = git('rev-parse', 'HEAD')
        self.assertEqual(last_legacy, git('rev-parse', 'HEAD^1'))
        self.enterContext(patch.object(project_cli, 'FEEDBACK_RECEIPT_INTRODUCTION', introduction))
        # The candidate is a synthetic PR merge with the rule on its base parent.
        git('checkout', '-qb', 'candidate', last_legacy)
        (self.root / 'feature.txt').write_text('Separate feature.\n')
        self.commit_fixture()
        git('checkout', '-q', 'protected')
        git('merge', '--quiet', '--no-ff', '--no-edit', 'candidate')
        return git, before, observed, last_legacy, introduction

    def check_feedback_range(self, base, checkpoint=None, *, error=None):
        env = {'SCOPEHUB_REVIEW_BASE': base, 'SCOPEHUB_CHECKPOINT_BASE': checkpoint or ''}
        with patch.dict(os.environ, env):
            for command in (lambda: review_history(self.root, base, checkpoint_base=checkpoint),
                            lambda: project_cli.local_feedback_history(self.root, load_project(self.root))):
                if error:
                    with self.assertRaisesRegex(ProjectError, error):
                        command()
                else:
                    self.assertEqual('passed', command()['status'])

    def test_feedback_range_crosses_protected_rule_introduction_with_exact_legacy_rows(self):
        git, before, observed, last_legacy, introduction = self.feedback_rule_cutover()
        for base in (before, observed, last_legacy, introduction):
            with self.subTest(base=base):
                self.check_feedback_range(base)
        self.check_feedback_range(observed, before)
        accepted = self.legacy_feedback('addressed')
        # An already closed observation may relocate its source without pretending to recur.
        # Inheriting that same legacy resolution for a newly seen closure needs the exact row.
        self.save_feedback({**accepted, 'source': 'Verified retained public observation.'})
        self.check_feedback_range(introduction)
        self.check_feedback_range(last_legacy)
        self.check_feedback_range(observed, error='new regression evidence')
        for edit in ({'source': 'Edited source.'}, {'observation': 'Different failure.'}, {'occurrence': 2},
                     {'resolution': {**accepted['resolution'], 'cause': 'Edited after review.'}}):
            with self.subTest(edit=edit):
                self.save_feedback({**accepted, **edit})
                self.check_feedback_range(before, error='new regression evidence')
        self.save_feedback(self.legacy_feedback())
        self.check_feedback_range(before, error='requires a new occurrence')
        self.save_feedback({**self.legacy_feedback(), 'occurrence': 2})
        self.check_feedback_range(before)
        self.save_feedback(dict(accepted, id='new-major-closure'))
        self.check_feedback_range(before, error='Feedback disappeared')
        path = self.root / 'project/feedback.json'
        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': [accepted, dict(accepted, id='new-major-closure')]}))
        self.check_feedback_range(before, error='Closing feedback new-major-closure requires new regression evidence')

    def test_legacy_disposition_cannot_overrule_a_later_checkpoint_or_working_candidate(self):
        git, before, observed, last_legacy, introduction = self.feedback_rule_cutover()
        candidate = git('rev-parse', 'HEAD')
        accepted = self.legacy_feedback('addressed')
        for row, message in ((self.legacy_feedback(), 'new regression evidence'),
                             ({**self.legacy_feedback(), 'occurrence': 2}, 'occurrence cannot go backwards'),
                             (None, 'new regression evidence')):
            with self.subTest(row=row):
                git('checkout', '--quiet', '--detach', introduction)
                if row:
                    self.save_feedback(row)
                else:
                    (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': []}))
                post_rule = self.commit_fixture()
                # A dirty restoration over the current candidate must also be checked.
                self.save_feedback(accepted)
                self.check_feedback_range(before, error=message)
                git('checkout', '--quiet', '--detach', '--force', candidate)
                for prior in (observed, last_legacy, introduction):
                    self.check_feedback_range(prior, post_rule, error=message)
                    self.check_feedback_range(post_rule, prior, error=message)

    def test_long_review_preserves_the_closing_task_and_its_original_receipt(self):
        git, before, observed, last_legacy, introduction = self.feedback_rule_cutover()
        second = archive_evidence(self.root, 'M0-001', self.report(system='Windows'))
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task['evidence'].append(second)
        self.save_ledger(ledger)
        self.check_feedback_range(before)
        original = task['evidence'][:]
        for replacement in ([second], list(reversed(original))):
            with self.subTest(replacement=replacement):
                task['evidence'] = replacement
                self.save_ledger(ledger)
                self.check_feedback_range(before, error='Accepted task history changed')
        task['evidence'] = original
        task['closure_reason'] = 'Rewritten historical disposition.'
        self.save_ledger(ledger)
        self.check_feedback_range(before, error='Accepted task history changed')
        task['title'] += ' Edited after acceptance'
        self.save_ledger(ledger)
        self.check_feedback_range(before, error='lacks passing evidence for its definition')

    def test_verified_source_relocation_remains_auditable_after_another_protected_merge(self):
        git, before, observed, last_legacy, introduction = self.feedback_rule_cutover()
        accepted = self.legacy_feedback('addressed')
        source = accepted['source'].replace('.reports/', 'archives/')
        git('checkout', '-qb', 'relocate')
        self.save_feedback({**accepted, 'source': source})
        self.check_feedback_range(introduction)
        self.commit_fixture()
        git('checkout', '--quiet', 'protected')
        git('merge', '--quiet', '--no-ff', '--no-edit', 'relocate')
        self.check_feedback_range(before, observed)
        self.check_feedback_range(last_legacy)
        for edited in (source.replace('field-1', 'unverified'), source.replace('Original check', 'Another report'),
                       source.replace('archives/', 'vendor/archives/'), source.replace('.json.', '.json.copy.')):
            with self.subTest(edited=edited):
                self.save_feedback({**accepted, 'source': edited})
                with self.assertRaisesRegex(ProjectError, 'new regression evidence|nonlocal report path'):
                    review_history(self.root, before)
        for field, value in (('source_commit', '3' * 40), ('report_sha256', '4' * 64)):
            with self.subTest(field=field):
                self.save_feedback({**accepted, 'source': source,
                                    'diagnostic_reports': [{**accepted['diagnostic_reports'][0], field: value}]})
                self.check_feedback_range(before, error='new regression evidence')

    def test_unrelated_candidate_and_shallow_history_cannot_claim_the_receipt_cutover(self):
        git, before, observed, last_legacy, introduction = self.feedback_rule_cutover()
        merged = git('rev-parse', 'HEAD')
        git('checkout', '--quiet', '--detach', observed)
        self.save_feedback(self.legacy_feedback('addressed'))
        (self.root / 'candidate-rule.txt').write_text('Candidate-declared cutover.\n')
        forged = self.commit_fixture()
        git('update-ref', 'refs/remotes/origin/main', forged)
        git('replace', forged, merged)
        with patch.dict(os.environ, {'SCOPEHUB_FEEDBACK_RECEIPT_INTRODUCTION': forged}):
            self.check_feedback_range(before, error='new regression evidence')
        git('replace', '-d', forged)
        git('checkout', '--quiet', '--detach', merged)
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': []}))
        removed = self.commit_fixture()
        git('checkout', '--quiet', '--detach', merged)
        # A separate review base has the row, but the second checkpoint's ancestry is cut off.
        (self.root / '.git/shallow').write_text(removed + '\n', encoding='ascii')
        self.check_feedback_range(last_legacy, removed, error='new regression evidence')
        # All objects are readable, but Git cannot prove the introduction's parent.
        (self.root / '.git/shallow').write_text(introduction + '\n', encoding='ascii')
        self.check_feedback_range(before, error='new regression evidence')
        self.check_feedback_range(merged)

    def test_default_feedback_preflight_uses_the_locally_observed_protected_merge_base(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, capture_output=True, check=True).stdout

        def check(*, expected=0):
            proc = subprocess.run([sys.executable, str(self.root / 'scripts/project.py'), 'check'], cwd=self.root,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(expected, proc.returncode, (proc.stdout, proc.stderr))
            return json.loads(proc.stdout)

        shutil.copytree(Path(harness.__file__).parent, self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        (self.root / 'project/branch-protection.json').write_bytes(json_bytes({'branch': 'fixture-main'}))
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.feedback())
        fork = self.commit_fixture()
        ref = 'refs/remotes/origin/fixture-main'
        git('update-ref', ref, fork)
        git('checkout', '-qb', 'fixture-work')
        (self.root / 'feature.txt').write_text('Work in progress.\n')
        legitimate = self.commit_fixture()
        self.save_feedback(self.feedback('addressed'))
        self.assertIn('new regression evidence', check(expected=1)['error'])
        self.commit_fixture()
        self.assertIn('new regression evidence', check(expected=1)['error'])
        git('checkout', '-qb', 'fixture-advanced-main', fork)
        pending = self.feedback()
        extra = dict(pending, id='only-on-advanced-main')
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': [pending, extra]}))
        advanced = self.commit_fixture()
        git('update-ref', ref, advanced)
        git('checkout', '--detach', legitimate)
        result = check()['feedback_history']
        self.assertEqual([fork, legitimate], result['compared_commits'])
        self.assertEqual({'source': 'local_protected_merge_base', 'ref': ref, 'tracking_commit': advanced, 'commit': fork},
                         result['review_basis'])
        git('update-ref', '-d', ref)
        result = check()['feedback_history']
        self.assertEqual([legitimate], result['compared_commits'])
        self.assertEqual(('none', ref), (result['review_basis']['source'], result['review_basis']['ref']))

    def test_feedback_intake_survives_history_and_stale_task_evidence(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.feedback())
        observed = self.commit_fixture()
        self.assertEqual(['fixture-defect'], workfront(self.root)['feedback_needing_assignment'])

        self.save_feedback(self.feedback('addressed'))
        with self.assertRaisesRegex(ProjectError, 'new regression evidence'):
            review_history(self.root, observed)

        # A run which saw the observation can be checkpointed before closure.
        self.save_feedback(self.feedback())
        code, verified = self.run_harness(('--task', 'M0-001'))
        self.assertEqual(0, code)
        self.assertTrue(verified['observed_feedback'])
        extra = archive_evidence(self.root, 'M0-001', self.root / '.reports/test-report.json')
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['evidence'].append(extra)
        self.save_ledger(ledger)
        checkpoint = self.commit_fixture()
        self.save_feedback(self.feedback('addressed'))
        self.assertEqual('passed', review_history(self.root, observed)['status'])
        self.assertEqual('passed', review_history(self.root, checkpoint)['status'])
        self.assertEqual([], workfront(self.root)['feedback_needing_assignment'])

        resolved = self.commit_fixture()
        amended = self.feedback('addressed')
        amended['observation'] += ' A distinct second failure was observed.'
        self.save_feedback(amended)
        for base in (observed, resolved):
            with self.subTest(base=base), self.assertRaisesRegex(ProjectError, 'new regression evidence'):
                review_history(self.root, base)

        reopened = self.feedback()
        self.save_feedback(reopened)
        with self.assertRaisesRegex(ProjectError, 'requires a new occurrence'):
            review_history(self.root, resolved)
        reopened['occurrence'] = 2
        self.save_feedback(reopened)
        self.assertEqual('passed', review_history(self.root, resolved)['status'])
        recurrence = self.commit_fixture()
        closed_again = dict(self.feedback('addressed'), occurrence=2)
        self.save_feedback(closed_again)
        for base in (resolved, recurrence):
            with self.subTest(base=base), self.assertRaisesRegex(ProjectError, 'new regression evidence'):
                review_history(self.root, base)
        self.save_feedback(self.feedback())
        with self.assertRaisesRegex(ProjectError, 'cannot go backwards'):
            review_history(self.root, recurrence)
        self.save_feedback(reopened)
        code, rerun = self.run_harness(('--task', 'M0-001'))
        self.assertEqual(0, code)
        self.assertNotEqual(verified['observed_feedback'], rerun['observed_feedback'])
        receipt = archive_evidence(self.root, 'M0-001', self.root / '.reports/test-report.json')
        task_by_id(ledger, 'M0-001')['evidence'].append(receipt)
        self.save_ledger(ledger)
        self.save_feedback(closed_again)
        self.assertEqual('passed', review_history(self.root, recurrence)['status'])

        path = self.root / 'project/feedback.json'
        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': []}))
        with self.assertRaisesRegex(ProjectError, 'Feedback disappeared: fixture-defect'):
            review_history(self.root, observed)

    def test_newly_recorded_resolution_requires_post_base_regression_evidence(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        before_finding = self.commit_fixture()
        self.save_feedback(self.feedback('addressed'))
        with self.assertRaisesRegex(ProjectError, 'new regression evidence'):
            review_history(self.root, before_finding)

    def test_history_rejects_rewritten_decisions_and_reopened_completed_tasks(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit_fixture()
        self.assertEqual('passed', review_history(self.root, base)['status'])
        self.edit_decision()
        with self.assertRaisesRegex(ProjectError, 'Accepted decision history changed'):
            review_history(self.root, base)
        subprocess.run(['git', 'restore', 'contracts/decisions/0003-repository-harness.json'],
                       cwd=self.root, check=True, capture_output=True)
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='planned', next_action='Pretend the completed task is new.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history changed'):
            review_history(self.root, base)

    def test_git_subprocess_cannot_borrow_another_checkout_for_feedback_history_or_evidence(self):
        shutil.copytree(Path(harness.__file__).parent, self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        probe = next(row for row in catalog['checks'] if row['id'] == 'rust-lints')
        probe['command'] = ['{python}', '-c', 'from pathlib import Path; import subprocess; '
                            'root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).decode().strip(); '
                            'raise SystemExit(Path(root).resolve() != Path.cwd().resolve())']
        catalog_path.write_bytes(json_bytes(catalog))
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.save_feedback(self.feedback())
        target_head = self.commit_fixture()
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / 'caller'
            subprocess.run(['git', 'clone', '--quiet', '--no-local', str(self.root), str(other)], check=True)
            self.save_feedback(self.feedback('addressed'))
            shutil.copyfile(self.root / 'project/feedback.json', other / 'project/feedback.json')
            subprocess.run(['git', 'add', 'project/feedback.json'], cwd=other, check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve a distinct legacy disposition'],
                           cwd=other, check=True)

            def from_caller(script, *args, code=0, root=self.root, isolated=False):
                command = [Path(sys.executable).as_posix(), *(['-I', '-S'] if isolated else []),
                           (root / 'scripts' / script).as_posix(), *args]
                alias = f'!cd {shlex.quote(root.as_posix())} && ' + shlex.join(command)
                result = subprocess.run(['git', '--git-dir=' + (other / '.git').as_posix(), '--work-tree=' + other.as_posix(),
                                         '-c', 'alias.verify-target=' + alias, 'verify-target'], cwd=other,
                                        capture_output=True, text=True, timeout=30, env={**os.environ, 'SCOPEHUB_STORAGE': 'off'})
                self.assertEqual(code, result.returncode, (result.stdout, result.stderr))
                return result.stdout

            for checkout, repo, account in ((self.root, 'fixture/target', 'target-user'), (other, 'fixture/caller', 'caller-user')):
                subprocess.run(['git', 'config', '--local', 'remote.origin.url', f'https://github.com/{repo}.git'], cwd=checkout, check=True)
                subprocess.run(['git', 'config', '--local', 'github.account', account], cwd=checkout, check=True)
            real_command = ci.command
            def github(argv, root):
                if argv[0] == 'git':
                    return real_command(argv, root)
                if argv[1:3] == ['auth', 'switch']:
                    self.assertEqual('target-user', argv[-1])
                return 'target-user'
            with (patch.dict(os.environ, {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}),
                  patch.object(ci, 'command', side_effect=github)):
                self.assertEqual('fixture/target', ci.github_identity(self.root))

            rejected = json.loads(from_caller('project.py', 'check', code=1))
            self.assertIn('Closing feedback fixture-defect requires new regression evidence', rejected['error'])
            self.assertIn('new regression evidence', json.loads(from_caller('check.py', '--task', 'M0-001', code=1))['error'])
            self.save_feedback(self.feedback())
            (self.root / 'implementation.py').write_text('value = 2\n')
            self.assertEqual([target_head], json.loads(from_caller('project.py', 'check'))['feedback_history']['compared_commits'])
            self.assertEqual(target_head, json.loads(from_caller('project.py', 'history', '--base', 'HEAD'))['base_commit'])
            self.assertEqual(target_head, json.loads(from_caller('recovery.py', isolated=True))['git']['head'])
            self.assertEqual(target_head, json.loads(from_caller('verification_scope.py', '--base', 'HEAD'))['checkout'])

            from_caller('check.py', '--task', 'M0-001')
            report = self.root / '.reports/test-report.json'
            source = read_json(report)['source']
            self.assertEqual(source_identity(self.root), source)
            self.assertTrue(source['working_tree_dirty'])
            receipt = json.loads(from_caller('project.py', 'record', 'M0-001', '--report', report.as_posix()))
            self.assertEqual(source, read_json(self.root / receipt['path'])['source'])

            # The target can also be a registered Git worktree with a .git file and a shared object store.
            linked = Path(directory) / 'linked'
            subprocess.run(['git', 'worktree', 'add', '--quiet', '--detach', str(linked), target_head], cwd=self.root, check=True)
            self.assertTrue((linked / '.git').is_file())
            state = json.loads(from_caller('project.py', 'status', root=linked))
            self.assertEqual(target_head, state['source']['git_head'])
            self.assertFalse(state['source']['working_tree_dirty'])

    def test_history_json_is_read_as_utf8_under_a_legacy_windows_locale(self):
        base = self.commit_fixture()
        original_run = subprocess.run

        def legacy_locale(argv, **kwargs):
            if argv[:2] == ['git', 'show'] and kwargs.get('text') and not kwargs.get('encoding'):
                kwargs['encoding'] = 'cp1252'
            return original_run(argv, **kwargs)

        with patch('scripts.project.subprocess.run', side_effect=legacy_locale):
            self.assertEqual('passed', review_history(self.root, base)['status'])

    def test_hosted_history_recovers_force_pushed_checkpoint_and_enforces_its_history(self):
        base = self.commit_fixture()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        checkpoint = self.commit_fixture()
        subprocess.run(['git', 'reset', '--hard', base], cwd=self.root, check=True, capture_output=True)
        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        self.commit_fixture()

        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'checkout'

            def git(*args, check=True):
                return subprocess.run(['git', *args], cwd=checkout, capture_output=True, check=check)

            subprocess.run(['git', 'clone', '--no-local', self.root.as_uri(), str(checkout)],
                           check=True, capture_output=True)
            repository = 'fixture/base-repository'
            git('remote', 'set-url', 'origin', 'https://example.invalid/fixture/fork.git')
            git('config', '--local', f'url.{self.root.as_uri()}.insteadOf', f'https://github.com/{repository}.git')
            self.assertNotEqual(0, git('cat-file', '-t', checkpoint, check=False).returncode)
            refs = git('show-ref').stdout
            env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': repository,
                   'SCOPEHUB_REVIEW_BASE': base, 'SCOPEHUB_CHECKPOINT_BASE': checkpoint}
            output = StringIO()
            with (patch.object(project_cli, 'ROOT', checkout), patch.dict(os.environ, env),
                  patch.object(sys, 'argv', ['project.py', 'history', '--fetch-missing-checkpoint']),
                  redirect_stdout(output), self.assertRaises(SystemExit) as stopped):
                project_cli.main()
            self.assertEqual(1, stopped.exception.code)
            self.assertIn('Accepted task history changed or disappeared: M0-001', json.loads(output.getvalue())['error'])
            self.assertEqual(b'commit', git('cat-file', '-t', checkpoint).stdout.strip())
            self.assertEqual(refs, git('show-ref').stdout)
            self.assertFalse((checkout / '.git/FETCH_HEAD').exists())

            with patch.object(project_cli.subprocess, 'run', wraps=subprocess.run) as git_calls:
                recover_hosted_checkpoint(checkout, checkpoint, repository)
                self.assertEqual(1, git_calls.call_count)
            with self.assertRaisesRegex(ProjectError, 'Cannot retrieve previous checkpoint'):
                recover_hosted_checkpoint(checkout, '1' * 40, repository)

    def test_hosted_checkpoint_rejects_non_sha_revisions_and_non_commit_objects(self):
        self.commit_fixture()
        blob = subprocess.run(['git', 'rev-parse', 'HEAD:implementation.py'], cwd=self.root,
                              check=True, capture_output=True, text=True).stdout.strip()
        with patch.object(project_cli.subprocess, 'run', wraps=subprocess.run) as git_calls:
            for revision in ('--upload-pack=bad', 'HEAD', blob[:8], f'{blob}^{{object}}'):
                with self.subTest(revision=revision), self.assertRaisesRegex(ProjectError, 'full previous checkpoint SHA'):
                    recover_hosted_checkpoint(self.root, revision, 'fixture/repo')
            with self.assertRaisesRegex(ProjectError, 'GitHub verification repository'):
                recover_hosted_checkpoint(self.root, blob, 'fixture/repo?query=1')
            recover_hosted_checkpoint(self.root, '0' * 40, 'fixture/repo')
            git_calls.assert_not_called()
            with self.assertRaisesRegex(ProjectError, 'not an available Git commit'):
                recover_hosted_checkpoint(self.root, blob, 'fixture/repo')
            self.assertEqual(1, git_calls.call_count)

    def append_native_receipt(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit_fixture()
        ref = archive_evidence(self.root, 'M0-001', self.report(system='Windows'))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['evidence'].append(ref)
        self.save_ledger(ledger)
        return base, ref

    def test_history_allows_validated_append_only_receipts_on_completed_task(self):
        base, _ = self.append_native_receipt()
        self.assertEqual('passed', review_history(self.root, base)['status'])

    def test_history_rejects_evidence_removal_reorder_replacement_and_state_changes(self):
        base, _ = self.append_native_receipt()
        original = self.ledger()
        for change in ['remove', 'reorder', 'replace', 'state']:
            with self.subTest(change=change):
                ledger = read_json(self.root / 'project/tasks.json')
                task = task_by_id(ledger, 'M0-001')
                evidence = task['evidence']
                if change == 'remove': evidence.pop(0)
                elif change == 'reorder': evidence.reverse()
                elif change == 'replace': evidence[0] = evidence[-1]
                else: task['closure_reason'] = 'Retroactive explanation'
                self.save_ledger(ledger)
                with self.assertRaisesRegex(ProjectError, 'Accepted task history'):
                    review_history(self.root, base)
                self.save_ledger(original)

    def test_history_rejects_appended_receipt_for_different_definition_or_missing_checks(self):
        base, ref = self.append_native_receipt()
        path = self.root / ref['path']
        original = path.read_bytes()
        for field in ['task_definition_sha256', 'checks']:
            with self.subTest(field=field):
                receipt = read_json(path)
                receipt[field] = '0' * 64 if field == 'task_definition_sha256' else receipt['checks'][:1]
                path.write_bytes(json_bytes(receipt))
                ledger = self.ledger()
                task_by_id(ledger, 'M0-001')['evidence'][-1]['sha256'] = digest(path.read_bytes())
                self.save_ledger(ledger)
                with self.assertRaisesRegex(ProjectError, 'Appended evidence'):
                    review_history(self.root, base)
                path.write_bytes(original)

    def test_history_keeps_cancelled_records_immutable(self):
        ref = archive_evidence(self.root, 'M0-001', self.report())
        extra = archive_evidence(self.root, 'M0-001', self.report(system='Windows'))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='cancelled', evidence=[ref], next_action=None,
                                         closure_reason='Superseded fixture scope.')
        ledger['focus_task_id'] = 'M0-002'
        self.save_ledger(ledger)
        base = self.commit_fixture()
        task_by_id(ledger, 'M0-001')['evidence'].append(extra)
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history'):
            review_history(self.root, base)


    def test_missing_history_base_does_not_claim_comparison(self):
        self.assertEqual('not_applicable', review_history(self.root)['status'])
        with self.assertRaises(subprocess.CalledProcessError):
            review_history(self.root, 'missing-base-385ab')

    def test_unknown_selection_is_an_error(self):
        project = load_project(self.root)
        for args in [{'profile': 'typo'}, {'task_id': 'M0-999'}]:
            with self.subTest(args=args), self.assertRaisesRegex(ProjectError, 'Unknown'):
                harness.select_checks(project, **args)
