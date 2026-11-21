"""Exercise an interrupted storage sweep against a real, offline Cargo build."""

import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import unittest
from unittest.mock import patch

from scripts import storage
from tests.project.test_storage import RepositoryFixture


@unittest.skipUnless(sys.platform == 'linux', 'The collector uses Linux filesystem locks.')
class CargoStorageTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.require_write_lease()
        self.cargo = self.tool('cargo')
        self.cargo_env = {
            **{name: value for name, value in os.environ.items()
               if not name.startswith('CARGO_') and name not in {
                   'RUSTFLAGS', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
               }},
            'RUSTC': self.tool('rustc'),
            'CARGO_HOME': str(self.directory / 'cargo-home'),
            'CARGO_INCREMENTAL': '1',
        }

    def tool(self, name):
        rustup = shutil.which('rustup')
        if rustup is None:
            self.skipTest('Rustup is unavailable.')
        project = Path(__file__).resolve().parents[2]
        pin = tomllib.loads((project / 'rust-toolchain.toml').read_text(encoding='utf-8'))['toolchain']['channel']
        tool = subprocess.run([rustup, 'which', '--toolchain', pin, name], cwd=self.directory,
                              env={**os.environ, 'RUSTUP_AUTO_INSTALL': '0'},
                              capture_output=True, text=True, timeout=10)
        if tool.returncode:
            self.skipTest(f'The pinned {pin} toolchain has no installed {name}.')
        return tool.stdout.strip()

    def run_cargo(self, root, command, *options):
        result = subprocess.run([self.cargo, command, '--offline', *options], cwd=root,
                                env={**self.cargo_env, 'CARGO_TARGET_DIR': str(root / 'target')},
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def crate_worktree(self, library, main=None):
        self.write(self.root, 'Cargo.toml',
                   b'[package]\nname = "scopehub-fixture"\nversion = "0.0.0"\nedition = "2021"\n')
        self.write(self.root, 'src/lib.rs', library)
        if main is not None:
            self.write(self.root, 'src/main.rs', main)
        self.git(self.root, 'add', 'Cargo.toml', 'src')
        self.git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--quiet', '-m', 'Add an offline Cargo fixture')
        return self.worktree('cargo linked checkout')

    def require_write_lease(self):
        import fcntl

        if not hasattr(fcntl, 'F_SETLEASE'):
            self.skipTest('Kernel file leases are unavailable.')
        probe = self.write(self.directory, 'lease-probe', b'isolated fixture')
        with probe.open('rb') as file:
            try:
                fcntl.fcntl(file, fcntl.F_SETLEASE, fcntl.F_WRLCK)
            except OSError as error:
                self.skipTest(f'Fixture filesystem cannot take a write lease: {error}')

    def run_binary(self, path, expected):
        result = subprocess.run([str(path)], cwd=path.parents[2], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected + '\n', result.stdout)

    @staticmethod
    def file_state(paths):
        observed = {}
        for path in paths:
            with path.open('rb') as file:
                observed[path] = (os.fstat(file.fileno()).st_ino, hashlib.file_digest(file, 'sha256').hexdigest())
        return observed

    @staticmethod
    def sessions(debug):
        return {path for path in (debug / 'incremental').glob('*/s-*') if path.is_dir()}

    def test_interrupted_conservative_sweep_retries_and_cargo_rebuilds(self):
        linked = self.crate_worktree(
            b'pub fn message() -> &\'static str { "initial fixture" }\n',
            b'fn main() { println!("{}", scopehub_fixture::message()); }\n',
        )
        for root in (self.root, linked):
            self.run_cargo(root, 'build')

        debug = linked / 'target/debug'
        binary = debug / 'scopehub-fixture'
        current_debug = self.root / 'target/debug'
        current_sessions = self.sessions(current_debug)
        initial_sessions = self.sessions(debug)
        self.assertGreaterEqual(len(initial_sessions), 2)
        deps = list((debug / 'deps').iterdir())
        cold, young = sorted(path for path in deps if path.suffix == '.d')
        hardlinked, = (path for path in deps if path.suffix == '.rlib')
        self.assertEqual(1, cold.stat().st_nlink)
        self.assertEqual(1, young.stat().st_nlink)
        old = time.time() - 48 * 60 * 60
        for path in (cold, hardlinked):
            os.utime(path, (old, old))
        evidence = self.write(linked, '.reports/runs/fixture/result.json', b'{"status":"fixture-passed"}\n')
        evidence_library = evidence.with_name('archived-library.rlib')
        os.link(hardlinked, evidence_library)
        retained = [binary, current_debug / binary.name, evidence, evidence_library,
                    *(path for path in deps if path != cold), *debug.glob('.cargo*-lock')]
        original = self.file_state(retained)

        context = storage.LinuxContext({Path('/'): 'ext4'}, set(), inaccessible_processes=1)
        remove_group = storage.remove_group

        def interrupt_after_group(parent_fd, name, pattern, progress, mount_id, deadline, *, completed_only=False):
            before = progress['files']
            remove_group(parent_fd, name, pattern, progress, mount_id, deadline, completed_only=completed_only)
            if progress['files'] > before:
                raise storage.TimeLimit('fixture interrupted after collecting one group')

        with patch.object(storage, 'linux_context', return_value=context):
            with cold.open('rb'):
                with patch.object(storage, 'remove_group', side_effect=interrupt_after_group):
                    interrupted = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
                self.assertIn('fixture interrupted', interrupted['collection_blocked'])
                self.assertEqual(1, len(initial_sessions - self.sessions(debug)), interrupted)
                self.assertEqual(original, self.file_state(retained))
                self.run_binary(binary, 'initial fixture')

                reader_open = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
                self.assertFalse(self.sessions(debug), reader_open)
                self.assertTrue(cold.is_file(), reader_open)

            retried = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
            self.assertTrue(retried['collected'], retried)
            self.assertFalse(cold.exists(), retried)
            self.assertEqual(current_sessions, self.sessions(current_debug))
            self.assertEqual(original, self.file_state(retained))
            self.run_binary(binary, 'initial fixture')

        self.write(linked, 'src/lib.rs', b'pub fn message() -> &\'static str { "rebuilt fixture" }\n')
        self.run_cargo(linked, 'build')
        self.run_binary(binary, 'rebuilt fixture')
        self.assertGreaterEqual(len(self.sessions(debug)), 2)
        self.assertTrue(cold.is_file())
        archived = [evidence, evidence_library]
        self.assertEqual({path: original[path] for path in archived}, self.file_state(archived))

    def test_interruption_inside_incremental_session_preserves_binary_and_allows_rebuild(self):
        linked = self.crate_worktree(
            b'pub fn message() -> &\'static str { "initial fixture" }\n',
            b'fn main() { println!("{}", scopehub_fixture::message()); }\n',
        )
        self.run_cargo(linked, 'build')
        debug = linked / 'target/debug'
        binary = debug / 'scopehub-fixture'
        evidence = self.write(linked, '.reports/runs/fixture/result.json')
        unrelated = self.write(debug, 'incremental/manual/notes.txt')
        retained = [binary, evidence, unrelated, *(debug / 'incremental').glob('*/*.lock')]
        original = self.file_state(retained)
        context = storage.LinuxContext({Path('/'): 'ext4'}, set(), inaccessible_processes=1)
        unlink = os.unlink
        removed_marker = None

        def interrupt_during_unlink(name, *, dir_fd):
            nonlocal removed_marker
            if removed_marker is not None:
                raise InterruptedError(errno.EINTR, 'fixture interrupted inside a session')
            unlink(name, dir_fd=dir_fd)
            if name in {'dep-graph.bin', 'query-cache.bin', 'work-products.bin'}:
                removed_marker = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name

        with patch.object(storage, 'linux_context', return_value=context):
            with patch.object(storage.os, 'unlink', side_effect=interrupt_during_unlink):
                interrupted = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
            row = self.report_entry(interrupted, linked / 'target')
            self.assertEqual('collection_interrupted', row['reason'], interrupted)
            self.assertIn('fixture interrupted inside a session', row['detail'])
            self.assertIsNotNone(removed_marker)
            self.assertFalse(removed_marker.exists())
            remainder = list(removed_marker.parent.iterdir())
            self.assertTrue(remainder)
            torn_session = self.file_state(remainder)
            self.assertTrue(interrupted['collected'])
            self.assertEqual(original, self.file_state(retained))
            self.run_binary(binary, 'initial fixture')

            storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
            self.assertEqual(set(remainder), set(removed_marker.parent.iterdir()))
            self.assertEqual(torn_session, self.file_state(remainder))

        self.write(linked, 'src/lib.rs', b'pub fn message() -> &\'static str { "rebuilt fixture" }\n')
        self.run_cargo(linked, 'build')
        self.run_binary(binary, 'rebuilt fixture')
        self.assertEqual({path: original[path] for path in (evidence, unrelated)},
                         self.file_state([evidence, unrelated]))

    def test_real_cold_test_harness_waits_for_exit_and_closed_readers(self):
        linked = self.crate_worktree(b'''\
#[cfg(test)]
mod tests {
    #[test]
    fn smoke() { assert_eq!("scopehub-fixture", env!("CARGO_PKG_NAME")); }

    #[test]
    #[ignore]
    fn hold_executable() {
        use std::{env, fs, path::PathBuf, thread, time::{Duration, Instant}};
        let ready = PathBuf::from(env::var_os("SCOPEHUB_TEST_READY").unwrap());
        let release = PathBuf::from(env::var_os("SCOPEHUB_TEST_RELEASE").unwrap());
        let stage = ready.with_extension("tmp");
        fs::write(&stage, b"running").unwrap();
        fs::rename(stage, ready).unwrap();
        let start = Instant::now();
        while !release.exists() {
            assert!(start.elapsed() < Duration::from_secs(15));
            thread::sleep(Duration::from_millis(10));
        }
    }
}
''')
        build = self.run_cargo(linked, 'test', '--lib', '--no-run', '--message-format=json')
        artifacts = [json.loads(line) for line in build.stdout.splitlines()]
        harness, = (Path(record['executable']) for record in artifacts if record.get('executable'))
        self.assertEqual(linked / 'target/debug/deps', harness.parent)
        with harness.open('rb') as file:
            self.assertEqual(b'\x7fELF', file.read(4))
        self.assertEqual(1, harness.stat().st_nlink)
        old = time.time() - 48 * 60 * 60
        os.utime(harness, (old, old))
        lookalike = harness.with_name('scopehub_fixture-0123456789abcdef')
        shutil.copy2(harness, lookalike)
        lookalike_dep = self.write(lookalike.parent, lookalike.name + '.d', f'{lookalike}: src/lib.rs\n'.encode())
        retained = [lookalike, lookalike_dep]
        preserved = self.file_state(retained)

        ready = self.directory / 'test-executable-ready'
        release = self.directory / 'test-executable-release'
        env = {**os.environ, 'SCOPEHUB_TEST_READY': str(ready), 'SCOPEHUB_TEST_RELEASE': str(release)}
        context = storage.LinuxContext({Path('/'): 'ext4'}, set(), inaccessible_processes=1)
        with patch.object(storage, 'linux_context', return_value=context):
            preview = storage.run(self.root, high_bytes=1, low_bytes=0)
            self.assertEqual(1, self.report_entry(preview, linked / 'target')['cold_test_harnesses'])
            self.assertTrue(harness.is_file(), preview)
            with subprocess.Popen([str(harness), '--ignored', '--exact', 'tests::hold_executable'],
                                  cwd=linked, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) as process:
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(b'running', ready.read_bytes())
                    self.assertIsNone(process.poll())
                    with self.assertRaises(OSError) as busy:
                        os.close(os.open(harness, os.O_WRONLY))
                    self.assertEqual(errno.ETXTBSY, busy.exception.errno)
                    live = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
                    self.assertTrue(harness.is_file(), live)
                    self.assertIsNone(process.poll())
                finally:
                    release.touch()
                    output, error = process.communicate(timeout=15)
                self.assertEqual(0, process.returncode, error)
                self.assertIn('1 passed', output)

            with harness.open('rb'):
                reader_open = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
                self.assertTrue(harness.is_file(), reader_open)
            idle = storage.run(self.root, collect=True, high_bytes=1, low_bytes=0)
            self.assertFalse(harness.exists(), idle)
            self.assertEqual(1, sum(item.get('leased_test_harnesses', 0) for item in idle['collected']), idle)
            self.assertEqual(preserved, self.file_state(retained))

        rebuilt = self.run_cargo(linked, 'test', '--lib')
        self.assertIn('1 passed; 0 failed; 1 ignored', rebuilt.stdout)
        self.assertTrue(harness.is_file())
