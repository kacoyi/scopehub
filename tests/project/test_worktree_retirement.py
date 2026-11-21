"""Exercise retirement observations in temporary native Git worktrees."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import worktree, worktree_retirement


IDLE = (set(), [])


class WorktreeRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.primary = self.directory / 'primary'
        self.primary.mkdir()
        self.managed = self.directory / 'worktree checkouts'
        self.managed.mkdir()
        self.archive = self.directory / 'retained data'
        self.archive.mkdir()
        self.common = self.primary / '.git'
        self.git('init', '--quiet', '--initial-branch=main')
        self.git('config', '--local', 'user.name', 'Retirement Fixture')
        self.git('config', '--local', 'user.email', 'fixture@example.invalid')
        self.git('config', '--local', 'scopehub.worktreeRoot', str(self.managed))
        (self.primary / '.gitignore').write_text('.reports/\n.env\n', encoding='utf-8')
        (self.primary / 'fixture.txt').write_text('shared baseline', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '--quiet', '-m', 'baseline')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.candidate = self.add_worktree('candidate')

    def git(self, *args, cwd=None):
        return os.fsdecode(worktree.git(cwd or self.primary, *args).stdout)

    def add_worktree(self, name):
        path = self.managed / name
        self.git('worktree', 'add', '--quiet', '-b', name, '--', str(path), 'HEAD')
        return path

    def head(self, path):
        return self.git('rev-parse', 'HEAD', cwd=path).strip()

    def commit(self, path, relative, text):
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(text, encoding='utf-8')
        self.git('add', '--', relative, cwd=path)
        self.git('commit', '--quiet', '-m', 'fixture change', cwd=path)
        return self.head(path)

    def report(self, activity, *, archive_root=None, dispositions=None):
        registered = list(worktree.registrations(self.primary))
        with patch.object(worktree_retirement, 'observe_activity', return_value=activity):
            return worktree_retirement.retirement_report(
                self.primary, self.common, registered, archive_root=archive_root, dispositions=dispositions,
            )

    def row(self, activity, path=None, **kwargs):
        return next(row for row in self.report(activity, **kwargs)['worktrees']
                    if Path(row['path']) == (path or self.candidate))

    def assert_held(self, row, reason):
        self.assertEqual(row['state'], 'held', row)
        self.assertIn(reason, row['holds'], row)

    def assert_candidate(self, row):
        self.assertEqual(row['state'], 'candidate', row)
        self.assertEqual(row['holds'], [], row)

    def test_integrated_clean_checkout_without_local_evidence_is_candidate_offline(self):
        row = self.row(IDLE)
        self.assert_candidate(row)
        self.assertEqual(row['head'], self.head(self.candidate))
        self.assertEqual(row['recovery_ref'], 'refs/remotes/origin/main')
        self.assertEqual(row['integration']['tip'], self.head(self.primary))
        self.assertTrue(row['clean'])
        self.assert_held(self.row(IDLE, self.primary), 'primary_checkout')

        self.git('switch', '--quiet', '--detach', cwd=self.candidate)
        self.git('branch', '-d', 'candidate')
        self.assert_candidate(self.row(IDLE))

    def test_retirement_report_preserves_git_and_checkout_bytes(self):
        metadata = worktree.git_path(self.candidate, '--git-dir')
        paths = [self.candidate / '.git', self.candidate / 'fixture.txt', metadata / 'index',
                 metadata / 'HEAD', self.common / 'config']
        before = self.git('worktree', 'list', '--porcelain')
        refs = self.git('show-ref')
        stored = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

        self.assert_candidate(self.row(IDLE))

        self.assertEqual(self.git('worktree', 'list', '--porcelain'), before)
        self.assertEqual(self.git('show-ref'), refs)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, stored)

    def test_isolated_python_cli_reports_registered_checkouts(self):
        archive = self.directory / 'absent-archive'
        result = subprocess.run(
            [sys.executable, '-I', '-S', worktree.__file__, 'retirement', '--archive-root', str(archive)],
            cwd=self.primary, capture_output=True, encoding='utf-8', timeout=30,
            env={**os.environ, 'SCOPEHUB_STORAGE': 'off'},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {Path(row['path']): row for row in json.loads(result.stdout)['worktrees']}
        self.assertEqual(set(rows), {self.primary, self.candidate})
        self.assert_held(rows[self.primary], 'primary_checkout')
        self.assertNotIn('worktree_registration_changed', rows[self.candidate]['holds'])
        self.assertTrue((self.candidate / '.git').exists())
        self.assertFalse(archive.exists())

    def test_dirty_checkout_and_unintegrated_local_history_are_held(self):
        original = self.head(self.candidate)
        new_head = self.commit(self.candidate, 'fixture.txt', 'local only')
        self.assertNotEqual(new_head, original)
        self.git('merge', '--ff-only', new_head)
        self.assert_held(self.row(IDLE), 'not_integrated_or_disposed')

        self.git('update-ref', 'refs/remotes/origin/main', new_head)
        self.assert_candidate(self.row(IDLE))

        staged = self.candidate / 'staged.txt'
        staged.write_text('staged edit', encoding='utf-8')
        self.git('add', 'staged.txt', cwd=self.candidate)
        self.assert_held(self.row(IDLE), 'dirty_checkout')

    def test_in_progress_merge_holds_a_checkout_with_clean_porcelain(self):
        branch = self.add_worktree('empty-change')
        self.git('commit', '--quiet', '--allow-empty', '-m', 'Empty merge input', cwd=branch)
        self.git('merge', '--no-commit', '--no-ff', 'empty-change', cwd=self.candidate)
        self.assertEqual(self.git('status', '--porcelain', cwd=self.candidate), '')
        self.assert_held(self.row(IDLE), 'git_operation_in_progress')

    def test_ignored_evidence_requires_external_verified_bytes(self):
        payloads = {'.reports/diagnostic.log': b'failed step\n', '.env': b'SYNTHETIC=local\n'}
        retained = self.archive / 'worktrees' / self.head(self.candidate) / 'files'
        for relative, data in payloads.items():
            path = self.candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertEqual(self.git('status', '--porcelain', cwd=self.candidate), '')
        self.assert_held(self.row(IDLE), 'unretained_local_evidence')
        self.assert_held(self.row(IDLE, archive_root=self.archive), 'unretained_local_evidence')

        for relative, data in payloads.items():
            destination = retained / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        row = self.row(IDLE, archive_root=self.archive)
        self.assert_candidate(row)
        self.assertEqual(row['local_evidence']['verified_retained_files'], len(payloads))

        (retained / '.env').write_bytes(b'SYNTHETIC=evil!\n')
        self.assert_held(self.row(IDLE, archive_root=self.archive), 'unretained_local_evidence')
        self.assertEqual((self.candidate / '.env').read_bytes(), payloads['.env'])

    def test_archive_ancestor_does_not_count_evidence_in_its_own_checkout(self):
        nested = self.archive / 'worktrees' / self.head(self.candidate) / 'files'
        nested.parent.mkdir(parents=True)
        self.git('worktree', 'move', str(self.candidate), str(nested))
        self.candidate = nested
        local = self.candidate / '.env'
        original = b'SYNTHETIC=local\n'
        local.write_bytes(original)

        report = self.report(IDLE, archive_root=self.archive)
        row = next(row for row in report['worktrees'] if Path(row['path']) == self.candidate)
        self.assertTrue(report['archive_error'])
        self.assert_held(row, 'unretained_local_evidence')
        self.assertEqual(local.read_bytes(), original)

    def test_archive_link_back_to_checkout_does_not_preserve_evidence(self):
        local = self.candidate / '.env'
        local.write_bytes(b'SYNTHETIC=local\n')
        archived = self.archive / 'worktrees' / self.head(self.candidate) / 'files' / '.env'
        archived.parent.mkdir(parents=True)
        try:
            archived.symlink_to(local)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'The fixture cannot create a file symlink: {error}')

        self.assert_held(self.row(IDLE, archive_root=self.archive), 'unretained_local_evidence')

    def test_activity_observations_can_hold_one_or_all_checkouts(self):
        unrelated = self.add_worktree('unrelated')
        self.assert_held(self.row(({self.candidate / 'fixture.txt'}, [])), 'active_session_or_process')
        self.assert_candidate(self.row(({unrelated}, [])))
        self.assert_held(self.row((set(), ['host_process_observation_unavailable'])),
                         'host_process_observation_unavailable')
        self.assert_held(self.row((set(), ['herdr_observation_unavailable'])), 'herdr_observation_unavailable')

    def test_live_downstream_record_blocks_even_when_candidate_is_clean(self):
        downstream = self.add_worktree('downstream')
        relative = Path(os.path.relpath(self.candidate / '.reports/original.json', downstream)).as_posix()
        path = 'project/feedback.json'
        downstream_record = {'schema_version': '1.0', 'items': [{
            'id': 'fixture-path-consumer', 'status': 'open', 'source': relative,
            'severity': 'major', 'observation': 'Read the original diagnostic during reproduction.',
            'task_ids': ['M0-031'], 'regression_checks': ['project-state'],
            'resolution': None, 'deferral_reason': None,
        }]}
        self.commit(downstream, path, json.dumps(downstream_record))
        self.assert_held(self.row(IDLE), 'downstream_path_consumer')

        downstream_record['items'][0]['source'] = str(self.candidate / '.reports/original.json')
        self.commit(downstream, path, json.dumps(downstream_record))
        self.assert_held(self.row(IDLE), 'downstream_path_consumer')

        # Use a component boundary; a sibling with a longer name does not consume this path.
        downstream_record['items'][0]['source'] = relative.replace('/candidate/', '/candidate-copy/')
        self.commit(downstream, path, json.dumps(downstream_record))
        self.assert_candidate(self.row(IDLE))

    def test_untracked_and_ignored_symlinks_record_downstream_path_consumers(self):
        downstream = self.add_worktree('downstream')
        for name in ('diagnostic link', '.reports/diagnostic link'):
            with self.subTest(name=name):
                link = downstream / name
                link.parent.mkdir(exist_ok=True)
                try:
                    link.symlink_to(self.candidate / 'fixture.txt')
                except (NotImplementedError, OSError) as error:
                    self.skipTest(f'The fixture cannot create a file symlink: {error}')
                self.assert_held(self.row(IDLE), 'downstream_path_consumer')

                link.unlink()
                self.assert_candidate(self.row(IDLE))

    def test_effective_git_config_can_name_an_exact_worktree_consumer(self):
        self.git('config', '--local', 'scopehub.observationSource', str(self.candidate))
        self.assert_held(self.row(IDLE), 'downstream_path_consumer')

        self.git('config', '--local', '--unset', 'scopehub.observationSource')
        self.assert_candidate(self.row(IDLE))

    def test_tracked_and_untracked_scripts_are_observed_as_path_consumers(self):
        downstream = self.add_worktree('downstream')
        relative = Path(os.path.relpath(self.candidate / '.reports/original.json', downstream)).as_posix()
        script = 'scripts/replay.sh'
        consumer = f"#!/bin/sh\ncat -- '{relative}'\n"
        (downstream / script).parent.mkdir()
        (downstream / script).write_text(consumer, encoding='utf-8')
        self.assert_held(self.row(IDLE), 'downstream_path_consumer')

        self.commit(downstream, script, consumer)
        self.assert_held(self.row(IDLE), 'downstream_path_consumer')

        self.commit(downstream, script, "#!/bin/sh\ncat -- '.reports/replay.json'\n")
        self.assert_candidate(self.row(IDLE))

    def test_unavailable_consumer_observation_holds_an_idle_candidate(self):
        git_status = worktree_retirement.git_status

        def read_status(checkout):
            if checkout == self.primary:
                raise PermissionError('Fixture prevents inspection of the consumer.')
            return git_status(checkout)

        with patch.object(worktree_retirement, 'git_status', side_effect=read_status):
            self.assert_held(self.row(IDLE), 'path_consumer_observation_incomplete')

    def test_explicit_disposition_binds_exact_head_and_recoverable_ref(self):
        head = self.commit(self.candidate, 'fixture.txt', 'superseded experiment')
        records = {
            'version': 1,
            'dispositions': [{
                'head': head,
                'disposition': 'superseded',
                'reason': 'The replacement was reviewed at the integration checkpoint.',
                'recoverable_ref': 'refs/heads/archive/experiment',
            }],
        }
        review = self.add_worktree('review')
        source_path = 'project/retirement.json'
        reviewed = self.commit(review, source_path, json.dumps(records))
        reference = f'{reviewed}:{source_path}'
        self.git('update-ref', 'refs/remotes/origin/main', reviewed)
        self.assertEqual(self.row(IDLE, dispositions=reference)['state'], 'held')

        self.git('update-ref', 'refs/heads/archive/experiment', head)
        row = self.row(IDLE, dispositions=reference)
        self.assert_candidate(row)
        self.assertEqual(row['recovery_ref'], 'refs/heads/archive/experiment')
        self.assertEqual(row['integration']['source'], reference)

        self.commit(self.candidate, 'fixture.txt', 'a new experiment')
        self.assert_held(self.row(IDLE, dispositions=reference), 'not_integrated_or_disposed')
