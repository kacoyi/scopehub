"""Exercise the worktree CLI against disposable native Git repositories."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/worktree.py'
ROOT_CONFIG = 'scopehub.worktreeRoot'


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.home = self.directory / 'home'
        self.home.mkdir()
        self.primary = self.directory / 'projects' / 'scopehub'
        self.primary.mkdir(parents=True)
        self.managed = self.directory / 'managed checkouts'
        self.common = self.primary / '.git'
        self.creation_lock = self.common / 'scopehub-worktree-create.lock'
        self.env = {
            **{key: value for key, value in os.environ.items() if not key.startswith('GIT_')},
            'HOME': str(self.home),
            'USERPROFILE': str(self.home),
            'GIT_CONFIG_GLOBAL': str(self.home / 'gitconfig'),
            'GIT_CONFIG_NOSYSTEM': '1',
            'SCOPEHUB_STORAGE': 'off',
        }
        self.git('init', '--quiet', '--initial-branch=main')
        self.git('config', '--local', 'user.name', 'Worktree Fixture')
        self.git('config', '--local', 'user.email', 'fixture@example.invalid')
        self.git('config', '--local', ROOT_CONFIG, str(self.managed))
        self.commit(self.primary, 'initial')

    def run_command(self, args, cwd, *, check=True):
        result = subprocess.run(
            args, cwd=cwd, env=self.env, capture_output=True, encoding='utf-8', timeout=10,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def git(self, *args, cwd=None, check=True):
        return self.run_command(['git', *args], cwd or self.primary, check=check)

    def cli(self, *args, cwd=None, check=True):
        return self.run_command(
            [sys.executable, '-I', '-S', str(SCRIPT), *args], cwd or self.primary, check=check,
        )

    def commit(self, cwd, content):
        (cwd / 'fixture.txt').write_text(content, encoding='utf-8')
        self.git('add', 'fixture.txt', cwd=cwd)
        self.git('commit', '--quiet', '-m', content, cwd=cwd)
        return self.head(cwd)

    def head(self, cwd):
        return self.git('rev-parse', 'HEAD', cwd=cwd).stdout.strip()

    def branch(self, cwd):
        return self.git('symbolic-ref', '--short', 'HEAD', cwd=cwd).stdout.strip()

    def assert_rejected(self, *args):
        result = self.cli(*args, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stderr.strip())

    def wait_for_marker(self, marker, process):
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), f'Child process did not reach {marker.name}.')

    def retry_create_after_lock_release(self, *args, cwd=None):
        deadline = time.monotonic() + 10
        while True:
            result = self.cli(*args, cwd=cwd, check=False)
            if 'already running' not in result.stderr or time.monotonic() >= deadline:
                self.assertEqual(result.returncode, 0, result.stderr)
                return
            time.sleep(0.01)

    def test_create_and_reuse_preserve_primary_and_dirty_linked_checkout(self):
        primary_head = self.head(self.primary)
        # A non-Git marker in an ancestor does not make the managed root a checkout.
        (self.directory / '.git').mkdir()
        self.git('branch', 'codex/task', primary_head)
        self.cli('create', 'task', '--branch', 'codex/task')
        linked = self.managed / 'task'
        self.assertEqual(self.head(linked), primary_head)
        self.assertEqual(self.branch(linked), 'codex/task')
        self.commit(linked, 'task progress')
        (linked / 'fixture.txt').write_text('uncommitted tracked edit', encoding='utf-8')
        self.git('add', 'fixture.txt', cwd=linked)
        (linked / 'untracked.txt').write_text('untracked work', encoding='utf-8')
        before = self.git('status', '--porcelain', cwd=linked).stdout
        before_head = self.head(linked)
        self.cli('create', 'task', '--branch', 'codex/task', cwd=linked)

        self.assertEqual(Path(self.cli('use', 'task').stdout.strip()), linked)
        listing = self.cli('list').stdout
        self.assertIn('managed checkouts', listing)
        self.assertIn('codex/task', listing)
        self.assertEqual(self.head(linked), before_head)
        self.assertEqual(self.git('status', '--porcelain', cwd=linked).stdout, before)
        self.assertEqual((linked / 'fixture.txt').read_text(encoding='utf-8'), 'uncommitted tracked edit')
        self.assertEqual((linked / 'untracked.txt').read_text(encoding='utf-8'), 'untracked work')
        self.assertEqual(self.branch(self.primary), 'main')
        self.assertEqual(self.head(self.primary), primary_head)
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_simultaneous_create_cannot_check_out_one_branch_twice(self):
        self.git('branch', 'codex/task')
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(self.cli, 'create', name, '--branch', 'codex/task', check=False)
                        for name in ('first', 'second')]
            results = [request.result() for request in requests]

        self.assertEqual(sum(result.returncode == 0 for result in results), 1, results)
        registration = self.git('worktree', 'list', '--porcelain').stdout.splitlines()
        self.assertEqual(registration.count('branch refs/heads/codex/task'), 1)
        self.assertEqual(len(list(self.managed.iterdir())), 1)

    def test_abrupt_lock_holder_death_allows_create_on_same_lock_file(self):
        older = self.directory / 'older checkout'
        self.git('worktree', 'add', '--quiet', '--detach', str(older), 'HEAD')
        legacy_lock = self.common / 'scopehub-worktree.lock'
        legacy_lock.mkdir()
        self.creation_lock.write_bytes(b'preserved lock bytes')
        lock_before = self.creation_lock.stat()
        ready = self.directory / 'lock-ready'
        child_script = """\
