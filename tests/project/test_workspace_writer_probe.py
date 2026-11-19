"""Regression tests for the browser fixture's owned workspace process probe."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    'workspace_test_writer',
    Path(__file__).resolve().parents[1] / 'ubuntu-preview' / 'workspace_test_writer.py',
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def process(pid, executable, parent=None, command=()):
    result = Mock(pid=pid)
    result.exe.return_value = str(executable)
    result.parent.return_value = parent
    result.cmdline.return_value = list(command)
    result.children.return_value = []
    return result


class WorkspaceWriterProbeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / 'synthetic'
        self.node = process(101, self.root / 'node.exe')
        self.redirector = self.root / '.venv' / 'Scripts' / 'python.exe'

    def test_accepts_direct_node_or_one_exact_virtualenv_redirector(self):
        wrapper = process(102, self.redirector, self.node)
        for parent in (self.node, wrapper):
            with self.subTest(parent=parent.pid):
                probe = process(103, self.root / 'python.exe', parent)
                self.assertIs(PROBE.owning_node(probe, self.node.pid, self.redirector), self.node)

    def test_rejects_wrong_pid_other_python_and_nested_redirectors(self):
        wrapper = process(102, self.redirector, self.node)
        wrong_wrapper = process(104, self.root / 'other' / 'python.exe', self.node)
        nested_wrapper = process(105, self.redirector, wrapper)
        for parent, pid in ((wrapper, 77), (wrong_wrapper, self.node.pid), (nested_wrapper, self.node.pid)):
            with self.subTest(parent=parent.pid, node_pid=pid):
                probe = process(103, self.root / 'python.exe', parent)
                with self.assertRaisesRegex(RuntimeError, 'must own the process probe'):
                    PROBE.owning_node(probe, pid, self.redirector)

    def test_writer_lookup_rechecks_vite_and_writer_parentage(self):
        host = process(106, self.root / 'node.exe', self.node, ['--mode', 'ubuntu-workspace'])
        executable = self.root / 'target' / 'debug' / 'workspace_bridge.exe'
        writer = process(107, executable, host)
        self.node.children.return_value = [host]
        host.children.return_value = [writer]
        self.assertIs(PROBE.owned_writer(self.node, host.pid, executable), writer)

        for child, expected_parent in ((host, self.node), (writer, host)):
            with self.subTest(detached=child.pid):
                child.parent.return_value = None
                with self.assertRaisesRegex(RuntimeError, 'no longer owns its workspace writer'):
                    PROBE.owned_writer(self.node, host.pid, executable)
                child.parent.return_value = expected_parent

        host.cmdline.return_value = ['--mode', 'ubuntu-preview']
        with self.assertRaisesRegex(RuntimeError, 'no longer owns its workspace service'):
            PROBE.owned_writer(self.node, host.pid, executable)
