"""Exercise the hosted publication boundary with isolated synthetic evidence."""

from contextlib import redirect_stdout
import base64
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from scripts import check_chart_history, verification_evidence
from scripts.check import PRIVATE_CHECK_LOGS, redacted_check_log
from scripts.ci_artifacts import PublicationError, prepare_reports, stage_artifacts
from scripts.project import ProjectError, archive_evidence, digest, json_bytes, load_project, read_json, verified_candidate_files
from scripts.verification_evidence import FIELD_ACTIONS, collect_session, field_observation, integration_validation, release_artifacts
from tests.contracts.test_qualification import observed_case
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


class PublicationTests(ProjectFixture):
    sentinel = b'Synthetic stand-in for private bytes.\n'

    def setUp(self):
        super().setUp()
        host_temp = tempfile.TemporaryDirectory()
        self.addCleanup(host_temp.cleanup)
        self.destination = Path(host_temp.name) / 'verification'
        self.reports = self.root / '.reports'

    def write(self, relative, contents):
        path = self.reports / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def save_report(self, report):
        for relative in ['test-report.json', 'runs/fixture/test-report.json']:
            self.write(relative, json_bytes(report))

    def add_catalog_checks(self, *checks):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        for check_id in checks:
            check = next((item for item in catalog['checks'] if item['id'] == check_id), None)
            if check is None:
                check = {'id': check_id, 'purpose': 'Synthetic verification fixture.', 'platforms': 'all',
                         'cadence': 'change', 'timeout_seconds': 10}
                catalog['checks'].append(check)
            check.update(status='implemented', command=['synthetic-check'])
        catalog['required_change_checks'] = [
            check['id'] for check in catalog['checks']
            if check['status'] == 'implemented' and check.get('cadence', 'change') == 'change'
        ]
        path.write_bytes(json_bytes(catalog))

    def hosted_report(self, *, system='Linux', event='pull_request'):
        prepare_reports(self.root)
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Retain synthetic evidence inputs')
        self.git('remote', 'add', 'origin', 'https://github.com/fixture/scopehub.git')
        head = self.git('rev-parse', 'HEAD')
        self.env = {
            'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': head, 'SCOPEHUB_SOURCE_HEAD': head,
            'GITHUB_REPOSITORY': 'fixture/scopehub', 'GITHUB_RUN_ID': '10',
            'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': event,
        }
        report = read_json(self.report(system=system))
        for result in report['checks']:
            if result['id'] in PRIVATE_CHECK_LOGS:
                contents = redacted_check_log(result['exit_code'])
                (self.root / result['log']).write_bytes(contents)
                result['log_sha256'] = digest(contents)
        report['ci'] = {
            'provider': 'github-actions', 'repository': 'fixture/scopehub', 'run_id': '10',
            'run_attempt': '2', 'event': event, 'head_sha': head, 'checkout_sha': head,
            'run_url': 'https://github.com/fixture/scopehub/actions/runs/10',
        }
        return report

    def add_result(self, report, check, *, status='passed'):
        exit_code = {'passed': 0, 'failed': 1, 'blocked': 77}[status]
        log = redacted_check_log(exit_code) if check in PRIVATE_CHECK_LOGS else b'Synthetic check output.\n'
        relative = f'runs/fixture/{check}.log'
        self.write(relative, log)
        report['checks'].append({
            'id': check, 'status': status, 'command': ['synthetic-check'],
            'exit_code': exit_code,
            'log': '.reports/' + relative, 'log_sha256': digest(log),
        })

    def test_refuses_any_preexisting_reports_root(self):
        saved = self.write('recovery/old.json', self.sentinel)
        with self.assertRaisesRegex(PublicationError, 'fresh .reports directory'):
            prepare_reports(self.root)
        self.assertEqual(self.sentinel, saved.read_bytes())

    def test_no_finalized_report_leaves_nothing_uploadable(self):
        prepare_reports(self.root)
        self.write('recovery/current.json', self.sentinel)
        self.write('runs/unfinished/environment.log', self.sentinel)
        self.assertFalse(stage_artifacts(self.root, self.destination, {}))
        self.assertFalse(self.destination.exists())

    def test_only_current_named_logs_report_and_synthetic_browser_outputs_are_published(self):
        self.add_catalog_checks('chart-history', 'ubuntu-preview', 'market-live-stream')
        report = self.hosted_report()
        self.add_result(report, 'chart-history')
        self.add_result(report, 'ubuntu-preview')
        self.add_result(report, 'market-live-stream')
        self.save_report(report)
        summary = b'{"status":"passed"}\n'
        browser = {
            'chart-history/runs/fixture/summary.json': summary,
            'chart-history/runs/fixture/browser-results.json': b'{}\n',
            'chart-history/runs/fixture/browser/history-startup/history-candles.png': b'synthetic PNG',
            'ubuntu-preview/fixture/results.json': b'{}\n',
            'ubuntu-preview/fixture/browser/preview-open/futures-candles.png': b'synthetic PNG',
            'runs/fixture/market-live-stream/results.json': b'{}\n',
            'runs/fixture/market-live-stream/browser/market-stream-final/synthetic-replay-final.png': b'synthetic PNG',
        }
        for relative, contents in browser.items():
            self.write(relative, contents)
        for relative in [
            'recovery/old.json', 'runs/old/test-report.json', 'runs/old/project-state.log',
            'runs/fixture/unlisted.log', 'runs/fixture/configured-model-session.observation.json',
            'runs/fixture/candidate/profile.json', 'observations/private/screenshots.png',
            'chart-history/latest.json', 'chart-history/runs/old/browser-results.json',
            'ubuntu-preview/old/results.json', 'ubuntu-preview/fixture/browser/preview-open/trace.zip',
            'ubuntu-preview/fixture/browser/preview-open/private.png', 'ubuntu-preview/fixture/profile.json',
            '.private',
        ]:
            self.write(relative, self.sentinel)

        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        published = {path.relative_to(self.destination).as_posix(): path.read_bytes()
                     for path in self.destination.rglob('*') if path.is_file()}
        expected = {
            **browser, 'chart-history/latest.json': summary,
            'test-report.json': json_bytes(report), 'runs/fixture/test-report.json': json_bytes(report),
            **{item['log'].removeprefix('.reports/'): (self.root / item['log']).read_bytes()
               for item in report['checks']},
        }
        self.assertEqual(expected, published)
        imported = self.reports / 'ci/10/linux'
        shutil.copytree(self.destination, imported)
        reference = archive_evidence(self.root, 'M0-001', imported / 'test-report.json', imported)
        self.assertEqual(digest(json_bytes(report)), read_json(self.root / reference['path'])['report_sha256'])

    def test_failed_run_retains_finalized_report_log_and_synthetic_failure_screenshot(self):
        self.add_catalog_checks('ubuntu-preview')
        report = self.hosted_report()
        report['status'] = 'failed'
        self.add_result(report, 'ubuntu-preview', status='failed')
        self.save_report(report)
        screenshot = 'ubuntu-preview/fixture/browser/preview-open/test-failed-1.png'
        self.write(screenshot, b'synthetic failure image')
        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        self.assertEqual(b'synthetic failure image', (self.destination / screenshot).read_bytes())
        self.assertTrue((self.destination / 'runs/fixture/ubuntu-preview.log').is_file())
        self.assertEqual('failed', read_json(self.destination / 'test-report.json')['status'])

    def test_rejects_another_attempt_and_mismatched_named_report(self):
        report = self.hosted_report()
        self.save_report(report)
        with self.assertRaisesRegex(PublicationError, 'current hosted job'):
            stage_artifacts(self.root, self.destination, {**self.env, 'GITHUB_RUN_ATTEMPT': '3'})
        self.write('runs/fixture/test-report.json', self.sentinel)
        with self.assertRaisesRegex(PublicationError, 'reports differ'):
            stage_artifacts(self.root, self.destination, self.env)
        self.assertFalse(self.destination.exists())

    def test_rejects_log_traversal_and_tampering(self):
        report = self.hosted_report()
        result = report['checks'][0]
        original_log = result['log']
        result['log'] = '.reports/runs/fixture/../../recovery/private.log'
        self.save_report(report)
        with self.assertRaisesRegex(PublicationError, 'outside the current verification run'):
            stage_artifacts(self.root, self.destination, self.env)
        result['log'] = original_log
        self.save_report(report)
        self.write(original_log.removeprefix('.reports/'), self.sentinel)
        with self.assertRaisesRegex(PublicationError, 'recorded digest'):
            stage_artifacts(self.root, self.destination.with_name('tampered'), self.env)

    def test_undeclared_log_cannot_be_published(self):
        report = self.hosted_report()
        self.add_result(report, 'configured-model-session', status='failed')
        self.save_report(report)
        with self.assertRaisesRegex(PublicationError, 'absent from the active catalog'):
            stage_artifacts(self.root, self.destination, self.env)
        self.assertFalse((self.destination / 'test-report.json').exists())

    def test_private_check_only_publishes_its_redacted_exit_status_even_on_failure(self):
        check = 'credential-boundaries'
        self.add_catalog_checks(check)
        report = self.hosted_report()
        self.add_result(report, check, status='failed')
        report['status'] = 'failed'
        self.save_report(report)
        relative = f'runs/fixture/{check}.log'
        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        self.assertEqual(redacted_check_log(1), (self.destination / relative).read_bytes())

        contents = redacted_check_log(1) + self.sentinel
        self.write(relative, contents)
        report['checks'][-1]['log_sha256'] = digest(contents)
        self.save_report(report)
        rejected = self.destination.with_name('unredacted')
        with self.assertRaisesRegex(PublicationError, 'more than its redacted exit status'):
            stage_artifacts(self.root, rejected, self.env)
        self.assertFalse((rejected / relative).exists())

    def record_session(self, report, *, deployment='local'):
        check = 'configured-model-session'
        result = next((item for item in report['checks'] if item['id'] == check), None)
        if result is None:
            self.add_result(report, check)
            result = report['checks'][-1]
        answers = observed_case(deployment=deployment)
        del answers['voice']
        record = {
            'check': check, 'modality': 'text', 'commit': report['source']['git_head'],
            'inputs_sha256': report['source']['sha256'], 'provider': 'synthetic', 'route': 'synthetic-fixture',
            'data_revision': 'synthetic-revision', 'authorized_scope': 'synthetic-model-qualification',
            'observed_at': '2026-01-01T12:00:00Z', 'model': 'synthetic', 'deployment': deployment,
            'qualification': answers,
        }
        path = self.write(f'runs/fixture/{check}.observation.json', json_bytes(record))
        result['observation'] = collect_session(path.parent, check, report['source'])
        return path

    def test_rejects_raw_content_embedded_in_otherwise_valid_session_observation(self):
        self.add_catalog_checks('configured-model-session')
        report = self.hosted_report()
        path = self.record_session(report)
        record = read_json(path)
        record['qualification']['initial']['request'] = self.sentinel.decode()
        contents = json_bytes(record)
        path.write_bytes(contents)
        report['checks'][-1]['observation']['evidence_sha256'] = digest(contents)
        self.save_report(report)
        with self.assertRaisesRegex(ProjectError, 'Incorrect initial interval or calculation'):
            stage_artifacts(self.root, self.destination, self.env)
        self.assertFalse((self.destination / 'test-report.json').exists())

    def test_field_import_keeps_only_manifest_and_referenced_synthetic_screenshots(self):
        check = 'ubuntu-workspace-observation'
        self.add_catalog_checks(check)
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['checks'].append(check)
        self.save_ledger(ledger)
        report = self.hosted_report()
        directory = f'observations/{check}'
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XxGCPgAAAABJRU5ErkJggg=='
        )
        self.write(f'{directory}/fixture.png', image)
        self.write(f'{directory}/unreferenced.png', self.sentinel)
        self.write(f'{directory}/profile.json', self.sentinel)
        record = {
            'kind': check, 'commit': report['source']['git_head'], 'inputs_sha256': report['source']['sha256'],
            'operator': 'synthetic operator', 'observed_at': '2026-01-01T12:00:00Z',
            'surface': 'ubuntu-graphical-workspace', 'screenshot_scope': 'synthetic',
            'actions': dict.fromkeys(FIELD_ACTIONS[check], {'status': 'passed', 'observed': 'Synthetic outcome.'}),
            'external_sessions': dict.fromkeys(['text', 'voice', 'cli', 'desktop'], 'pending'),
            'screenshots': [{'path': 'fixture.png', 'sha256': digest(image)}],
        }
        manifest = self.write(f'{directory}/observation.json', json_bytes(record))
        result = next(item for item in report['checks'] if item['id'] == check)
        result['field_observation'] = field_observation(self.root, check)
        self.save_report(report)

        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        self.assertEqual({'observation.json', 'fixture.png'}, {p.name for p in (self.destination / directory).iterdir()})
        self.assertEqual(manifest.read_bytes(), (self.destination / directory / manifest.name).read_bytes())
        self.assertEqual(image, (self.destination / directory / 'fixture.png').read_bytes())
        expected = result['field_observation']

        del record['screenshot_scope']
        manifest.write_bytes(json_bytes(record))
        result['field_observation'] = field_observation(self.root, check)
        self.save_report(report)
        with (patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}),
              patch('sys.argv', ['verification_evidence.py', check]),
              patch.object(verification_evidence, 'verified_file') as read_image,
              redirect_stdout(StringIO()), self.assertRaises(SystemExit) as blocked):
            verification_evidence.main(self.root)
        self.assertEqual(77, blocked.exception.code)
        read_image.assert_not_called()
        without_field = self.destination.with_name('unqualified')
        self.assertTrue(stage_artifacts(self.root, without_field, self.env))
        self.assertFalse((without_field / 'observations').exists())
        self.assertEqual(redacted_check_log(0), (without_field / f'runs/fixture/{check}.log').read_bytes())
        self.assertTrue((without_field / 'runs/fixture/project-state.log').is_file())
        imported = self.reports / 'ci/10/linux'
        shutil.copytree(self.destination, imported)
        reference = archive_evidence(self.root, 'M0-001', imported / 'test-report.json', imported)
        self.assertEqual(expected, read_json(self.root / reference['path'])['field_observations'][check])

    def make_candidate(self, report):
        candidate = self.reports / 'runs/fixture/candidate'
        identity = {'commit': report['source']['git_head'], 'inputs_sha256': report['source']['sha256']}

        def save(name, contents):
            self.write('runs/fixture/candidate/' + name, contents)
            return {'path': name, 'sha256': digest(contents)}

        installer = save('installer/Synthetic setup.exe', b'synthetic installer bytes')
        host = {'product_type': 1, 'arch': 'x64', 'webview2': 'fixture'}
        observation = {**identity, 'installer_sha256': installer['sha256'], 'operator': 'synthetic',
                       'observed_at': '2026-01-01T12:00:00Z', 'clean_install': 'passed', 'upgrade': 'passed'}
        windows = [
            save(f'{build}.json', json_bytes({**observation, 'host': {**host, 'family': family, 'build': build}}))
            for build, family in [(19045, 'Windows 10'), (22631, 'Windows 11')]
        ]
        performance = save('performance.json', json_bytes({
            **observation,
            'host': dict(host, family='Windows 11', build=22631, cpu='fixture', ram='fixture',
                         display_scale='fixture', refresh_rate='fixture'),
            'cold_first_frame_ms': [1000] * 20, 'warm_first_frame_ms': [1000] * 20,
            'update_to_present_ms': [200] * 72000, 'private_bytes_per_minute': [1024] * 120,
            'final_bar_presented': True,
        }))
        notices = [save('THIRD_PARTY_NOTICES', b'Synthetic package copyright and notices.\r\n'),
                   save('third-party-licenses/fixture-lib/LICENSE-MIT', b'Synthetic MIT license text.\n')]
        manifest = dict(identity, installer=installer, windows_observations=windows, performance=performance,
                        third_party_licenses=notices, webview2='downloadBootstrapper')
        save('build-manifest.json', json_bytes(manifest))
        self.add_result(report, 'release-artifacts')
        policy = load_project(self.root)['spec']['acceptance']['performance']
        report['candidate_artifacts'] = release_artifacts(candidate.parent, report['source'], policy)
        return candidate, manifest

    def test_present_notice_must_be_named_in_the_accepted_manifest(self):
        self.add_catalog_checks('release-artifacts')
        report = self.hosted_report(system='Windows', event='workflow_dispatch')
        candidate, manifest = self.make_candidate(report)
        self.assertTrue((candidate / 'THIRD_PARTY_NOTICES').is_file())
        policy = load_project(self.root)['spec']['acceptance']['performance']
        for declaration in ({'third_party_licenses': []}, {}):
            without_notices = {key: value for key, value in manifest.items() if key != 'third_party_licenses'}
            (candidate / 'build-manifest.json').write_bytes(json_bytes({**without_notices, **declaration}))
            for hosted in (False, True):
                with self.subTest(declaration=declaration, hosted=hosted), self.assertRaisesRegex(ProjectError, 'third-party'):
                    release_artifacts(candidate.parent, report['source'], policy, hosted=hosted)

    def test_bound_candidate_remains_compatible_with_hosted_release_readiness(self):
        self.add_catalog_checks('release-artifacts', 'configured-model-session')
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['checks'].append('configured-model-session')
        self.save_ledger(ledger)
        report = self.hosted_report(system='Windows', event='workflow_dispatch')
        candidate, manifest = self.make_candidate(report)
        session = self.record_session(report, deployment='cloud')
        report['selection'] = {'profile': 'release', 'task_id': None, 'checks': [c['id'] for c in report['checks']]}
        report['integration_validation'] = integration_validation(report['checks'])
        self.save_report(report)
        (candidate / 'private-profile.json').write_bytes(self.sentinel)
        self.write('runs/fixture/session-dump.json', self.sentinel)
        self.write('runs/fixture/product-profile/config.json', self.sentinel)

        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        downloaded = self.reports / 'ci/10/windows'
        shutil.copytree(self.destination, downloaded)
        published = downloaded / 'runs/fixture/candidate'
        paths = ['build-manifest.json', manifest['installer']['path'], manifest['performance']['path'],
                 *(ref['path'] for ref in manifest['windows_observations'] + manifest['third_party_licenses'])]
        self.assertEqual(set(paths), {path.relative_to(published).as_posix()
                                      for path in published.rglob('*') if path.is_file()})
        for relative in paths:
            self.assertEqual((candidate / relative).read_bytes(), (published / relative).read_bytes())
        self.assertEqual(session.read_bytes(), (downloaded / 'runs/fixture' / session.name).read_bytes())
        self.assertFalse((downloaded / 'runs/fixture/session-dump.json').exists())
        self.assertFalse((downloaded / 'runs/fixture/product-profile').exists())
        results = {check['id']: check for check in report['checks']}
        project = load_project(self.root)
        project['checks'] = {key: {'status': 'implemented', 'command': result['command']}
                             for key, result in results.items()}
        self.assertEqual(report['candidate_artifacts'], verified_candidate_files(
            self.root, project, report, results, report['source'], downloaded / '..' / 'windows' / 'test-report.json',
            downloaded, report['source']['git_head'],
        ))
        reference = archive_evidence(self.root, 'M0-001', downloaded / 'test-report.json', downloaded)
        receipt = read_json(self.root / reference['path'])
        self.assertIn('configured-model-session', [item['id'] for item in receipt['checks']])

    def test_rejects_candidate_reference_escaping_its_directory(self):
        self.add_catalog_checks('release-artifacts')
        report = self.hosted_report(system='Windows', event='workflow_dispatch')
        candidate, manifest = self.make_candidate(report)
        self.write('runs/fixture/private.exe', self.sentinel)
        for index, path in enumerate(['../private.exe', r'..\private.exe', 'C:/private.exe', '//server/private.exe']):
            with self.subTest(path=path):
                manifest['installer']['path'] = path
                contents = json_bytes(manifest)
                (candidate / 'build-manifest.json').write_bytes(contents)
                report['candidate_artifacts']['manifest_sha256'] = digest(contents)
                self.save_report(report)
                with self.assertRaisesRegex(PublicationError, 'Invalid artifact path'):
                    stage_artifacts(self.root, self.destination.with_name(f'case-{index}'), self.env)

    def test_refuses_symlinked_candidate_directory_even_with_matching_bytes(self):
        self.add_catalog_checks('release-artifacts')
        report = self.hosted_report(system='Windows', event='workflow_dispatch')
        candidate, _ = self.make_candidate(report)
        self.save_report(report)
        original = candidate / 'installer'
        outside = original.rename(self.destination.with_name('outside'))
        try:
            original.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'Directory symlinks are unavailable: {error}')
        with self.assertRaisesRegex(PublicationError, 'ordinary directories'):
            stage_artifacts(self.root, self.destination, self.env)

    def failed_candidate(self):
        self.add_catalog_checks('release-artifacts')
        report = self.hosted_report(system='Windows', event='workflow_dispatch')
        candidate, manifest = self.make_candidate(report)
        report['checks'][-1].update(status='failed', exit_code=1)
        report.update(status='failed', candidate_artifacts=None)
        return report, candidate, manifest

    def replace_candidate_record(self, candidate, ref, record):
        contents = json_bytes(record)
        (candidate / ref['path']).write_bytes(contents)
        ref['sha256'] = digest(contents)

    def test_failed_candidate_retains_exact_installer_notices_and_safe_measurements(self):
        report, candidate, manifest = self.failed_candidate()
        ref = manifest['performance']
        metrics = read_json(candidate / ref['path'])
        windows = manifest['windows_observations'][0]
        install = read_json(candidate / windows['path'])
        policy = load_project(self.root)['spec']['acceptance']['performance']

        cases = [
            ('slow', [500] * 72000, 'passed', 'no passing bound performance'),
            ('interrupted', [170, 250], 'passed', 'Incomplete or invalid performance series'),
            ('installation', [200] * 72000, 'failed', 'lacks a passing installation and upgrade'),
        ]
        for name, samples, install_status, reason in cases:
            with self.subTest(name=name):
                metrics['update_to_present_ms'] = samples
                metrics['final_bar_presented'] = name != 'interrupted'
                self.replace_candidate_record(candidate, ref, metrics)
                upgrade = 'passed' if install_status == 'passed' else 'not_run'
                self.replace_candidate_record(candidate, windows, dict(install, clean_install=install_status, upgrade=upgrade))
                (candidate / 'build-manifest.json').write_bytes(json_bytes(manifest))
                self.save_report(report)
                destination = self.destination.with_name(name)
                self.assertTrue(stage_artifacts(self.root, destination, self.env))
                published = destination / 'runs/fixture/candidate'
                files = [path for path in candidate.rglob('*') if path.is_file()]
                for path in files:
                    self.assertEqual(path.read_bytes(), (published / path.relative_to(candidate)).read_bytes())
                final = read_json(destination / 'test-report.json')
                self.assertIsNone(final['candidate_artifacts'])
                self.assertEqual('failed', final['checks'][-1]['status'])
                with self.assertRaisesRegex(ProjectError, reason):
                    release_artifacts(published.parent, report['source'], policy, hosted=True)

    def test_failed_candidate_omits_unqualified_observations_and_keeps_verified_payload(self):
        report, candidate, manifest = self.failed_candidate()
        windows = manifest['windows_observations'][0]
        failed = read_json(candidate / windows['path'])
        failed.update(clean_install='failed', raw_failure=self.sentinel.decode())
        self.replace_candidate_record(candidate, windows, failed)
        ref = manifest['performance']
        metrics = read_json(candidate / ref['path'])
        metrics['update_to_present_ms'] = [200, self.sentinel.decode()]
        self.replace_candidate_record(candidate, ref, metrics)
        (candidate / 'build-manifest.json').write_bytes(json_bytes(manifest))
        self.save_report(report)

        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        published = self.destination / 'runs/fixture/candidate'
        expected = [manifest['installer'], manifest['windows_observations'][1], *manifest['third_party_licenses']]
        self.assertEqual({ref['path'] for ref in expected},
                         {path.relative_to(published).as_posix() for path in published.rglob('*') if path.is_file()})
        for ref in expected:
            self.assertEqual(ref['sha256'], digest((published / ref['path']).read_bytes()))

    def test_candidate_manifest_rejects_unknown_and_shadowed_fields_before_staging(self):
        report, candidate, manifest = self.failed_candidate()
        raw = self.sentinel.decode()
        variants = {
            'unknown': json_bytes(dict(manifest, raw_profile=raw)),
            'reference': json_bytes(dict(manifest, installer=dict(manifest['installer'], raw_profile=raw))),
            'shadowed': b'{"installer":' + json_bytes({'raw_profile': raw}) + b',' + json_bytes(manifest)[1:],
        }
        for name, contents in variants.items():
            with self.subTest(name=name):
                (candidate / 'build-manifest.json').write_bytes(contents)
                self.save_report(report)
                published = self.destination.with_name(name)
                self.assertTrue(stage_artifacts(self.root, published, self.env))
                self.assertTrue((published / 'test-report.json').is_file())
                self.assertFalse((published / 'runs/fixture/candidate').exists())

    def test_chart_producer_uses_harness_run_and_reports_only_synthetic_outputs(self):
        prepare_reports(self.root)

        def run(command, *, cwd, env, check):
            output = self.reports / 'chart-history/runs/fixture'
            self.assertEqual(output, Path(env['SCOPEHUB_HISTORY_REPORT_DIR']))
            if 'playwright' in command:
                self.assertEqual(['--trace', 'off'], command[-2:])
                self.write('chart-history/runs/fixture/browser-results.json', b'{}\n')
                self.write('chart-history/runs/fixture/browser/history-startup/trace.zip', self.sentinel)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(check_chart_history, 'ROOT', self.root), \
             patch.object(check_chart_history.shutil, 'which', side_effect=lambda name: name), \
             patch.object(check_chart_history.subprocess, 'run', side_effect=run), \
             patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'SCOPEHUB_RUN_DIR': str(self.reports / 'runs/fixture')}), \
             redirect_stdout(StringIO()), self.assertRaises(SystemExit) as stop:
            check_chart_history.main()
        self.assertEqual(0, stop.exception.code)
        result = read_json(self.reports / 'chart-history/runs/fixture/summary.json')
        self.assertEqual([{'path': '.reports/chart-history/runs/fixture/browser-results.json', 'sha256': digest(b'{}\n')}],
                         result['artifacts'])
