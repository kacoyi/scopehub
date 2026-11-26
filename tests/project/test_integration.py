"""Reject premature integration despite superficially successful CI."""

from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import integration, timing, verification_scope
from scripts.project import (
    ProjectError, archive_evidence, digest, feedback_fingerprint,
    json_bytes, load_project, read_json, source_identity,
)
from tests.project.test_continuity import ProjectFixture, REPO
from tests.project.fixtures import task_by_id


UBUNTU = 'verify (ubuntu-latest)'
WINDOWS = 'verify (windows-latest)'
UBUNTU_FULL = 'Run pnpm check'
WINDOWS_FULL = 'Run uv run python scripts/check.py --require-windows'
PR_BUDGET = 'verification-overrun-without-timely-remediation'
PUSH_BUDGET = 'verification-main-push-feedback-budget-overrun'
REPAIR_TASK = 'M0-087'
FOLLOW_UP_TASK = 'M0-091'


def step(name, *, status='completed', conclusion='success'):
    return {'name': name, 'status': status, 'conclusion': conclusion}


CHECKOUT_STEP = step('Run actions/checkout@' + '1' * 40)


def observations():
    policy = {'repository': 'fixture/project', 'branch': 'main', 'configuration': {
        'required_status_checks': {'strict': True, 'checks': [
            {'context': name, 'app_id': 123} for name in (UBUNTU, WINDOWS)
        ]},
        'required_pull_request_reviews': {'dismiss_stale_reviews': True, 'required_approving_review_count': 0},
        'enforce_admins': True, 'allow_force_pushes': False, 'allow_deletions': False,
        'required_conversation_resolution': True,
    }}
    protection = deepcopy(policy['configuration'])
    for key in ['enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution']:
        protection[key] = {'enabled': protection[key]}
    pr = {'number': 1, 'url': 'https://github.com/fixture/project/pull/1', 'state': 'OPEN', 'isDraft': False,
          'headRefOid': 'a' * 40, 'headRefName': 'task', 'baseRefOid': 'b' * 40, 'baseRefName': 'main',
          'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN', 'reviewDecision': ''}
    return {'source': {'git_head': 'a' * 40, 'working_tree_dirty': False}, 'pr': pr, 'final_pr': deepcopy(pr),
            'base_current': True, 'workflow': {'status': 'passed', 'reason': 'Observed success.',
                                             'run': {'id': 1, 'run_attempt': 2}},
            'jobs': [{'name': name, 'status': 'completed', 'conclusion': 'success', 'head_sha': 'a' * 40,
                      'steps': [CHECKOUT_STEP, step(harness)]}
                     for name, harness in [(UBUNTU, UBUNTU_FULL), (WINDOWS, WINDOWS_FULL)]],
            'policy': policy, 'protection': protection,
            'scope': {'completed_task_ids': ['M0-001'], 'blockers': []}, 'observations_stable': True}


def checkout_log(pr, commit, git='/usr/bin/git'):
    return '\n'.join(f'2026-11-21T16:42:53.0000000Z {message}' for message in (
        '##[group]Run actions/checkout@' + '1' * 40,
        '##[endgroup]',
        '##[group]Checking out the ref',
        f'[command]{git} checkout --progress --force refs/remotes/pull/{pr}/merge',
        '##[endgroup]',
        f'[command]{git} log -1 --format=%H',
        commit,
    ))


class IntegrationAssessmentTests(unittest.TestCase):
    def test_checkout_identifies_the_tested_ref_and_head_on_both_runner_platforms(self):
        commit = '7' * 40
        for git in ('/usr/bin/git', r'"C:\Program Files\Git\bin\git.exe"'):
            self.assertEqual((3, commit), integration.checked_pull_request(StringIO(checkout_log(3, commit, git))))
        with self.assertRaisesRegex(ProjectError, 'checkout log'):
            integration.checked_pull_request(StringIO(checkout_log(3, commit).replace(
                '##[group]Checking out the ref', '##[group]Run pnpm check')))

    def test_log_download_handles_other_encodings_and_reports_missing_evidence(self):
        commit = '7' * 40

        def download(argv, **options):
            self.assertEqual('repos/fixture/project/actions/jobs/10/logs', argv[-1])
            options['stdout'].buffer.write(b'\xff\r\n' + checkout_log(3, commit).encode())
            return subprocess.CompletedProcess(argv, 0, stderr=b'')

        with patch.object(subprocess, 'run', side_effect=download):
            self.assertEqual((3, commit), integration.job_checkout('.', 'fixture/project', 10))
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, stderr=b'HTTP 404')):
            with self.assertRaisesRegex(ProjectError, 'job 10: HTTP 404'):
                integration.job_checkout('.', 'fixture/project', 10)

    def test_technical_pass_requires_a_separate_review_and_authorization(self):
        result = integration.assess(**observations())
        self.assertTrue(result['technical_ready'])
        self.assertTrue(result['review_candidate'])
        self.assertEqual('not_evaluated', result['authorization'])
        self.assertEqual('present_merge_proposal', result['recommendation'])
        self.assertTrue(any('owner merge authorization' in item for item in result['decision_requirements']))

    def test_draft_can_reach_review_but_cannot_be_merged(self):
        for merge_state in ['CLEAN', 'DRAFT']:
            args = observations()
            args['pr'].update(isDraft=True, mergeStateStatus=merge_state)
            args['final_pr'] = deepcopy(args['pr'])
            result = integration.assess(**args)
            self.assertFalse(result['technical_ready'])
            self.assertTrue(result['review_candidate'])
            self.assertEqual(1, len(result['blockers']))

    def test_local_changes_wrong_head_and_stale_base_cannot_pass(self):
        for key, value in [('working_tree_dirty', True), ('git_head', 'c' * 40), ('git_head', None)]:
            args = observations()
            args['source'][key] = value
            self.assertFalse(integration.assess(**args)['technical_ready'])
        args = observations()
        args['base_current'] = False
        self.assertFalse(integration.assess(**args)['review_candidate'])

    def test_unknown_conflicting_closed_and_review_blocked_prs_cannot_pass(self):
        for key, value in [('state', 'CLOSED'), ('state', 'MERGED'), ('baseRefName', 'other'),
                           ('mergeable', 'CONFLICTING'), ('mergeable', 'UNKNOWN'),
                           ('mergeStateStatus', 'BEHIND'), ('mergeStateStatus', 'BLOCKED'),
                           ('mergeStateStatus', 'UNSTABLE'), ('mergeStateStatus', 'UNKNOWN'),
                           ('reviewDecision', 'CHANGES_REQUESTED'), ('reviewDecision', 'REVIEW_REQUIRED')]:
            with self.subTest(key=key, value=value):
                args = observations()
                args['pr'][key] = value
                args['final_pr'] = deepcopy(args['pr'])
                self.assertFalse(integration.assess(**args)['technical_ready'])

    def test_workflow_success_cannot_hide_missing_skipped_or_wrong_revision_jobs(self):
        for jobs in [[], [{'name': 'other', 'status': 'completed', 'conclusion': 'success'}]]:
            args = observations()
            args['jobs'] = jobs
            self.assertFalse(integration.assess(**args)['technical_ready'])
        for key, value in [('conclusion', 'skipped'), ('conclusion', 'neutral'), ('conclusion', 'failure'),
                           ('conclusion', 'cancelled'), ('status', 'in_progress'), ('head_sha', 'c' * 40)]:
            args = observations()
            args['jobs'][0][key] = value
            self.assertFalse(integration.assess(**args)['technical_ready'])
        args = observations()
        args['jobs'].append(args['jobs'][0])
        self.assertFalse(integration.assess(**args)['technical_ready'])

    def test_successful_old_jobs_do_not_override_the_latest_workflow(self):
        for status in ['failed', 'blocked']:
            args = observations()
            args['workflow'].update(status=status, reason='The latest run is not successful.')
            self.assertFalse(integration.assess(**args)['technical_ready'])

    def test_remote_changes_during_inspection_invalidate_the_snapshot(self):
        for key, value in [('headRefOid', 'c' * 40), ('baseRefOid', 'c' * 40), ('state', 'CLOSED'),
                           ('isDraft', True), ('reviewDecision', 'CHANGES_REQUESTED')]:
            args = observations()
            args['final_pr'][key] = value
            self.assertFalse(integration.assess(**args)['review_candidate'])
        args = observations()
        args['observations_stable'] = False
        self.assertFalse(integration.assess(**args)['technical_ready'])

    def test_live_protection_and_delivery_blockers_are_not_advisory(self):
        args = observations()
        args['protection']['enforce_admins']['enabled'] = False
        self.assertFalse(integration.assess(**args)['technical_ready'])
        args = observations()
        args['scope']['blockers'] = ['Unfinished delivery task: M0-001']
        self.assertFalse(integration.assess(**args)['technical_ready'])

    def test_unavailable_observations_exit_nonzero_without_an_authorization_claim(self):
        output = StringIO()
        with (patch.object(sys, 'argv', ['integration.py', '--pr', '1']),
              patch.object(integration, 'inspect', side_effect=ProjectError('API unavailable')),
              redirect_stdout(output)):
            with self.assertRaises(SystemExit) as stopped:
                integration.main()
        self.assertEqual(1, stopped.exception.code)
        result = json.loads(output.getvalue())
        self.assertFalse(result['technical_ready'])
        self.assertEqual('not_evaluated', result['authorization'])


class IntegrationFixture(ProjectFixture):
    def git(self, *args):
        return subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                               '-c', 'commit.gpgsign=false', *args], cwd=self.root, capture_output=True,
                              check=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '--quiet', '-m', 'Preserve fixture')
        return source_identity(self.root)['git_head']

    def merge_observation(self, base, head, *, tree_revision=None):
        tree = self.git('rev-parse', f'{tree_revision or head}^{{tree}}')
        merge = self.git('commit-tree', tree, '-p', base, '-p', head, '-m', 'Merge fixture')
        return {'sha': merge, 'parents': [{'sha': base}, {'sha': head}], 'tree': {'sha': tree}}


