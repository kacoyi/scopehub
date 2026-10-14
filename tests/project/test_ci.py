"""Reject stale hosted results and preserve provenance when importing native evidence."""

import shutil
import subprocess
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
                'conclusion': 'success', 'run_attempt': 1, 'event': 'pull_request',
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
