import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'ubuntu-preview' / 'interrupt_owned_worker.py'
SPEC = importlib.util.spec_from_file_location('interrupt_owned_worker', SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class MarketProbeOwnerTest(unittest.TestCase):
    def setUp(self):
        self.launcher = SOURCE.parent / '.venv' / 'Scripts' / 'python.exe'
        self.node = Mock(pid=17)
        self.node.exe.return_value = str(self.launcher.parent / 'node.exe')
        self.helper = Mock()
        self.helper.cmdline.return_value = ['python', '-I', str(SOURCE), '17']
        self.wrapper = Mock()
        self.wrapper.exe.return_value = str(self.launcher)
        self.wrapper.cmdline.return_value = ['venv-python', *self.helper.cmdline()[1:]]
        self.wrapper.parent.return_value = self.node

    def test_direct_and_redirected_launch_reach_the_same_test_runner(self):
        for parent, redirector in [(self.node, None), (self.wrapper, self.launcher)]:
            with self.subTest(redirected=redirector is not None):
                self.helper.parent.return_value = parent
                self.assertIs(probe.owned_test_runner(self.helper, 17, redirector), self.node)

    def test_different_runner_or_unrelated_launcher_cannot_own_the_helper(self):
        self.helper.parent.return_value = self.wrapper
        for pid, launcher, args in [
            (18, self.launcher, self.wrapper.cmdline.return_value),
            (17, self.launcher.with_name('other-python.exe'), self.wrapper.cmdline.return_value),
            (17, self.launcher, ['python', '-I', 'another-test', '17']),
        ]:
            with self.subTest(pid=pid, launcher=launcher.name, script=args[2]):
                self.wrapper.cmdline.return_value = args
                with self.assertRaises(RuntimeError):
                    probe.owned_test_runner(self.helper, pid, launcher)
