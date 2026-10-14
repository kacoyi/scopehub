"""Reject premature integration despite superficially successful CI."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import integration
from scripts.project import ProjectError, archive_evidence, json_bytes, load_project, source_identity
from tests.project.test_continuity import ProjectFixture
from tests.project.fixtures import task_by_id


def observations():
    policy = {'repository': 'fixture/project', 'branch': 'main', 'configuration': {
        'required_status_checks': {'strict': True, 'checks': [{'context': 'native', 'app_id': 123}]},
        'required_pull_request_reviews': {'dismiss_stale_reviews': True, 'required_approving_review_count': 0},
        'enforce_admins': True, 'allow_force_pushes': False, 'allow_deletions': False,
        'required_conversation_resolution': True,
    }}
    protection = deepcopy(policy['configuration'])
    for key in ['enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution']:
        protection[key] = {'enabled': protection[key]}
    pr = {'number': 1, 'url': 'https://github.com/fixture/project/pull/1', 'state': 'OPEN', 'isDraft': False,
          'headRefOid': 'a' * 40, 'baseRefOid': 'b' * 40, 'baseRefName': 'main',
          'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN', 'reviewDecision': ''}
    return {'source': {'git_head': 'a' * 40, 'working_tree_dirty': False}, 'pr': pr, 'final_pr': deepcopy(pr),
            'base_current': True, 'workflow': {'status': 'passed', 'reason': 'Observed success.',
                                             'run': {'id': 1, 'run_attempt': 2}},
            'jobs': [{'name': 'native', 'status': 'completed', 'conclusion': 'success', 'head_sha': 'a' * 40}],
            'policy': policy, 'protection': protection,
            'scope': {'completed_task_ids': ['M0-001'], 'blockers': []}, 'observations_stable': True}


class IntegrationAssessmentTests(unittest.TestCase):
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
        args['jobs'] *= 2
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


class IntegrationHistoryTests(ProjectFixture):
    def commit(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, capture_output=True, check=True)
        subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve fixture'],
                       cwd=self.root, capture_output=True, check=True)
        return source_identity(self.root)['git_head']

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

    def test_collection_is_read_only_and_checks_the_latest_attempt_and_live_identity(self):
        args = observations()
        (self.root / 'project/branch-protection.json').write_bytes(json_bytes(args['policy']))
        base = self.commit()
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        head = self.commit()
        pr = dict(args['pr'], baseRefOid=base, headRefOid=head)
        run = {'id': 100, 'head_sha': head, 'status': 'completed', 'conclusion': 'success',
               'run_attempt': 2, 'path': '.github/workflows/verify.yml', 'event': 'pull_request'}
        queries = []

        def command(argv, root):
            queries.append(argv)
            if argv[0] == 'git':
                return original(argv, root)
            if argv[:3] == ['gh', 'pr', 'view']:
                return json.dumps(pr)
            if 'actions/runs' in ' '.join(argv) and '--paginate' not in argv:
                return json.dumps({'workflow_runs': [run]})
            if '--paginate' in argv:
                self.assertIn('/runs/100/attempts/2/jobs?', argv[-1])
                return json.dumps([{'jobs': [dict(args['jobs'][0], head_sha=head)]}])
            self.assertEqual('repos/fixture/project/branches/main/protection', argv[-1])
            return json.dumps(args['protection'])

        original = integration.command
        with (patch.object(integration, 'github_identity', return_value='fixture/project'),
              patch.object(integration, 'command', side_effect=command)):
            result = integration.inspect(self.root, 1)
        self.assertTrue(result['technical_ready'])
        self.assertEqual(2, sum(argv[:3] == ['gh', 'pr', 'view'] for argv in queries))
        self.assertEqual(head, result['source']['git_head'])
        self.assertFalse(source_identity(self.root)['working_tree_dirty'])
        with patch.object(integration, 'github_identity', return_value='unrelated/project'):
            with self.assertRaisesRegex(ProjectError, 'another repository'):
                integration.inspect(self.root, 1)
