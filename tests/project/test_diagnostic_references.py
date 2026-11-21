"""Verify that diagnostic dependencies stay portable across checkout layouts."""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.project import (
    ProjectError, archive_evidence, json_bytes, load_project, review_history, task_definition_hash,
)
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


LOCATOR = {
    'run_id': 'fixture-run',
    'source_commit': 'a' * 40,
    'report_sha256': 'b' * 64,
}


class DiagnosticReferenceTests(ProjectFixture):
    def change_task(self, task_id='M0-001', **fields):
        ledger = self.ledger()
        task_by_id(ledger, task_id).update(fields)
        self.save_ledger(ledger)

    def commit(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(
            ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
             'commit', '-m', 'Record fixture'],
            cwd=self.root, check=True, capture_output=True,
        )
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()

    def test_structured_locator_and_matching_logical_hint_load_offline(self):
        before = task_definition_hash(load_project(self.root)['tasks']['M0-001'])
        self.change_task(
            diagnostic_reports=[LOCATOR],
            next_action='Read archives/runs/fixture-run/test-report.json under the development storage root.',
        )
        with patch.dict(os.environ, {'SCOPEHUB_ARCHIVE_ROOT': str(self.root / 'unmounted')}):
            task = load_project(self.root)['tasks']['M0-001']
        self.assertEqual([LOCATOR], task['diagnostic_reports'])
        self.assertNotEqual(before, task_definition_hash(task))

    def test_external_paths_and_unbound_logical_paths_are_rejected(self):
        for path in (
            '../sibling/.reports/runs/fixture/test-report.json',
            'sibling/.reports/fixture.log',
            '/mnt/storage/archives/runs/fixture-run/test-report.json',
            r'C:\Development Tree\.reports\fixture.log',
            r'C:\Development Tree\.REPORTS\fixture.log',
            r'..\sibling\.reports\test-report.json',
            'file:///tmp/checkout/.reports/test-report.json',
            'archives/runs/unrelated/test-report.json',
            '../archives/runs/fixture-run/test-report.json',
        ):
            with self.subTest(path=path):
                self.change_task(
                    diagnostic_reports=[LOCATOR],
                    next_action=f'Run a review with --report="{path}".',
                )
                with self.assertRaisesRegex(ProjectError, 'nonlocal report path'):
                    load_project(self.root)

    def test_feedback_paths_use_the_same_locator_rules(self):
        feedback = {
            'id': 'fixture-feedback', 'source': 'archives/runs/fixture-run/test-report.json',
            'observation': 'The selected check was blocked.', 'severity': 'minor',
            'status': 'open', 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
            'resolution': None, 'deferral_reason': None, 'diagnostic_reports': [LOCATOR],
        }
        path = self.root / 'project/feedback.json'
        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': [feedback]}))
        self.assertEqual([LOCATOR], load_project(self.root)['feedback']['items'][0]['diagnostic_reports'])
        feedback['source'] = 'archives/runs/other-run/test-report.json'
        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': [feedback]}))
        with self.assertRaisesRegex(ProjectError, 'fixture-feedback.*nonlocal report path'):
            load_project(self.root)

    def test_local_report_paths_and_web_links_remain_readable(self):
        self.change_task(
            next_action='Read ./.reports/test-report.json, ./retained/test-report.json or https://example.test/diagnostic.json.',
        )
        with (self.root / '.gitignore').open('a', encoding='utf-8') as ignored:
            ignored.write('retained/\n')
        report = self.report()
        self.assertIn('path', archive_evidence(self.root, 'M0-001', report))
        self.assertIn('path', archive_evidence(self.root, 'M0-001', Path('.reports/test-report.json')))
        local_copy = self.root / 'retained/test-report.json'
        local_copy.parent.mkdir()
        local_copy.write_bytes(report.read_bytes())
        self.assertIn('path', archive_evidence(self.root, 'M0-001', local_copy))
        with tempfile.TemporaryDirectory() as other:
            external = Path(other) / 'test-report.json'
            external.write_bytes(report.read_bytes())
            with self.assertRaisesRegex(ProjectError, 'checkout-local'):
                archive_evidence(self.root, 'M0-001', external)

    def test_history_preserves_legacy_paths_and_rejects_a_new_closed_dependency(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        self.change_task(closure_reason='Original note about ../legacy/.reports/test-report.json.')
        base = self.commit()
        self.assertEqual('passed', review_history(self.root, base)['status'])
        self.change_task(
            'M0-003', status='cancelled', next_action=None,
            closure_reason='Read ../sibling/.reports/test-report.json.',
        )
        with self.assertRaisesRegex(ProjectError, 'M0-003.*nonlocal report path'):
            review_history(self.root, base)

    def test_history_checks_added_operational_literals_and_leaves_fixtures_alone(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        inherited = scripts / 'inherited.py'
        inherited.write_text("read('../legacy/.reports/test-report.json')\n", encoding='utf-8')
        base = self.commit()
        fixtures = self.root / 'tests'
        fixtures.mkdir()
        (fixtures / 'fixture.py').write_bytes(inherited.read_bytes())
        self.assertEqual('passed', review_history(self.root, base)['status'])
        added = scripts / 'new.py'
        added.write_text("read('../sibling/.reports/test-report.json')\n", encoding='utf-8')
        with self.assertRaisesRegex(ProjectError, 'new.py.*nonlocal report path'):
            review_history(self.root, base)
        self.commit()
        with self.assertRaisesRegex(ProjectError, 'new.py.*nonlocal report path'):
            review_history(self.root, base)
        added.write_text("read('.reports/test-report.json')\n", encoding='utf-8')
        self.assertEqual('passed', review_history(self.root, base)['status'])