import runpy
import sys
from pathlib import Path

creation_lock = runpy.run_path(sys.argv[1])['creation_lock']
with creation_lock(Path(sys.argv[2])):
    Path(sys.argv[3]).touch()
    sys.stdin.read(1)
"""
        args = [sys.executable, '-I', '-S', '-c', child_script, str(SCRIPT), str(self.common), str(ready)]
        create_args = ('create', 'task', '--new-branch', 'codex/task', '--base', 'HEAD')
        with subprocess.Popen(args, cwd=older, env=self.env, stdin=subprocess.PIPE) as holder:
            try:
                self.wait_for_marker(ready, holder)
                collision = self.cli(*create_args, cwd=older, check=False)
                self.assertNotEqual(collision.returncode, 0, collision.stdout)
                self.assertIn('retry', collision.stderr.lower())
                self.assertFalse(self.managed.exists())
                self.assertEqual(self.git('branch', '--list', 'codex/task').stdout, '')
            finally:
                if holder.poll() is None:
                    holder.kill()
                holder.wait(timeout=10)
            self.assertNotEqual(holder.returncode, 0)

        self.retry_create_after_lock_release(*create_args, cwd=older)
        self.assertEqual(self.branch(self.managed / 'task'), 'codex/task')
        self.assertTrue(os.path.samestat(lock_before, self.creation_lock.stat()))
        self.assertEqual(self.creation_lock.read_bytes(), b'preserved lock bytes')
        self.assertTrue(legacy_lock.is_dir())

    def test_native_add_retains_lock_while_running_after_cli_is_killed(self):
        self.assert_native_add_retains_lock(subprocess.Popen.kill)

    @unittest.skipUnless(os.name == 'posix', 'A targeted SIGINT is available on POSIX.')
    def test_native_add_retains_lock_after_targeted_cli_interrupt(self):
        self.assert_native_add_retains_lock(lambda process: process.send_signal(signal.SIGINT))

    def assert_native_add_retains_lock(self, interrupt):
        ready = self.directory / 'checkout-ready'
        resume = self.directory / 'checkout-resume'
        hook_dir = self.directory / 'hooks'
        hook_dir.mkdir()
        self.git('config', '--local', 'core.hooksPath', str(hook_dir))
        hook_script = """\
import sys
import time
from pathlib import Path

