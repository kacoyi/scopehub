import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.diagnostic_archive import (
    DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic, validate_locator,
)


RUN = '20261121T015908.326119Z-721f69a6'
COMMIT = 'a1' * 20
HARNESS_LOG = b'original verification output\n'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class DiagnosticArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.archive = self.directory / 'archive-before-move'

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_report(self, path, report):
        contents = (json.dumps(report, indent=2) + '\n').encode()
        self.write(path, contents)
        return {'run_id': RUN, 'source_commit': COMMIT, 'report_sha256': sha256(contents)}

    def harness_report(self):
        return {
            'report_version': '2.0',
            'run_id': RUN,
            'source': {'git_head': COMMIT, 'working_tree_dirty': False},
            'status': 'blocked',
            'checks': [
                {'id': 'environment', 'status': 'passed', 'exit_code': 0,
                 'log': f'.reports/runs/{RUN}/environment.log', 'log_sha256': sha256(HARNESS_LOG)},
                {'id': 'native-acceptance', 'status': 'blocked', 'reason_code': 'unsupported_platform'},
                {'id': 'owner-observation', 'status': 'blocked', 'exit_code': 77,
                 'reason_code': 'observation_unavailable',
                 'log': f'.reports/runs/{RUN}/owner-observation.log', 'log_sha256': sha256(b'pending')},
            ],
        }

    def harness(self, report=None):
        run = self.archive / 'runs' / RUN
        self.write(run / 'environment.log', HARNESS_LOG)
        self.write(run / 'owner-observation.log', b'pending')
        path = run / 'test-report.json'
        return path, self.write_report(path, self.harness_report() if report is None else report)

    def checksums(self, directory, *paths):
        data = ''.join(f'{sha256((directory / path).read_bytes())}  {path}\n' for path in paths)
        return self.write(directory / 'SHA256SUMS', data.encode())

    def harness_with_observation(self, check, binding):
        report = self.harness_report()
        log = f'{check}.log'
        report['checks'].append({
            'id': check, 'status': 'passed', 'exit_code': 0,
            'log': f'.reports/runs/{RUN}/{log}', 'log_sha256': sha256(HARNESS_LOG), **binding,
        })
        path, locator = self.harness(report)
        directory = path.parent
        self.write(directory / log, HARNESS_LOG)
        return directory, locator, (path.name, 'environment.log', 'owner-observation.log', log)

    def test_locator_is_independent_of_archive_and_checkout_paths(self):
        path, locator = self.harness()
        self.assertEqual(diagnostic_locator(path), locator)
        moved = self.archive.rename(self.directory / 'retained-reports')
        self.assertIsNone(validate_locator(locator))
        with self.assertRaises(DiagnosticArchiveError):
            resolve_diagnostic(locator, self.archive)
        result = resolve_diagnostic(locator, moved)
        self.assertEqual(result['report_path'], str(moved.resolve() / 'runs' / RUN / 'test-report.json'))
        self.assertEqual(result['recorded_status'], 'blocked')
        self.assertEqual(result['selected_check_statuses'], {
            'environment': 'passed', 'native-acceptance': 'blocked', 'owner-observation': 'blocked',
        })
        self.assertEqual(result['report_kind'], 'harness')
        self.assertEqual(result['verified_logs'], ['environment', 'owner-observation'])
        self.assertFalse(result['manifest_verified'])

    def test_only_safe_run_ids_and_full_content_identity_are_valid(self):
        _, locator = self.harness()
        cases = [
            {'run_id': value} for value in (
                '../runs', 'nested/path', r'C:\run', r'nested\path', '.', 'run.', 'CON', 'NUL.txt', 'a\n',
                '_run', 'a' * 129,
            )
        ] + [
            {'source_commit': 'main'}, {'source_commit': COMMIT[:12]}, {'source_commit': '0' * 40},
            {'report_sha256': 'A' * 64}, {'report_sha256': 'a' * 63}, {'path': '/host/report.json'},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(DiagnosticArchiveError):
                validate_locator({**locator, **changes})
        self.assertIsNone(validate_locator({**locator, 'source_commit': 'a' * 64}))
        with self.assertRaisesRegex(DiagnosticArchiveError, 'archive root is required'):
            resolve_diagnostic(locator, None)

    def test_report_digest_run_id_source_and_status_bind_independently(self):
        path, locator = self.harness()
        with self.assertRaisesRegex(DiagnosticArchiveError, 'source Git commit'):
            resolve_diagnostic({**locator, 'source_commit': 'c' * 40}, self.archive)
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'SHA-256'):
            resolve_diagnostic(locator, self.archive)
        report = self.harness_report()
        report['run_id'] = 'another-run'
        locator = self.write_report(path, report)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'run ID'):
            resolve_diagnostic(locator, self.archive)
        report['run_id'] = RUN
        report['status'] = 'unknown'
        locator = self.write_report(path, report)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'recorded harness outcome'):
            resolve_diagnostic(locator, self.archive)
        duplicated = path.read_bytes().replace(b'"unknown"', b'"failed", "status": "passed"')
        self.write(path, duplicated)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'Duplicate fields'):
            resolve_diagnostic({**locator, 'report_sha256': sha256(duplicated)}, self.archive)

    def test_missing_changed_and_other_run_logs_are_rejected(self):
        path, locator = self.harness()
        log = path.parent / 'environment.log'
        log.write_bytes(b'changed')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'diagnostic bytes: environment.log'):
            resolve_diagnostic(locator, self.archive)
        log.unlink()
        with self.assertRaisesRegex(DiagnosticArchiveError, 'environment.log'):
            resolve_diagnostic(locator, self.archive)
        report = self.harness_report()
        report['checks'][0]['log'] = '.reports/runs/different-run/environment.log'
        _, locator = self.harness(report)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'outside its diagnostic run'):
            resolve_diagnostic(locator, self.archive)

    def test_checksum_manifest_covers_retained_files(self):
        path, locator = self.harness()
        directory = path.parent
        extra = self.write(directory / 'workflow' / 'result.json', b'{"status":"interrupted"}')
        manifest = self.checksums(directory, 'test-report.json', 'environment.log', 'owner-observation.log',
                                  'workflow/result.json')
        self.assertTrue(resolve_diagnostic(locator, self.archive)['manifest_verified'])
        extra.write_bytes(b'{}')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'diagnostic bytes: workflow/result.json'):
            resolve_diagnostic(locator, self.archive)
        self.checksums(directory, 'test-report.json', 'environment.log', 'owner-observation.log')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'differ from SHA256SUMS'):
            resolve_diagnostic(locator, self.archive)
        manifest.write_bytes(f'{sha256(b"outside")}  ../outside.log\n'.encode())
        with self.assertRaisesRegex(DiagnosticArchiveError, 'Invalid archive-relative path'):
            resolve_diagnostic(locator, self.archive)

    def test_blocked_run_requires_report_bound_field_observation_and_screenshots(self):
        check = 'ubuntu-workspace-observation'
        relative = f'observations/{check}'
        manifest = f'{relative}/observation.json'
        screenshot = f'{relative}/screenshots/workspace.png'
        image = b'\x89PNG\r\n\x1a\n screenshot evidence'
        contents = json.dumps({
            'kind': check, 'commit': COMMIT, 'surface': 'ubuntu-graphical-workspace',
            'screenshots': [{'path': 'screenshots/workspace.png', 'sha256': sha256(image)}],
        }).encode()
        directory, locator, files = self.harness_with_observation(check, {
            'field_observation': {
                'kind': check, 'commit': COMMIT, 'surface': 'ubuntu-graphical-workspace',
                'observation_sha256': sha256(contents),
            },
        })
        self.checksums(directory, *files)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'observation.json'):
            resolve_diagnostic(locator, self.archive)
        self.write(directory / manifest, contents)
        self.checksums(directory, *files, manifest)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'workspace.png'):
            resolve_diagnostic(locator, self.archive)

        self.write(directory / screenshot, image)
        self.checksums(directory, *files, manifest, screenshot)
        result = resolve_diagnostic(locator, self.archive)
        self.assertEqual(result['recorded_status'], 'blocked')
        self.assertEqual(result['selected_check_statuses'][check], 'passed')
        self.assertEqual(result['verified_observations'], [manifest])
        self.assertTrue(result['manifest_verified'])

        self.write(directory / screenshot, b'changed screenshot')
        self.checksums(directory, *files, manifest, screenshot)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'changed diagnostic bytes: .*workspace.png'):
            resolve_diagnostic(locator, self.archive)
        # A valid bundle-level copy cannot hide a damaged per-run snapshot.
        self.write(self.archive / manifest, contents)
        self.write(self.archive / screenshot, image)
        self.write(directory / manifest, contents + b' ')
        self.checksums(directory, *files, manifest, screenshot)
        with self.assertRaisesRegex(DiagnosticArchiveError, 'changed diagnostic bytes: .*observation.json'):
            resolve_diagnostic(locator, self.archive)

    def test_report_bound_session_observation_survives_archive_without_checksum_manifest(self):
        check = 'configured-model-session'
        relative = f'{check}.observation.json'
        contents = json.dumps({'check': check, 'commit': COMMIT, 'modality': 'text'}).encode()
        directory, locator, _ = self.harness_with_observation(check, {
            'observation': {'evidence_sha256': sha256(contents)},
        })
        with self.assertRaisesRegex(DiagnosticArchiveError, relative):
            resolve_diagnostic(locator, self.archive)
        self.write(directory / relative, contents)
        result = resolve_diagnostic(locator, self.archive)
        self.assertEqual(result['verified_observations'], [relative])
        self.assertFalse(result['manifest_verified'])
        self.write(directory / relative, contents + b' ')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'changed diagnostic bytes'):
            resolve_diagnostic(locator, self.archive)

    def test_linked_archive_files_cannot_reference_another_checkout(self):
        path, locator = self.harness()
        outside = self.write(self.directory / 'other-checkout' / 'environment.log', HARNESS_LOG)
        log = path.parent / 'environment.log'
        log.unlink()
        try:
            log.symlink_to(outside)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'Symlinks unavailable: {error}')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'ordinary directories|reparse point'):
            resolve_diagnostic(locator, self.archive)

    def test_archives_with_identical_hardlinked_bytes_can_resolve(self):
        path, locator = self.harness()
        try:
            os.link(path, self.archive / 'retained-test-report.json')
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'Hard links unavailable: {error}')
        self.assertEqual(resolve_diagnostic(locator, self.archive)['recorded_status'], 'blocked')

    def test_report_bound_candidate_manifest_verifies_retained_files_without_release_acceptance(self):
        report = self.harness_report()
        inputs = sha256(b'candidate inputs')
        report['source']['sha256'] = inputs
        candidate = self.archive / 'runs' / RUN / 'candidate'

        def retain(relative, data):
            self.write(candidate / relative, data)
            return {'path': relative, 'sha256': sha256(data)}

        installer = retain('ScopeHub Setup.exe', b'synthetic installer')
        windows = [retain('Windows 10.json', b'{}'), retain('Windows 11.json', b'{}')]
        performance = retain('performance.json', b'{}')
        notice = retain('THIRD_PARTY_NOTICES.txt', b'Fixture notice text')
        manifest = self.write(candidate / 'build-manifest.json', json.dumps({
            'commit': COMMIT,
            'inputs_sha256': inputs,
            'installer': installer,
            'windows_observations': windows,
            'performance': performance,
            'third_party_licenses': [notice],
        }).encode())
        report['candidate_artifacts'] = {
            'manifest_sha256': sha256(manifest.read_bytes()), 'installer_sha256': installer['sha256'],
        }
        _, locator = self.harness(report)
        result = resolve_diagnostic(locator, self.archive)
        self.assertEqual(result['recorded_status'], 'blocked')
        self.assertTrue(result['manifest_verified'])
        (candidate / performance['path']).write_bytes(b'changed results')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'diagnostic bytes: candidate/performance.json'):
            resolve_diagnostic(locator, self.archive)
        manifest.write_bytes(b'{}')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'Candidate manifest differs'):
            resolve_diagnostic(locator, self.archive)

    def test_recovery_manifest_verifies_original_bytes_and_preserves_outcome(self):
        path = self.archive / 'recovery' / RUN / 'diagnostic.json'
        original = self.write(path.parent / 'originals/project/tasks.json', b'{malformed evidence')
        report = {
            'diagnostic_version': '1.0',
            'kind': 'scaffold_recovery',
            'status': 'attention_required',
            'acceptance': 'not_evaluated',
            'snapshot': f'.reports/recovery/{RUN}/diagnostic.json',
            'git': {'head': COMMIT, 'working_tree': 'M project/tasks.json'},
            'files': [
                {'path': 'project/tasks.json', 'status': 'malformed', 'sha256': sha256(original.read_bytes())},
                {'path': '.reports/test-report.json', 'status': 'unavailable'},
            ],
        }
        locator = self.write_report(path, report)
        self.assertEqual(diagnostic_locator(path), locator)
        resolved = resolve_diagnostic(locator, self.archive)
        self.assertEqual(resolved['report_kind'], 'recovery')
        self.assertEqual(resolved['recorded_status'], 'attention_required')
        self.assertTrue(resolved['source_working_tree_dirty'])
        self.assertEqual(resolved['verified_originals'], ['project/tasks.json'])
        self.assertTrue(resolved['manifest_verified'])
        extra = self.write(path.parent / 'originals/.reports/test-report.json', b'{}')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'differ from the recovery snapshot'):
            resolve_diagnostic(locator, self.archive)
        extra.unlink()
        original.write_bytes(b'changed')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'diagnostic bytes: originals/project/tasks.json'):
            resolve_diagnostic(locator, self.archive)
