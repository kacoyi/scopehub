"""Prevent dependency aliases from hiding forbidden layer relationships."""

from contextlib import redirect_stdout
from io import BytesIO, TextIOWrapper
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_architecture import desktop_capability_errors, rust_boundaries, valid_operation_actors
from scripts.rust_workspace import run_operations, verification_command, workspace_packages


class ArchitectureTests(unittest.TestCase):
    def test_operation_actors_follow_the_declared_roles_and_private_workspace_authority(self):
        roles = ['user', 'ai', 'system']
        self.assertTrue(valid_operation_actors({'actors': ['system']}, roles))
        self.assertTrue(valid_operation_actors({'actors': ['ai', 'user']}, roles))
        for actors in ([], ['user', 'user'], ['unknown']):
            self.assertFalse(valid_operation_actors({'actors': actors}, roles))
        for actors in (['ai'], ['system'], ['user', 'ai']):
            self.assertFalse(valid_operation_actors({'actors': actors, 'transport': 'private_workspace'}, roles))
        self.assertTrue(valid_operation_actors({'actors': ['user'], 'transport': 'private_workspace'}, roles))
        self.assertFalse(valid_operation_actors({'actors': ['user'], 'transport': 'private_workspace'}, ['ai']))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace()

    def workspace(self, dependencies='', members='["crates/domain"]'):
        (self.root / 'Cargo.toml').write_text(
            f'[workspace]\nresolver = "3"\nmembers = {members}\n' + dependencies)

    def crate(self, relative, name, dependencies='', source=''):
        path = self.root / relative
        (path / 'src').mkdir(parents=True, exist_ok=True)
        (path / 'src/lib.rs').write_text(source)
        (path / 'Cargo.toml').write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n' + dependencies)

    def domain(self, dependencies):
        self.crate('crates/domain', 'scopehub-domain', dependencies)

    def test_desktop_registry_acl_rejects_extra_permissions_windows_remote_and_inline_grants(self):
        source = Path(__file__).resolve().parents[2] / 'apps/desktop/src-tauri'
        target = self.root / 'apps/desktop/src-tauri'
        target.mkdir(parents=True)
        for name in ['build.rs', 'tauri.conf.json']:
            shutil.copyfile(source / name, target / name)
        shutil.copytree(source / 'capabilities', target / 'capabilities')
        self.assertEqual([], desktop_capability_errors(self.root))
        path = target / 'capabilities/main.json'
        original = json.loads(path.read_text())
        for change in [{'permissions':['allow-execute-operation','shell:default']},
                       {'windows':['*']}, {'remote':{'urls':['https://example.com']}},
                       {'webviews':['*']}, {'local':False}]:
            with self.subTest(change=change):
                path.write_text(json.dumps({**original, **change}))
                self.assertTrue(desktop_capability_errors(self.root))
        path.write_text(json.dumps(original))
        extra = target / 'capabilities/extra.toml'
        extra.write_text('identifier = "extra"')
        self.assertTrue(desktop_capability_errors(self.root))
        extra.unlink()
        path = target / 'tauri.conf.json'
        config = json.loads(path.read_text())
        config['app']['security']['capabilities'].append({'identifier':'extra','permissions':['fs:default']})
        path.write_text(json.dumps(config))
        self.assertTrue(desktop_capability_errors(self.root))

    def test_desktop_capability_files_use_utf8_with_a_legacy_locale(self):
        source = Path(__file__).resolve().parents[2] / 'apps/desktop/src-tauri'
        target = self.root / 'apps/desktop/src-tauri'
        (target / 'capabilities').mkdir(parents=True)
        for name in ['build.rs', 'tauri.conf.json']:
            shutil.copyfile(source / name, target / name)
        name = 'capabilities/main.json'
        capability = json.loads((source / name).read_text(encoding='utf-8'))
        capability['description'] = 'Reviewed desktop access for a synthetic “sample”.'
        (target / name).write_text(json.dumps(capability, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run(
            [sys.executable, '-X', 'utf8=0', '-c',
             'import sys; from pathlib import Path; '
             'from scripts.check_architecture import desktop_capability_errors; '
             'sys.exit(bool(desktop_capability_errors(Path(sys.argv[1]))))', str(self.root)],
            cwd=source.parents[2], env={**os.environ, 'LC_ALL': 'C', 'PYTHONCOERCECLOCALE': '0'},
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workspace_alias_cannot_hide_database_dependency(self):
        self.workspace('[workspace.dependencies]\nstore = { package = "rusqlite", version = "1" }\n')
        self.domain('[dependencies]\nstore = { workspace = true }\n')
        self.assertIn('Infrastructure dependency in scopehub-domain: rusqlite', rust_boundaries(self.root))

    def test_target_specific_workspace_alias_cannot_hide_internal_layer(self):
        self.workspace('[workspace.dependencies]\nport = { package = "scopehub-persistence", path = "crates/persistence" }\n')
        self.crate('crates/persistence', 'scopehub-persistence')
        self.domain('[target.\'cfg(windows)\'.dependencies]\nport = { workspace = true }\n')
        self.assertIn('Forbidden internal dependency: scopehub-domain -> scopehub-persistence', rust_boundaries(self.root))

    def test_normal_workspace_library_is_allowed(self):
        self.workspace('[workspace.dependencies]\nserde = "1"\n')
        self.domain('[dependencies]\nserde = { workspace = true }\n')
        self.assertEqual([], rust_boundaries(self.root))

    def test_development_catalog_controls_actual_rust_dependencies(self):
        self.crate('crates/application', 'scopehub-application')
        self.domain('[dependencies]\napp = { package = "scopehub-application", path = "../application" }\n')
        directory = self.root / 'project'
        directory.mkdir()
        (directory / 'development.json').write_text(json.dumps({'modules': [
            {'id': 'domain', 'paths': ['crates/domain'], 'dependencies': []},
            {'id': 'application', 'paths': ['crates/application'], 'dependencies': ['domain']},
        ]}))
        self.assertIn('Forbidden internal dependency: scopehub-domain -> scopehub-application', rust_boundaries(self.root))

    def test_nested_glob_members_cannot_hide_forbidden_dependencies(self):
        self.workspace(members='["crates/nested/*"]')
        self.crate('crates/nested/domain', 'scopehub-domain', '[dependencies]\nrusqlite = "1"\n')
        self.assertIn('Infrastructure dependency in scopehub-domain: rusqlite', rust_boundaries(self.root))

    def test_implicit_path_members_receive_boundary_checks(self):
        self.domain('[dependencies]\nhelper = { package = "unregistered-helper", path = "../nested/helper" }\n')
        self.crate('crates/nested/helper', 'unregistered-helper')
        self.assertIn('Unregistered internal layer: unregistered-helper', rust_boundaries(self.root))
        self.assertEqual(2, len(workspace_packages(self.root)))

    def test_missing_members_and_failed_metadata_are_not_empty_success(self):
        self.assertTrue(rust_boundaries(self.root))
        with patch('scripts.rust_workspace.subprocess.run', side_effect=OSError('cargo unavailable')):
            self.assertEqual(['cargo unavailable'], rust_boundaries(self.root))

    def test_unknown_platform_is_rejected_and_native_selection_includes_desktop(self):
        self.domain('')
        self.crate('apps/desktop', 'scopehub-desktop', '[package.metadata.scopehub]\ncheck_platform = "windows-x64"\nportable_test_lib = true\n')
        self.workspace(members='["crates/domain", "apps/desktop"]')
        packages = workspace_packages(self.root)
        linux, coverage = verification_command(packages, 'test', system='Linux', machine='x86_64')
        self.assertEqual(['scopehub-desktop'], coverage['portable_library_tests'])
        _, native_tests = verification_command(packages, 'test', system='Windows', machine='AMD64')
        self.assertEqual([], native_tests['portable_library_tests'])
        self.assertIn('--workspace', linux)
        self.assertEqual(['scopehub-domain'], coverage['included'])
        self.assertEqual('scopehub-desktop', coverage['excluded'][0]['package'])
        windows, coverage = verification_command(packages, 'clippy', system='Windows', machine='AMD64')
        self.assertNotIn('--exclude', windows)
        self.assertIn('--all-targets', windows)
        self.assertEqual({'scopehub-domain', 'scopehub-desktop'}, set(coverage['included']))
        self.assertEqual([], coverage['excluded'])
        packages[0]['metadata'] = {'scopehub': {'check_platform': 'typo'}}
        with self.assertRaisesRegex(ValueError, 'Unsupported check platform'):
            verification_command(packages, 'test')

    def test_operations_use_the_host_workspace_graph_and_run_only_the_scoped_target(self):
        checkout = Path(__file__).resolve().parents[2]
        packages = workspace_packages(checkout)
        for system, machine in [('Linux', 'x86_64'), ('Windows', 'AMD64'), ('Windows', 'ARM64')]:
            with self.subTest(system=system, machine=machine):
                workspace, _ = verification_command(packages, 'test', system=system, machine=machine)
                command, selection = verification_command(packages, 'operations', system=system, machine=machine)
                self.assertEqual([*workspace, '--test', 'workspace_process', 'operations_workflow::'], command)
                self.assertEqual([], selection['portable_library_tests'])

    def test_operations_reject_a_missing_or_ambiguous_eligible_target(self):
        target = {'kind': ['test'], 'name': 'workspace_process'}
        persistence = {'name': 'scopehub-persistence', 'targets': [target]}
        other = {'name': 'scopehub-other', 'targets': [target]}
        for packages in [[other], [persistence, other]]:
            with self.subTest(packages=packages), self.assertRaisesRegex(ValueError, 'one eligible scopehub-persistence'):
                verification_command(packages, 'operations', system='Linux', machine='x86_64')

    def test_operations_require_one_summary_with_passes_and_no_ignored_cases(self):
        success = b'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 18 filtered out; finished in 0.34s\r\n'
        empty = success.replace(b'2 passed', b'0 passed')
        ignored = empty.replace(b'0 ignored', b'2 ignored')
        cases = [
            ('passing', success, 0, 0),
            ('zero matches', empty, 0, 1),
            ('all ignored', ignored, 0, 1),
            ('partly ignored', success.replace(b'2 passed', b'1 passed').replace(b'0 ignored', b'3 ignored'), 0, 1),
            ('missing summary', b'no summary\n', 0, 1),
            ('duplicate summary', success + success, 0, 1),
            ('cargo failure', success, 101, 101),
        ]
        for name, output, exit_code, expected in cases:
            with self.subTest(name=name), TextIOWrapper(BytesIO(), encoding='utf-8') as console:
                with redirect_stdout(console), patch('scripts.rust_workspace.subprocess.Popen') as launch:
                    process = launch.return_value.__enter__.return_value
                    process.stdout = BytesIO(output)
                    process.wait.return_value = exit_code
                    self.assertEqual(expected, run_operations(['cargo']))
                launch.assert_called_once()
                streamed = console.buffer.getvalue()
                self.assertTrue(streamed.startswith(output))
                if expected == 1:
                    self.assertIn(b'passing cases and zero ignored cases', streamed[len(output):])
                else:
                    self.assertEqual(output, streamed)

    def test_new_member_test_runs_even_when_absent_from_default_members(self):
        self.domain('')
        self.crate('crates/nested/application', 'scopehub-application', source='''
#[test]
fn member_is_not_omitted() { panic!("new workspace member was executed"); }
''')
        self.workspace('default-members = ["crates/domain"]\n',
                       members='["crates/domain", "crates/nested/application"]')
        subprocess.run(['cargo', 'generate-lockfile', '--offline'], cwd=self.root,
                       check=True, capture_output=True, timeout=30)
        command, coverage = verification_command(workspace_packages(self.root), 'test', system='Linux', machine='x86_64')
        self.assertIn('scopehub-application', coverage['included'])
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('new workspace member was executed', result.stdout)
