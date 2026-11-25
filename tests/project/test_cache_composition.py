"""Exercise fixed cache boundaries, measurements, and anonymous failure reports."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import cache_composition


REPOSITORY = Path(__file__).resolve().parents[2]


class CacheCompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cargo_home = self.root / 'cargo-home'
        self.target = self.root / 'workspace' / 'target'

    def write(self, relative_path, contents):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def measure(self, **options):
        return cache_composition.measure(self.cargo_home, self.target, **options)

    def test_fixed_components_have_independently_counted_logical_bytes(self):
        for path, content in (
            ('cargo-home/registry/index/sparse/.cache/membership', b'1'),
            ('cargo-home/registry/cache/source/archive.crate', b'23'),
            ('cargo-home/git/db/repo/objects/pack/p1', b'456'),
            ('workspace/target/debug/deps/compiler-output', b'abcd'),
            ('workspace/target/debug/deps/empty-output', b''),
            ('workspace/target/debug/build/script/out/resource', b'efghi'),
            ('workspace/target/debug/incremental/unit/object', b'123456'),
            ('workspace/target/debug/.fingerprint/unit/stamp', b'1234567'),
            ('workspace/target/debug/examples/bridge.exe', b'12345678'),
            ('workspace/target/CACHEDIR.TAG', b'123456789'),
            ('workspace/target/release/library.dll', b'1234567890'),
            ('cargo-home/credentials.toml', b'ignored outside fixed cargo roots'),
            ('cargo-home/registry/src/checkout/private-source', b'also outside'),
            ('workspace/target-sibling/private-source', b'outside target'),
        ):
            self.write(path, content)

        with patch('builtins.open', side_effect=AssertionError('file contents were opened')), \
                patch('io.open', side_effect=AssertionError('file contents were opened')):
            measurement = self.measure()

        self.assertEqual(measurement['status'], 'complete')
        self.assertIsNone(measurement['limit'])
        self.assertEqual(measurement['missing_roots'], 0)
        self.assertEqual(measurement['errors'], 0)
        self.assertEqual(measurement['skipped_links'], 0)
        self.assertEqual(measurement['components'], {
            'cargo_registry_index': {'files': 1, 'logical_bytes': 1},
            'cargo_registry_cache': {'files': 1, 'logical_bytes': 2},
            'cargo_git_db': {'files': 1, 'logical_bytes': 3},
            'target_deps': {'files': 2, 'logical_bytes': 4},
            'target_build': {'files': 1, 'logical_bytes': 5},
            'target_incremental': {'files': 1, 'logical_bytes': 6},
            'target_fingerprints': {'files': 1, 'logical_bytes': 7},
            'target_examples': {'files': 1, 'logical_bytes': 8},
            'target_other': {'files': 2, 'logical_bytes': 19},
        })

    def test_hardlinks_are_counted_once_per_path(self):
        original = self.write('workspace/target/debug/deps/artifact.exe', b'12345')
        alias = self.target / 'debug' / 'examples' / 'alias.exe'
        alias.parent.mkdir()
        try:
            os.link(original, alias)
            os.link(original, self.root / 'outside-hardlink')
        except (NotImplementedError, OSError):
            self.skipTest('Hardlinks are unavailable on the test filesystem.')

        measurement = self.measure()

        self.assertEqual(measurement['status'], 'complete')
        self.assertEqual(measurement['components']['target_deps'], {'files': 1, 'logical_bytes': 5})
        self.assertEqual(measurement['components']['target_examples'], {'files': 1, 'logical_bytes': 5})
        self.assertEqual(sum(row['files'] for row in measurement['components'].values()), 2)
        self.assertEqual(original.stat().st_nlink, 3)

    def test_symlinked_roots_ancestors_and_entries_do_not_supply_bytes(self):
        outside = self.write('outside/cache/private-file', b'not measured')
        kept = self.write('workspace/target/debug/examples/safe.exe', b'123')
        registry = self.cargo_home / 'registry'
        self.cargo_home.mkdir()
        git = self.cargo_home / 'git'
        git.mkdir()
        try:
            (git / 'db').symlink_to(outside.parent, target_is_directory=True)
            registry.symlink_to(outside.parent.parent, target_is_directory=True)
            (kept.parent / 'directory-link').symlink_to(outside.parent, target_is_directory=True)
            (kept.parent / 'file-link').symlink_to(outside)
            (kept.parent / 'missing-link').symlink_to(outside.parent / 'missing')
        except (NotImplementedError, OSError):
            self.skipTest('Symlinks are unavailable on the test filesystem.')

        measurement = self.measure()

        self.assertEqual(measurement['components']['cargo_registry_cache'], {'files': 0, 'logical_bytes': 0})
        self.assertEqual(measurement['components']['cargo_git_db'], {'files': 0, 'logical_bytes': 0})
        self.assertEqual(measurement['components']['target_examples'], {'files': 1, 'logical_bytes': 3})
        self.assertGreaterEqual(measurement['skipped_links'], 5)
        self.assertNotIn('private-file', json.dumps(measurement))

    def test_windows_reparse_attribute_rejects_directory_and_regular_mode(self):
        for mode in (stat.S_IFDIR, stat.S_IFREG):
            with self.subTest(mode=mode):
                reparse = SimpleNamespace(st_mode=mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                ordinary = SimpleNamespace(st_mode=mode, st_file_attributes=stat.FILE_ATTRIBUTE_ARCHIVE)
                self.assertTrue(cache_composition._is_link(reparse))
                self.assertFalse(cache_composition._is_link(ordinary))
        self.assertTrue(cache_composition._is_link(SimpleNamespace(st_mode=stat.S_IFLNK)))
        self.assertFalse(cache_composition._is_link(SimpleNamespace(st_mode=stat.S_IFREG)))

    def test_scandir_neither_counts_nor_descends_into_reparse_entries(self):
        link_directory = self.write('workspace/target/reparse-directory/nested', b'hidden').parent
        link_file = self.write('workspace/target/reparse-file', b'hidden')
        self.write('workspace/target/ordinary-file', b'123')
        scandir = os.scandir
        reparse_entries = {link_directory, link_file}

        class ReparseEntry:
            def __init__(self, entry):
                self.entry = entry

            def __getattr__(self, name):
                return getattr(self.entry, name)

            def __fspath__(self):
                return self.entry.path

            def stat(self, *, follow_symlinks):
                if follow_symlinks:
                    raise AssertionError('The observation followed a reparse entry.')
                original = self.entry.stat(follow_symlinks=False)
                return SimpleNamespace(
                    st_mode=original.st_mode,
                    st_size=original.st_size,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )

        @contextmanager
        def scan(path):
            self.assertNotEqual(Path(path), link_directory)
            with scandir(path) as entries:
                yield (ReparseEntry(entry) if Path(entry.path) in reparse_entries else entry
                       for entry in entries)

        with patch.object(cache_composition.os, 'scandir', side_effect=scan):
            measurement = self.measure()

        self.assertEqual(measurement['status'], 'complete')
        self.assertEqual(measurement['skipped_links'], 2)
        self.assertEqual(measurement['components']['target_other'], {'files': 1, 'logical_bytes': 3})

    @unittest.skipUnless(os.name == 'nt', 'Requires a native Windows junction.')
    def test_real_windows_directory_junction_does_not_supply_bytes(self):
        outside = self.write('outside/private-file', b'not counted').parent
        self.write('workspace/target/debug/deps/ordinary', b'123')
        junction = self.target / 'debug' / 'deps' / 'junction'

        def remove_junction():
            if junction.is_junction():
                junction.rmdir()

        self.addCleanup(remove_junction)
        environment = {
            **os.environ,
            'SCOPEHUB_FIXTURE_JUNCTION': str(junction),
            'SCOPEHUB_FIXTURE_JUNCTION_TARGET': str(outside),
        }
        result = subprocess.run(
            'cmd.exe /d /v:off /c mklink /J "%SCOPEHUB_FIXTURE_JUNCTION%" "%SCOPEHUB_FIXTURE_JUNCTION_TARGET%"',
            capture_output=True, check=False, timeout=10, env=environment,
        )
        if result.returncode != 0:
            self.skipTest('Directory junctions are unavailable on the test filesystem.')
        self.assertTrue(junction.is_junction())

        measurement = self.measure()

        self.assertEqual(measurement['status'], 'complete')
        self.assertEqual(measurement['skipped_links'], 1)
        self.assertEqual(measurement['components']['target_deps'], {'files': 1, 'logical_bytes': 3})
        self.assertEqual((outside / 'private-file').read_bytes(), b'not counted')


    def test_missing_roots_are_a_complete_empty_observation(self):
        measurement = self.measure()

        self.assertEqual(measurement['status'], 'complete')
        self.assertEqual(measurement['missing_roots'], 4)
        self.assertEqual(measurement['entries'], 0)
        self.assertEqual(measurement['errors'], 0)
        self.assertTrue(all(row == {'files': 0, 'logical_bytes': 0}
                            for row in measurement['components'].values()))

    def test_unreadable_directory_keeps_safe_counts_and_hides_error_detail(self):
        self.write('workspace/target/available-output', b'1234')
        private_path = self.write('workspace/target/private-directory/unreadable-file', b'ignored').parent
        scandir = os.scandir

        def selective_scandir(path):
            if Path(path) == private_path:
                raise PermissionError('private exception detail', str(private_path))
            return scandir(path)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cache_composition.os, 'scandir', side_effect=selective_scandir), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            measurement = self.measure()

        self.assertEqual(measurement['status'], 'incomplete')
        self.assertIsNone(measurement['limit'])
        self.assertEqual(measurement['errors'], 1)
        self.assertEqual(measurement['components']['target_other'], {'files': 1, 'logical_bytes': 4})
        exported = json.dumps(measurement) + stdout.getvalue() + stderr.getvalue()
        for secret in ('private-directory', 'unreadable-file', 'private exception detail', str(self.root)):
            self.assertNotIn(secret, exported)

    def test_entry_budget_stops_before_including_more_files(self):
        self.write('workspace/target/first', b'abc')
        self.write('workspace/target/second', b'def')

        measurement = self.measure(max_entries=1)

        self.assertEqual(measurement['status'], 'incomplete')
        self.assertEqual(measurement['limit'], 'entries')
        self.assertEqual(measurement['entries'], 1)
        self.assertEqual(measurement['components']['target_other'], {'files': 1, 'logical_bytes': 3})

    def test_time_budget_expires_using_injected_clock(self):
        self.write('workspace/target/file', b'abc')
        clock_values = iter((0.0,))

        measurement = self.measure(max_seconds=0.5, clock=lambda: next(clock_values, 1.0))

        self.assertEqual(measurement['status'], 'incomplete')
        self.assertEqual(measurement['limit'], 'time')
        self.assertEqual(measurement['entries'], 0)
        self.assertEqual(measurement['components']['target_other'], {'files': 0, 'logical_bytes': 0})


class CacheCompositionCliTests(unittest.TestCase):
    def test_invalid_phase_emits_only_the_anonymous_unavailable_record(self):
        with tempfile.TemporaryDirectory() as location:
            rejected_value = str(Path(location) / 'private-phase-value')
            result = subprocess.run(
                [sys.executable, '-B', str(REPOSITORY / 'scripts/cache_composition.py'), rejected_value],
                cwd=location, capture_output=True, encoding='utf-8', timeout=10,
            )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, 'SCOPEHUB_CACHE_COMPOSITION {"schema_version":1,"status":"unavailable"}\n')
        self.assertEqual(result.stderr, '')


class CacheCompositionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.decision = {
            'profile': 'full', 'base': 'b' * 40, 'checkout': 'c' * 40,
            'reason_code': 'change_requires_full', 'change_count': 3,
        }
        self.env = {
            'RUNNER_OS': 'Windows', 'RUNNER_ARCH': 'X64',
            'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_BASE_REF': 'main', 'GITHUB_HEAD_REF': 'codex/audit-native-cache-composition',
            'GITHUB_RUN_ID': '12345', 'GITHUB_SHA': 'c' * 40,
            'SCOPEHUB_SOURCE_HEAD': 'd' * 40, 'SCOPEHUB_SCOPE_BASE': 'b' * 40,
            'SCOPEHUB_CACHE_LANE': 'core', 'SCOPEHUB_CACHE_MATCH': 'prefix',
            'SCOPEHUB_SCOPE_DECISION': json.dumps(self.decision),
        }

    def test_valid_record_reserves_fixed_identity_phase_and_match_fields(self):
        record = cache_composition.provenance(self.env, 'restored')
        self.assertEqual(record, {
            'schema_version': 1, 'event': 'pull_request', 'profile': 'full',
            'platform': 'Windows', 'architecture': 'X64', 'lane': 'core', 'phase': 'restored',
            'run_id': '12345', 'run_attempt': 1, 'checkout_sha': 'c' * 40,
            'source_head': 'd' * 40, 'base_sha': 'b' * 40, 'cache_match': 'prefix',
            'accounting': 'regular_file_paths_logical_bytes_including_hardlink_aliases',
        })
        for mode in ('primary_exact', 'legacy_equivalent'):
            with self.subTest(mode=mode):
                env = dict(self.env, SCOPEHUB_CACHE_LANE='browser', SCOPEHUB_CACHE_MATCH=mode)
                record = cache_composition.provenance(env, 'post-check')
                self.assertEqual((record['lane'], record['cache_match'], record['phase']),
                                 ('browser', mode, 'post-check'))

    def test_rejects_unrelated_events_branches_platforms_lanes_and_attempts(self):
        for key, value in (
            ('GITHUB_EVENT_NAME', 'workflow_dispatch'), ('GITHUB_BASE_REF', 'elsewhere'),
            ('GITHUB_HEAD_REF', 'Codex/audit-native-cache-composition'),
            ('RUNNER_OS', 'Linux'), ('RUNNER_ARCH', 'ARM64'), ('GITHUB_RUN_ATTEMPT', '2'),
            ('SCOPEHUB_CACHE_LANE', 'python'), ('SCOPEHUB_CACHE_MATCH', 'not_restored'),
            ('GITHUB_RUN_ID', 'invalid-provenance'),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                cache_composition.provenance(dict(self.env, **{key: value}), 'restored')

    def test_fallback_full_and_unbound_selector_records_are_not_measured(self):
        for overrides in (
            {'reason_code': 'comparison_unavailable'}, {'profile': 'records'},
            {'base': 'e' * 40}, {'checkout': 'e' * 40}, {'change_count': True},
            {'change_count': 1.5}, {'change_count': 0},
        ):
            with self.subTest(overrides=overrides):
                raw = json.dumps(dict(self.decision, **overrides))
                env = dict(self.env, SCOPEHUB_SCOPE_DECISION=raw)
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.dict(os.environ, env, clear=True), \
                        patch.object(sys, 'argv', ['observer', 'restored']), \
                        patch.object(cache_composition, 'measure') as measure, \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(cache_composition.main(), 1)
                measure.assert_not_called()
                self.assertEqual(stderr.getvalue(), '')
                self.assertEqual(json.loads(stdout.getvalue().split(' ', 1)[1]),
                                 {'schema_version': 1, 'status': 'unavailable'})


class CacheCompositionWorkflowTests(unittest.TestCase):
    def test_only_the_designated_native_pr_lanes_get_observations(self):
        workflow = (REPOSITORY / '.github/workflows/verify.yml').read_text(encoding='utf-8')
        steps = re.split(r'^      - ', workflow, flags=re.MULTILINE)
        restored, = (step for step in steps if step.startswith('name: Observe restored Cargo cache composition\n'))
        post_check, = (step for step in steps if step.startswith('name: Observe post-check Cargo cache composition\n'))
        guarded = re.search(r'^        if: &cache_composition_pr[^\n]*\n((?:^          [^\n]*\n)*)',
                            restored, flags=re.MULTILINE)
        self.assertIsNotNone(guarded)
        conditions = ' '.join(guarded.group().split())
        self.assertIn('        if: *cache_composition_pr\n', post_check)
        for clause in (
            "github.event_name == 'pull_request'",
            'github.event.pull_request.head.repo.full_name == github.repository',
            "steps.scope.outcome == 'success'",
            "steps.scope.outputs.profile == 'full'",
            "steps.scope.outputs.decision != ''",
            "matrix.lane == 'core'",
            "matrix.lane == 'browser'",
            "steps.cargo_cache.outcome == 'success'",
            "steps.cargo_cache.outputs['cache-matched-key'] != ''",
        ):
            self.assertIn(clause, conditions)
        self.assertRegex(conditions, r"github\.(head_ref|event\.pull_request\.head\.ref) == 'codex/audit-native-cache-composition'")
        self.assertRegex(conditions, r"github\.(base_ref|event\.pull_request\.base\.ref) == 'main'")
        self.assertRegex(conditions, r"github\.run_attempt == (?:'1'|1)(?=[ )]|$)")
        self.assertRegex(conditions, r"(runner\.os == 'Windows'|matrix\.os == 'windows-latest')")
        for step in (restored, post_check):
            self.assertNotIn('uses:', step)
            self.assertIn('continue-on-error: true', step)
            self.assertNotIn('GITHUB_OUTPUT', step)
            self.assertIn('scripts/cache_composition.py', step)

    def test_observation_positions_bracket_checks_on_the_same_producer(self):
        workflow = (REPOSITORY / '.github/workflows/verify.yml').read_text(encoding='utf-8')
        ordered = [
            'id: cargo_cache',
            'name: Observe restored Cargo cache composition',
            'name: Verify the Windows ${{ matrix.lane }} lane',
            'name: Observe post-check Cargo cache composition',
            'name: Save the Cargo cache for this selection',
        ]
        positions = [workflow.index(marker) for marker in ordered]
        self.assertEqual(positions, sorted(positions))
