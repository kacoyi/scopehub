"""Exercise retirement observations in temporary native Git worktrees."""

import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import worktree, worktree_retirement


IDLE = (set(), [])
PNPM_TOOL_DEPENDENCY = (
    "    dependencies:\n      '@fixture/tool':\n"
    "        specifier: '^1.2.0'\n        version: 1.2.3(peer@2.0.0)\n"
)
PNPM_TOOL_RESOLUTIONS = (
    "\npackages:\n\n  '@fixture/tool@1.2.3':\n"
    "    resolution: {integrity: fixture}\n"
    "\nsnapshots:\n\n  '@fixture/tool@1.2.3(peer@2.0.0)': {}\n"
)


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

    def test_private_refs_and_fetch_head_require_shared_recoverability(self):
        head = self.head(self.candidate)
        checkpoint = self.git('commit-tree', 'HEAD^{tree}', '-p', head, '-m', 'Private checkpoint',
                              cwd=self.candidate).strip()
        refs = ('refs/worktree/checkpoint', 'refs/bisect/checkpoint', 'refs/rewritten/checkpoint', 'ORIG_HEAD')
        for ref in refs:
            self.git('update-ref', ref, checkpoint, cwd=self.candidate)
        fetch_head = worktree.git_path(self.candidate, '--git-dir') / 'FETCH_HEAD'
        fetch_head.write_text(f'{checkpoint}\tnot-for-merge\tfixture fetch\n', encoding='ascii')
        other = self.add_worktree('other')
        self.git('update-ref', 'refs/worktree/other-checkpoint', checkpoint, cwd=other)

        row = self.row(IDLE)
        self.assert_held(row, 'unrecoverable_worktree_git_state')
        self.assertEqual(row['head'], head)
        self.assertEqual(row['details']['unrecoverable_worktree_git_state'],
                         {ref: [checkpoint] for ref in (*refs, 'FETCH_HEAD')})

        # The checkpoint is safe once it is reachable as an ancestor of a shared ref.
        successor = self.git('commit-tree', 'HEAD^{tree}', '-p', checkpoint, '-m', 'Retained successor').strip()
        self.git('update-ref', 'refs/heads/retained-checkpoint', successor)
        self.assert_candidate(self.row(IDLE))

    def test_detached_head_reflog_requires_shared_recoverability(self):
        baseline = self.head(self.candidate)
        self.git('switch', '--quiet', '--detach', cwd=self.candidate)
        self.git('commit', '--allow-empty', '-qm', 'Detached experiment', cwd=self.candidate)
        experiment = self.head(self.candidate)
        self.git('switch', '--quiet', '--detach', baseline, cwd=self.candidate)

        row = self.row(IDLE)
        self.assert_held(row, 'unrecoverable_worktree_git_state')
        self.assertEqual(row['details']['unrecoverable_worktree_git_state']['logs/HEAD'], [experiment])
        self.git('update-ref', 'refs/tags/retained-experiment', experiment)
        self.assert_candidate(self.row(IDLE))

    def test_effective_global_filter_holds_only_the_checkout_using_it_and_never_executes(self):
        home = self.directory / 'global Git configuration'
        home.mkdir()
        marker = self.directory / 'filter invocation'
        mark = f'printf invoked > {shlex.quote(marker.as_posix())}'
        with patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home), 'XDG_CONFIG_HOME': str(home)}):
            filtered = self.add_worktree('filtered')
            attributes = filtered / '.gitattributes'
            attributes.write_text('fixture.txt filter=lfs\n', encoding='ascii')
            self.git('-c', 'filter.lfs.clean=', '-c', 'filter.lfs.process=', '-c', 'filter.lfs.required=false',
                     'add', '--', '.gitattributes', cwd=filtered)
            (filtered / 'fixture.txt').write_text('changed fixture activates clean conversion\n', encoding='utf-8')
            self.git('config', '--global', 'filter.lfs.clean', f'{mark}; cat')
            self.git('config', '--global', 'filter.lfs.process', f'{mark}; exit 1')
            self.git('config', '--global', 'filter.lfs.required', 'true')
            self.assert_candidate(self.row(IDLE))

            row = self.row(IDLE, filtered)
            self.assert_held(row, 'external_clean_filter_unverified')
            self.assertEqual(row['details']['external_clean_filter_unverified'],
                             ['filter.lfs.clean', 'filter.lfs.process'])
            self.assert_candidate(self.row(IDLE))
            self.assertFalse(marker.exists())

            # A worktree-level attribute change can suppress the filter configured by the index.
            attributes.write_text('fixture.txt -filter\n', encoding='ascii')
            row = self.row(IDLE, filtered)
            self.assert_held(row, 'dirty_checkout')
            self.assertNotIn('external_clean_filter_unverified', row['holds'])
            self.assertFalse(marker.exists())

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
                'recoverable_ref': 'refs/scopehub/recovery/experiment',
            }],
        }
        review = self.add_worktree('review')
        source_path = 'project/retirement.json'
        reviewed = self.commit(review, source_path, json.dumps(records))
        reference = f'{reviewed}:{source_path}'
        self.git('update-ref', 'refs/remotes/origin/main', reviewed)
        self.assertEqual(self.row(IDLE, dispositions=reference)['state'], 'held')

        ref = 'refs/scopehub/recovery/experiment'
        self.git('symbolic-ref', ref, 'refs/heads/candidate')
        self.assert_held(self.row(IDLE, dispositions=reference), 'not_integrated_or_disposed')
        self.git('symbolic-ref', '--delete', ref)
        self.git('update-ref', ref, head)
        row = self.row(IDLE, dispositions=reference)
        self.assert_candidate(row)
        self.assertEqual(row['recovery_ref'], ref)
        self.assertEqual(row['integration']['source'], reference)

        self.commit(self.candidate, 'fixture.txt', 'a new experiment')
        self.assert_held(self.row(IDLE, dispositions=reference), 'not_integrated_or_disposed')

    def test_harness_and_development_registries_keep_operational_dependencies(self):
        downstream = self.add_worktree('downstream')
        path = self.candidate / 'scripts/check.py'
        for record, data in (
            ('project/checks.json', {'checks': [{'id': 'fixture', 'command': ['{python}', str(path)]}]}),
            ('project/development.json', {'herdr': {'profiles': {'implement': {'command': [str(path)]}}}}),
        ):
            with self.subTest(record=record):
                document = json.dumps(data)
                self.commit(downstream, record, document)
                row = self.row(IDLE)
                self.assert_held(row, 'downstream_path_consumer')
                self.assertEqual(row['details']['downstream_path_consumer'], [f'{downstream}: {record}'])
                self.commit(downstream, record, document.replace(json.dumps(str(path))[1:-1], 'scripts/check.py'))
                self.assert_candidate(self.row(IDLE))

    def test_nested_manifest_resolves_its_dependency_relative_to_the_manifest(self):
        manifest = 'crates/application/Cargo.toml'
        destination = self.candidate / 'crates/domain'
        head = self.commit(self.candidate, 'crates/domain/Cargo.toml', '[package]\nname = "domain"\n')
        self.git('update-ref', 'refs/remotes/origin/main', head)
        downstream = self.add_worktree('downstream')
        current = downstream / manifest
        candidate_relative = Path(os.path.relpath(destination, current.parent)).as_posix()
        self.assertEqual(candidate_relative, '../../../candidate/crates/domain')
        equivalent = (candidate_relative, './' + candidate_relative,
                      candidate_relative.replace('candidate/', './candidate/'), destination.as_posix())
        for reference in equivalent:
            with self.subTest(reference=reference):
                self.commit(downstream, manifest, f'[dependencies]\ndomain = {{ path = "{reference}" }}\n')
                row = self.row(IDLE)
                self.assert_held(row, 'downstream_path_consumer')
                self.assertEqual(row['details']['downstream_path_consumer'], [f'{downstream}: {manifest}'])

        self.commit(downstream, manifest, '[dependencies]\ndomain = { path = "../domain" }\n')
        self.assert_candidate(self.row(IDLE))

        self.commit(downstream, manifest, '[dependencies\ndomain = { path = "../domain" }\n')
        row = self.row(IDLE)
        self.assert_held(row, 'path_consumer_observation_incomplete')
        self.assertIn(f'Cannot parse Cargo manifest {current}',
                      row['details']['path_consumer_observation_incomplete'][0])

    def test_cargo_path_reaches_lexical_and_physical_checkout_targets(self):
        downstream = self.add_worktree('downstream')
        manifest = 'crates/application/Cargo.toml'
        alias = self.managed / 'alias'
        external = self.directory / 'external'
        external.mkdir()
        lexical = self.candidate / 'domain-link'
        try:
            alias.symlink_to(self.candidate, target_is_directory=True)
            lexical.symlink_to(external, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'The fixture cannot create a directory symlink: {error}')
        for relative in ('../../../alias', '../../../candidate/domain-link'):
            with self.subTest(relative=relative):
                self.commit(downstream, manifest, f'[dependencies]\ndomain = {{ path = "{relative}" }}\n')
                row = self.row(IDLE)
                self.assert_held(row, 'downstream_path_consumer')
                self.assertEqual(row['details']['downstream_path_consumer'], [f'{downstream}: {manifest}'])

    def test_registry_symlink_within_checkout_keeps_its_command_dependencies(self):
        downstream = self.add_worktree('downstream')
        expected = str(self.candidate / 'scripts/check.py')
        registry = downstream / 'project/checks.json'
        target = 'project/checks.linux.json'
        self.commit(downstream, target, json.dumps({'checks': [{'command': ['{python}', expected]}]}))
        try:
            registry.symlink_to('checks.linux.json')
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'The fixture cannot create a file symlink: {error}')
        row = self.row(IDLE)
        self.assert_held(row, 'downstream_path_consumer')
        self.assertEqual(row['details']['downstream_path_consumer'],
                         [f'{downstream}: project/checks.json'])

        self.commit(downstream, target, json.dumps({'checks': [{'command': ['{python}', 'scripts/check.py']}]}))
        self.assert_candidate(self.row(IDLE))

    def test_indexed_regular_file_replaced_by_link_protects_its_live_target(self):
        downstream = self.add_worktree('downstream')
        relative = 'scripts/check.py'
        self.commit(downstream, relative, 'print("fixture")')
        self.assert_candidate(self.row(IDLE))

        link = downstream / relative
        link.unlink()
        try:
            link.symlink_to(self.candidate / 'fixture.txt')
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'The fixture cannot create a file symlink: {error}')
        self.assertTrue(self.git('ls-files', '--stage', '--', relative, cwd=downstream).startswith('100644 '))
        row = self.row(IDLE)
        self.assert_held(row, 'downstream_path_consumer')
        self.assertEqual(row['details']['downstream_path_consumer'], [f'{downstream}: {link}'])

    def test_windows_substitution_paths_match_the_registered_checkout_spelling(self):
        for target, candidate in (
            (r'\\?\C:\managed\Candidate\not-yet-created.txt', r'c:\managed\candidate'),
            (r'\\?\UNC\server\share\candidate\missing', r'\\server\share\candidate'),
        ):
            with self.subTest(target=target):
                canonical = worktree_retirement.comparable_path(PureWindowsPath(target))
                self.assertTrue(canonical.is_relative_to(PureWindowsPath(candidate)))
                self.assertFalse(canonical.is_relative_to(PureWindowsPath(candidate + '-copy')))

    def test_editable_environment_paths_report_the_actual_python_consumer(self):
        downstream = self.add_worktree('downstream')
        self.commit(downstream, '.gitignore', '.venv/\n')
        self.commit(downstream, 'uv.lock', '# fixture lock\n')
        venv = downstream / '.venv'
        site = venv / ('Lib/site-packages' if os.name == 'nt' else 'lib/python3.12/site-packages')
        site.mkdir(parents=True)
        (venv / 'CACHEDIR.TAG').write_bytes(worktree_retirement.storage.CARGO_TAG + b'\n')
        startup = site / '_editable.pth'
        local_path = os.path.relpath(downstream, site)
        target_path = os.path.relpath(self.candidate, site)
        startup.write_text(f'# {target_path}\n{local_path}\n', encoding='utf-8')
        self.assert_candidate(self.row(IDLE))

        startup.write_text(f'{target_path}\n', encoding='utf-8')
        row = self.row(IDLE)
        self.assert_held(row, 'downstream_path_consumer')
        self.assertEqual(row['details']['downstream_path_consumer'],
                         [f'{downstream}: {startup.relative_to(downstream).as_posix()}'])

    def test_editable_python_libraries_are_enumerated_from_actual_directory_entries(self):
        environment = self.candidate / '.venv'
        first = environment / 'Lib/site-packages/example.pth'
        first.parent.mkdir(parents=True)
        first.write_text('# fixture\n', encoding='utf-8')

        with patch.object(worktree, 'entry_info', wraps=worktree.entry_info) as lookup:
            self.assertEqual(list(worktree_retirement.editable_python_paths(self.candidate, {})), [(first, True)])
        # On Linux, detect the same spelling guesses that silently revisit a file on Windows.
        library_lookups = [call.args[0].name for call in lookup.call_args_list
                           if call.args[0].parent == environment and call.args[0].name != 'pyvenv.cfg']
        self.assertEqual(library_lookups, ['Lib'])

        second = environment / 'lib/python3.12/site-packages/example.pth'
        if not (environment / 'lib').exists():
            second.parent.mkdir(parents=True)
            second.write_text('# separate fixture\n', encoding='utf-8')
            linked = environment / 'lib64'
            try:
                linked.symlink_to('lib', target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f'The fixture cannot create a library symlink: {error}')
            observed = set(worktree_retirement.editable_python_paths(self.candidate, {}))
            self.assertEqual(observed, {(first, True), (second, True), (linked, False)})

    def test_cargo_generated_outputs_are_distinguished_from_unique_build_data(self):
        self.commit(self.candidate, '.gitignore', 'target/\n')
        head = self.commit(self.candidate, 'Cargo.lock', '# fixture lock\n')
        self.git('update-ref', 'refs/remotes/origin/main', head)
        target = self.candidate / 'target'
        build = target / 'debug/build/example-0123456789abcdef'
        output = build / 'out'
        output.mkdir(parents=True)
        (target / 'CACHEDIR.TAG').write_bytes(worktree_retirement.storage.CARGO_TAG + b'\n')
        (build / 'invoked.timestamp').write_bytes(b'')
        (output / '0123456789abcdef-helper.o').write_bytes(b'fixture build object')

        row = self.row(IDLE)
        self.assert_candidate(row)
        self.assertGreaterEqual(row['local_evidence']['generated_entries_excluded'], 2)

        diagnostic = output / 'user-observation.json'
        diagnostic.write_bytes(b'{"record":"unique diagnostic"}\n')
        relative = diagnostic.relative_to(self.candidate)
        row = self.row(IDLE)
        self.assert_held(row, 'unretained_local_evidence')
        self.assertEqual(row['details']['unretained_local_evidence'], [str(relative)])

        archive = self.archive / 'worktrees' / head / 'files' / relative
        archive.parent.mkdir(parents=True)
        os.link(diagnostic, archive)
        self.assert_held(self.row(IDLE, archive_root=self.archive), 'unretained_local_evidence')
        archive.unlink()
        archive.write_bytes(diagnostic.read_bytes())
        preserved = self.row(IDLE, archive_root=self.archive)
        self.assert_candidate(preserved)
        self.assertEqual(preserved['local_evidence']['verified_retained_files'], 1)

    def test_unknown_cargo_intermediate_files_hold_retirement_and_review_intake(self):
        checkout = self.candidate
        self.commit(checkout, '.gitignore', 'target/\n')
        head = self.commit(checkout, 'Cargo.lock', '# fixture lock\n')
        self.git('update-ref', 'refs/remotes/origin/main', head)
        debug = checkout / 'target/debug'
        deps = debug / 'deps'
        fingerprint = debug / '.fingerprint/fixture-0123456789abcdef'
        incremental = debug / 'incremental/fixture-0123456789abc/s-abc-def-0123456789abc'
        for directory in (deps, fingerprint, incremental):
            directory.mkdir(parents=True)
        (debug.parent / 'CACHEDIR.TAG').write_bytes(worktree_retirement.storage.CARGO_TAG + b'\n')
        for product in (
                deps / 'libfixture-0123456789abcdef.rlib',
                deps / 'fixture-0123456789abcdef.d',
                deps / 'fixture-0123456789abcdef.exe',
                fingerprint / 'lib-fixture.json',
                incremental / 'dep-graph.bin',
                incremental.parent / 's-abc-def.lock'):
            product.write_bytes(b'' if product.suffix == '.lock' else b'fixture Cargo output')

        partial = (set(), ['process_observation_incomplete'])
        self.assert_candidate(self.row(IDLE))
        self.assertTrue(self.row(partial)['review_intake'])

        notes = (deps / 'one-off.log', fingerprint / 'unique.json', incremental / 'unique.log')
        for note in notes:
            with self.subTest(path=note.relative_to(checkout)):
                note.write_text('Unique local diagnostic\n', encoding='utf-8')
                row = self.row(partial)
                self.assert_held(row, 'unretained_local_evidence')
                self.assertFalse(row['review_intake'])
                self.assertEqual(row['details']['unretained_local_evidence'], [str(note.relative_to(checkout))])
                destination = self.archive / 'worktrees' / head / 'files' / note.relative_to(checkout)
                destination.parent.mkdir(parents=True)
                destination.write_bytes(note.read_bytes())
                self.assert_candidate(self.row(IDLE, archive_root=self.archive))
                self.assertTrue(self.row(partial, archive_root=self.archive)['review_intake'])
                note.unlink()

    def test_scoped_pnpm_projection_requires_a_local_lock_and_package_identity(self):
        checkout = self.candidate
        self.commit(checkout, '.gitignore', 'node_modules/\n')
        self.commit(checkout, '.gitattributes', 'pnpm-lock.yaml -text\n')
        dependency = '{"dependencies":{"@fixture/tool":"^1.2.0"}}\n'
        self.commit(checkout, 'package.json', dependency)
        self.commit(checkout, 'apps/ui/package.json', dependency)
        lock = checkout / 'pnpm-lock.yaml'
        lock_text = ("lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n" + PNPM_TOOL_DEPENDENCY +
                     "\n  'apps/ui':\n" + PNPM_TOOL_DEPENDENCY + PNPM_TOOL_RESOLUTIONS)
        lock.write_bytes(lock_text.replace('\n', '\r\n').encode('utf-8'))
        self.git('add', '--', 'pnpm-lock.yaml', cwd=checkout)
        self.git('commit', '-qm', 'Retain a CRLF lock fixture', cwd=checkout)
        head = self.head(checkout)
        self.git('update-ref', 'refs/remotes/origin/main', head)
        modules = checkout / 'node_modules'
        store = modules / '.pnpm'
        package = store / '@fixture+tool@1.2.3_peer@2.0.0/node_modules/@fixture/tool'
        package.mkdir(parents=True)
        (package / 'package.json').write_text('{"name":"@fixture/tool","version":"1.2.3"}\n', encoding='utf-8')
        plain_package = store / 'plain@1.0.0/node_modules/plain'
        plain_package.mkdir(parents=True)
        metadata = modules / '.modules.yaml'
        metadata.write_text(json.dumps({
            'packageManager': 'pnpm@11.28.5', 'nodeLinker': 'isolated',
            'layoutVersion': 5, 'virtualStoreDir': '.pnpm',
        }), encoding='utf-8')
        top_level = modules / '@fixture/tool'
        nested = checkout / 'apps/ui/node_modules/@fixture/tool'
        for projected, target in ((top_level, package), (nested, package), (modules / 'plain', plain_package)):
            projected.parent.mkdir(parents=True, exist_ok=True)
            try:
                projected.symlink_to(os.path.relpath(target, projected.parent), target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f'The fixture cannot create a package symlink: {error}')

        self.assert_candidate(self.row(IDLE))
        manifest = checkout / 'apps/ui/package.json'
        original = manifest.read_bytes()
        manifest.write_text(dependency.replace('^1.2.0', '^1.0.0'), encoding='utf-8')
        row = self.row(IDLE)
        self.assertEqual(row['details']['unretained_local_evidence'], [str(nested.relative_to(checkout))])
        manifest.write_bytes(original)

        alternate_resolution = lock_text.replace("  'apps/ui':\n" + PNPM_TOOL_DEPENDENCY,
                                                  "  'apps/ui':\n" + PNPM_TOOL_DEPENDENCY.replace('peer@2', 'peer@3'))
        lock.write_bytes(alternate_resolution.encode('utf-8'))
        row = self.row(IDLE)
        self.assertEqual(row['details']['unretained_local_evidence'], [str(nested.relative_to(checkout))])
        lock.write_bytes(lock_text.replace('\n', '\r\n').encode('utf-8'))

        scope = nested.parent
        note = scope / 'evidence.json'
        note.write_text('{"record":"unique"}\n', encoding='utf-8')
        row = self.row(IDLE)
        self.assert_held(row, 'unretained_local_evidence')
        self.assertEqual(row['details']['unretained_local_evidence'], [str(note.relative_to(checkout))])

        # Directory and package names alone cannot reconstruct an unlocked package.
        unlocked = store / '@fixture+unlocked@1.2.3/node_modules/@fixture/unlocked'
        unlocked.mkdir(parents=True)
        (unlocked / 'package.json').write_text('{"name":"@fixture/unlocked","version":"1.2.3"}', encoding='utf-8')
        (scope / 'unlocked').symlink_to(unlocked, target_is_directory=True)
        (scope / 'missing').symlink_to(store / '@fixture+missing@1.2.3/node_modules/@fixture/missing',
                                      target_is_directory=True)
        row = self.row(IDLE)
        self.assertEqual(set(row['details']['unretained_local_evidence']),
                         {str((scope / name).relative_to(checkout)) for name in ('evidence.json', 'missing', 'unlocked')})

        metadata.write_text('{"virtualStoreDir":"../another-store"}\n', encoding='utf-8')
        row = self.row(IDLE)
        self.assertIn(str(top_level.relative_to(checkout)), row['details']['unretained_local_evidence'])
        self.assertIn(str(nested.relative_to(checkout)), row['details']['unretained_local_evidence'])
        self.assertNotIn(str((modules / 'plain').relative_to(checkout)), row['details']['unretained_local_evidence'])

    def test_private_workspace_package_does_not_explain_manual_root_link(self):
        checkout = self.candidate
        self.commit(checkout, '.gitignore', 'node_modules/\n')
        self.commit(checkout, 'package.json', '{}\n')
        self.commit(checkout, 'apps/ui/package.json', '{"dependencies":{"@fixture/tool":"^1.2.0"}}\n')
        head = self.commit(checkout, 'pnpm-lock.yaml', "lockfileVersion: '9.0'\n\nimporters:\n\n"
                           '  .: {}\n\n  apps/ui:\n' + PNPM_TOOL_DEPENDENCY + PNPM_TOOL_RESOLUTIONS)
        self.git('update-ref', 'refs/remotes/origin/main', head)
        modules = checkout / 'node_modules'
        package = modules / '.pnpm/@fixture+tool@1.2.3_peer@2.0.0/node_modules/@fixture/tool'
        package.mkdir(parents=True)
        (package / 'package.json').write_text('{"name":"@fixture/tool","version":"1.2.3"}', encoding='utf-8')
        metadata = modules / '.modules.yaml'
        aliases = {'@fixture/tool': 'private'}
        configuration = {
            'packageManager': 'pnpm@11.28.5', 'nodeLinker': 'isolated',
            'layoutVersion': 5, 'virtualStoreDir': '.pnpm', 'publicHoistPattern': [],
            'hoistedDependencies': {'@fixture/tool@1.2.3(peer@2.0.0)': aliases},
        }
        metadata.write_text(json.dumps(configuration), encoding='utf-8')
        workspace_link = checkout / 'apps/ui/node_modules/@fixture/tool'
        root_link = modules / '@fixture/tool'
        for projection in (workspace_link, root_link):
            projection.parent.mkdir(parents=True)
            try:
                projection.symlink_to(os.path.relpath(package, projection.parent), target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f'The fixture cannot create a package symlink: {error}')

        row = self.row(IDLE)
        self.assert_held(row, 'unretained_local_evidence')
        self.assertEqual(row['details']['unretained_local_evidence'], [str(root_link.relative_to(checkout))])

        aliases['@fixture/tool'] = 'public'
        configuration['publicHoistPattern'] = ['@fixture/*']
        metadata.write_text(json.dumps(configuration), encoding='utf-8')
        self.assert_candidate(self.row(IDLE))

        configuration['hoistedDependencies'] = {'@fixture/tool@1.2.3(peer@3.0.0)': aliases}
        metadata.write_text(json.dumps(configuration), encoding='utf-8')
        row = self.row(IDLE)
        self.assert_held(row, 'unretained_local_evidence')
        self.assertEqual(row['details']['unretained_local_evidence'], [str(root_link.relative_to(checkout))])

    def test_scoped_pnpm_links_to_external_stores_preserve_path_consumers(self):
        checkout = self.candidate
        self.commit(checkout, '.gitignore', 'node_modules/\n')
        self.commit(checkout, 'package.json', '{"dependencies":{"@fixture/tool":"^1.2.0"}}\n')
        head = self.commit(checkout, 'pnpm-lock.yaml', "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n" +
                           PNPM_TOOL_DEPENDENCY + PNPM_TOOL_RESOLUTIONS)
        self.git('update-ref', 'refs/remotes/origin/main', head)
        foreign = self.add_worktree('foreign')
        modules = checkout / 'node_modules'
        modules.mkdir()
        (modules / '.modules.yaml').write_text(json.dumps({
            'packageManager': 'pnpm@11.28.5', 'nodeLinker': 'isolated',
            'layoutVersion': 5, 'virtualStoreDir': '.pnpm',
        }), encoding='utf-8')
        package = 'node_modules/.pnpm/@fixture+tool@1.2.3_peer@2.0.0/node_modules/@fixture/tool'
        foreign_package = foreign / package
        external_package = self.directory / 'external' / package
        projection = modules / '@fixture/tool'
        projection.parent.mkdir()
        for target in (foreign_package, external_package):
            with self.subTest(target=target):
                target.mkdir(parents=True)
                (target / 'package.json').write_text('{"name":"@fixture/tool","version":"1.2.3"}', encoding='utf-8')
                try:
                    projection.symlink_to(target, target_is_directory=True)
                except (NotImplementedError, OSError) as error:
                    self.skipTest(f'The fixture cannot create a package symlink: {error}')
                row = self.row(IDLE)
                self.assert_held(row, 'unretained_local_evidence')
                self.assertEqual(row['details']['unretained_local_evidence'], [str(projection.relative_to(checkout))])
                if target == foreign_package:
                    foreign_row = self.row(IDLE, foreign)
                    self.assert_held(foreign_row, 'downstream_path_consumer')
                    self.assertEqual(foreign_row['details']['downstream_path_consumer'],
                                     [f'{checkout}: {projection}'])
                projection.unlink()

    def test_partial_process_visibility_only_admits_otherwise_eligible_reviews(self):
        review = self.candidate
        active = self.add_worktree('active')
        unretained = self.add_worktree('unretained')
        consumed = self.add_worktree('consumed')
        marker = review / '.env'
        marker.write_bytes(b'SYNTHETIC=review\n')
        archived = self.archive / 'worktrees' / self.head(review) / 'files/.env'
        archived.parent.mkdir(parents=True)
        archived.write_bytes(marker.read_bytes())
        (unretained / '.env').write_bytes(b'SYNTHETIC=unique\n')
        self.commit(self.primary, 'project/checks.json',
                    json.dumps({'checks': [{'command': [str(consumed / 'fixture.txt')]}]}))

        activity = ({active}, ['process_observation_incomplete'])
        rows = {Path(row['path']): row for row in self.report(activity, archive_root=self.archive)['worktrees']}
        self.assert_held(rows[review], 'process_observation_incomplete')
        self.assertTrue(rows[review]['review_intake'])
        self.assertEqual(rows[review]['local_evidence']['verified_retained_files'], 1)
        for path, blocker in ((active, 'active_session_or_process'), (unretained, 'unretained_local_evidence'),
                              (consumed, 'downstream_path_consumer'), (self.primary, 'primary_checkout')):
            self.assert_held(rows[path], blocker)
            self.assertFalse(rows[path]['review_intake'])
        self.assertFalse(self.row(IDLE, archive_root=self.archive)['review_intake'])
