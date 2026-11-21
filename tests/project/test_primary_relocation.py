"""Exercise primary rename and rollback against disposable Git repositories."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/primary_relocation.py'


@unittest.skipUnless(sys.platform == 'linux', 'Primary relocation is a Linux host procedure')
class PrimaryRelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old = self.root / 'original' / 'repo'
        self.new = self.root / 'canonical' / 'new repo'
        self.linked = self.root / 'managed checkouts' / 'task'
        self.detached = self.root / 'legacy' / 'detached'
        self.stale = self.root / 'legacy' / 'stale'
        self.state = self.root / 'state.json'
        self.old.mkdir(parents=True)
        self.new.parent.mkdir()
        self.git(self.old, 'init', '--initial-branch=main')
        self.git(self.old, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.old, 'config', 'user.name', 'Fixture')
        (self.old / '.gitignore').write_text('.reports/\nignored.local\n')
        (self.old / 'input.txt').write_text('committed\n')
        self.git(self.old, 'add', '.')
        self.git(self.old, 'commit', '-m', 'fixture')
        self.git(self.old, 'worktree', 'add', '-b', 'task', str(self.linked))
        self.git(self.old, 'worktree', 'add', '--detach', str(self.detached))
        self.git(self.old, 'worktree', 'lock', '--reason', 'retain fixture', str(self.detached))
        self.git(self.old, 'worktree', 'add', '--detach', str(self.stale))
        self.stale.rename(self.stale.with_name('unregistered'))
        self.git(self.old, 'tag', 'saved-tip')
        (self.linked / 'input.txt').write_text('staged\n')
        self.git(self.linked, 'add', 'input.txt')
        (self.linked / 'input.txt').write_text('unstaged\n')
        (self.linked / 'new file').write_text('new\n')
        (self.old / 'ignored.local').write_text('ignored\n')
        (self.old / '.reports').mkdir()
        (self.old / '.reports/run.log').write_text('retained\n')
        (self.old / '.reports/retained-link').symlink_to('run.log')

    def run_command(self, command, *, check=True):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        result = subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True, timeout=20)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def git(self, root, *args):
        return self.run_command(['git', '-C', str(root), *args]).stdout.strip()

    def cli(self, *args, check=True):
        return self.run_command([sys.executable, '-E', '-S', '-B', str(SCRIPT), '--state', str(self.state),
                                 *map(str, args)], check=check)

    def capture(self):
        self.cli('capture', self.old, self.new)

    def assert_linked(self, primary):
        gitdir = self.linked / '.git'
        admin = Path(self.git(self.linked, 'rev-parse', '--absolute-git-dir'))
        common = Path(self.git(self.detached, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
        self.assertEqual(common, primary / '.git')
        self.assertEqual(admin.parent, common / 'worktrees')
        self.assertEqual(gitdir.read_text().strip(), f'gitdir: {admin}')
        self.assertEqual(Path((admin / 'gitdir').read_text().strip()), gitdir)
        self.assertEqual(self.git(primary, 'rev-parse', 'saved-tip'), self.git(primary, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.linked, 'diff', '--cached', '--numstat'), '1\t1\tinput.txt')
        self.assertEqual((self.linked / 'input.txt').read_text(), 'unstaged\n')
        self.assertEqual((self.linked / 'new file').read_text(), 'new\n')
        self.assertEqual((primary / '.reports/retained-link').read_text(), 'retained\n')
        self.assertEqual((primary / 'ignored.local').read_text(), 'ignored\n')

    def test_moves_with_old_path_hidden_then_rolls_back(self):
        self.capture()
        self.assertEqual(len(json.loads(self.state.read_bytes())['baseline']['worktrees']), 4)
        inode = self.old.stat().st_ino
        self.cli('move')
        self.assertFalse(os.path.lexists(self.old))
        self.assertEqual(self.new.stat().st_ino, inode)
        self.assert_linked(self.new)
        self.cli('verify')
        self.cli('rollback')
        self.assertFalse(self.new.exists())
        self.assertEqual(self.old.stat().st_ino, inode)
        self.assert_linked(self.old)
        self.cli('verify')

    def test_repairs_interrupted_rename_and_retires_the_compatible_handle(self):
        self.capture()
        self.old.rename(self.new)
        self.cli('move', '--keep-link')
        self.assertTrue(self.old.is_symlink())
        self.assert_linked(self.new)
        self.cli('retire-link')
        self.assertFalse(os.path.lexists(self.old))
        self.cli('rollback')
        self.assert_linked(self.old)

    def test_rejects_changed_ignored_evidence_and_occupied_destination(self):
        self.capture()
        log = self.old / '.reports/run.log'
        log.write_text('altered fixture\n')
        result = self.cli('move', check=False)
        self.assertIn('Worktree state differs', result.stderr)
        self.assertTrue(self.old.is_dir())
        log.write_text('retained\n')
        self.new.mkdir()
        result = self.cli('move', check=False)
        self.assertIn('Expected one physical checkout', result.stderr)
        self.assertTrue(self.old.is_dir())
        self.assertTrue(self.new.is_dir())
