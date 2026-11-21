"""Verify that diagnostic dependencies stay portable across checkout layouts."""

from contextlib import chdir, redirect_stdout
from io import StringIO
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import project
from scripts.project import (
    ProjectError, archive_evidence, json_bytes, load_project, read_json, review_history, task_definition_hash,
)
from scripts.worktree import WorktreeError
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
        receipt = archive_evidence(self.root, 'M0-001', report)
        with chdir(report.parent):
            self.assertEqual(receipt, archive_evidence(self.root, 'M0-001', Path(report.name)))
        local_copy = self.root / 'retained/test-report.json'
        local_copy.parent.mkdir()
        local_copy.write_bytes(report.read_bytes())
        self.assertEqual(receipt, archive_evidence(self.root, 'M0-001', local_copy))

    def test_record_package_script_honors_pnpm_invocation_directory(self):
        report = self.report()
        receipt = archive_evidence(self.root, 'M0-001', report)
        output = StringIO()
        with (
            chdir(self.root),
            patch.object(project, 'ROOT', self.root),
            patch('sys.argv', ['project.py', 'record', 'M0-001', '--report', report.name]),
            patch.dict(os.environ, {'npm_lifecycle_event': 'project:record', 'INIT_CWD': str(report.parent)}),
            redirect_stdout(output),
        ):
            project.main()
        self.assertEqual(receipt, json.loads(output.getvalue()))

    def test_exported_report_uses_verified_local_log_and_source_bindings(self):
        report = self.report()
        receipt = archive_evidence(self.root, 'M0-001', report)
        with tempfile.TemporaryDirectory() as other:
            external = Path(other) / 'test-report.json'
            external.write_bytes(report.read_bytes())
            self.assertEqual(receipt, archive_evidence(self.root, 'M0-001', external))
            log = self.root / read_json(report)['checks'][0]['log']
            log.write_bytes(b'Changed local verification output.\n')
            with self.assertRaisesRegex(ProjectError, 'log changed after the run'):
                archive_evidence(self.root, 'M0-001', external)
            (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
            with self.assertRaisesRegex(ProjectError, 'stale report'):
                archive_evidence(self.root, 'M0-001', external)

    def test_record_reports_unverifiable_checkout_metadata(self):
        report = self.report()
        with patch.object(project, 'has_git_metadata', side_effect=WorktreeError('Unresolved Git marker')):
            with self.assertRaisesRegex(ProjectError, 'Cannot verify the report checkout.*Unresolved Git marker'):
                archive_evidence(self.root, 'M0-001', report)

    def test_record_rejects_a_report_still_in_another_checkout(self):
        self.commit()
        report = self.report()
        with tempfile.TemporaryDirectory() as other:
            linked = Path(other) / 'linked'
            subprocess.run(
                ['git', 'worktree', 'add', '--detach', str(linked), 'HEAD'],
                cwd=self.root, check=True, capture_output=True,
            )
            external = linked / '.reports/test-report.json'
            external.parent.mkdir()
            external.write_bytes(report.read_bytes())
            with self.assertRaisesRegex(ProjectError, 'another checkout'):
                archive_evidence(self.root, 'M0-001', external)
            exported = Path(other) / 'test-report.json'
            exported.write_bytes(external.read_bytes())
            self.assertEqual(
                archive_evidence(self.root, 'M0-001', report),
                archive_evidence(self.root, 'M0-001', exported),
            )

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

    def test_history_checks_new_application_native_and_workflow_report_paths(self):
        paths = {
            'apps/desktop/replay.ts': "const replayPath = '{path}';\n",
            'apps/desktop/ui/index.html': '<script src="{path}"></script>\n',
            'crates/persistence/src/initial.sql': "ATTACH DATABASE '{path}' AS replay;\n",
            'packages/core/consumer.rs': 'const REPLAY_PATH: &str = "{path}";\n',
            '.github/workflows/windows.yml': "jobs:\n  replay:\n    steps:\n      - run: cat '{path}'\n",
        }
        inherited_paths = {
            'apps/desktop/inherited.ts': paths['apps/desktop/replay.ts'],
            'apps/desktop/ui/inherited.html': paths['apps/desktop/ui/index.html'],
            'crates/persistence/src/inherited.sql': paths['crates/persistence/src/initial.sql'],
        }
        for relative, template in inherited_paths.items():
            inherited = self.root / relative
            inherited.parent.mkdir(parents=True, exist_ok=True)
            inherited.write_text(template.format(path='../legacy/.reports/replay.json'), encoding='utf-8')
        base = self.commit()
        for relative, template in inherited_paths.items():
            with (self.root / relative).open('a', encoding='utf-8') as existing:
                existing.write(template.format(path='.reports/local.json'))
        for relative in ('apps/desktop/node_modules/dep/index.ts', 'packages/core/generated/replay.rs',
                         'python/fixtures/consumer.py'):
            fixture = self.root / relative
            fixture.parent.mkdir(parents=True)
            fixture.write_text("'../fixture/.reports/replay.json'\n", encoding='utf-8')
        self.assertEqual('passed', review_history(self.root, base)['status'])
        for relative, template in paths.items():
            with self.subTest(path=relative):
                source = self.root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(template.format(path='../sibling/.reports/replay.json'), encoding='utf-8')
                with self.assertRaisesRegex(ProjectError, f'{source.name}.*nonlocal report path'):
                    review_history(self.root, base)
                self.commit()
                with self.assertRaisesRegex(ProjectError, f'{source.name}.*nonlocal report path'):
                    review_history(self.root, base)
                source.write_text(template.format(path=r'..\\sibling\\.reports\\replay.json'), encoding='utf-8')
                with self.assertRaisesRegex(ProjectError, f'{source.name}.*nonlocal report path'):
                    review_history(self.root, base)
                source.write_text(template.format(path='.reports/replay.json'), encoding='utf-8')
                self.assertEqual('passed', review_history(self.root, base)['status'])

    def test_history_checks_changed_executable_catalog_commands(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        checks = {item['id']: item for item in catalog['checks']}
        checks['rust-lints']['command'] = ['{python}', '../legacy/.reports/replay.py']
        path.write_bytes(json_bytes(catalog))
        base = self.commit()

        # Reformatting the catalog does not change an inherited command.
        path.write_text(json.dumps(catalog), encoding='utf-8')
        self.assertEqual('passed', review_history(self.root, base)['status'])
        checks['project-state']['command'] = ['{python}', 'replay.py', '../sibling/.reports/replay.json']
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'project-state.*project/checks.json.*nonlocal report path'):
            review_history(self.root, base)
        self.commit()
        with self.assertRaisesRegex(ProjectError, 'project-state.*project/checks.json.*nonlocal report path'):
            review_history(self.root, base)
        checks['project-state']['command'][-1] = '.reports/replay.json'
        path.write_bytes(json_bytes(catalog))
        self.assertEqual('passed', review_history(self.root, base)['status'])
