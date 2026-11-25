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


class MarketWorkerWitnessTest(unittest.TestCase):
    def setUp(self):
        self.root = SOURCE.parents[2]
        self.executable = self.root / 'target' / 'debug' / 'examples' / 'history_bridge.exe'
        self.interpreter = self.root / 'python.exe'
        self.redirector = self.root / '.venv' / 'Scripts' / 'python.exe'
        self.script = SOURCE.with_name('market_interruption_worker.py')
        self.witness = '75b47087-ebfb-4c39-8ee0-e0169397fd37'
        self.owner = Mock(pid=17)
        self.bridge = self.process(self.executable, ['--test-market-interruption', self.witness])
        self.bridge.parents.return_value = [self.owner]
        self.owner.children.return_value = [self.bridge]
        self.worker = self.process(self.interpreter, [str(self.script), self.witness])
        self.worker.parents.return_value = [self.bridge, self.owner]
        self.bridge.children.return_value = [self.worker]

    def process(self, executable, args):
        process = Mock()
        process.name.return_value = executable.name
        process.exe.return_value = str(executable)
        process.cwd.return_value = str(self.root)
        process.cmdline.return_value = [str(executable), *args]
        process.children.return_value = []
        return process

    def select(self, redirector=None):
        return probe.owned_worker(self.owner, self.root, self.executable, self.interpreter, redirector, self.witness)

    def test_selects_only_the_admitted_original_direct_or_redirected(self):
        self.assertIs(self.select(), self.worker)
        wrapper = self.process(self.redirector, [str(self.script), self.witness])
        self.bridge.children.return_value = [wrapper]
        wrapper.children.return_value = [self.worker]
        self.worker.parents.return_value = [wrapper, self.bridge, self.owner]
        self.assertIs(self.select(self.redirector), self.worker)
        wrapper.children.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'found 0'):
            self.select(self.redirector)

    def test_ordinary_replacement_and_decoys_do_not_supply_the_original(self):
        replacement = self.process(self.interpreter, ['-m', 'worker_runtime', '--role', 'market', '--demo'])
        wrong_witness = self.process(self.interpreter, [str(self.script), 'other'])
        wrong_executable = self.process(self.interpreter.with_name('decoy.exe'), [str(self.script), self.witness])
        bad_bridge = self.process(self.executable.with_suffix('.bak'), ['--test-market-interruption', self.witness])
        bad_bridge.children.return_value = [self.worker]
        self.bridge.children.return_value = [replacement, wrong_witness, wrong_executable]
        self.owner.children.return_value = [self.bridge, bad_bridge, self.worker]
        with self.assertRaisesRegex(RuntimeError, 'found 0'):
            self.select()

    def test_ambiguous_and_changed_ancestry_are_rejected(self):
        second = self.process(self.interpreter, [str(self.script), self.witness])
        self.bridge.children.return_value = [self.worker, second]
        with self.assertRaisesRegex(RuntimeError, 'found 2'):
            self.select()
        self.bridge.children.return_value = [self.worker]
        self.worker.parents.return_value = [self.owner]
        with self.assertRaisesRegex(RuntimeError, 'no longer owns'):
            self.select()
