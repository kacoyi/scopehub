"""Storage regression fixtures use only disposable repositories and worktrees."""

import errno
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import storage


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/storage.py'
CARGO_TAG = (
    'Signature: 8a477f597d28d172789f06886806bc55\n'
    '# This file is a cache directory tag created by cargo.\n'
    '# For information about cache directory tags see https://bford.info/cachedir/\n'
)
FIXTURE_ARTIFACT = 'debug/deps/libscopehub_fixture-0123456789abcdef.rlib'


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.checkouts = self.directory / 'checkouts'
        self.checkouts.mkdir()
        self.root = self.checkouts / 'main'
        self.initialize(self.root)

    def git(self, root, *args):
        return subprocess.run(
            ['git', '-C', str(root), '-c', 'core.hooksPath=.git/no-hooks',
             '-c', 'commit.gpgSign=false', *args],
            env={name: value for name, value in os.environ.items() if not name.startswith('GIT_')},
            check=True, capture_output=True, timeout=10,
        )

    def initialize(self, root):
        root.mkdir()
        self.git(root, 'init', '--quiet')
        (root / '.gitignore').write_text('/target/\n', encoding='utf-8')
        (root / 'Cargo.toml').write_text(
            '[package]\nname = "scopehub-fixture"\nversion = "0.0.0"\n', encoding='utf-8',
        )
        self.git(root, 'add', '.gitignore', 'Cargo.toml')
        self.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--quiet', '-m', 'Create fixture')

    def worktree(self, name):
        path = self.checkouts / name
        self.git(self.root, 'worktree', 'add', '--quiet', '--detach', str(path), 'HEAD')
        return path

    def write(self, root, name, content=b'evidence fixture'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def cargo_cache(self, worktree, *, size=16_384, stamp=None, target_dir='target'):
        target = worktree / target_dir
        self.write(target, 'CACHEDIR.TAG', CARGO_TAG.encode('ascii'))
        self.write(target, '.rustc_info.json', b'{}')
        for lock in ('.cargo-lock', '.cargo-artifact-lock', '.cargo-build-lock'):
            self.write(target, f'debug/{lock}', b'')
        for name in ('.fingerprint', 'deps', 'incremental', 'build', 'examples'):
            (target / 'debug' / name).mkdir()
        artifact = self.write(target, FIXTURE_ARTIFACT, b'\x01' * size)
        if stamp is not None:
            for path in [artifact, *artifact.parents[:3], *target.rglob('*')]:
                os.utime(path, (stamp, stamp))
        return artifact

    def report_entry(self, result, path):
        return next(entry for entry in result['targets'] if Path(entry['path']) == path)

    def rustc_session(self, debug, *, complete=False):
        incremental = 'incremental/scopehub_fixture-abc12def34567'
        query = self.write(debug, f'{incremental}/s-abc12-xyz89-rnd67/query-cache.bin')
        if complete:
            for name in ('dep-graph.bin', 'work-products.bin'):
                self.write(query.parent, name)
        lock = self.write(debug, f'{incremental}/s-abc12-xyz89.lock', b'')
        return query, lock


class StorageReportTests(RepositoryFixture):
    def test_bootstrap_cli_defaults_to_read_only_report(self):
        linked = self.worktree('linked checkout')
        artifact = self.cargo_cache(linked)
        current = self.cargo_cache(self.root)
        unrelated = self.checkouts / 'unrelated checkout'
        self.initialize(unrelated)
        outside_artifact = self.cargo_cache(unrelated)
        before = {file: (file.read_bytes(), file.stat().st_ino)
                  for file in (artifact, current, outside_artifact)}

        process = subprocess.run(
            [sys.executable, '-I', '-S', str(SCRIPT)], cwd=self.root,
            capture_output=True, encoding='utf-8', timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual({Path(entry['path']) for entry in result['targets']},
                         {linked / 'target', self.root / 'target'})
        self.assertEqual({file: (file.read_bytes(), file.stat().st_ino) for file in before}, before)

    def test_bootstrap_cli_inventories_migrated_worktree_with_shared_local_config(self):
        managed = self.directory / 'managed worktrees'
        managed.mkdir()
        original = self.worktree('migrating')
        artifact = self.cargo_cache(original)
        evidence = self.write(original, '.reports/runs/fixture/result.json')
        migrated = managed / 'migrated checkout'
        self.git(self.root, 'worktree', 'move', str(original), str(migrated))
        legacy = self.cargo_cache(self.worktree('legacy'))
        outside = self.cargo_cache(self.worktree(self.directory / 'external'))
        self.git(self.root, 'config', '--local', 'scopehub.worktreeRoot', str(managed))
        moved = migrated / artifact.relative_to(original)
        moved_evidence = migrated / evidence.relative_to(original)
        before = {file: file.read_bytes() for file in (moved, moved_evidence, legacy, outside)}

        for checkout in (self.root, migrated):
            with self.subTest(checkout=checkout):
                process = subprocess.run(
                    [sys.executable, '-I', '-S', str(SCRIPT)], cwd=checkout,
                    capture_output=True, encoding='utf-8', timeout=10,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(Path(result['managed_worktree_root']), managed)
                self.assertEqual(Path(result['current_worktree']), checkout)
                self.assertEqual({entry['path'] for entry in result['targets']},
                                 {str(migrated / 'target'), str(legacy.parents[2]), str(outside.parents[2])})
                self.assertIsNone(self.report_entry(result, outside.parents[2])['allocated_bytes'])
                if sys.platform == 'linux':
                    self.assertGreater(self.report_entry(result, migrated / 'target')['allocated_bytes'], 0)
                    self.assertGreater(self.report_entry(result, legacy.parents[2])['allocated_bytes'], 0)
        self.assertEqual({file: file.read_bytes() for file in before}, before)

    def test_worktree_specific_config_does_not_expand_repository_scope(self):
        managed = self.directory / 'managed'
        managed.mkdir()
        external = self.worktree(managed / 'external')
        self.cargo_cache(external)
        self.git(self.root, 'config', '--local', 'extensions.worktreeConfig', 'true')
        self.git(self.root, 'config', '--worktree', 'scopehub.worktreeRoot', str(managed))

        result = storage.run(self.root)

        self.assertIsNone(result['managed_worktree_root'])
        self.assertIsNone(self.report_entry(result, external / 'target')['allocated_bytes'])

    def test_nested_root_does_not_authorize_collection_scope(self):
        containing = self.worktree('containing')
        managed = containing / 'target' / 'managed'
        nested = self.worktree(managed / 'nested')
        self.cargo_cache(nested)
        self.git(self.root, 'config', '--local', 'scopehub.worktreeRoot', str(managed))

        result = storage.run(self.root)

        self.assertIsNone(result['managed_worktree_root'])
        self.assertIsNone(self.report_entry(result, nested / 'target')['allocated_bytes'])

    @unittest.skipUnless(sys.platform == 'linux', 'Allocated block counts are checked on Linux.')
    def test_sparse_files_are_reported_as_allocated_blocks(self):
        linked = self.worktree('linked')
        self.cargo_cache(linked)
        sparse = self.write(linked / 'target', 'debug/deps/libfixture-9876543210abcdef.rlib', b'')
        with sparse.open('wb') as file:
            file.truncate(64 * 1024**2)
        if sparse.stat().st_blocks >= sparse.stat().st_size // 512:
            self.skipTest('Fixture filesystem does not support sparse files.')

        result = storage.run(self.root)
        target = linked / 'target'
        allocated = sum(path.stat().st_blocks * 512 for path in [target, *target.rglob('*')])
        self.assertEqual(self.report_entry(result, target)['allocated_bytes'], allocated)
        self.assertTrue(sparse.exists())


@unittest.skipUnless(sys.platform == 'linux', 'Collection fixtures exercise Linux filesystem locks.')
class CollectionTests(RepositoryFixture):
    def collect(self, high_bytes=1, low_bytes=0, *, mounts=None, active_paths=(), inaccessible=0):
        # The fixture supplies namespace visibility. Real file locks and deletion
        # continue to operate on the temporary repositories.
        context = storage.LinuxContext(mounts or {Path('/'): 'ext4'}, set(active_paths),
                                       inaccessible_processes=inaccessible)
        with patch.object(storage, 'linux_context', return_value=context):
            return storage.run(self.root, collect=True, high_bytes=high_bytes, low_bytes=low_bytes)

    def test_threshold_and_repeat_collection_preserve_cargo_markers_and_release_files(self):
        linked = self.worktree('linked')
        artifact = self.cargo_cache(linked)
        target = linked / 'target'
        retained = [target / 'CACHEDIR.TAG', target / 'debug/.cargo-lock',
                    self.write(target, 'release/scopehub'),
                    self.write(target, f'x86_64-unknown-linux-gnu/{FIXTURE_ARTIFACT}'),
                    self.write(linked, '.reports/runs/fixture/result.json'),
                    self.write(target, 'debug/scopehub'),
                    self.write(target, 'debug/deps/build-notes.json'),
                    self.write(target, 'debug/build/fixture-0123456789abcdef/out/private.bin'),
                    self.write(target, 'debug/examples/fixture')]
        original = [path.read_bytes() for path in retained]

        self.collect(high_bytes=64 * 1024**3)
        self.assertTrue(artifact.is_file())
        self.collect()
        self.assertFalse(artifact.exists())
        self.assertEqual([path.read_bytes() for path in retained], original)
        second = self.collect()
        self.assertEqual(second['collected'], [])
        self.assertEqual([path.read_bytes() for path in retained], original)

    def test_report_above_high_watermark_keeps_every_artifact(self):
        linked = self.worktree('linked')
        artifact = self.cargo_cache(linked)
        context = storage.LinuxContext({Path('/'): 'ext4'}, set())
        with patch.object(storage, 'linux_context', return_value=context):
            report = storage.run(self.root, high_bytes=1, low_bytes=0)
        self.assertGreater(report['allocated_bytes'], 1)
        self.assertEqual(report['collected'], [])
        self.assertEqual(report['process_visibility'], 'full')
        self.assertEqual(report['inaccessible_processes'], 0)
        self.assertEqual(report['collection_scope'], 'debug_intermediates')
        self.assertTrue(artifact.is_file())

    def test_above_high_watermark_collects_oldest_and_stops_at_low_watermark(self):
        older_tree = self.worktree('older')
        newer_tree = self.worktree('newer')
        older = self.cargo_cache(older_tree, stamp=1000)
        newer = self.cargo_cache(newer_tree, stamp=2000)
        total = storage.run(self.root)['allocated_bytes']
        reclaim = older.stat().st_blocks * 512
        self.assertGreater(reclaim, 0)

        exact_high = self.collect(high_bytes=total, low_bytes=total - reclaim)
        self.assertEqual(exact_high['collected'], [])
        collected = self.collect(high_bytes=total - 1, low_bytes=total - reclaim)

        self.assertEqual([Path(item['path']) for item in collected['collected']], [older_tree / 'target'])
        self.assertLessEqual(collected['estimated_remaining_bytes'], total - reclaim)
        self.assertFalse(older.exists())
        self.assertTrue(newer.exists())

    def test_preserves_current_tracked_nested_external_symlinked_and_unknown_cache(self):
        current = self.cargo_cache(self.root)
        tracked_tree = self.worktree('tracked')
        tracked = self.cargo_cache(tracked_tree)
        self.git(tracked_tree, 'add', '--force', f'target/{FIXTURE_ARTIFACT}')
        unrelated = self.checkouts / 'unrelated'
        self.initialize(unrelated)
        unrelated_artifact = self.cargo_cache(unrelated)
        external_tree = self.worktree(self.directory / 'external')
        external = self.cargo_cache(external_tree)
        containing = self.worktree('containing')
        containing_artifact = self.cargo_cache(containing)
        nested_tree = self.worktree(containing / 'target/debug/deps/nested')
        nested = self.cargo_cache(nested_tree)
        symlink_tree = self.worktree('symlink')
        (symlink_tree / 'target').symlink_to(unrelated / 'target', target_is_directory=True)
        unknown_tree = self.worktree('unknown')
        unknown = self.write(unknown_tree / 'target', FIXTURE_ARTIFACT)
        partial_tree = self.worktree('partial-lock-layout')
        partial = self.cargo_cache(partial_tree)
        (partial_tree / 'target/debug/.cargo-artifact-lock').unlink()
        normal = self.cargo_cache(self.worktree('normal'))

        result = self.collect()

        self.assertFalse(normal.exists(), result)
        self.assertTrue((symlink_tree / 'target').is_symlink())
        for protected in (current, tracked, unrelated_artifact, external, containing_artifact, nested, unknown,
                          partial):
            with self.subTest(protected=protected):
                self.assertTrue(protected.is_file())

    def test_git_locked_or_foreign_registration_does_not_authorize_collection(self):
        locked_tree = self.worktree('locked-worktree')
        locked = self.cargo_cache(locked_tree)
        self.git(self.root, 'worktree', 'lock', '--reason', 'fixture in use', str(locked_tree))
        replaced_tree = self.worktree('replaced-checkout')
        (replaced_tree / '.git').unlink()
        self.git(replaced_tree, 'init', '--quiet')
        foreign = self.cargo_cache(replaced_tree)
        normal = self.cargo_cache(self.worktree('normal'))

        result = self.collect()

        self.assertFalse(normal.exists(), result)
        self.assertTrue(locked.is_file(), result)
        self.assertTrue(foreign.is_file(), result)

    def test_managed_collection_retains_actual_nested_and_mounted_paths(self):
        managed = self.directory / 'managed'
        managed.mkdir()
        self.git(self.root, 'config', '--local', 'scopehub.worktreeRoot', str(managed))
        container = self.cargo_cache(self.worktree(managed / 'container'))
        nested = self.cargo_cache(self.worktree(container.parent / 'nested'))
        mounted_cache = self.cargo_cache(self.worktree(managed / 'mounted-cache'))
        mounted_checkout = self.worktree(managed / 'mounted-checkout')
        mounted = self.cargo_cache(mounted_checkout)
        normal = self.cargo_cache(self.worktree(managed / 'normal'))
        replaced = self.worktree(managed / 'replaced')
        (replaced / '.git').unlink()
        self.git(replaced, 'init', '--quiet')
        foreign = self.cargo_cache(replaced)
        unrelated = managed / 'unregistered'
        self.initialize(unrelated)
        unregistered = self.cargo_cache(unrelated)

        result = self.collect(mounts={Path('/'): 'ext4', mounted_cache.parent: 'ext4',
                                      mounted_checkout: 'ext4'})

        self.assertEqual([entry['path'] for entry in result['collected']], [str(normal.parents[2])])
        self.assertEqual(self.report_entry(result, container.parents[2])['reason'], 'nested_repository_or_evidence')
        self.assertIsNone(self.report_entry(result, nested.parents[2])['allocated_bytes'])
        self.assertEqual(self.report_entry(result, mounted_cache.parents[2])['reason'], 'mount_boundary')
        self.assertEqual(self.report_entry(result, mounted.parents[2])['reason'], 'unsupported_filesystem_or_mount')
        self.assertNotIn(str(unregistered.parents[2]), {entry['path'] for entry in result['targets']})
        self.assertFalse(normal.exists())
        for path in (container, nested, mounted_cache, mounted, foreign, unregistered):
            self.assertTrue(path.is_file(), path)

    def test_collection_rechecks_managed_scope_before_deletion(self):
        managed = self.directory / 'managed'
        managed.mkdir()
        self.git(self.root, 'config', '--local', 'scopehub.worktreeRoot', str(managed))
        artifact = self.cargo_cache(self.worktree(managed / 'managed-member'))
        managed_directory = storage.managed_worktree_directory

        def unset_after_snapshot(current, registered):
            directory = managed_directory(current, registered)
            if directory is not None:
                self.git(self.root, 'config', '--local', '--unset', 'scopehub.worktreeRoot')
            return directory

        with patch.object(storage, 'managed_worktree_directory', side_effect=unset_after_snapshot):
            result = self.collect()

        self.assertEqual(result['collected'], [])
        self.assertEqual(self.report_entry(result, artifact.parents[2])['reason'], 'worktree_directory_changed')
        self.assertTrue(artifact.is_file())

    def test_held_cargo_locks_exclude_only_their_cache(self):
        import fcntl

        for lock in ('.cargo-lock', '.cargo-artifact-lock', '.cargo-build-lock'):
            with self.subTest(lock=lock):
                locked_tree = self.worktree(f'locked-{lock}')
                protected = self.cargo_cache(locked_tree)
                other = self.cargo_cache(self.worktree(f'idle-{lock}'))
                with (locked_tree / 'target/debug' / lock).open('rb') as held:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = self.collect()
                    self.assertTrue(protected.exists(), result)
                    self.assertFalse(other.exists(), result)

    def test_held_rustc_session_lock_preserves_the_whole_target(self):
        import fcntl

        locked_tree = self.worktree('incremental-locked')
        protected = self.cargo_cache(locked_tree)
        query, lock = self.rustc_session(locked_tree / 'target/debug')
        other = self.cargo_cache(self.worktree('idle'))

        with lock.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.collect()

        self.assertTrue(protected.is_file(), result)
        self.assertEqual(query.read_bytes(), b'evidence fixture')
        self.assertEqual(lock.read_bytes(), b'')
        self.assertFalse(other.exists(), result)

    def test_current_configured_target_in_another_worktree_is_protected(self):
        for name in ('CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_BUILD_DIR'):
            with self.subTest(variable=name):
                configured = self.worktree(f'configured-{name}')
                protected = self.cargo_cache(configured)
                other = self.cargo_cache(self.worktree(f'idle-{name}'))
                target = configured / 'target'
                value = str(target) if name == 'CARGO_TARGET_DIR' else os.path.relpath(target, self.root)
                with patch.dict(os.environ, {name: value}):
                    result = self.collect()
                self.assertEqual(self.report_entry(result, target)['reason'], 'current_configured_target')
                self.assertTrue(protected.is_file(), result)
                self.assertFalse(other.exists(), result)

    def test_busy_paths_and_mount_boundaries_remain_untouched(self):
        busy = self.cargo_cache(self.worktree('busy'))
        mounted = self.cargo_cache(self.worktree('mounted'))
        normal = self.cargo_cache(self.worktree('idle'))
        result = self.collect(mounts={Path('/'): 'ext4', mounted.parent: 'ext4'},
                              active_paths=[busy])
        self.assertTrue(busy.exists(), result)
        self.assertTrue(mounted.exists(), result)
        self.assertFalse(normal.exists(), result)

    def test_collection_rechecks_activity_after_acquiring_locks(self):
        artifact = self.cargo_cache(self.worktree('linked'))
        mounts = {Path('/'): 'ext4'}
        idle = storage.LinuxContext(mounts, set())
        busy = storage.LinuxContext(mounts, {artifact})
        with patch.object(storage, 'linux_context', side_effect=[idle, busy]):
            result = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
        self.assertEqual(result['collected'], [])
        self.assertEqual(self.report_entry(result, artifact.parents[2])['reason'], 'active_process')
        self.assertTrue(artifact.is_file())

    def test_expired_automatic_budget_leaves_cache_untouched(self):
        artifact = self.cargo_cache(self.worktree('linked'))
        context = storage.LinuxContext({Path('/'): 'ext4'}, set())
        with patch.object(storage, 'linux_context', return_value=context):
            result = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0,
                                 deadline=time.monotonic() - 1)
        self.assertEqual(result['collected'], [])
        self.assertFalse(result['inventory_complete'])
        self.assertTrue(artifact.is_file())

    def test_unknown_namespace_leaves_every_candidate_untouched(self):
        artifact = self.cargo_cache(self.worktree('linked'))
        with patch.object(storage, 'linux_context', side_effect=storage.StorageError('hidden processes')):
            result = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
        self.assertEqual(result['collected'], [])
        self.assertFalse(result['collection_available'])
        self.assertEqual(result['process_visibility'], 'unavailable')
        self.assertIn('hidden processes', result['collection_blocked'])
        self.assertTrue(artifact.is_file())

    def test_partial_visibility_only_collects_complete_unlocked_incremental_sessions(self):
        import fcntl

        idle = self.worktree('visible-idle')
        dependency = self.cargo_cache(idle)
        idle_debug = idle / 'target/debug'
        completed, retained_lock = self.rustc_session(idle_debug, complete=True)
        retained = [dependency, self.write(idle_debug, 'scopehub'),
                    self.write(idle_debug, 'deps/test-0123456789abcdef'),
                    self.write(idle_debug, 'deps/test-0123456789abcdef.d'),
                    self.write(idle_debug, '.fingerprint/scopehub-fixture-0123456789abcdef/lib-scopehub_fixture')]

        sessions = {}
        for name in ('incomplete', 'unknown', 'executable', 'locked', 'active'):
            tree = self.worktree(name)
            self.cargo_cache(tree)
            sessions[name] = self.rustc_session(tree / 'target/debug', complete=name != 'incomplete')
        unknown = self.write(sessions['unknown'][0].parent, 'report.txt')
        sessions['executable'][0].chmod(0o700)

        with sessions['locked'][1].open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.collect(active_paths=[sessions['active'][0]], inaccessible=2)

        self.assertTrue(result['collection_available'], result)
        self.assertEqual(result['process_visibility'], 'partial')
        self.assertEqual(result['inaccessible_processes'], 2)
        self.assertEqual(result['collection_scope'], 'completed_incremental_sessions_and_leased_dependencies')
        self.assertEqual([Path(item['path']) for item in result['collected']], [idle / 'target'])
        self.assertFalse(completed.parent.exists(), result)
        self.assertEqual(retained_lock.read_bytes(), b'')
        for path in [*retained, unknown, *(session[0] for session in sessions.values())]:
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), result)

    def test_independent_direct_child_cache_is_counted_once_and_only_it_is_collected(self):
        linked = self.worktree('linked')
        outer = linked / 'target'
        child = outer / 'ubuntu-workspace'
        nested = child / 'grandchild'
        unknown = outer / 'unknown'
        cross_target = outer / 'x86_64-unknown-linux-gnu'
        self.cargo_cache(linked)
        for cache in (child, nested, unknown, cross_target):
            self.cargo_cache(linked, target_dir=cache.relative_to(linked))
        (unknown / '.rustc_info.json').unlink()
        (cross_target / '.rustc_info.json').unlink()
        (cross_target / 'CACHEDIR.TAG').unlink()
        queries = {cache: self.rustc_session(cache / 'debug', complete=True)[0]
                   for cache in (child, nested, unknown, cross_target)}
        expected_usage = sum(path.stat().st_blocks * 512 for path in [outer, *outer.rglob('*')])

        result = self.collect(inaccessible=1)

        self.assertEqual(result['allocated_bytes'], expected_usage)
        self.assertEqual(self.report_entry(result, outer)['allocated_bytes'], expected_usage)
        self.assertEqual(Path(self.report_entry(result, child)['included_in']), outer)
        self.assertEqual([Path(item['path']) for item in result['collected']], [child])
        self.assertFalse(queries[child].exists(), result)
        for cache in (nested, unknown, cross_target):
            with self.subTest(cache=cache):
                self.assertTrue(queries[cache].is_file(), result)

    def require_exclusive_leases(self):
        import fcntl

        probe = self.write(self.directory, 'lease-probe', b'')
        if not hasattr(fcntl, 'F_SETLEASE'):
            self.skipTest('The host does not expose Linux file leases.')
        with probe.open('rb') as descriptor:
            try:
                fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_WRLCK)
            except OSError as error:
                self.skipTest(f'The fixture filesystem does not permit exclusive file leases ({error.errno}).')
            fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_UNLCK)

    def test_cold_partial_dependencies_require_an_exclusive_lease(self):
        self.require_exclusive_leases()
        cache = self.worktree('linked')
        available = self.cargo_cache(cache)
        deps = available.parent
        busy = self.write(deps, 'libheld-1111111111111111.rlib')
        removable = [available, *(self.write(deps, f'libfixture-2222222222222222.{suffix}')
                                  for suffix in ('rmeta', 'd', 'o'))]
        executable = self.write(deps, 'libexec-3333333333333333.rlib')
        executable.chmod(0o700)
        shared = deps / 'libshared-4444444444444444.rlib'
        evidence = self.write(self.directory, 'external-evidence.rlib')
        os.link(evidence, shared)
        retained = [executable, shared, self.write(deps, 'libplugin-5555555555555555.so'),
                    self.write(deps, 'test-6666666666666666'), self.write(deps, 'manual.rlib')]
        aged = time.time() - 2 * 24 * 60 * 60
        for artifact in [busy, *removable, *retained]:
            os.utime(artifact, (aged, aged))
        fresh = self.write(deps, 'libfresh-7777777777777777.rlib')

        hold_open = '''\
import sys

with open(sys.argv[1], "rb"):
    print("ready", flush=True)
    sys.stdin.read()
'''
        with subprocess.Popen([sys.executable, '-c', hold_open, str(busy)], cwd=self.directory,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as reader:
            self.assertTrue(select.select([reader.stdout], [], [], 5)[0], 'Reader did not open the fixture')
            self.assertEqual(reader.stdout.readline(), b'ready\n')
            result = self.collect(inaccessible=1)
            self.assertTrue(busy.is_file(), result)
            reader.communicate(input=b'', timeout=5)
            self.assertEqual(reader.returncode, 0)

        for artifact in removable:
            with self.subTest(artifact=artifact):
                self.assertFalse(artifact.exists(), result)
        second = self.collect(inaccessible=1)
        self.assertFalse(busy.exists(), second)
        for artifact in [fresh, *retained, evidence]:
            with self.subTest(artifact=artifact):
                self.assertEqual(artifact.read_bytes(), b'evidence fixture')

    def test_reader_arriving_during_a_lease_defers_collection(self):
        import fcntl

        self.require_exclusive_leases()
        artifact = self.cargo_cache(self.worktree('linked'), stamp=time.time() - 2 * 24 * 60 * 60)
        try_open = '''\
import os
import sys

try:
    descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK)
except BlockingIOError:
    sys.exit(0)
os.close(descriptor)
sys.exit(1)
'''
        kernel_fcntl = fcntl.fcntl
        readers = []

        def start_reader_before_verifying_lease(fd, command, *args):
            if command == fcntl.F_GETLEASE and not readers:
                child = subprocess.run([sys.executable, '-c', try_open, str(artifact)],
                                       cwd=self.directory, capture_output=True, timeout=5)
                readers.append(child.returncode)
            return kernel_fcntl(fd, command, *args)

        with patch.object(fcntl, 'fcntl', side_effect=start_reader_before_verifying_lease):
            first = self.collect(inaccessible=1)

        self.assertEqual(readers, [0], first)
        self.assertEqual(first['collected'], [])
        self.assertTrue(artifact.is_file())
        second = self.collect(inaccessible=1)
        self.assertFalse(artifact.exists(), second)

    def test_cold_elf_lookalikes_and_non_test_outputs_are_retained(self):
        self.require_exclusive_leases()
        aged = time.time() - 30 * 24 * 60 * 60
        linked = self.worktree('linked')
        dependency = self.cargo_cache(linked, stamp=aged)
        debug = linked / 'target/debug'
        ident = b'\x7fELF\x02\x01\x01' + bytes(9)
        header = struct.pack('<16sHHIQQQIHHHHHH', ident, 3, 62, 1, 0x400078, 64, 0, 0, 64, 56, 1, 64, 0, 0)
        segment = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, 121, 121, 4096)
        elf = header + segment + b'\0'
        metadata_fields = {'rustc': 1, 'target': 2, 'profile': 3, 'path': 4, 'deps': []}

        def write_fingerprint(hash_suffix, name, **overrides):
            group = debug / '.fingerprint' / f'fixture-{hash_suffix}'
            self.write(group, name, b'0123456789abcdef')
            dep_info = self.write(group, f'dep-{name}', b'fixture cargo dependency information')
            metadata = {**metadata_fields, 'local': [{'CheckDepInfo': {
                'dep_info': dep_info.relative_to(debug.parent).as_posix(),
            }}], **overrides}
            self.write(group, f'{name}.json', json.dumps(metadata).encode('utf-8'))

        paths = [
            'deps/missing-1111111111111111',
            'deps/wrong_hash-2222222222222222',
            'deps/wrong_target-3333333333333333',
            'deps/wrong_metadata-4444444444444444',
            'deps/ordinary-5555555555555555',
            'deps/plugin-6666666666666666.so',
            'examples/sample-7777777777777777',
            'installed-8888888888888888',
            'deps/wrong_dep_info-aaaaaaaaaaaaaaaa',
        ]
        write_fingerprint('9999999999999999', 'test-bin-wrong-hash')
        write_fingerprint('3333333333333333', 'test-bin-other-target')
        write_fingerprint('4444444444444444', 'test-bin-wrong-metadata', deps='not a list')
        write_fingerprint('5555555555555555', 'bin-ordinary')
        write_fingerprint('6666666666666666', 'test-bin-plugin')
        write_fingerprint('7777777777777777', 'test-bin-sample')
        write_fingerprint('8888888888888888', 'test-bin-installed')
        write_fingerprint('aaaaaaaaaaaaaaaa', 'test-bin-wrong-dep-info', local=[
            {'CheckDepInfo': {'dep_info': 'debug/.fingerprint/an-unrelated-group/dep-test-bin-wrong-dep-info'}},
        ])
        self.write(debug, 'deps/missing-1111111111111111.d', b'missing: fixture.rs\n')
        lookalikes = [self.write(debug, relative, elf) for relative in paths]
        for artifact in lookalikes:
            artifact.chmod(0o700)
            os.utime(artifact, (aged, aged))

        result = self.collect(inaccessible=1)

        self.assertFalse(dependency.exists(), result)
        self.assertEqual(self.report_entry(result, linked / 'target')['cold_test_harnesses'], 0)
        for artifact in lookalikes:
            with self.subTest(artifact=artifact):
                self.assertEqual(artifact.read_bytes(), elf)

    def test_process_permission_denial_keeps_auditing_visible_processes(self):
        proc = self.directory / 'proc'
        denied, visible = [proc / str(os.getpid() + offset) for offset in (1, 2)]
        uid = os.geteuid()
        for process in (denied, visible):
            self.write(process, 'status', f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n'.encode('ascii'))
            self.write(process, 'cmdline', b'')
            self.write(process, 'maps', b'')
            (process / 'fd').mkdir()
        (visible / 'cwd').symlink_to(self.root, target_is_directory=True)
        proc_paths = {
            '/proc': SimpleNamespace(iterdir=lambda: iter((denied, visible))),
            '/proc/locks': self.write(proc, 'locks', b''),
            '/proc/self/ns/user': SimpleNamespace(stat=lambda: SimpleNamespace(st_ino=0xEFFFFFFD)),
            '/proc/self/ns/pid': SimpleNamespace(stat=lambda: SimpleNamespace(st_ino=0xEFFFFFFC)),
        }
        readlink = os.readlink

        def observe(path):
            if path == '/proc/self':
                return str(os.getpid())
            if path == denied / 'cwd':
                raise PermissionError(errno.EACCES, 'fixture hides this process')
            return readlink(path)

        mounts = {Path('/'): 'ext4', Path('/proc'): 'proc'}
        with (patch.object(storage, 'Path', side_effect=lambda path: proc_paths.get(path, Path(path))),
              patch.object(storage.os, 'readlink', side_effect=observe),
              patch.object(storage, 'mount_table', return_value=(mounts, [('proc', '/')]))):
            context = storage.linux_context()

        self.assertEqual(context.inaccessible_processes, 1)
        self.assertIn(self.root, context.active_paths)

    def test_collection_cannot_keep_full_scope_when_visibility_degrades_after_lock(self):
        tree = self.worktree('linked')
        dependency = self.cargo_cache(tree)
        self.rustc_session(tree / 'target/debug', complete=True)
        mounts = {Path('/'): 'ext4'}
        full = storage.LinuxContext(mounts, set())
        partial = storage.LinuxContext(mounts, set(), inaccessible_processes=1)

        with patch.object(storage, 'linux_context', side_effect=[full, partial]):
            result = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)

        self.assertTrue(dependency.is_file(), result)

    def test_only_known_cargo_intermediates_within_debug_are_collected(self):
        linked = self.worktree('linked')
        artifact = self.cargo_cache(linked)
        debug = linked / 'target/debug'
        fingerprint = '.fingerprint/scopehub-fixture-0123456789abcdef'
        query, incremental_lock = self.rustc_session(debug)
        disposable = [artifact, self.write(debug, f'{fingerprint}/lib-scopehub_fixture'),
                      self.write(debug, f'{fingerprint}/lib-scopehub_fixture.json'), query]
        retained = [self.write(debug, 'deps/reference-0123456789abcdef.png'),
                    self.write(debug, '.fingerprint/manual/report.json'),
                    self.write(debug, 'incremental/manual/fixture.log')]

        result = self.collect()

        for path in disposable:
            with self.subTest(disposable=path):
                self.assertFalse(path.exists(), result)
        for path in retained:
            with self.subTest(retained=path):
                self.assertEqual(path.read_bytes(), b'evidence fixture')
        self.assertEqual(incremental_lock.read_bytes(), b'')

    def test_does_not_follow_symlink_or_treat_shared_inode_as_reclaimable(self):
        linked = self.worktree('symlinked')
        protected = self.cargo_cache(linked)
        symlink = protected.parent / 'libsymlink-1111111111111111.rlib'
        disposable = self.cargo_cache(self.worktree('hardlinked'))
        evidence = self.write(self.root, '.reports/runs/fixture/artifact.rlib')
        hardlink = disposable.parent / 'libhardlink-2222222222222222.rlib'
        symlink.symlink_to(evidence)
        os.link(evidence, hardlink)
        original_inode = evidence.stat().st_ino
        expected_release = disposable.stat().st_blocks * 512

        result = self.collect()

        self.assertTrue(symlink.is_symlink(), result)
        self.assertTrue(protected.is_file())
        self.assertEqual(result['allocated_bytes'] - result['estimated_remaining_bytes'], expected_release)
        self.assertEqual(evidence.stat().st_ino, original_inode)
        self.assertEqual(evidence.read_bytes(), b'evidence fixture')

    def test_unsupported_platform_reports_without_collecting(self):
        artifact = self.cargo_cache(self.worktree('linked'))
        with patch.object(storage.sys, 'platform', 'darwin'):
            result = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
        self.assertEqual(result['collected'], [])
        self.assertFalse(result['collection_available'])
        self.assertTrue(artifact.is_file())


if __name__ == '__main__':
    unittest.main()