class IntegrationHistoryTests(IntegrationFixture):
    def test_completed_slice_can_reach_review_without_completing_future_product_tasks(self):
        base = self.commit()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        scope = integration.delivery_scope(self.root, load_project(self.root), base)
        self.assertEqual(['M0-001'], scope['completed_task_ids'])
        self.assertEqual([], scope['blockers'])
        self.assertEqual('planned', task_by_id(self.ledger(), 'M5-001')['status'])

    def test_an_active_or_empty_slice_cannot_be_integrated_as_completed(self):
        base = self.commit()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['status'] = 'active'
        self.save_ledger(ledger)
        scope = integration.delivery_scope(self.root, load_project(self.root), base)
        self.assertEqual(['M0-001'], scope['unfinished_task_ids'])
        self.assertEqual(2, len(scope['blockers']))

    def test_history_and_missing_completion_evidence_are_rejected(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['evidence'] = []
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'lacks passing evidence'):
            load_project(self.root)
        task_by_id(ledger, 'M0-001').update(status='planned', next_action='Unsupported reopening.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history'):
            integration.delivery_scope(self.root, load_project(self.root), base)

    def test_critical_and_scope_relevant_major_feedback_block_review_even_if_deferred(self):
        base = self.commit()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        project = load_project(self.root)
        for severity, task, blocked in [('critical', 'M5-001', True), ('major', 'M0-001', True),
                                         ('major', 'M5-001', False), ('minor', 'M0-001', False)]:
            project['feedback']['items'] = [{'id': 'observed-defect', 'status': 'deferred',
                                             'severity': severity, 'task_ids': [task]}]
            scope = integration.delivery_scope(self.root, project, base)
            self.assertEqual(blocked, bool(scope['blockers']))

    def budget_handoff_fixture(self, *, records_only=False, bind_push_feedback=True, follow_up_ready=True,
                               configure_handoff=True, policy_on_base=False):
        delivery = self.root / 'project/delivery.json'
        feedback = self.root / 'project/feedback.json'
        rule = {'pull_request_feedback_id': PR_BUDGET, 'main_push_feedback_id': PUSH_BUDGET,
                'repair_task_id': REPAIR_TASK, 'follow_up_task_id': FOLLOW_UP_TASK,
                'next_action': 'Observe the protected main push and evaluate it with pnpm project:timing.'}
        configured = read_json(delivery)
        if configure_handoff:
            configured['source_control']['post_merge_main_push_budget'] = rule
        (self.root / 'project/branch-protection.json').write_bytes(json_bytes(observations()['policy']))
        if records_only or policy_on_base:
            delivery.write_bytes(json_bytes(configured))
        ledger = self.ledger()
        aliases = {'M0-001': REPAIR_TASK, 'M0-002': FOLLOW_UP_TASK}
        for task in ledger['tasks']:
            task['id'] = aliases.get(task['id'], task['id'])
            task['depends_on'] = [aliases.get(key, key) for key in task['depends_on']]
        ledger['focus_task_id'] = REPAIR_TASK
        self.save_ledger(ledger)
        base = self.commit()
        if not records_only:
            delivery.write_bytes(json_bytes(configured))
            (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        ledger = self.ledger()
        handoff = task_by_id(ledger, FOLLOW_UP_TASK)
        handoff.update(depends_on=[REPAIR_TASK] if follow_up_ready else [REPAIR_TASK, 'M0-004'], next_action=rule['next_action'],
                       checks=['project-state', 'rust-lints'],
                       plan=deepcopy(task_by_id(ledger, REPAIR_TASK)['plan']))
        self.save_ledger(ledger)
        repair = self.commit()
        items = []
        for event, identifier, owners in [('pull_request', PR_BUDGET, [REPAIR_TASK]),
                                          ('push', PUSH_BUDGET, [REPAIR_TASK, FOLLOW_UP_TASK])]:
            items.append({
                'id': identifier, 'source': 'Synthetic timing controls.', 'observation': f'Budget breach on {event}.',
                'severity': 'major', 'status': 'open', 'task_ids': owners, 'regression_checks': ['rust-lints'],
                'resolution': None, 'deferral_reason': None,
                'verification_budget': {'repository': 'fixture/project', 'platform': 'Windows', 'profile': 'full',
                                        'event': event, 'repair_commit': repair},
                'diagnostic_reports': [{'run_id': identifier, 'source_commit': base, 'report_sha256': '1' * 64}],
            })
        feedback.write_bytes(json_bytes({'schema_version': '1.0', 'items': items}))
        self.commit()
        report = self.report(REPAIR_TASK)
        report_data = read_json(report)
        report_data['observed_feedback'] = [feedback_fingerprint(item) for item in items[:2 if bind_push_feedback else 1]]
        report.write_bytes(json_bytes(report_data))
        receipt = archive_evidence(self.root, REPAIR_TASK, report)
        ledger = self.ledger()
        task_by_id(ledger, REPAIR_TASK).update(status='done', evidence=[receipt], next_action=None)
        ledger['focus_task_id'] = FOLLOW_UP_TASK
        self.save_ledger(ledger)
        items[0].update(status='addressed', resolution=dict(cause='Fixture.', correction='Fixture.', prevention='Fixture.'))
        feedback.write_bytes(json_bytes({'schema_version': '1.0', 'items': items}))
        self.commit()
        return base, repair

    def inspect_budget_handoff(self, base, repair, *, elapsed=899, timing_run='100', timing_status='recovered',
                               omit_windows=False, fail_windows=False, conclusion='success', attempt=1,
                               push_controls=True, push_attempt=None, timing_inputs=None, push_status='pending',
                               records=False, expect_no_timing=False):
        args = observations()
        source = source_identity(self.root)
        head = source['git_head']
        pr = dict(args['pr'], baseRefOid=base, headRefOid=head)
        tested = self.merge_observation(base, head)
        run = {'id': 100, 'run_attempt': attempt, 'status': 'completed', 'conclusion': 'success', 'head_sha': head,
               'head_branch': 'task', 'event': 'pull_request', 'path': integration.WORKFLOW}
        jobs = [dict(job, id=index, head_sha=head) for index, job in enumerate(args['jobs'], 1000)]
        if records:
            for job in jobs:
                job['steps'] = [CHECKOUT_STEP, step('Verify records changes')]
        if omit_windows:
            jobs.pop()
        elif fail_windows:
            jobs[1]['conclusion'] = 'failure'
        scope = {'repository': 'fixture/project', 'event': 'pull_request', 'profile': 'full', 'platform': 'Windows'}
        latest = {'hosted_run_id': timing_run, 'hosted_head': head, 'source_commit': tested['sha'],
                  'job_id': 1001, 'conclusion': conclusion, 'elapsed_seconds': elapsed,
                  'inputs_sha256': timing_inputs or source['sha256']}
        recovery = {'feedback_id': PR_BUDGET, 'scope': scope,
                    'recovery': {'status': timing_status, 'repair_commit': repair,
                                 'comparisons': [{'latest_attempt': latest, 'recovered': {'hosted_run_id': '99'}}]}}
        main_recovery = {
            'feedback_id': PUSH_BUDGET, 'scope': dict(scope, event='push'),
            'recovery': {'status': push_status, 'repair_commit': repair,
                         'comparisons': [{'latest_attempt': push_attempt, 'history_missing': False}] if push_controls else []},
        }
        original = integration.command

        def command(argv, root):
            if argv[0] == 'git':
                return original(argv, root)
            if argv[:3] == ['gh', 'pr', 'view']:
                return json.dumps(pr)
            if 'event=pull_request' in argv:
                return json.dumps([{'workflow_runs': [run]}])
            if f'/runs/100/attempts/{attempt}/jobs?' in argv[-1]:
                return json.dumps([{'jobs': jobs}])
            if argv[-1].endswith(f'/git/commits/{tested["sha"]}'):
                return json.dumps(tested)
            self.assertEqual('repos/fixture/project/branches/main/protection', argv[-1])
            return json.dumps(args['protection'])

        with (patch.object(integration, 'github_identity', return_value='fixture/project'),
              patch.object(integration, 'command', side_effect=command),
              patch.object(integration, 'job_checkout', return_value=(1, tested['sha'])),
              patch.object(timing, 'offline_budget_status',
                           return_value={'budget_recovery': [recovery, main_recovery]},
                           side_effect=AssertionError('Unrelated scope queried timing') if expect_no_timing else None)):
            return integration.inspect(self.root, 1)

    def test_verified_repair_can_handoff_only_the_open_main_push_measurement(self):
        base, repair = self.budget_handoff_fixture()
        result = self.inspect_budget_handoff(base, repair)
        self.assertEqual('full', result['workflow']['expected_profile'])
        self.assertTrue(result['technical_ready'], result['blockers'])
        handoff = result['delivery_scope']['post_merge_main_push_budget']
        self.assertEqual((PUSH_BUDGET, FOLLOW_UP_TASK, repair, 'pending'), tuple(
            handoff[key] for key in ('feedback_id', 'follow_up_task_id', 'repair_commit', 'status')))
        self.assertEqual('open', load_project(self.root)['feedback']['items'][1]['status'])
        merged = self.merge_observation(base, result['source']['git_head'])['sha']
        self.git('checkout', '--quiet', '--detach', merged)
        event = self.root / '.reports/push-event.json'
        event.write_bytes(json_bytes({'ref': 'refs/heads/main', 'before': base, 'after': merged}))
        ordinary_push = verification_scope.preview(self.root, ci_env={
            'GITHUB_EVENT_NAME': 'push', 'GITHUB_EVENT_PATH': str(event),
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': merged,
        })
        self.assertEqual(('full', base, merged, 'change_requires_full'), (
            ordinary_push['profile'], ordinary_push['base'], ordinary_push['checkout'], ordinary_push['reason_code']))

    def test_budget_handoff_requires_latest_pr_timing_and_both_required_jobs(self):
        base, repair = self.budget_handoff_fixture()
        for change in ({'elapsed': 901}, {'timing_run': '99'}, {'timing_status': 'pending'},
                       {'conclusion': 'failure'}, {'omit_windows': True}, {'fail_windows': True}, {'attempt': 2},
                       {'timing_inputs': '2' * 64}, {'push_controls': False},
                       {'push_attempt': {'conclusion': 'failure'}},
                       {'push_attempt': {'conclusion': 'success', 'elapsed_seconds': 901}}):
            with self.subTest(change=change):
                result = self.inspect_budget_handoff(base, repair, **change)
                self.assertFalse(result['technical_ready'])
                self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', result['blockers'])

    def test_budget_handoff_cannot_borrow_an_unbound_regression_receipt(self):
        base, repair = self.budget_handoff_fixture(bind_push_feedback=False)
        result = self.inspect_budget_handoff(base, repair)
        self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', result['blockers'])

    def prematurely_close_push_feedback(self):
        path = self.root / 'project/feedback.json'
        feedback = read_json(path)
        feedback['items'][1].update(
            task_ids=[REPAIR_TASK], status='addressed',
            resolution=dict(cause='Fixture.', correction='Fixture.', prevention='Fixture.'),
        )
        path.write_bytes(json_bytes(feedback))
        self.commit()

    def test_repair_pr_cannot_close_the_main_timing_finding_with_the_repair_receipt(self):
        base, repair = self.budget_handoff_fixture()
        self.prematurely_close_push_feedback()
        result = self.inspect_budget_handoff(base, repair)
        self.assertEqual('passed', result['delivery_scope']['history']['status'])
        self.assertFalse(result['technical_ready'], result)
        self.assertIn(f'Main-push timing closure lacks a verified main-push observation: {PUSH_BUDGET}', result['blockers'])

    def check_cannot_redirect_main_push_policy(self, *, policy_on_base):
        base, repair = self.budget_handoff_fixture(policy_on_base=policy_on_base)
        self.prematurely_close_push_feedback()
        path = self.root / 'project/delivery.json'
        original = read_json(path)
        for target in (PUSH_BUDGET + '-typo', PR_BUDGET, None):
            with self.subTest(target=target):
                delivery = deepcopy(original)
                if target is None:
                    delivery['source_control'].pop('post_merge_main_push_budget')
                else:
                    delivery['source_control']['post_merge_main_push_budget']['main_push_feedback_id'] = target
                path.write_bytes(json_bytes(delivery))
                self.commit()
                result = self.inspect_budget_handoff(base, repair, expect_no_timing=True)
                self.assertEqual('passed', result['delivery_scope']['history']['status'])
                self.assertFalse(result['technical_ready'], result['blockers'])
                reason = ('The PR changes or removes the protected main-push budget policy.' if policy_on_base
                          else 'The main-push budget policy must name the governed M0-087 findings and M0-091.')
                self.assertIn(reason, result['blockers'])

    def test_repair_pr_cannot_redirect_the_existing_main_push_policy(self):
        self.check_cannot_redirect_main_push_policy(policy_on_base=True)

    def test_first_policy_introduction_cannot_misspell_the_main_push_finding(self):
        self.check_cannot_redirect_main_push_policy(policy_on_base=False)

    def test_new_policy_requires_both_governed_feedback_records(self):
        base, repair = self.budget_handoff_fixture()
        path = self.root / 'project/feedback.json'
        original = read_json(path)
        for missing in (PR_BUDGET, PUSH_BUDGET):
            with self.subTest(missing=missing):
                feedback = {**original, 'items': [item for item in original['items'] if item['id'] != missing]}
                path.write_bytes(json_bytes(feedback))
                self.commit()
                result = self.inspect_budget_handoff(base, repair, expect_no_timing=True)
                self.assertIn(f'The main-push budget policy references unavailable records: {missing}.',
                              result['blockers'])
                self.assertFalse(result['technical_ready'])

    def measured_main_push_fixture(self, *, evidence_event='push'):
        base, repair = self.budget_handoff_fixture()
        main = self.merge_observation(base, source_identity(self.root)['git_head'])['sha']
        self.git('checkout', '--quiet', '--detach', main)
        self.git('remote', 'add', 'origin', 'https://github.com/fixture/project.git')
        feedback_path = self.root / 'project/feedback.json'
        feedback = read_json(feedback_path)
        push = feedback['items'][1]
        report = read_json(self.report(FOLLOW_UP_TASK, system='Windows'))
        report['ci'] = {
            'provider': 'github-actions', 'repository': 'fixture/project', 'run_id': '101', 'run_attempt': '1',
            'event': evidence_event, 'head_sha': main, 'checkout_sha': main,
            'run_url': 'https://github.com/fixture/project/actions/runs/101',
        }
        report['observed_feedback'] = [feedback_fingerprint(push)]
        artifact = self.root / '.reports/ci/101/windows'
        for check in report['checks']:
            log = artifact / check['log'].removeprefix('.reports/')
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_bytes((self.root / check['log']).read_bytes())
        report_path = artifact / 'test-report.json'
        report_path.write_bytes(json_bytes(report))
        receipt = archive_evidence(self.root, FOLLOW_UP_TASK, report_path, artifact)

        ledger = self.ledger()
        ledger['focus_task_id'] = 'M0-004'
        task_by_id(ledger, FOLLOW_UP_TASK).update(status='done', evidence=[receipt], next_action=None)
        self.save_ledger(ledger)
        push.update(status='addressed', resolution=dict(cause='Fixture.', correction='Fixture.', prevention='Fixture.'))
        feedback_path.write_bytes(json_bytes(feedback))
        self.commit()
        witness = {'hosted_run_id': '101', 'hosted_head': main, 'source_commit': main, 'elapsed_seconds': 899,
                   'conclusion': 'success', 'inputs_sha256': report['source']['sha256']}
        return main, repair, witness

    def test_verified_main_timing_can_close_in_a_records_only_pull_request(self):
        base, repair, witness = self.measured_main_push_fixture()
        result = self.inspect_budget_handoff(base, repair, push_status='recovered', push_attempt=witness,
                                             timing_status='pending', records=True)
        self.assertTrue(result['technical_ready'], result['blockers'])
        self.assertEqual('records', result['workflow']['expected_profile'])
        self.assertEqual([FOLLOW_UP_TASK], result['delivery_scope']['completed_task_ids'])
        self.assertNotIn('post_merge_main_push_budget', result['delivery_scope'])

    def test_main_timing_closure_requires_a_current_success_on_protected_history(self):
        base, repair, witness = self.measured_main_push_fixture()
        future = source_identity(self.root)['git_head']
        for status, sample in (
            ('pending', None), ('pending', witness),
            ('recovered', dict(witness, conclusion='failure')),
            ('recovered', dict(witness, elapsed_seconds=901)),
            ('recovered', dict(witness, hosted_head=future, source_commit=future)),
            ('recovered', dict(witness, hosted_run_id='102')),
            ('recovered', dict(witness, inputs_sha256='2' * 64)),
        ):
            with self.subTest(status=status, sample=sample):
                result = self.inspect_budget_handoff(base, repair, push_status=status, push_attempt=sample, records=True)
                self.assertFalse(result['technical_ready'])
                self.assertIn(f'Main-push timing closure lacks a verified main-push observation: {PUSH_BUDGET}',
                              result['blockers'])

    def test_main_timing_closure_cannot_use_a_pull_request_receipt(self):
        base, repair, witness = self.measured_main_push_fixture(evidence_event='pull_request')
        result = self.inspect_budget_handoff(base, repair, push_status='recovered', push_attempt=witness, records=True)
        self.assertFalse(result['technical_ready'])

    def test_unrelated_product_change_does_not_recheck_existing_budget_closure(self):
        _, repair, _ = self.measured_main_push_fixture()
        base = source_identity(self.root)['git_head']
        (self.root / 'implementation.py').write_text('value = 3\n', encoding='utf-8')
        ledger = self.ledger()
        task_by_id(ledger, 'M0-004')['plan'] = deepcopy(task_by_id(ledger, REPAIR_TASK)['plan'])
        self.save_ledger(ledger)
        self.commit()
        receipt = archive_evidence(self.root, 'M0-004', self.report('M0-004'))
        ledger['focus_task_id'] = 'M0-003'
        task_by_id(ledger, 'M0-004').update(status='done', evidence=[receipt], next_action=None)
        self.save_ledger(ledger)
        self.commit()
        result = self.inspect_budget_handoff(base, repair, expect_no_timing=True)
        self.assertTrue(result['technical_ready'], result['blockers'])

    def test_budget_handoff_cannot_assign_a_follow_up_waiting_on_other_work(self):
        base, repair = self.budget_handoff_fixture(follow_up_ready=False)
        self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', self.inspect_budget_handoff(base, repair)['blockers'])

    def test_budget_handoff_requires_an_applicable_checked_in_rule(self):
        base, repair = self.budget_handoff_fixture(configure_handoff=False)
        self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', self.inspect_budget_handoff(base, repair)['blockers'])

    def test_budget_handoff_requires_an_open_linked_measurement_and_unmerged_repair(self):
        base, repair = self.budget_handoff_fixture()
        unrelated = self.git('commit-tree', f'{base}^{{tree}}', '-p', base, '-m', 'Unrelated repair')
        feedback = self.root / 'project/feedback.json'
        original = read_json(feedback)
        for changes in (
            {'repair': base}, {'repair': unrelated}, {'repair': '8' * 40}, {'event': 'pull_request'},
            {'owners': [REPAIR_TASK]}, {'owners': [FOLLOW_UP_TASK], 'regression': ['project-state']},
            {'status': 'deferred'}, {'severity': 'minor'}, {'pr_status': 'open'},
        ):
            with self.subTest(changes=changes):
                data = deepcopy(original)
                pr, push = data['items']
                marker = changes.get('repair', repair)
                for item in (pr, push):
                    item['verification_budget']['repair_commit'] = marker
                push['verification_budget']['event'] = changes.get('event', 'push')
                push['task_ids'] = changes.get('owners', push['task_ids'])
                push['regression_checks'] = changes.get('regression', push['regression_checks'])
                push['severity'] = changes.get('severity', push['severity'])
                if changes.get('status') == 'deferred':
                    push.update(status='deferred', deferral_reason='Deliberate fixture deferral.')
                if changes.get('pr_status') == 'open':
                    pr.update(status='open', resolution=None)
                feedback.write_bytes(json_bytes(data))
                self.commit()
                result = self.inspect_budget_handoff(base, marker)
                self.assertIn(f"Unresolved {push['severity']} feedback: {PUSH_BUDGET}", result['blockers'])
                self.assertNotIn('post_merge_main_push_budget', result['delivery_scope'])

    def test_budget_handoff_cannot_override_product_feedback_or_stale_inputs(self):
        base, repair = self.budget_handoff_fixture()
        feedback = self.root / 'project/feedback.json'
        data = read_json(feedback)
        for severity, task in [('major', REPAIR_TASK), ('critical', 'M0-004')]:
            data['items'].append(dict(id=f'product-{severity}', source='Fixture product behavior.',
                                      observation='A product regression remains open.', severity=severity, status='open',
                                      task_ids=[task], regression_checks=['project-state'],
                                      resolution=None, deferral_reason=None))
        feedback.write_bytes(json_bytes(data))
        self.commit()
        result = self.inspect_budget_handoff(base, repair)
        self.assertIn('post_merge_main_push_budget', result['delivery_scope'])
        self.assertCountEqual(['Unresolved major feedback: product-major', 'Unresolved critical feedback: product-critical'],
                              result['blockers'])
        (self.root / 'implementation.py').write_text('value = 3\n', encoding='utf-8')
        self.commit()
        self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', self.inspect_budget_handoff(base, repair)['blockers'])

    def test_records_only_push_cannot_claim_the_ordinary_full_measurement(self):
        base, repair = self.budget_handoff_fixture(records_only=True)
        result = self.inspect_budget_handoff(base, repair)
        self.assertEqual('records', result['workflow']['expected_profile'])
        self.assertIn(f'Unresolved major feedback: {PUSH_BUDGET}', result['blockers'])

    def observe_harness(self, base, head, ubuntu_steps, windows_steps, *, tested=None):
        fixture = observations()
        pr = dict(fixture['pr'], baseRefOid=base, headRefOid=head)
        run = {'id': 100, 'run_attempt': 2, 'head_sha': head, 'status': 'completed', 'conclusion': 'success',
               'path': '.github/workflows/verify.yml', 'event': 'pull_request'}
        jobs = [dict(job, id=job_id, head_sha=head, steps=[CHECKOUT_STEP, *steps])
                for job_id, (job, steps) in enumerate(zip(fixture['jobs'], [ubuntu_steps, windows_steps]), 1000)]
        if tested is None:
            tested = self.merge_observation(base, head)
        api_responses = iter([json.dumps([{'jobs': jobs}]), json.dumps(tested)])
        original = integration.command

        def command(argv, root):
            return original(argv, root) if argv[0] == 'git' else next(api_responses)

        with (patch.object(integration, 'command', side_effect=command),
              patch.object(integration, 'job_checkout', return_value=(1, tested['sha']))):
            return integration.verified_pr_run(self.root, 'fixture/project', pr, [run], False, [UBUNTU, WINDOWS])

    def test_narrow_profiles_allow_matching_or_platform_full_harnesses(self):
        locale = self.root / 'apps/desktop/ui/src/i18n/locales/zh-Hans/core.json'
        locale.parent.mkdir(parents=True)
        locale.write_bytes(json_bytes({'open': '打开 {name}'}))
        tooling = self.root / 'tests/project/test_herdr.py'
        tooling.parent.mkdir(parents=True)
        tooling.write_text('value = 1\n', encoding='utf-8')
        records = self.root / 'README.md'
        records.write_text('Fixture summary.\n', encoding='utf-8')
        base = self.commit()
        for profile, path, content in [
            ('records', records, b'Updated fixture summary.\n'),
            ('tooling', tooling, b'value = 2\n'),
            ('localization', locale, json_bytes({'open': '开启 {name}'})),
        ]:
            path.write_bytes(content)
            head = self.commit()
            scoped = step(f'Verify {profile} changes')
            for ubuntu_steps, windows_steps, observed in [
                ([scoped], [scoped], [profile, profile]),
                ([step(UBUNTU_FULL)], [scoped, step(WINDOWS_FULL, conclusion='skipped')], ['full', profile]),
                ([scoped], [step(WINDOWS_FULL)], [profile, 'full']),
                ([step(UBUNTU_FULL)], [step(WINDOWS_FULL)], ['full', 'full']),
            ]:
                with self.subTest(profile=profile, observed=observed):
                    result, jobs = self.observe_harness(base, head, ubuntu_steps, windows_steps)
                    self.assertEqual('passed', result['status'], result['reason'])
                    self.assertEqual(profile, result['expected_profile'])
                    self.assertEqual(observed, [job['verification_profile'] for job in jobs])
            base = head

    def test_narrow_scope_blocks_each_unsuccessful_or_incorrect_runner_harness(self):
        tooling = self.root / 'tests/project/test_herdr.py'
        tooling.parent.mkdir(parents=True)
        tooling.write_text('value = 1\n', encoding='utf-8')
        base = self.commit()
        tooling.write_text('value = 2\n', encoding='utf-8')
        head = self.commit()
        for name, invalid_steps, observed in [
            ('missing', [], None),
            ('skipped full', [step(WINDOWS_FULL, conclusion='skipped')], None),
            ('skipped narrow', [step('Verify tooling changes', conclusion='skipped')], None),
            ('failed narrow', [step('Verify tooling changes', conclusion='failure')], None),
            ('unfinished', [step('Verify tooling changes', status='in_progress', conclusion=None)], None),
            ('wrong runner', [step(UBUNTU_FULL)], None),
            ('wrong narrow profile', [step('Verify localization changes')], 'localization'),
            ('unresolved name', [step('Verify  changes')], None),
            ('manual task', [step('Verify the selected task')], None),
        ]:
            with self.subTest(case=name):
                result, jobs = self.observe_harness(base, head, [step(UBUNTU_FULL)], invalid_steps)
                self.assertEqual('blocked', result['status'])
                self.assertEqual('tooling', result['expected_profile'])
                self.assertIn(f"{WINDOWS} ({observed or 'no successful harness'})", result['reason'])
                self.assertEqual(['full', observed], [job['verification_profile'] for job in jobs])
        result, jobs = self.observe_harness(base, head, [step(WINDOWS_FULL)], [step('Verify tooling changes')])
        self.assertEqual('blocked', result['status'])
        self.assertIn(f'{UBUNTU} (no successful harness)', result['reason'])
        self.assertEqual([None, 'tooling'], [job['verification_profile'] for job in jobs])

    def test_merge_tree_difference_or_missing_proof_requires_full_verification(self):
        records = self.root / 'README.md'
        records.write_text('Fixture summary.\n', encoding='utf-8')
        base = self.commit()
        records.write_text('Updated fixture summary.\n', encoding='utf-8')
        head = self.commit()
        matching = self.merge_observation(base, head)
        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        merge_content = self.commit()
        different = self.merge_observation(base, head, tree_revision=merge_content)
        self.assertEqual(matching['parents'], different['parents'])
        self.assertNotEqual(matching['tree'], different['tree'])
        self.assertEqual('records', integration.committed_scope(self.root, base, head)['profile'])

        records_step = [step('Verify records changes')]
        for tested, reason in [
            (different, 'differs from the local PR head tree'),
            ({key: value for key, value in matching.items() if key != 'tree'}, 'merge tree was unavailable'),
            (dict(matching, tree={'sha': 'invalid'}), 'merge tree was unavailable'),
        ]:
            with self.subTest(tree=tested.get('tree')):
                result, jobs = self.observe_harness(base, head, records_step, records_step, tested=tested)
                self.assertEqual('blocked', result['status'])
                self.assertEqual('full', result['expected_profile'])
                self.assertIn(reason, result['expected_profile_reason'])
                self.assertEqual(['records', 'records'], [job['verification_profile'] for job in jobs])
                full, _ = self.observe_harness(base, head, [step(UBUNTU_FULL)], [step(WINDOWS_FULL)], tested=tested)
                self.assertEqual('passed', full['status'])
                self.assertEqual('full', full['expected_profile'])

        profile, reason = integration.expected_verification_profile(self.root, base, '0' * len(head), matching)
        self.assertEqual('full', profile)
        self.assertIn('local PR head tree could not be verified', reason)

    def test_collection_checks_the_latest_pr_attempt_despite_a_newer_task_dispatch(self):
        args = observations()
        (self.root / 'project/branch-protection.json').write_bytes(json_bytes(args['policy']))
        launcher = self.root / 'scripts/herdr.py'
        launcher.parent.mkdir()
        launcher.write_text('requires_approval = True\n', encoding='utf-8')
        base = self.commit()
        launcher.write_text('requires_approval = False\n', encoding='utf-8')
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        head = self.commit()
        head_tree = self.git('rev-parse', f'{head}^{{tree}}')
        pr = dict(args['pr'], baseRefOid=base, headRefOid=head)
        run = {'id': 100, 'head_sha': head, 'head_branch': 'task', 'status': 'completed', 'conclusion': 'success',
               'run_attempt': 2, 'path': '.github/workflows/verify.yml', 'event': 'pull_request'}
        # Lists of associated open PRs can be empty even for a real rerun.
        run['pull_requests'] = []
        associated = [{'number': 1, 'head': {'sha': head}, 'base': {'sha': base, 'ref': 'main'}}]
        foreign = dict(run, id=101, run_attempt=1, pull_requests=associated)
        stale = dict(run, id=102, run_attempt=1, pull_requests=associated)
        task_run = dict(run, id=103, event='workflow_dispatch', run_attempt=1)
        runs = [task_run, stale, foreign, run]
        previous = dict(run, id=99, run_attempt=1)
        checked = {990: (1, '6' * 40), 1000: (1, '7' * 40), 1010: (2, '8' * 40), 1020: (1, '9' * 40)}
        checked.update({job + 1: source for job, source in checked.items()})
        ubuntu_job, windows_job = args['jobs']
        windows_steps = list(windows_job['steps'])
        head_parent = {99: head, 100: head, 101: head, 102: head}
        queries = []

        def command(argv, root):
            queries.append(argv)
            if argv[0] == 'git':
                return original(argv, root)
            if argv[:3] == ['gh', 'pr', 'view']:
                return json.dumps(pr)
            if 'event=pull_request' in argv:
                return json.dumps([{'workflow_runs': runs[:2]}, {'workflow_runs': runs[2:]}])
            for run_id, attempt, parent in [(99, 1, base), (100, 2, base), (101, 1, base), (102, 1, 'f' * 40)]:
                _, merge_sha = checked[run_id * 10]
                if f'/runs/{run_id}/attempts/{attempt}/jobs?' in argv[-1]:
                    ubuntu = dict(ubuntu_job, head_sha=head, id=run_id * 10)
                    windows = dict(windows_job, head_sha=head, id=run_id * 10 + 1)
                    if run_id != 99:
                        windows['steps'] = windows_steps if run_id == 100 else [CHECKOUT_STEP]
                    return json.dumps([{'jobs': [ubuntu, windows]}])
                if argv[-1].endswith(f'/git/commits/{merge_sha}'):
                    return json.dumps({'sha': merge_sha, 'parents': [{'sha': parent}, {'sha': head_parent[run_id]}],
                                       'tree': {'sha': head_tree}})
            self.assertEqual('repos/fixture/project/branches/main/protection', argv[-1])
            return json.dumps(args['protection'])

        original = integration.command
        checkout = patch.object(integration, 'job_checkout', side_effect=lambda root, repo, job_id: checked[job_id])
        self.addCleanup(checkout.stop)
        checkout.start()
        with (patch.object(integration, 'github_identity', return_value='fixture/project'),
              patch.object(integration, 'command', side_effect=command),
              patch.object(integration, 'committed_scope', wraps=integration.committed_scope) as classify):
            result = integration.inspect(self.root, 1)
        classify.assert_called_once_with(self.root, base, head)
        self.assertTrue(result['technical_ready'])
        self.assertEqual('full', result['workflow']['expected_profile'])
        self.assertEqual({UBUNTU: 'full', WINDOWS: 'full'},
                         {job['name']: job['verification_profile'] for job in result['required_jobs']})
        self.assertEqual('pull_request', result['workflow']['run']['event'])
        self.assertEqual(100, result['workflow']['run']['id'])
        self.assertEqual({'pull_request': 1, 'commit': '7' * 40, 'base': base, 'head': head},
                         result['workflow']['checkout'])
        self.assertEqual([(1000, '7' * 40), (1001, '7' * 40)],
                         [(job['job_id'], job['commit']) for job in result['workflow']['required_checkouts']])
        self.assertEqual(2, sum('event=pull_request' in argv for argv in queries))
        self.assertEqual(2, sum(argv[:3] == ['gh', 'pr', 'view'] for argv in queries))
        self.assertEqual(head, result['source']['git_head'])
        self.assertFalse(source_identity(self.root)['working_tree_dirty'])
        with (patch.object(integration, 'github_identity', return_value='fixture/project'),
              patch.object(integration, 'command', side_effect=command)):
            runs[:] = [run]
            checked[1001] = (1, '9' * 40)
            mixed = integration.inspect(self.root, 1)
            self.assertFalse(mixed['technical_ready'])
            self.assertIn('different pull requests or merge commits', mixed['workflow']['reason'])
            checked[1001] = checked[1000]
            windows_steps.clear()
            missing = integration.inspect(self.root, 1)
            self.assertFalse(missing['technical_ready'])
            self.assertIn(f'{WINDOWS} has no successful pinned checkout', missing['workflow']['reason'])
            windows_steps[:] = [CHECKOUT_STEP, step('Verify tooling changes')]
            narrower = integration.inspect(self.root, 1)
            self.assertFalse(narrower['technical_ready'])
            self.assertIn(f'{WINDOWS} (tooling)', narrower['workflow']['reason'])
            self.assertEqual({UBUNTU: 'full', WINDOWS: 'tooling'},
                             {job['name']: job['verification_profile'] for job in narrower['required_jobs']})
            runs[:] = [run, previous]
            windows_steps[:] = [CHECKOUT_STEP]
            unexecuted = integration.inspect(self.root, 1)
            self.assertFalse(unexecuted['technical_ready'])
            self.assertEqual(100, unexecuted['workflow']['run']['id'])
            self.assertIn(f'{WINDOWS} (no successful harness)', unexecuted['workflow']['reason'])
            self.assertIsNone(unexecuted['required_jobs'][1]['verification_profile'])
            for only, reason in [(stale, 'different base'), (foreign, 'no checked run matches')]:
                runs[:] = [only]
                result = integration.inspect(self.root, 1)
                self.assertFalse(result['technical_ready'])
                self.assertIn(reason, result['workflow']['reason'])
                self.assertNotIn('expected_profile', result['workflow'])
            runs[:] = [stale, run]
            head_parent[102] = 'd' * 40
            wrong_head = integration.inspect(self.root, 1)
            self.assertFalse(wrong_head['technical_ready'])
            self.assertIn('does not match its declared head', wrong_head['workflow']['reason'])
            head_parent[102] = head
            # A newer attempt with unavailable identity cannot fall through to an older pass.
            runs[:] = [dict(stale, status='queued', conclusion=None), run]
            self.assertIn('queued or running', integration.inspect(self.root, 1)['workflow']['reason'])
            runs[:] = [foreign, dict(run, conclusion='failure')]
            failed = integration.inspect(self.root, 1)['workflow']
            self.assertEqual('failed', failed['status'])
            self.assertIn('did not succeed', failed['reason'])
        windows_steps[:] = windows_job['steps']
        runs[:] = [run]
        queries.clear()

        def changed_during_inspection(argv, root):
            if 'event=pull_request' in argv and any('event=pull_request' in old for old in queries):
                runs.append(dict(run, run_attempt=3, status='in_progress', conclusion=None))
            return command(argv, root)

        with (patch.object(integration, 'github_identity', return_value='fixture/project'),
              patch.object(integration, 'command', side_effect=changed_during_inspection)):
            raced = integration.inspect(self.root, 1)
        self.assertFalse(raced['technical_ready'])
        self.assertTrue(any('changed during inspection' in why for why in raced['blockers']))
        with patch.object(integration, 'github_identity', return_value='unrelated/project'):
            with self.assertRaisesRegex(ProjectError, 'another repository'):
                integration.inspect(self.root, 1)


class RecurringBudgetHandoffTests(IntegrationFixture):
    # Use the usual delivery-scope entry with a committed protected binding and authenticated
    # observations supplied by the existing workflow and timing readers.
    def recurring_fixture(self, *, protected=True):
        path = self.root / 'project/delivery.json'
        ledger = self.ledger()
        for task, identity in (('M0-001', 'M0-139'), ('M0-002', 'M0-141')):
            task_by_id(ledger, task)['id'] = identity
            for row in ledger['tasks']:
                row['depends_on'] = [identity if dependency == task else dependency for dependency in row['depends_on']]
        ledger['focus_task_id'] = 'M0-139'
        follow_up = task_by_id(ledger, 'M0-141')
        follow_up.update(depends_on=['M0-139'], next_action='Observe the next protected main push.',
                         checks=['project-state', 'rust-lints'], plan=deepcopy(task_by_id(ledger, 'M0-139')['plan']))
        self.save_ledger(ledger)
        rule = {'main_push_feedback_id': 'recurring-push-budget', 'repair_task_id': 'M0-139',
                'follow_up_task_id': 'M0-141', 'next_action': follow_up['next_action'],
                'finding_sha256': '3' * 64, 'control_reports_sha256': '2' * 64, 'selected_check_count': 3}
        initial = self.commit()
        self.git('commit', '--quiet', '--allow-empty', '-m', 'Intake before the code correction')
        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        repair = self.commit()
        self.git('checkout', '--quiet', '--detach', initial)

        rule['repair_commit'] = repair
        configured = read_json(path)
        configured['source_control']['recurring_main_push_budget'] = rule
        if protected:
            path.write_bytes(json_bytes(configured))
            base = self.commit()
        else:
            base = initial
        path.write_bytes(json_bytes(configured))
        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        self.git('add', '.')
        tree = self.git('write-tree')
        joined = self.git('commit-tree', tree, '-p', base, '-p', repair, '-m', 'Combine repair and policy fixture')
        self.git('checkout', '--quiet', '--detach', joined)
        push = {'id': rule['main_push_feedback_id'], 'source': 'Fixture main-push controls.',
                'observation': 'Ordinary Windows main timing exceeded the objective.',
                'severity': 'major', 'status': 'open', 'task_ids': ['M0-139', 'M0-141'],
                'regression_checks': ['rust-lints'], 'resolution': None, 'deferral_reason': None,
                'diagnostic_reports': [{'run_id': 'windows-change-42-1', 'source_commit': base, 'report_sha256': '1' * 64}],
                'verification_budget': {'repository': 'fixture/project', 'event': 'push', 'profile': 'full',
                                        'platform': 'Windows', 'repair_commit': repair}}
        feedback = self.root / 'project/feedback.json'
        feedback.write_bytes(json_bytes({'schema_version': '1.0', 'items': [push]}))
        self.commit()
        report = self.report('M0-139')
        data = read_json(report)
        data['observed_feedback'] = [feedback_fingerprint(push)]
        report.write_bytes(json_bytes(data))
        receipt = archive_evidence(self.root, 'M0-139', report)
        ledger = self.ledger()
        task_by_id(ledger, 'M0-139').update(status='done', evidence=[receipt], next_action=None)
        ledger['focus_task_id'] = 'M0-141'
        self.save_ledger(ledger)
        self.commit()
        return base, rule, push

    def recurring_scope(self, base, *, wrong=None, current=None, native=None):
        head = source_identity(self.root)
        study = native[0] if native else self.merge_observation(base, head['git_head'])
        workflow = {'status': 'passed', 'expected_profile': 'full',
                    'run': {'id': 100, 'run_attempt': 1, 'head_sha': head['git_head'], 'event': 'pull_request'},
                    'checkout': {'head': head['git_head'], 'base': base, 'commit': study['sha'], 'pull_request': 1},
                    'required_checkouts': [{'name': name, 'job_id': index, 'commit': study['sha'], 'pull_request': 1}
                                           for index, name in enumerate((UBUNTU, WINDOWS), 1000)]}
        args = observations()
        jobs = [dict(job, id=index, head_sha=head['git_head'], verification_profile='full')
                for index, job in enumerate(args['jobs'], 1000)]
        if wrong == 'head':
            workflow['checkout']['head'] = '1' * 40
        elif wrong == 'base':
            workflow['checkout']['base'] = '1' * 40
        elif wrong == 'checkout':
            workflow['required_checkouts'][1]['commit'] = '1' * 40
        elif wrong == 'job':
            jobs[1]['conclusion'] = 'failure'
        elif wrong == 'profile':
            jobs[0]['verification_profile'] = 'tooling'
        elif wrong == 'scope':
            workflow['expected_profile'] = 'tooling'
        elif wrong == 'source':
            head['working_tree_dirty'] = True
        project = load_project(self.root)
        if current:
            current(project)
        evidence = (patch.object(integration, 'recurring_budget_evidence', return_value=native[1]) if native
                    else patch.object(integration, 'recurring_pr_evidence', return_value=wrong != 'evidence'))
        clock = patch.object(timing, 'hosted_timing', return_value=native[2]) if native else nullcontext()
        with evidence, clock:
            return integration.delivery_scope(self.root, project, base, source=head,
                                              policy=args['policy'], workflow=workflow, jobs=jobs)

    def test_recurring_binding_allows_only_its_open_push_after_protected_adoption(self):
        base, _, push = self.recurring_fixture()
        result = self.recurring_scope(base)
        self.assertEqual([], result['blockers'])
        handoff = result['recurring_main_push_budget']
        self.assertEqual((push['id'], 'M0-141', 'pending'),
                         (handoff['feedback_id'], handoff['follow_up_task_id'], handoff['status']))
        for wrong in ('head', 'base', 'checkout', 'job', 'profile', 'scope', 'source', 'evidence'):
            with self.subTest(wrong=wrong):
                self.assertIn(f"Unresolved major feedback: {push['id']}",
                              self.recurring_scope(base, wrong=wrong)['blockers'])
        for severity, owner in (('major', 'M0-139'), ('critical', 'M0-004')):
            with self.subTest(severity=severity):
                result = self.recurring_scope(base, current=lambda project: project['feedback']['items'].append({
                    'id': 'other-blocker', 'severity': severity, 'status': 'open', 'task_ids': [owner]}))
                self.assertEqual([f'Unresolved {severity} feedback: other-blocker'], result['blockers'])
        planned = self.recurring_scope(base, current=lambda project: project['tasks']['M0-139'].update(status='planned'))
        self.assertIn('The PR has no completed delivery task with archived evidence.', planned['blockers'])
        self.assertNotIn('recurring_main_push_budget', planned)
        feedback = self.root / 'project/feedback.json'
        items = read_json(feedback)
        items['items'][0].update(status='addressed', resolution={
            'cause': 'Fixture.', 'correction': 'Fixture.', 'prevention': 'Fixture.'})
        # Isolate the closure gate from the ordinary accepted-feedback check.
        closure = self.recurring_scope(base, current=lambda project: project['feedback']['items'].__setitem__(
            0, items['items'][0]))
        self.assertIn(f"Main-push timing closure lacks a verified main-push observation: {push['id']}", closure['blockers'])

    def test_completed_recurring_repair_requires_a_named_open_finding_and_ready_owner(self):
        base, _, push = self.recurring_fixture()
        self.assertEqual([], self.recurring_scope(base)['blockers'])

        def omit_obligation(project):
            project['feedback']['items'].clear()
            project['tasks'].pop('M0-141')

        def unready_owner(project):
            project['tasks']['M0-141']['checks'] = ['project-state']

        for change in (omit_obligation, unready_owner):
            with self.subTest(change=change.__name__):
                scope = self.recurring_scope(base, current=change)
                self.assertTrue(scope['blockers'])
                self.assertNotIn('recurring_main_push_budget', scope)

        def substitute_intake(project):
            older = self.git('rev-parse', push['verification_budget']['repair_commit'] + '^')
            project['feedback']['items'][0]['verification_budget']['repair_commit'] = older

        replaced = self.recurring_scope(base, current=substitute_intake)
        self.assertTrue(replaced['blockers'])
        self.assertNotIn('recurring_main_push_budget', replaced)

    def test_recurring_handoff_needs_one_complete_current_receipt_and_skips_older_receipts(self):
        base, _, push = self.recurring_fixture()
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-139')
        original, = task['evidence']
        full = read_json(self.root / original['path'])
        partial = {**full, 'checks': [row for row in full['checks'] if row['id'] in push['regression_checks']]}
        stale = {**full, 'source': {**full['source'], 'git_head': base, 'sha256': '4' * 64}}
        legacy = {key: value for key, value in full.items() if key != 'project_definition_sha256'}

        def receipt(suffix, value):
            relative = f'project/evidence/M0-139-{suffix}.json'
            data = json_bytes(value)
            (self.root / relative).write_bytes(data)
            return {'path': relative, 'sha256': digest(data)}

        task['evidence'] = [receipt('stale-complete', stale), receipt('current-partial', partial)]
        self.save_ledger(ledger)
        self.commit()
        split = self.recurring_scope(base)
        self.assertTrue(split['blockers'])
        self.assertNotIn('recurring_main_push_budget', split)

        task['evidence'] = [receipt('historical-schema', legacy), original]
        self.save_ledger(ledger)
        self.commit()
        scope = self.recurring_scope(base)
        self.assertEqual([], scope['blockers'])
        self.assertEqual(original['path'], scope['recurring_main_push_budget']['regression_receipt'])

    def test_recurring_binding_cannot_be_adopted_by_its_repair_candidate(self):
        base, _, push = self.recurring_fixture(protected=False)
        result = self.recurring_scope(base)
        self.assertNotIn('recurring_main_push_budget', result)
        self.assertIn('The recurring main-push budget binding requires separate protected adoption.', result['blockers'])
        self.assertIn(f"Unresolved major feedback: {push['id']}", result['blockers'])


    def test_recurring_policy_adoption_can_deliver_the_independent_policy_task(self):
        base = self.commit()
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['source_control']['recurring_main_push_budget'] = {
            'main_push_feedback_id': 'recurring-push-budget', 'repair_task_id': 'M0-139',
            'follow_up_task_id': 'M0-141', 'next_action': 'Observe the later protected main push.',
            'repair_commit': '4' * 40, 'finding_sha256': '1' * 64,
            'control_reports_sha256': '2' * 64, 'selected_check_count': 3,
        }
        path.write_bytes(json_bytes(delivery))
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.commit()
        result = self.recurring_scope(base)
        self.assertEqual(['M0-001'], result['completed_task_ids'])
        self.assertEqual([], result['blockers'])
        self.assertNotIn('recurring_main_push_budget', result)

    def native_observation(self, root, revision, *, run, elapsed, platform='Windows', event='push', head=None):
        report = read_json(self.report('M0-139', system=platform))
        run_id = f'windows-change-{run}-1' if platform == 'Windows' else f'fixture-linux-{run}'
        report.update(run_id=run_id, generated_at='2026-11-25T13:00:00Z', timing={'elapsed_seconds': elapsed - 30},
                      selection={'profile': 'full', 'checks': [row['id'] for row in report['checks']]},
                      native_requirement={'requested': platform == 'Windows', 'satisfied': True})
        report['source']['git_head'] = revision
        report['ci'] = {'repository': 'fixture/project', 'run_id': str(run), 'run_attempt': '1', 'event': event,
                        'head_sha': head or revision, 'checkout_sha': revision}
        directory = root / 'runs' / run_id
        directory.mkdir(parents=True)
        for row in report['checks']:
            original = self.root / row['log']
            (directory / f"{row['id']}.log").write_bytes(original.read_bytes())
            row['log'] = f".reports/runs/{run_id}/{row['id']}.log"
        raw = json_bytes(report)
        (directory / 'test-report.json').write_bytes(raw)
        locator = {'run_id': run_id, 'source_commit': revision, 'report_sha256': digest(raw)}
        sample = timing.report_sample(report, cache='warm')
        sample.update(
            job={'job_id': run, 'runner_label': 'windows-latest' if platform == 'Windows' else 'ubuntu-latest',
                 'status': 'completed', 'identity_issues': {'missing': []}},
            workflow_conclusion='success', hosted_head_branch='main' if event == 'push' else 'task',
            review_exclusion=None, windows_caches={'core': 'warm', 'browser': 'warm', 'recovery': 'warm',
                                                   'python': 'not_requested'},
        )
        sample['seconds']['total'] = elapsed
        return locator, sample

    def test_recurring_controls_require_three_bound_originals_and_identical_outcomes(self):
        base, rule, push = self.recurring_fixture()
        archive = self.root / '.reports' / 'archive-fixture'
        controls = [self.native_observation(archive, base, run=run, elapsed=elapsed)
                    for run, elapsed in enumerate((886, 938, 913), 41)]
        locators, samples = map(list, zip(*controls))
        push['diagnostic_reports'] = locators
        rule['control_reports_sha256'] = digest(json_bytes(sorted(
            (row['run_id'], row['source_commit'], row['report_sha256']) for row in locators)))
        rule['finding_sha256'] = feedback_fingerprint(push)
        rule['selected_check_count'] = len(samples[0]['selected'])
        project = load_project(self.root)
        result = {'budget_recovery': [{'feedback_id': push['id'], 'recovery': {
            'unavailable_reports': [], 'conflicting_reports': [], 'comparisons': [{'history_missing': False}]}}]}
        view = {'samples': samples, 'input_issues': []}

        def verify():
            return integration.recurring_budget_evidence(self.root, project, base, rule, push)

        with (patch.object(integration, 'diagnostic_archive_root', return_value=archive),
              patch.object(timing, 'retained_budget_inputs', return_value=([], [], [])),
              patch.object(timing, 'timing_view', return_value=view),
              patch.object(timing, 'budget_follow_up', return_value=result)):
            self.assertIsNotNone(verify())
            for change in ('missing', 'failed', 'partial-cache', 'changed-coverage'):
                with self.subTest(change=change):
                    view['samples'] = deepcopy(samples)
                    if change == 'missing':
                        view['samples'].pop()
                    elif change == 'failed':
                        view['samples'][1]['workflow_conclusion'] = 'failure'
                    elif change == 'partial-cache':
                        view['samples'][1]['windows_caches']['recovery'] = 'partial'
                    else:
                        view['samples'][1]['checks'][0]['reason_code'] = 'unrelated'
                    self.assertIsNone(verify())
            view['samples'] = samples
            changed = deepcopy(locators)
            changed[0]['report_sha256'] = '9' * 64
            push['diagnostic_reports'] = changed
            self.assertIsNone(verify())
            push['diagnostic_reports'] = locators
            push['regression_checks'] = ['project-state']
            self.assertIsNone(verify())
            push['regression_checks'] = ['rust-lints']
            log = next((archive / 'runs' / locators[-1]['run_id']).glob('*.log'))
            log.write_bytes(log.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'Missing or changed diagnostic bytes'):
                verify()

    def test_recurring_pr_reads_both_original_harnesses_and_enforces_the_native_deadline(self):
        base, _, push = self.recurring_fixture()
        head = source_identity(self.root)
        merge = self.merge_observation(base, head['git_head'])
        project = load_project(self.root)
        catalog = project['checks']
        selected = sorted(key for key, check in catalog.items()
                          if check.get('cadence', 'change') == 'change' and check['status'] != 'retired')
        rows = []
        for job, platform in enumerate(('Linux', 'Windows'), 1000):
            artifacts = self.root / '.reports' / 'ci' / '100' / platform.lower()
            locator, sample = self.native_observation(artifacts, merge['sha'], run=100, elapsed=839,
                                                       platform=platform, event='pull_request', head=head['git_head'])
            original = artifacts / 'runs' / locator['run_id'] / 'test-report.json'
            report = read_json(original)
            report['source'] = {**head, 'git_head': merge['sha']}
            report['selection']['checks'] = selected
            report['checks'] = []
            for key in selected:
                check = catalog[key]
                blocked = ('not_implemented' if check['status'] != 'implemented' else
                           'unsupported_platform' if platform == 'Linux' and check['platforms'] == 'windows-x64' else None)
                result = {'id': key, 'status': 'blocked' if blocked else 'passed'}
                if blocked:
                    result['reason_code'] = blocked
                else:
                    data = f'{key} original fixture\n'.encode()
                    (original.parent / f'{key}.log').write_bytes(data)
                    result.update(exit_code=0, log=f".reports/runs/{locator['run_id']}/{key}.log", log_sha256=digest(data))
                report['checks'].append(result)
            if platform == 'Linux':
                report['status'] = 'blocked'
            data = json_bytes(report)
            original.write_bytes(data)
            (artifacts / 'test-report.json').write_bytes(data)
            (artifacts / 'verification-scope.json').write_bytes(json_bytes({
                'kind': 'scopehub-verification-scope-v1', 'profile': 'full', 'base': base, 'checkout': merge['sha'],
                'reason_code': 'change_requires_full', 'change_count': 1, 'report_sha256': digest(data),
            }))
            rows.append({**sample, **timing.report_sample(report, cache='warm'),
                         'job': {**sample['job'], 'job_id': job},
                         'seconds': {**sample['seconds'], 'total': 839}})
        coverage = timing.coverage_key(rows[1])
        recovery = {'status': 'pending', 'comparisons': [{'latest_attempt': None}]}

        clock = {**rows[1]['ci'], 'status': 'completed', 'conclusion': 'success',
                 'workflow': {'matrix_complete': True, 'identity_complete': True, 'total_elapsed_seconds': 839,
                              'runner_work_seconds': 1600},
                 'jobs': [{**row['job'], 'platform': row['platform'], 'conclusion': 'success',
                           'identity_issues': {'missing': [], 'conflicts': []},
                           'seconds': {'runner': 800, 'total': 839}} for row in rows]}

        def scope(samples, observed_clock=clock):
            return self.recurring_scope(base, native=(merge, (samples, coverage, self.root, recovery), observed_clock))

        self.assertEqual([], scope(rows)['blockers'])
        slow_clock = deepcopy(clock)
        slow_clock['jobs'][1]['seconds']['total'] = 929
        slow_clock['workflow']['total_elapsed_seconds'] = 929
        slow_rows = [rows[0], {**rows[1], 'seconds': {**rows[1]['seconds'], 'total': 929}}]
        self.assertTrue(scope(slow_rows, slow_clock)['blockers'])
        variations = [
            [rows[0], {**rows[1], **changes}]
            for changes in ({'source_commit': base}, {'inputs_sha256': '3' * 64}, {'selected': selected[:-1]},
                            {'profile': 'tooling'}, {'workflow_conclusion': 'failure'},
                            {'job': {**rows[1]['job'], 'job_id': 9154}},
                            {'seconds': {**rows[1]['seconds'], 'total': 929}})
        ]
        variations.extend(([rows[1]], [{**rows[0], 'run_id': 'discarded-linux'}, rows[1]],
                           [rows[0], {**rows[1], 'run_id': 'discarded-windows'}]))
        for changed in variations:
            with self.subTest(identities=[sample['run_id'] for sample in changed]):
                result = scope(changed)
                self.assertIn(f"Unresolved major feedback: {push['id']}", result['blockers'])
                self.assertNotIn('recurring_main_push_budget', result)
        stored = self.root / '.reports' / 'ci' / '100' / 'linux' / 'verification-scope.json'
        record = read_json(stored)
        stored.write_bytes(json_bytes({**record, 'base': '1' * 40}))
        self.assertTrue(scope(rows)['blockers'])
        stored.write_bytes(json_bytes(record))
        original = self.root / '.reports' / 'ci' / '100' / 'windows' / 'test-report.json'
        original.write_bytes(original.read_bytes() + b'\n')
        self.assertTrue(scope(rows)['blockers'])

    def test_recurring_closure_requires_the_later_protected_push_and_original_receipt(self):
        base, rule, previous = self.recurring_fixture()
        main = self.merge_observation(base, source_identity(self.root)['git_head'])['sha']
        project = load_project(self.root)
        project['tasks']['M0-141']['status'] = 'done'
        push = dict(previous, status='addressed')
        repair = previous['verification_budget']['repair_commit']
        archive = self.root / '.reports' / 'archive-close'
        locator, sample = self.native_observation(archive, main, run=101, elapsed=899)
        witness = timing.recovery_observation(sample)
        recovery = {'status': 'recovered', 'repair_commit': repair, 'comparisons': [{'latest_attempt': witness}]}
        receipt = {'ci': {**sample['ci']}, 'source': {'git_head': main, 'sha256': sample['inputs_sha256']},
                   'environment': {'system': 'Windows', 'machine': 'AMD64'}, 'run_id': locator['run_id'],
                   'report_sha256': locator['report_sha256']}
        with (patch.object(integration, 'recurring_budget_evidence', return_value=(
                  [sample], timing.coverage_key(sample), archive, recovery)),
              patch.object(integration, 'budget_regression_receipts', return_value=[('receipt.json', receipt)])):
            def verify(revision=main):
                return integration.main_push_budget_closure(
                    self.root, project, revision, rule, push, previous, observations()['policy'])

            self.assertTrue(verify())
            self.assertFalse(verify(base))
            receipt['ci']['event'] = 'pull_request'
            self.assertFalse(verify())
            receipt['ci']['event'] = 'push'
            for excess in (901, None):
                witness['elapsed_seconds'] = excess
                self.assertFalse(verify())
            witness['elapsed_seconds'] = 899
            receipt['report_sha256'] = '5' * 64
            self.assertFalse(verify())


class ProtectedPythonObservationTests(IntegrationFixture):
    repair_task = 'M0-153'
    policy_task = 'M0-159'
    follow_up_task = 'M0-160'
    policy_key = 'protected_python_observation'
    finding_id = 'windows-main-python-discovery-process-deadline'
    python_check = 'python-contracts-workers'
    reviewed_inventory = (('test_a (fixture.Serial)', 'ok'), ('test_b (fixture.Serial)', 'ok'))
    inventory_sha256 = digest(json_bytes(reviewed_inventory))
    resolution = {'cause': 'Observed fixture timeout.', 'correction': 'Fixture repair.',
                  'prevention': 'Verify the protected observation.'}

    def save_feedback(self, *findings):
        (self.root / 'project/feedback.json').write_bytes(
            json_bytes({'schema_version': '1.0', 'items': list(findings)}))

    def finish(self, task_id, *, finding=None, event=None, run_id=None):
        report = self.report(task_id, system='Windows' if event else 'Linux')
        data = read_json(report)
        if event:
            revision = data['source']['git_head']
            run_id = run_id or ('101' if event == 'workflow_dispatch' else '102')
            data['ci'] = {'provider': 'github-actions', 'repository': 'fixture/project',
                          'run_id': run_id, 'run_attempt': '1', 'event': event,
                          'head_sha': revision, 'checkout_sha': revision,
                          'run_url': f'https://github.com/fixture/project/actions/runs/{run_id}'}
        if finding:
            data['observed_feedback'] = [feedback_fingerprint(finding)]
        report.write_bytes(json_bytes(data))
        receipt = archive_evidence(self.root, task_id, report)
        ledger = self.ledger()
        task = task_by_id(ledger, task_id)
        task.update(status='done', evidence=[*task['evidence'], receipt], next_action=None)
        if ledger['focus_task_id'] == task_id:
            ledger['focus_task_id'] = self.follow_up_task if task_id == self.repair_task else 'M0-003'
        self.save_ledger(ledger)
        return receipt

    def qualification_fixture(self, *, protected=True, finish_repair=True, extra_finding=False, timing_rules=False):
        self.git('remote', 'add', 'origin', 'https://github.com/fixture/project')
        (self.root / 'project/branch-protection.json').write_bytes(json_bytes(observations()['policy']))
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        python = deepcopy(next(row for row in catalog['checks'] if row['id'] == 'rust-lints'))
        python.update(id=self.python_check, timeout_seconds=900, windows_timeout_seconds=1200)
        catalog['checks'].append(python)
        catalog['required_change_checks'].append(self.python_check)
        path.write_bytes(json_bytes(catalog))

        ledger = self.ledger()
        aliases = {'M0-001': self.repair_task, 'M0-002': self.follow_up_task, 'M0-004': self.policy_task}
        for task in ledger['tasks']:
            task['id'] = aliases.get(task['id'], task['id'])
            task['depends_on'] = [aliases.get(key, key) for key in task['depends_on']]
        ledger['focus_task_id'] = self.repair_task
        repair = task_by_id(ledger, self.repair_task)
        repair['checks'] = ['project-state', self.python_check]
        task_by_id(ledger, self.policy_task)['plan'] = deepcopy(repair['plan'])
        repair['plan']['risks'].append({'failure': 'Incomplete serial discovery.', 'checks': [self.python_check]})
        follow_up = task_by_id(ledger, self.follow_up_task)
        follow_up.update(depends_on=[self.repair_task], checks=['project-state', self.python_check],
                         plan=deepcopy(repair['plan']),
                         next_action='Retain and verify the first protected main push containing the repair.')
        if timing_rules:
            for task_id in (REPAIR_TASK, FOLLOW_UP_TASK, 'M0-139', 'M0-141'):
                ledger['tasks'].append({**deepcopy(follow_up), 'id': task_id, 'depends_on': []})
        self.save_ledger(ledger)
        initial = self.commit()

        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        repair_source = self.commit()
        self.git('checkout', '--quiet', '--detach', initial)

        finding = {'id': self.finding_id, 'source': 'Synthetic original timeout.',
                   'observation': 'The serial process did not report a terminal result.',
                   'severity': 'major', 'status': 'open',
                   'task_ids': [self.repair_task, self.follow_up_task], 'regression_checks': [self.python_check],
                   'resolution': None, 'deferral_reason': None,
                   'diagnostic_reports': [{'run_id': 'windows-change-99-1', 'source_commit': initial,
                                           'report_sha256': '1' * 64}]}
        rule = {'policy_task_id': self.policy_task, 'repair_task_id': self.repair_task,
                'follow_up_task_id': self.follow_up_task, 'feedback_id': self.finding_id,
                'repair_commit': repair_source, 'finding_sha256': integration.python_finding_identity(finding)}
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['source_control'][self.policy_key] = rule
        findings = []
        if timing_rules:
            adopted = read_json(REPO / 'project/delivery.json')['source_control']
            for key in ('post_merge_main_push_budget', 'recurring_main_push_budget'):
                delivery['source_control'][key] = adopted[key]
            findings = [
                dict(finding, id=key, task_ids=[owner]) for key, owner in
                ((PR_BUDGET, REPAIR_TASK), (PUSH_BUDGET, FOLLOW_UP_TASK))
            ]
            self.save_feedback(*findings)
        if protected:
            path.write_bytes(json_bytes(delivery))
            self.commit()
            self.finish(self.policy_task)
            base = self.commit()
        else:
            base = initial
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.git('merge', '--quiet', '--no-ff', '--no-edit', repair_source)
        if not protected:
            path.write_bytes(json_bytes(delivery))
        if extra_finding:
            findings.append(dict(finding, id='unrelated-major-repair-finding'))
        self.save_feedback(finding, *findings)
        ledger = self.ledger()
        task_by_id(ledger, self.repair_task)['status'] = 'active'
        self.save_ledger(ledger)
        self.commit()
        if not protected:
            self.finish(self.policy_task)
            self.commit()
        if finish_repair:
            receipt = self.finish(self.repair_task, finding=finding, event='workflow_dispatch')
            ledger = self.ledger()
            task_by_id(ledger, self.follow_up_task)['protected_python_handoff'] = {
                'regression_receipt': receipt, 'inventory_sha256': self.inventory_sha256,
            }
            self.save_ledger(ledger)
            self.commit()
        return base, rule, finding

    def scope(self, base, *, inventory=True, push=None, reject_job=False, wrong_input=False, modify=None):
        source = source_identity(self.root)
        if wrong_input:
            source['sha256'] = '3' * 64
        head = source['git_head']
        tested = self.merge_observation(base, head)['sha']
        workflow = {'status': 'passed', 'expected_profile': 'full',
                    'run': {'id': 100, 'run_attempt': 1, 'head_sha': head, 'event': 'pull_request'},
                    'checkout': {'head': head, 'base': base, 'commit': tested, 'pull_request': 1},
                    'required_checkouts': [{'name': name, 'job_id': index, 'commit': tested, 'pull_request': 1}
                                           for index, name in enumerate((UBUNTU, WINDOWS), 1000)]}
        observed = observations()
        jobs = [dict(job, id=index, head_sha=head, verification_profile='full')
                for index, job in enumerate(observed['jobs'], 1000)]
        if reject_job:
            jobs[1]['conclusion'] = 'failure'
        project = load_project(self.root)
        if modify:
            modify(project)
        qualified_inventory = {'inventory_sha256': self.inventory_sha256} if inventory else None
        push_result = (patch.object(integration, 'python_push_observation', return_value=push)
                       if push is not None else nullcontext())
        with (patch.object(integration, 'python_pr_inventory', return_value=qualified_inventory),
              push_result):
            return integration.delivery_scope(self.root, project, base, source=source,
                                              policy=observed['policy'], workflow=workflow, jobs=jobs)

    def test_python_observation_admits_only_the_qualified_open_finding(self):
        base, rule, _ = self.qualification_fixture(extra_finding=True)
        result = self.scope(base)
        self.assertEqual(['Unresolved major feedback: unrelated-major-repair-finding'], result['blockers'])
        handoff = result[self.policy_key]
        self.assertEqual((self.finding_id, self.repair_task, self.follow_up_task, rule['repair_commit'], 'pending'),
                         tuple(handoff[key] for key in ('feedback_id', 'repair_task_id', 'follow_up_task_id',
                                                        'repair_commit', 'status')))
        for rejection in ({'inventory': False}, {'reject_job': True}, {'wrong_input': True},
                          {'modify': lambda project: project['feedback']['items'][0].update(
                              source='Substituted reference to the same report.')},
                          {'modify': lambda project: project['feedback']['items'][0].update(
                              task_ids=[self.repair_task])},
                          {'modify': lambda project: project['feedback']['items'][0].update(status='deferred')},
                          {'modify': lambda project: project['tasks'][self.follow_up_task].update(
                              depends_on=[self.repair_task, 'M0-003'])},
                          {'modify': lambda project: project['tasks'][self.repair_task].update(evidence=[])}):
            with self.subTest(rejection=tuple(rejection)):
                rejected = self.scope(base, **rejection)
                self.assertNotIn(self.policy_key, rejected)
                self.assertIn(f'Unresolved major feedback: {self.finding_id}', rejected['blockers'])

    def test_python_observation_keeps_an_active_repair_blocked(self):
        base, _, _ = self.qualification_fixture(finish_repair=False)
        result = self.scope(base)
        self.assertIn(f'Unfinished delivery task: {self.repair_task}', result['blockers'])
        self.assertIn(f'Unresolved major feedback: {self.finding_id}', result['blockers'])
        self.assertNotIn(self.policy_key, result)

    def test_python_observation_policy_cannot_adopt_and_use_itself(self):
        base, _, _ = self.qualification_fixture(protected=False)
        result = self.scope(base)
        self.assertIn('The Windows Python observation binding requires separate protected adoption.', result['blockers'])
        self.assertIn(f'Unresolved major feedback: {self.finding_id}', result['blockers'])
        self.assertNotIn(self.policy_key, result)

        omitted = self.scope(base, modify=lambda project: project['delivery']['source_control'].pop(self.policy_key))
        self.assertIn('Windows Python repair delivery requires a separately adopted protected observation binding.',
                      omitted['blockers'])
        self.assertNotIn(self.policy_key, omitted)

    def test_python_observation_cannot_close_from_pre_integration_receipts(self):
        base, _, finding = self.qualification_fixture()
        self.finish(self.follow_up_task, finding=finding, event='workflow_dispatch')
        self.save_feedback(dict(finding, status='addressed', resolution=self.resolution))
        self.commit()
        result = self.scope(base)
        self.assertTrue(any('open protected handoff' in blocker for blocker in result['blockers']), result)
        self.assertNotIn(self.policy_key, result)

    def test_protected_python_handoff_requires_the_committed_selected_receipt_and_inventory(self):
        base, _, finding = self.qualification_fixture()
        accepted = self.scope(base)[self.policy_key]
        alternate = self.finish(self.repair_task, finding=finding, event='workflow_dispatch', run_id='103')
        ledger = self.ledger()
        follow_up = task_by_id(ledger, self.follow_up_task)
        for replacement in ({'inventory_sha256': '4' * 64}, {'regression_receipt': alternate}):
            with self.subTest(replacement=replacement):
                follow_up['protected_python_handoff'] = {**accepted['protected_python_handoff'], **replacement}
                self.save_ledger(ledger)
                self.commit()
                rejected = self.scope(base)
                self.assertEqual('record_required', rejected[self.policy_key]['status'])
                self.assertEqual(accepted['protected_python_handoff'],
                                 rejected[self.policy_key]['protected_python_handoff'])
                self.assertIn(f'Unresolved major feedback: {self.finding_id}', rejected['blockers'])

    def test_ordinary_closure_uses_only_the_inventory_selected_before_merge(self):
        base, _, finding = self.qualification_fixture()
        self.finish(self.repair_task, finding=finding, event='workflow_dispatch', run_id='103')
        head = self.commit()
        pending = self.scope(base)[self.policy_key]
        handoff = pending['protected_python_handoff']
        self.assertEqual('pending', pending['status'])
        main = self.merge_observation(base, head)['sha']
        self.git('checkout', '--quiet', '--detach', main)
        self.git('update-ref', 'refs/remotes/origin/main', main)
        self.finish(self.follow_up_task, finding=finding, event='push')
        self.save_feedback(dict(finding, status='addressed', resolution=self.resolution))
        self.commit()
        receipt = read_json(self.root / handoff['regression_receipt']['path'])
        observation = {
            'diagnostic': {'run_id': 'windows-change-102-1', 'source_commit': main, 'report_sha256': '4' * 64},
            'report': {'source': receipt['source'], 'project_definition_sha256': receipt['project_definition_sha256']},
        }
        for inventory, status in ((self.reviewed_inventory[::-1], 'failed'), (self.reviewed_inventory, 'passed')):
            with self.subTest(status=status), patch.object(integration, 'python_push_evidence',
                                                           return_value={**observation, 'inventory': inventory}):
                result = self.scope(main)
                post_merge = result[self.policy_key]
                self.assertEqual(status, post_merge['status'])
                self.assertEqual(handoff, post_merge['protected_python_handoff'])
                if status == 'failed':
                    self.assertEqual('inventory_mismatch', post_merge['reason_code'])
                    self.assertIn('Windows Python deadline closure requires the verified first protected push.',
                                  result['blockers'])
                else:
                    self.assertEqual([], result['blockers'])
                    changed = self.scope(main, modify=lambda project: project['tasks'][self.follow_up_task].pop(
                        'protected_python_handoff'))
                    self.assertIn('The protected Windows Python handoff changed or disappeared.', changed['blockers'])

    def test_python_observation_retains_failures_and_only_closes_after_first_main_success(self):
        base, binding, finding = self.qualification_fixture(timing_rules=True)
        pending = self.scope(base)
        self.assertEqual([], pending['blockers'], pending)
        self.assertEqual('pending', pending[self.policy_key]['status'])
        head = source_identity(self.root)['git_head']
        main = self.merge_observation(base, head)['sha']
        self.git('checkout', '--quiet', '--detach', main)
        project = load_project(self.root)
        findings = project['feedback']['items']
        recurring_id = project['delivery']['source_control']['recurring_main_push_budget']['main_push_feedback_id']
        self.assertNotIn(recurring_id, {item['id'] for item in findings})
        recurring = dict(finding, id=recurring_id, task_ids=['M0-139', 'M0-141'])
        self.save_feedback(*findings, recurring)
        with_prior_timing_finding = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', with_prior_timing_finding)
        unchanged = self.scope(with_prior_timing_finding)
        self.assertNotIn(self.policy_key, unchanged)
        self.assertEqual(['The PR has no completed delivery task with archived evidence.'], unchanged['blockers'])
        missing = integration.python_push_observation(self.root, load_project(self.root), with_prior_timing_finding, binding,
                                                     {}, observations()['policy'])
        self.assertEqual(('missing', main), (missing['status'], missing['first_push_commit']))

        self.finish(self.follow_up_task, finding=finding, event='push')
        self.save_feedback(dict(finding, status='addressed', resolution=self.resolution), *findings[1:], recurring)
        self.commit()
        for protected_base in (main, with_prior_timing_finding):
            for status in ('missing', 'failed', 'passed'):
                with self.subTest(prior_timing_finding=protected_base != main, status=status):
                    push = {'status': status, 'first_push_commit': main, 'next_action': 'Fixture follow-through.'}
                    result = self.scope(protected_base, push=push)
                    self.assertEqual(status == 'passed', not result['blockers'], result)
                    observation = result[self.policy_key]
                    self.assertEqual((self.follow_up_task, status, main),
                                     (observation['follow_up_task_id'], observation['status'],
                                      observation['first_push_commit']))

    def test_failed_first_python_push_can_record_follow_through_with_the_finding_open(self):
        base, _, _ = self.qualification_fixture()
        head = source_identity(self.root)['git_head']
        main = self.merge_observation(base, head)['sha']
        self.git('checkout', '--quiet', '--detach', main)
        self.git('update-ref', 'refs/remotes/origin/main', main)

        locator = {'run_id': 'windows-change-102-1', 'source_commit': main, 'report_sha256': '4' * 64}
        ledger = self.ledger()
        task_by_id(ledger, self.follow_up_task).update(
            diagnostic_reports=[locator], next_action='Retain the failed result and investigate the serial deadline.')
        record_task = deepcopy(task_by_id(ledger, self.policy_task))
        record_task.update(id='M0-161', status='planned', evidence=[], next_action='Record the failed observation.')
        ledger['tasks'].append(record_task)
        self.save_ledger(ledger)
        self.commit()
        self.finish(record_task['id'])
        self.commit()

        push = {'status': 'failed', 'first_push_commit': main, 'diagnostic': locator,
                'next_action': 'Retain the failed result and investigate the serial deadline.'}
        result = self.scope(main, push=push)
        self.assertEqual([], result['blockers'], result)
        self.assertEqual('failed', result[self.policy_key]['status'])
        self.assertEqual('open', load_project(self.root)['feedback']['items'][0]['status'])
