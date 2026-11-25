"""Verify that diagnostic dependencies stay portable across checkout layouts."""

from contextlib import chdir, redirect_stdout
from io import StringIO
import json
import os
import shlex
import shutil
import subprocess
import sys
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

    def project_command(self, *args, exit_code=0):
        output = StringIO()
        with (patch.object(project, 'ROOT', self.root), patch('sys.argv', ['project.py', *args]),
              redirect_stdout(output)):
            if exit_code:
                with self.assertRaises(SystemExit) as raised:
                    project.main()
                self.assertEqual(exit_code, raised.exception.code)
            else:
                project.main()
        return json.loads(output.getvalue())

    def follow_review_command(self, command, *, replacements=None, exit_code=0):
        arguments = shlex.split(command)
        python = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
        self.assertEqual([python, '-B', 'scripts/project.py'], arguments[:3], command)
        substitutions = replacements or {}
        return self.project_command(*(substitutions.get(argument, argument) for argument in arguments[3:]),
                                    exit_code=exit_code)

    def test_bound_context_and_reopen_use_one_immutable_project_tree(self):
        spec_path = self.root / 'engineering-contract-v1.json'
        contract_path = self.root / 'contracts/contract.json'
        spec = read_json(spec_path)
        spec['product'] = {'purpose': {'$source': {'path': 'contracts/contract.json', 'pointer': '/purpose'}}}
        spec_path.write_bytes(json_bytes(spec))
        contract_path.write_bytes(json_bytes({'purpose': 'Read the exact committed purpose.'}))
        # The link expander resolves a referenced file before reading it.
        contract_read_path = contract_path.resolve(strict=True)
        path = self.root / 'project/feedback.json'
        first = {
            'id': 'fixture-feedback', 'source': 'Exact reviewed fixture Git source.',
            'observation': 'The earlier verified observation.', 'severity': 'minor',
            'status': 'open', 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
            'resolution': None, 'deferral_reason': None,
        }
        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': [first]}))
        reviewed = self.commit()
        second = {**first, 'observation': 'Changed worktree observation.'}
        current_ledger = self.ledger()
        earlier_action = task_by_id(current_ledger, 'M0-001')['next_action']
        task_by_id(current_ledger, 'M0-001')['next_action'] = 'Observe the changed worktree action.'
        read_text = Path.read_text

        def transient_read(current, *args, **kwargs):
            if current == path:
                return json_bytes({'schema_version': '1.0', 'items': [second]}).decode('utf-8')
            if current == contract_read_path:
                return '{"purpose": "Stale working purpose."}'
            if current == self.root / 'project/tasks.json':
                return json_bytes(current_ledger).decode('utf-8')
            return read_text(current, *args, **kwargs)

        with patch.object(Path, 'read_text', transient_read):
            args = ('context', 'M0-001', '--phase', 'verify')
            working = self.project_command(*args)
            self.assertEqual([second], working['feedback'])
            self.assertEqual('Observe the changed worktree action.', working['task']['next_action'])
            self.assertEqual('Stale working purpose.', working['product']['purpose'])
            self.assertEqual('pnpm project:source <path> <json-pointer>', working['source_command'])
            self.assertIn('pnpm project:context M0-001 --detail', working['commands'])
            self.assertIn('pnpm project:context M0-001 --phase verify', working['commands'])
            for options in ((), ('--detail',)):
                bound = self.project_command(*args, '--source', reviewed, *options)
                self.assertEqual([first], bound['feedback'])
                self.assertEqual(reviewed, bound['review_source'])
                for detail in (False, True):
                    with self.subTest(emitting_detail=bool(options), following_detail=detail):
                        links = [command for command in bound['commands']
                                 if 'context M0-001 ' in command and ('--detail' in command) == detail]
                        self.assertEqual(1, len(links))
                        followed = self.follow_review_command(links[0])
                        self.assertEqual(reviewed, followed['review_source'])
                        self.assertEqual([first], followed['feedback'])
                        self.assertEqual(earlier_action, followed['task']['next_action'])
                        self.assertEqual(detail, 'development' in followed)
                with self.subTest(emitting_detail=bool(options), following='generic source'):
                    self.assertIn('source_command', bound)
                    template = bound['source_command']
                    self.assertIn('"<path>" "<json-pointer>"', template)
                    section = self.follow_review_command(template, replacements={
                        '<path>': 'engineering-contract-v1.json', '<json-pointer>': '/product/purpose',
                    })
                    self.assertEqual(reviewed, section['source_commit'])
                    self.assertEqual('Read the exact committed purpose.', section['value'])
                route = bound.get('causal_investigation', {}).get('original_records', [])
                with self.subTest(emitting_detail=bool(options), following='feedback'):
                    self.assertEqual(1, len(route))
                    full_record = self.follow_review_command(route[0]['command'])
                    self.assertEqual(reviewed, full_record['source_commit'])
                    self.assertEqual(first, full_record['value'])
                with self.subTest(emitting_detail=bool(options), checking='command scope'):
                    self.assertEqual(2, len(bound['commands']))
                    self.assertTrue(all('context M0-001 ' in command for command in bound['commands']))
                    self.assertIn('pnpm project:impact', bound['current_checkout_commands'])
                if not options:
                    focused = bound
                    with self.subTest(following='product source'):
                        self.assertEqual('Read the exact committed purpose.', bound['product']['purpose'])
                        self.assertNotIn('brief_command', bound['product'])
                        product = self.follow_review_command(bound['product']['source_command'])
                        self.assertEqual(reviewed, product['source_commit'])
                        self.assertEqual({'purpose': 'Read the exact committed purpose.'}, product['value'])

        path.write_bytes(json_bytes({'schema_version': '1.0', 'items': [second]}))
        repeat, = (command for command in focused['commands'] if '--detail' not in command)
        dirty = self.follow_review_command(repeat, exit_code=1)
        self.assertIn('requires a clean checkout', dirty['error'])
        self.commit()
        moved = self.follow_review_command(repeat, exit_code=1)
        self.assertIn('does not match checkout HEAD', moved['error'])
        with self.subTest(following='feedback after worktree changes'):
            command = focused['causal_investigation']['original_records'][0]['command']
            reopened = self.follow_review_command(command)
            self.assertEqual(reviewed, reopened['source_commit'])
            self.assertEqual(first, reopened['value'])
        current = self.project_command('source', 'project/feedback.json', '/items/0')
        self.assertEqual(second, current['value'])

    def test_nested_public_source_distinguishes_working_and_committed_checkout_aliases(self):
        checkout = self.root.resolve(strict=True)
        shutil.copytree(Path(project.__file__).parent, checkout / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        spec_path = checkout / 'engineering-contract-v1.json'
        spec = read_json(spec_path)
        spec['product'] = {'purpose': {'$source': {'path': 'contracts/contract.json', 'pointer': '/purpose'}}}
        spec_path.write_bytes(json_bytes(spec))
        link_path = checkout / 'contracts/contract.json'
        link_path.write_bytes(json_bytes({
            'purpose': {'$source': {'path': 'contracts/purpose.json', 'pointer': '/text'}},
        }))
        leaf_path = checkout / 'contracts/purpose.json'
        leaf_path.write_bytes(json_bytes({'text': 'Committed nested purpose.'}))
        reviewed = self.commit()

        aliases = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve(strict=True)

        def directory_alias(target, alias):
            if os.name == 'nt':
                from _winapi import CreateJunction
                CreateJunction(str(target), str(alias))
                self.addCleanup(alias.rmdir)
            else:
                alias.symlink_to(target, target_is_directory=True)
                self.addCleanup(alias.unlink)

        alias = aliases / 'checkout alias'
        directory_alias(checkout, alias)
        self.assertNotEqual(checkout, alias)
        self.assertTrue(alias.samefile(checkout))

        def cli(workspace, *args, exit_code=0):
            result = subprocess.run([sys.executable, '-B', 'scripts/project.py', *args], cwd=workspace,
                                    capture_output=True, check=False, text=True)
            self.assertEqual(exit_code, result.returncode, result.stderr or result.stdout)
            return json.loads(result.stdout)

        read = ('source', 'engineering-contract-v1.json', '/product/purpose')
        context = ('context', 'M0-001', '--phase', 'verify', '--source', reviewed)
        for workspace in (checkout, alias):
            with self.subTest(path=workspace, phase='clean'):
                bound = cli(workspace, *context)
                self.assertEqual(reviewed, bound['review_source'])
                self.assertEqual('Committed nested purpose.', bound['product']['purpose'])

        leaf_path.write_bytes(json_bytes({'text': 'Changed working purpose.'}))
        for workspace in (checkout, alias):
            with self.subTest(path=workspace, phase='working'):
                ordinary = cli(workspace, *read)
                self.assertEqual('Changed working purpose.', ordinary['value'])
                self.assertNotIn('source_commit', ordinary)
                historical = cli(workspace, *read, '--source', reviewed)
                self.assertEqual('Committed nested purpose.', historical['value'])
                self.assertEqual(reviewed, historical['source_commit'])
                self.assertIn('requires a clean checkout', cli(workspace, *context, exit_code=1)['error'])

        outside = aliases / 'outside'
        outside.mkdir()
        (outside / 'purpose.json').write_bytes(json_bytes({'text': 'Outside fixture value.'}))
        directory_alias(outside, checkout / 'contracts/redirected')
        link_path.write_bytes(json_bytes({
            'purpose': {'$source': {'path': 'contracts/redirected/purpose.json', 'pointer': '/text'}},
        }))
        for workspace in (checkout, alias):
            with self.subTest(path=workspace, phase='outside'):
                rejected = cli(workspace, *read, exit_code=1)
                self.assertIn('Path escapes repository', rejected['error'])
                self.assertNotIn('value', rejected)
                self.assertNotIn('Outside fixture value.', json.dumps(rejected))
                historical = cli(workspace, *read, '--source', reviewed)
                self.assertEqual('Committed nested purpose.', historical['value'])
                self.assertEqual(reviewed, historical['source_commit'])

    def test_diagnostic_archive_configuration_is_explicit_and_ordered(self):
        report_path = self.report()
        report = read_json(report_path)
        report['source']['git_head'] = LOCATOR['source_commit']
        archive = self.root / '.reports' / 'archive with spaces'
        retained = archive / 'runs' / report['run_id']
        retained.mkdir(parents=True)
        original = retained / 'test-report.json'
        original.write_bytes(json_bytes(report))
        for check in report['checks']:
            log = self.root / check['log']
            (retained / log.name).write_bytes(log.read_bytes())
        locator = project.diagnostic_locator(original)
        args = ('diagnostic', 'resolve', locator['run_id'], locator['source_commit'], locator['report_sha256'])
        self.change_task(diagnostic_reports=[locator])
        reviewed = self.commit()
        bound = self.project_command('context', 'M0-001', '--phase', 'verify', '--source', reviewed)
        diagnostic, = bound['causal_investigation']['related_task_diagnostics']
        with patch.dict(os.environ, {'SCOPEHUB_ARCHIVE_ROOT': ''}):
            missing = self.project_command(*args, exit_code=1)
            self.assertIn('git config --local scopehub.archiveRoot', missing['error'])
            subprocess.run(['git', 'config', '--local', 'scopehub.archiveRoot', str(archive)],
                           cwd=self.root, check=True, capture_output=True)
            verified = self.project_command(*args)
            self.assertEqual(locator['report_sha256'], verified['report_sha256'])
            self.assertEqual(str(original.resolve()), verified['report_path'])
            self.assertEqual(verified, self.follow_review_command(diagnostic))
        with patch.dict(os.environ, {'SCOPEHUB_ARCHIVE_ROOT': str(archive / 'missing')}):
            self.assertEqual('failed', self.project_command(*args, exit_code=1)['status'])
            self.assertEqual(verified, self.project_command(*args, '--archive-root', str(archive)))
        with patch.object(project, 'diagnostic_archive_root', side_effect=AssertionError('Only resolve uses the archive')):
            self.assertEqual('passed', self.project_command('status')['status'])
            self.assertEqual('M0-001', self.project_command('context', 'M0-001')['task']['id'])

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
