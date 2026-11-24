"""Reject stale hosted results and preserve provenance when importing native evidence."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import ci
from scripts.check import ci_provenance
from scripts.project import (
    ProjectError, archive_evidence, checkpoint, digest, json_bytes, read_json, source_identity,
)
from tests.project.test_continuity import ProjectFixture


class HostedStatusTests(unittest.TestCase):
    head = 'a' * 40

    def run_record(self, **changes):
        return {'id': 10, 'head_sha': self.head, 'path': ci.WORKFLOW, 'status': 'completed',
                'conclusion': 'success', 'run_attempt': 1, 'event': 'pull_request', 'head_branch': 'main',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'head_repository': {'full_name': 'fixture/scopehub'},
                'html_url': 'https://github.com/fixture/scopehub/actions/runs/10', **changes}

    def test_other_commits_and_workflows_do_not_count(self):
        result = ci.classify_runs([self.run_record(head_sha='b' * 40),
                                   self.run_record(path='.github/workflows/unrelated.yml')], self.head)
        self.assertEqual('blocked', result['status'])
        self.assertIsNone(result['run'])

    def test_missing_running_cancelled_and_failed_runs_are_not_success(self):
        self.assertEqual('blocked', ci.classify_runs([], self.head)['status'])
        for status, conclusion, expected in [('queued', None, 'blocked'), ('in_progress', None, 'blocked'),
                                             ('completed', 'cancelled', 'failed'), ('completed', 'timed_out', 'failed'),
                                             ('completed', 'failure', 'failed')]:
            with self.subTest(status=status, conclusion=conclusion):
                result = ci.classify_runs([self.run_record(status=status, conclusion=conclusion)], self.head)
                self.assertEqual(expected, result['status'])

    def test_latest_failure_or_retry_overrides_older_green_run(self):
        for failure in [self.run_record(id=11, conclusion='failure'),
                        self.run_record(run_attempt=2, conclusion='failure')]:
            self.assertEqual('failed', ci.classify_runs([failure, self.run_record()], self.head)['status'])

    def test_dirty_source_and_explicit_old_run_cannot_claim_current_success(self):
        self.assertEqual('blocked', ci.classify_runs([self.run_record()], self.head, dirty=True)['status'])
        self.assertEqual('blocked', ci.classify_runs([self.run_record()], self.head, selected=True)['status'])
        self.assertEqual('passed', ci.classify_runs([self.run_record()], self.head)['status'])

    def inspect(self, records, *, branch='main', upstream=None, unavailable=None):
        source = {'git_head': self.head, 'working_tree_dirty': False}
        requests = []
        repository = 'fixture/scopehub'

        def command(argv, root):
            if argv[0] == 'git':
                lines = [f'# branch.head {branch}']
                if upstream:
                    lines.append(f'# branch.upstream {upstream}')
                return '\n'.join(lines)
            self.assertIn(f'repos/{repository}/actions/workflows/verify.yml/runs', argv)
            query = dict(argv[i + 1].split('=', 1) for i, flag in enumerate(argv) if flag == '-f')
            requests.append(query)
            if query['event'] == unavailable:
                raise ProjectError('Hosted data unavailable')
            values = [r for r in records if r['event'] == query['event'] and r['path'] == ci.WORKFLOW
                      and all(r[key] == query[arg] for arg, key in
                              [('head_sha', 'head_sha'), ('branch', 'head_branch'), ('status', 'status')] if arg in query)]
            values.sort(key=lambda r: (r['id'], r['run_attempt']), reverse=True)
            if '--paginate' in argv:
                self.assertIn('--slurp', argv)
                return json.dumps([{'workflow_runs': [r]} for r in values] or [{'workflow_runs': []}])
            return json.dumps({'workflow_runs': values[:int(query['per_page'])]})

        with (patch.object(ci, 'github_identity', return_value=repository),
              patch.object(ci, 'source_identity', return_value=source),
              patch.object(ci, 'read_json', return_value={'branch': 'main'}),
              patch.object(ci, 'ci_budget_status', return_value={}),
              patch.object(ci, 'command', side_effect=command)):
            return ci.status(), requests

    def test_main_push_keeps_its_own_status_with_manual_and_weekly_results(self):
        manual = self.run_record(id=12, event='workflow_dispatch')
        weekly = self.run_record(id=13, event='schedule')
        failed_push = self.run_record(event='push', conclusion='failure')
        result, queries = self.inspect([manual, weekly, failed_push])
        self.assertEqual(('failed', 10), (result['status'], result['run']['id']))
        self.assertEqual(('passed', 12), (result['manual_verification']['status'], result['manual_verification']['run']['id']))
        self.assertFalse(result['weekly_regression']['requires_review'])
        self.assertEqual([{'event': event, 'branch': 'main', 'head_sha': self.head, 'per_page': '100'}
                          for event in ['push', 'workflow_dispatch']], queries[:2])
        self.assertEqual({'event': 'schedule', 'branch': 'main', 'status': 'completed', 'per_page': '1'}, queries[2])
        result, _ = self.inspect([manual, weekly])
        self.assertEqual(('blocked', None), (result['status'], result['run']))
        self.assertEqual('passed', result['manual_verification']['status'])

    def test_weekly_attention_survives_a_new_head_narrow_pass_or_queued_weekly(self):
        push = self.run_record(id=16, event='push')
        failed = self.run_record(id=11, head_sha='b' * 40, event='schedule', conclusion='failure')
        queued = self.run_record(id=17, event='schedule', status='queued', conclusion=None)
        result, _ = self.inspect([push, failed, queued])
        self.assertEqual('passed', result['status'])
        weekly = result['weekly_regression']
        self.assertTrue(weekly['requires_review'])
        self.assertEqual(('failed', 11, 'b' * 40), (weekly['status'], weekly['run']['id'], weekly['run']['head_sha']))
        recovered, _ = self.inspect([push, failed, dict(queued, status='completed', conclusion='success')])
        self.assertFalse(recovered['weekly_regression']['requires_review'])
        missing, _ = self.inspect([push], unavailable='schedule')
        self.assertEqual('passed', missing['status'])
        self.assertEqual('unavailable', missing['weekly_regression']['status'])
        self.assertTrue(missing['weekly_regression']['requires_review'])

    def test_weekly_missing_expired_or_undated_remains_visible_with_successful_main(self):
        main = self.run_record(event='push')
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        for records, outcome, freshness in [
            ([], 'missing', 'unknown'),
            ([self.run_record(event='schedule', created_at=old)], 'passed', 'overdue'),
            ([self.run_record(event='schedule', created_at=None)], 'passed', 'unknown'),
        ]:
            with self.subTest(outcome=outcome, freshness=freshness):
                result, _ = self.inspect([main, *records])
                self.assertEqual('passed', result['status'])
                weekly = result['weekly_regression']
                self.assertEqual((outcome, freshness, True),
                                 (weekly['status'], weekly['freshness'], weekly['requires_review']))

    def test_task_branch_requires_its_own_origin_pr_even_at_a_shared_commit(self):
        ours = self.run_record(id=14, head_branch='feature', conclusion='failure')
        other = self.run_record(id=24, head_branch='other')
        fork = self.run_record(id=22, head_branch='feature', head_repository={'full_name': 'another/scopehub'})
        manual = self.run_record(id=23, head_branch='feature', event='workflow_dispatch')
        result, queries = self.inspect([ours, other, fork, manual, self.run_record(id=25, event='push')],
                                       branch='local-alias', upstream='origin/feature')
        self.assertEqual(('failed', 14), (result['status'], result['run']['id']))
        self.assertEqual(('pull_request', 'feature', self.head),
                         (queries[0]['event'], queries[0]['branch'], queries[0]['head_sha']))
        self.assertEqual(23, result['manual_verification']['run']['id'])
        first_push, _ = self.inspect([ours, self.run_record(id=25, event='push')], branch='feature', upstream='origin/main')
        self.assertEqual(('failed', 14), (first_push['status'], first_push['run']['id']))

    def test_detached_source_checks_the_main_push_or_unambiguous_pr(self):
        prs = [self.run_record(head_branch='one'), self.run_record(id=11, head_branch='two')]
        push = self.run_record(id=8, event='push', conclusion='failure')
        for history, expected, observed in [([*prs, push], 'failed', 8), (prs[:1], 'passed', 10),
                                             (prs, 'blocked', None)]:
            with self.subTest(history=history):
                result, _ = self.inspect(history, branch='(detached)')
                self.assertEqual(expected, result['status'])
                self.assertEqual(observed, (result['run'] or {}).get('id'))

    def test_explicit_manual_and_weekly_status_preserve_selection_scope(self):
        source = {'git_head': self.head, 'working_tree_dirty': False}
        for event, scope in [('workflow_dispatch', 'selection recorded in this manual run'),
                             ('schedule', 'Scheduled full change regression')]:
            with (self.subTest(event=event), patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
                  patch.object(ci, 'source_identity', return_value=source),
                  patch.object(ci, 'command', return_value=json.dumps(self.run_record(id=11, event=event))) as call):
                result = ci.status(run_id=11)
                self.assertEqual('blocked', result['status'])
                self.assertEqual(event, result['run']['event'])
                self.assertIn(scope, result['claim_scope'])
                call.assert_called_once_with(['gh', 'api', 'repos/fixture/scopehub/actions/runs/11'], ci.ROOT)


class HostedEvidenceTests(ProjectFixture):
    repository = 'fixture/scopehub'

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def commit_fixture(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve synthetic inputs')
        self.git('remote', 'add', 'origin', f'https://github.com/{self.repository}.git')
        return self.git('rev-parse', 'HEAD')

    def test_current_hosted_result_requires_visible_checkout_and_submodule_state(self):
        head = self.commit_fixture()
        run = {'id': 11, 'path': ci.WORKFLOW, 'head_sha': head, 'status': 'completed',
               'conclusion': 'success', 'run_attempt': 1, 'event': 'pull_request'}

        def observed():
            with (patch.object(ci, 'github_identity', return_value=self.repository),
                  patch.object(ci, 'read_json', return_value={'branch': 'main'}),
                  patch.object(ci, 'checkout_branch', return_value='topic'),
                  patch.object(ci, 'workflow_runs', return_value=[run]),
                  patch.object(ci, 'ci_budget_status', return_value={})):
                return ci.status(self.root)['status']

        self.assertEqual('passed', observed())
        self.git('config', 'status.showUntrackedFiles', 'no')
        untracked = self.root / 'untracked fixture.txt'
        untracked.touch()
        self.assertEqual('blocked', observed())
        untracked.unlink()
        for option in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(option=option):
                self.git('update-index', f'--{option}', '--', 'implementation.py')
                self.assertEqual('blocked', observed())
                self.git('update-index', f'--no-{option}', '--', 'implementation.py')
                self.assertEqual('passed', observed())

        dependency = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        subprocess.run(['git', 'init', '-q', str(dependency)], check=True, capture_output=True)
        (dependency / 'tracked.txt').write_text('Dependency fixture\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(dependency), 'add', '.'], check=True, capture_output=True)
        author = ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid']
        subprocess.run(['git', '-C', str(dependency), *author, 'commit', '-qm', 'Dependency fixture'],
                       check=True, capture_output=True)
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(dependency), 'dependency')
        self.git('config', 'submodule.dependency.ignore', 'all')
        self.git(*author, 'commit', '-qm', 'Track fixture submodule')
        run['head_sha'] = self.git('rev-parse', 'HEAD')
        self.assertEqual('passed', observed())
        self.git('-C', 'dependency', 'config', 'status.showUntrackedFiles', 'no')
        nested = self.root / 'dependency' / 'nested-untracked.txt'
        nested.touch()
        self.assertEqual('blocked', observed())
        nested.unlink()
        self.git('-C', 'dependency', 'update-index', '--assume-unchanged', '--', 'tracked.txt')
        self.assertEqual('blocked', observed())

    def provenance(self, head):
        return {'provider': 'github-actions', 'repository': self.repository, 'run_id': '10',
                'run_attempt': '2', 'event': 'pull_request', 'head_sha': head, 'checkout_sha': head,
                'run_url': f'https://github.com/{self.repository}/actions/runs/10'}

    def artifact(self):
        head = self.commit_fixture()
        local_report = self.report()
        report = read_json(local_report)
        report['ci'] = self.provenance(head)
        directory = self.root / '.reports/ci/10/windows'
        directory.mkdir(parents=True)
        shutil.copytree(self.root / '.reports/runs', directory / 'runs')
        path = directory / 'test-report.json'
        path.write_bytes(json_bytes(report))
        return directory, path

    def test_import_keeps_original_report_logs_and_separate_merge_checkout(self):
        directory, path = self.artifact()
        original_latest = (self.root / '.reports/test-report.json').read_bytes()
        report = read_json(path)
        report['ci']['checkout_sha'] = 'b' * 40
        report['source']['git_head'] = 'b' * 40
        path.write_bytes(json_bytes(report))
        original_report = path.read_bytes()
        ref = archive_evidence(self.root, 'M0-001', path, directory)
        receipt = read_json(self.root / ref['path'])
        self.assertEqual(report['ci'], receipt['ci'])
        self.assertEqual(digest(original_report), receipt['report_sha256'])
        self.assertEqual(original_report, path.read_bytes())
        self.assertEqual(original_latest, (self.root / '.reports/test-report.json').read_bytes())
        self.finish_first(ref)

    def test_foreign_head_and_inconsistent_checkout_are_rejected(self):
        directory, path = self.artifact()
        original = path.read_bytes()
        for field in ['head_sha', 'checkout_sha']:
            report = read_json(path)
            report['ci'][field] = 'b' * 40
            path.write_bytes(json_bytes(report))
            with self.subTest(field=field), self.assertRaisesRegex(ProjectError, 'another commit or checkout'):
                archive_evidence(self.root, 'M0-001', path, directory)
            path.write_bytes(original)

    def test_dirty_hosted_checkout_and_wrong_repository_are_rejected(self):
        directory, path = self.artifact()
        report = read_json(path)
        report['source']['working_tree_dirty'] = True
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'clean checkout'):
            archive_evidence(self.root, 'M0-001', path, directory)
        report['source']['working_tree_dirty'] = False
        report['ci']['repository'] = 'unrelated/repository'
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'configured HTTPS origin'):
            archive_evidence(self.root, 'M0-001', path, directory)

    def test_tampered_log_and_path_escape_are_rejected(self):
        directory, path = self.artifact()
        report = read_json(path)
        log = directory / report['checks'][0]['log'].removeprefix('.reports/')
        original = log.read_bytes()
        log.write_text('Changed after the hosted run.\n')
        with self.assertRaisesRegex(ProjectError, 'log changed'):
            archive_evidence(self.root, 'M0-001', path, directory)
        log.write_bytes(original)
        report['checks'][0]['log'] = '.reports/runs/../../outside.log'
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'repository-relative path'):
            archive_evidence(self.root, 'M0-001', path, directory)

    def test_artifacts_must_be_isolated_and_include_provenance(self):
        directory, path = self.artifact()
        for outside in [self.root, self.root / '.reports']:
            with self.subTest(outside=outside), self.assertRaisesRegex(ProjectError, 'must be isolated'):
                archive_evidence(self.root, 'M0-001', path, outside)
        other = self.root / '.reports/other'
        other.mkdir()
        with self.assertRaisesRegex(ProjectError, 'must belong'):
            archive_evidence(self.root, 'M0-001', path, other)
        report = read_json(path)
        del report['ci']
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'require CI provenance'):
            archive_evidence(self.root, 'M0-001', path, directory)

    def test_provenance_distinguishes_source_head_and_tested_merge(self):
        head = self.commit_fixture()
        source = source_identity(self.root)
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': self.repository, 'GITHUB_RUN_ID': '10',
               'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': 'pull_request', 'SCOPEHUB_SOURCE_HEAD': 'b' * 40}
        record = ci_provenance(source, env, self.root)
        self.assertEqual(head, record['checkout_sha'])
        self.assertEqual('b' * 40, record['head_sha'])
        weekly = ci_provenance(source, {**env, 'GITHUB_EVENT_NAME': 'schedule', 'SCOPEHUB_SOURCE_HEAD': head}, self.root)
        self.assertEqual(('schedule', head), (weekly['event'], weekly['checkout_sha']))
        self.assertIsNone(ci_provenance(source, {}, self.root))
        del env['SCOPEHUB_SOURCE_HEAD']
        with self.assertRaisesRegex(ProjectError, 'CIProvenance'):
            ci_provenance(source, env, self.root)

    def test_provenance_rejects_a_dirty_checkout(self):
        head = self.commit_fixture()
        (self.root / 'implementation.py').write_text('value = 2\n')
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': self.repository, 'GITHUB_RUN_ID': '10',
               'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': 'push', 'SCOPEHUB_SOURCE_HEAD': head}
        with self.assertRaisesRegex(ProjectError, 'clean checkout'):
            ci_provenance(source_identity(self.root), env, self.root)

    def test_checkpoint_distinguishes_local_commit_and_unknown_upstream(self):
        self.assertEqual('uncommitted', checkpoint(self.root)['checkpoint_state'])
        head = self.commit_fixture()
        result = checkpoint(self.root)
        self.assertEqual(head, result['head_sha'])
        self.assertEqual('committed', result['checkpoint_state'])
        self.assertIsNone(result['upstream'])
        self.assertIsNone(result['ahead'])
        self.assertEqual('blocked', result['local_preflight'])
        self.git('branch', 'fixture-upstream')
        self.git('branch', '--set-upstream-to=fixture-upstream')
        result = checkpoint(self.root)
        self.assertEqual((0, 0), (result['ahead'], result['behind']))
        (self.root / 'implementation.py').write_text('value = 2\n')
        self.assertEqual('uncommitted', checkpoint(self.root)['checkpoint_state'])

    def test_account_marker_is_required_and_remote_cannot_embed_credentials(self):
        with patch.object(ci, 'command', return_value='https://github.com/fixture/scopehub.git') as command:
            with self.assertRaisesRegex(ProjectError, 'owner-approved'):
                ci.github_identity(self.root)
            self.assertEqual(1, command.call_count)
        self.git('config', '--local', 'github.account', 'fixture')
        with patch.object(ci, 'command', side_effect=['https://github.com/fixture/scopehub.git', '', 'fixture']) as command:
            self.assertEqual(self.repository, ci.github_identity(self.root))
            self.assertIn('switch', command.call_args_list[1].args[0])
        with patch.object(ci, 'command', return_value='https://token@github.com/fixture/scopehub.git'):
            with self.assertRaisesRegex(ProjectError, 'without embedded credentials'):
                ci.github_identity(self.root)

    def test_mismatched_authenticated_account_is_rejected(self):
        self.git('config', '--local', 'github.account', 'fixture')
        with patch.object(ci, 'command', side_effect=['https://github.com/fixture/scopehub', '', 'unrelated']):
            with self.assertRaisesRegex(ProjectError, 'does not match'):
                ci.github_identity(self.root)