ready, resume = map(Path, sys.argv[1:])
ready.touch()
deadline = time.monotonic() + 20
while not resume.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
sys.exit(0 if resume.exists() else 1)
"""
        hook = hook_dir / 'post-checkout'
        command = shlex.join([sys.executable, '-I', '-S', '-c', hook_script, str(ready), str(resume)])
        hook.write_text(f'#!/bin/sh\nexec {command}\n', encoding='utf-8', newline='\n')
        hook.chmod(0o755)
        first = ('create', 'first', '--new-branch', 'codex/first', '--base', 'HEAD')
        second = ('create', 'second', '--new-branch', 'codex/second', '--base', 'HEAD')
        args = [sys.executable, '-I', '-S', str(SCRIPT), *first]
        with subprocess.Popen(args, cwd=self.primary, env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as creator:
            try:
                self.wait_for_marker(ready, creator)
                interrupt(creator)
                creator.wait(timeout=10)
                collision = self.cli(*second, check=False)
                self.assertNotEqual(collision.returncode, 0, collision.stdout)
                self.assertIn('already running', collision.stderr)
                self.assertFalse((self.managed / 'second').exists())
            finally:
                resume.touch()
                if creator.poll() is None:
                    creator.kill()
                    creator.wait(timeout=10)

        self.retry_create_after_lock_release(*second)
        self.cli(*first)
        self.assertEqual(self.branch(self.managed / 'first'), 'codex/first')
        self.assertEqual(self.branch(self.managed / 'second'), 'codex/second')

    def test_reject_symlink_at_creation_lock_without_touching_target(self):
        target = self.directory / 'unrelated file'
        target.write_bytes(b'keep this file')
        try:
            self.creation_lock.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'Fixture cannot create a file symlink: {error}')
        self.assert_rejected('create', 'task', '--new-branch', 'codex/task', '--base', 'HEAD')
        self.assertEqual(target.read_bytes(), b'keep this file')
        self.assertTrue(self.creation_lock.is_symlink())
        self.assertFalse(self.managed.exists())

    def test_reject_hardlink_at_creation_lock_without_touching_target(self):
        target = self.directory / 'unrelated file'
        target.write_bytes(b'keep this file')
        try:
            os.link(target, self.creation_lock)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'Fixture cannot create a file hardlink: {error}')
        self.assert_rejected('create', 'task', '--new-branch', 'codex/task', '--base', 'HEAD')
        self.assertEqual(target.read_bytes(), b'keep this file')
        self.assertTrue(os.path.samefile(target, self.creation_lock))
        self.assertFalse(self.managed.exists())

    def test_create_new_integration_branch_at_deliberate_base(self):
        base = self.head(self.primary)
        self.git('branch', 'reviewed-base', base)
        primary_head = self.commit(self.primary, 'unrelated primary progress')
        args = ('create', 'integration', '--new-branch', 'codex/integration')
        self.cli(*args, '--base', 'refs/heads/reviewed-base')
        linked = self.managed / 'integration'
        self.assertEqual(self.branch(linked), 'codex/integration')
        self.assertEqual(self.head(linked), base)
        linked_head = self.commit(linked, 'integration progress')
        self.cli(*args, '--base', base)
        self.assert_rejected(*args, '--base', primary_head)
        self.assertEqual(self.head(linked), linked_head)
        self.assertEqual(self.head(self.primary), primary_head)

    def test_interrupted_registration_reports_initialization_without_changing_checkout(self):
        create_args = ('create', 'task', '--new-branch', 'codex/task', '--base', 'HEAD')
        self.cli(*create_args)
        linked = self.managed / 'task'
        preserved = linked / 'untracked.txt'
        preserved.write_text('keep fixture work', encoding='utf-8')
        self.git('worktree', 'lock', '--reason', 'initializing', str(linked))

        for args in (('use', 'task'), create_args):
            with self.subTest(args=args):
                result = self.cli(*args, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('initializ', result.stderr.lower())
                self.assertEqual(self.branch(linked), 'codex/task')
                self.assertEqual(preserved.read_text(encoding='utf-8'), 'keep fixture work')

        self.git('worktree', 'unlock', str(linked))
        self.git('worktree', 'lock', '--reason', 'keep fixture registered', str(linked))
        self.cli(*create_args)
        self.assertEqual(Path(self.cli('use', 'task').stdout.strip()), linked)
        self.assertEqual(preserved.read_text(encoding='utf-8'), 'keep fixture work')

    def test_branch_only_from_interrupted_add_can_be_retried_as_existing(self):
        base = self.head(self.primary)
        self.git('branch', 'codex/task', base)
        primary_head = self.commit(self.primary, 'unrelated primary progress')
        create_args = ('create', 'task', '--new-branch', 'codex/task', '--base', 'HEAD')
        result = self.cli(*create_args, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--branch codex/task', result.stderr)
        self.assertFalse((self.managed / 'task').exists())

        self.cli('create', 'task', '--branch', 'codex/task')
        self.assertEqual(self.head(self.managed / 'task'), base)
        self.assertEqual(self.head(self.primary), primary_head)

    def test_reject_path_traversal_occupied_paths_and_branch_conflicts(self):
        self.git('branch', 'attention')
        self.git('branch', 'other')
        escaped = self.directory / 'escaped'
        for name in ('..', '../escaped', 'nested/task', 'nested\\task', 'com1.txt', 'task.', str(escaped)):
            with self.subTest(name=name):
                self.assert_rejected('create', name, '--branch', 'attention')
        self.assertFalse(escaped.exists())
        occupied = self.managed / 'occupied'
        occupied.mkdir(parents=True)
        self.assert_rejected('create', 'occupied', '--branch', 'attention')
        self.assertEqual(list(occupied.iterdir()), [])
        self.assert_rejected('use', 'occupied')

        self.cli('create', 'task', '--branch', 'attention')
        self.assert_rejected('create', 'task', '--branch', 'other')
        self.assert_rejected('create', 'duplicate', '--branch', 'attention')
        self.assert_rejected('create', 'primary', '--branch', 'main')
        self.assert_rejected('create', 'existing', '--new-branch', 'other', '--base', 'HEAD')
        self.assertEqual(self.branch(self.managed / 'task'), 'attention')
        self.assertEqual({path.name for path in self.managed.iterdir()}, {'occupied', 'task'})

    def test_reject_worktree_root_inside_checkout(self):
        unrelated = self.directory / 'unrelated checkout'
        unrelated.mkdir()
        self.git('init', '--quiet', cwd=unrelated)
        for parent in (self.primary, unrelated):
            with self.subTest(parent=parent):
                unsafe = parent / 'tools' / 'worktrees'
                self.git('config', '--local', ROOT_CONFIG, str(unsafe))
                self.assert_rejected('create', 'task', '--new-branch', 'task', '--base', 'HEAD')
                self.assertFalse(unsafe.exists())
        self.assertEqual(self.git('branch', '--list', 'task').stdout, '')

    def test_reject_relative_root_and_multiple_local_roots(self):
        self.git('config', '--local', ROOT_CONFIG, '../surprise')
        self.assert_rejected('create', 'task', '--new-branch', 'task', '--base', 'HEAD')
        self.git('config', '--local', ROOT_CONFIG, str(self.managed))
        other = self.directory / 'other root'
        self.git('config', '--local', '--add', ROOT_CONFIG, str(other))
        self.assert_rejected('create', 'task', '--new-branch', 'task', '--base', 'HEAD')
        self.assertFalse(self.managed.exists())
        self.assertFalse(other.exists())
        self.assertEqual(self.git('branch', '--list', 'task').stdout, '')

    def test_existing_branch_is_literal_and_attached_even_when_tag_is_ambiguous(self):
        base = self.head(self.primary)
        self.git('branch', 'topic', base)
        self.git('switch', '--quiet', 'topic')
        self.git('switch', '--quiet', 'main')
        self.commit(self.primary, 'revision for same-named tag')
        self.git('tag', 'topic')

        self.assert_rejected('create', 'expanded', '--branch', '@{-1}')
        self.cli('create', 'task', '--branch', 'topic')
        linked = self.managed / 'task'
        self.assertEqual(self.git('symbolic-ref', 'HEAD', cwd=linked).stdout.strip(), 'refs/heads/topic')
        self.assertEqual(self.head(linked), base)

    def test_reject_symlinked_checkout_name(self):
        self.managed.mkdir()
        external = self.directory / 'external'
        external.mkdir()
        try:
            (self.managed / 'redirect').symlink_to(external, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'Fixture cannot create a directory symlink: {error}')
        self.assert_rejected('create', 'redirect', '--new-branch', 'task', '--base', 'HEAD')
        self.assert_rejected('use', 'redirect')
        self.assertEqual(list(external.iterdir()), [])
        self.assertEqual(self.git('branch', '--list', 'task').stdout, '')

    def test_reject_an_unrelated_checkout_at_a_registered_path(self):
        self.cli('create', 'task', '--new-branch', 'task', '--base', 'HEAD')
        replaced = self.managed / 'task'
        (replaced / '.git').unlink()
        self.git('init', '--quiet', '--initial-branch=unrelated', cwd=replaced)
        (replaced / 'fixture.txt').write_text('unrelated work', encoding='utf-8')
        self.assert_rejected('use', 'task')
        self.assert_rejected('create', 'task', '--branch', 'task')
        self.assertEqual(self.branch(replaced), 'unrelated')
        self.assertEqual((replaced / 'fixture.txt').read_text(encoding='utf-8'), 'unrelated work')

    def test_reject_a_same_branch_gitdir_from_another_linked_checkout(self):
        first, second = self.managed / 'first', self.managed / 'second'
        self.git('worktree', 'add', '--quiet', '-b', 'task', str(first), 'HEAD')
        self.git('worktree', 'add', '--quiet', '--force', str(second), 'task')
        marker = first / '.git'
        original = marker.read_bytes()
        borrowed = (second / '.git').read_bytes()
        self.assertNotEqual(original, borrowed)
        marker.write_bytes(borrowed)

        self.assertEqual(self.branch(first), 'task')
        self.assertEqual(self.git('rev-parse', '--absolute-git-dir', cwd=first).stdout,
                         self.git('rev-parse', '--absolute-git-dir', cwd=second).stdout)
        self.assert_rejected('use', 'first')
        self.assert_rejected('create', 'first', '--branch', 'task')
        self.assertEqual(marker.read_bytes(), borrowed)
        self.assertEqual(Path(self.cli('use', 'second').stdout.strip()), second)

    def test_reject_gitdir_outside_native_worktree_registry_with_matching_backlink(self):
        linked = self.managed / 'task'
        self.git('worktree', 'add', '--quiet', '-b', 'task', str(linked), 'HEAD')
        git_dir = Path(self.git('rev-parse', '--absolute-git-dir', cwd=linked).stdout.strip())
        copied_dir = shutil.copytree(git_dir, self.directory / 'copied admin directory')
        (copied_dir / 'commondir').write_bytes(os.fsencode(self.common.as_posix()) + b'\n')
        marker = linked / '.git'
        marker.write_bytes(b'gitdir: ' + os.fsencode(copied_dir.as_posix()) + b'\n')

        self.assertEqual(Path(os.fsdecode((copied_dir / 'gitdir').read_bytes()).strip()), marker)
        self.assertEqual(self.branch(linked), 'task')
        self.assertEqual(Path(self.git('rev-parse', '--absolute-git-dir', cwd=linked).stdout.strip()), copied_dir)
        self.assert_rejected('use', 'task')
        self.assert_rejected('create', 'task', '--branch', 'task')

    def test_new_checkout_reports_branch_changed_by_native_hook(self):
        hook_dir = self.directory / 'hooks'
        hook_dir.mkdir()
        self.git('config', '--local', 'core.hooksPath', str(hook_dir))
        self.git('branch', 'hook-branch')
        hook = hook_dir / 'post-checkout'
        hook.write_text('#!/bin/sh\nexec git symbolic-ref HEAD refs/heads/hook-branch\n',
                        encoding='utf-8', newline='\n')
        hook.chmod(0o755)

        self.assert_rejected('create', 'task', '--new-branch', 'task', '--base', 'HEAD')
        linked = self.managed / 'task'
        self.assertEqual(self.branch(linked), 'hook-branch')
        self.assertEqual(self.head(linked), self.head(self.primary))
        self.assertEqual(self.branch(self.primary), 'main')

    def test_use_accepts_a_detached_worktree_with_relative_git_paths(self):
        linked = self.managed / 'detached'
        result = self.git('worktree', 'add', '--quiet', '--relative-paths', '--detach', str(linked), 'HEAD',
                          check=False)
        if 'unknown option' in result.stderr:
            self.skipTest('Git does not support relative worktree paths.')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('symbolic-ref', '--quiet', 'HEAD', cwd=linked, check=False).returncode, 1)
        self.assertEqual(Path(self.cli('use', 'detached').stdout.strip()), linked)

    def test_pinned_git_alias_works_from_an_older_checkout_with_home_default(self):
        older = self.directory / 'older checkout'
        old_head = self.head(self.primary)
        self.git('worktree', 'add', '--quiet', '-b', 'older', str(older), old_head)
        primary_head = self.commit(self.primary, 'unrelated primary progress')
        self.git('config', '--local', '--unset', ROOT_CONFIG)
        self.git('config', '--global', ROOT_CONFIG, str(self.directory / 'global checkouts'))
        pinned = self.directory / 'pinned tools' / 'worktree.py'
        pinned.parent.mkdir()
        shutil.copyfile(SCRIPT, pinned)
        command = shlex.join([sys.executable, '-I', '-S', str(pinned)])
        self.git('config', '--local', 'alias.scopehub-worktree', f'!{command}')
        self.assertFalse((older / 'scripts' / 'worktree.py').exists())
        self.git('scopehub-worktree', 'create', 'future', '--new-branch', 'codex/future',
                 '--base', 'HEAD', cwd=older)
        selected = self.git('scopehub-worktree', 'use', 'future', cwd=older).stdout.strip()
        linked = self.home / 'worktrees' / 'scopehub' / 'future'
        self.assertEqual(Path(selected), linked)
        self.assertEqual(self.branch(linked), 'codex/future')
        self.assertEqual(self.head(linked), old_head)
        self.assertEqual(self.branch(older), 'older')
        self.assertEqual(self.branch(self.primary), 'main')
        self.assertEqual(self.head(self.primary), primary_head)
