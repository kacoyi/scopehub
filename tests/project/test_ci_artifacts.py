"""Exercise the hosted publication boundary with isolated synthetic evidence."""

from contextlib import redirect_stderr, redirect_stdout
import base64
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from scripts import check_chart_history, ci_artifacts, verification_evidence
from scripts.check import PRIVATE_CHECK_LOGS, redacted_check_log
from scripts.ci_artifacts import SCOPE_FILE, PublicationError, prepare_reports, stage_artifacts
from scripts.diagnostic_archive import DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic
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

    def stage_job(self, environment):
        runner = self.destination.parent
        summary = runner / 'step-summary.md'
        outputs = runner / 'outputs'
        messages = StringIO()
        with (patch.dict(os.environ, {
            **environment, 'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': str(runner),
            'GITHUB_OUTPUT': str(outputs), 'GITHUB_STEP_SUMMARY': str(summary),
        }), patch.object(ci_artifacts, 'ROOT', self.root),
              patch.object(sys, 'argv', ['ci_artifacts.py', 'stage']),
              redirect_stdout(messages), redirect_stderr(messages)):
            ci_artifacts.main()
        staged = dict(line.split('=', 1) for line in outputs.read_text(encoding='utf-8').splitlines())
        return staged, summary, messages.getvalue()

    def test_stage_job_publishes_a_small_timing_record_and_summary(self):
        report = self.hosted_report()
        for check in report['checks']:
            check['duration_seconds'] = 3
        report['checks'][0]['command'].append(self.sentinel.decode().strip())
        report['selection'] = {'profile': 'full', 'task_id': None,
                               'checks': [check['id'] for check in report['checks']]}
        report['generated_at'] = '2026-11-21T00:00:05Z'
        report['timing'] = {'elapsed_seconds': 5}
        self.save_report(report)
        self.write('verification-timing.json', self.sentinel)

        outputs, summary, _ = self.stage_job(self.env)
        record = Path(outputs['timing_path'])
        self.assertEqual('verification-timing.json', record.name)
        self.assertEqual(record.read_bytes(), (Path(outputs['path']) / record.name).read_bytes())
        self.assertNotIn(self.sentinel.rstrip(), record.read_bytes())
        self.assertNotIn(b'Synthetic test fixture, not product validation.', record.read_bytes())
        self.assertNotIn(b'"python_executable"', record.read_bytes())
        summary_text = summary.read_text(encoding='utf-8')
        self.assertIn('harness', summary_text.lower())
        self.assertNotIn(self.sentinel.decode().strip(), summary_text)
        self.assertFalse((Path(outputs['path']) / SCOPE_FILE).exists())

    def scope_env(self, *, profile='full', reason='change_requires_full', count=1):
        decision = {'profile': profile, 'reason_code': reason, 'base': 'b' * 40,
                    'checkout': self.env['GITHUB_SHA'], 'change_count': count}
        return {**self.env, 'SCOPEHUB_SCOPE_OUTCOME': 'success', 'SCOPEHUB_SCOPE_PROFILE': profile,
                'SCOPEHUB_SCOPE_BASE': decision['base'], 'SCOPEHUB_SCOPE_DECISION': json.dumps(decision)}

    def test_windows_bundle_contains_typed_scope_and_retains_the_original_failed_report(self):
        report = self.hosted_report(system='Windows', event='push')
        report['status'] = 'failed'
        report['selection'] = {'profile': 'tooling', 'task_id': None,
                               'checks': [row['id'] for row in report['checks']]}
        self.save_report(report)
        source = self.write(SCOPE_FILE, self.sentinel)
        env = self.scope_env(profile='tooling', reason='reviewed_changes')

        outputs, _, _ = self.stage_job(env)
        self.assert_core_artifacts_retained(outputs, report)
        staged = Path(outputs['path'])
        scope = read_json(staged / SCOPE_FILE)
        self.assertEqual({'kind': 'scopehub-verification-scope-v1',
                          **json.loads(env['SCOPEHUB_SCOPE_DECISION']),
                          'report_sha256': digest((staged / 'test-report.json').read_bytes())}, scope)
        self.assertLess((staged / SCOPE_FILE).stat().st_size, 512)
        self.assertEqual(self.sentinel, source.read_bytes())
        self.assertNotIn(b'reviewed_changes', Path(outputs['timing_path']).read_bytes())

    def test_full_scope_provenance_distinguishes_risk_unavailable_comparison_and_selector_failure(self):
        report = self.hosted_report()
        report['selection'] = {'profile': 'full', 'task_id': None,
                               'checks': [row['id'] for row in report['checks']]}
        self.save_report(report)
        risk = self.scope_env(count=12)
        unavailable = self.scope_env(reason='comparison_unavailable', count=None)
        unavailable['SCOPEHUB_SCOPE_DECISION'] = json.dumps({
            **json.loads(unavailable['SCOPEHUB_SCOPE_DECISION']), 'base': None,
        })
        cases = (
            ('risk', risk, 'change_requires_full', 12),
            ('unavailable', unavailable, 'comparison_unavailable', None),
            ('empty', self.scope_env(reason='no_changes', count=0), 'no_changes', 0),
            ('failed', {**risk, 'SCOPEHUB_SCOPE_OUTCOME': 'failure', 'SCOPEHUB_SCOPE_DECISION': self.sentinel.decode()},
             'selector_failed', None),
            ('no-output', {**risk, 'SCOPEHUB_SCOPE_PROFILE': 'records', 'SCOPEHUB_SCOPE_DECISION': ''},
             'selector_output_unavailable', None),
            ('unknown', {**risk, 'SCOPEHUB_SCOPE_PROFILE': 'unknown'}, 'selector_output_unavailable', None),
        )
        for name, environment, reason, count in cases:
            with self.subTest(name=name):
                destination = self.destination.with_name(name)
                self.assertTrue(stage_artifacts(self.root, destination, environment))
                path = destination / SCOPE_FILE
                scope = read_json(path)
                self.assertEqual(('full', reason, count), (scope['profile'], scope['reason_code'], scope['change_count']))
                self.assertEqual('b' * 40 if name in {'risk', 'empty'} else None, scope['base'])
                self.assertNotIn(self.sentinel.rstrip(), path.read_bytes())

    def test_scope_provenance_rejects_untyped_unbound_or_unexecuted_decisions(self):
        report = self.hosted_report()
        report['selection'] = {'profile': 'full', 'task_id': None,
                               'checks': [row['id'] for row in report['checks']]}
        self.save_report(report)
        env = self.scope_env()
        decision = json.loads(env['SCOPEHUB_SCOPE_DECISION'])
        mutations = (
            {'additional': self.sentinel.decode()}, {'reason_code': self.sentinel.decode()},
            {'reason_code': 'reviewed_changes'}, {'checkout': 'a' * 40}, {'base': '1' * 40}, {'base': 9},
            {'change_count': True}, {'change_count': -1}, {'change_count': 2**31}, {'base': None},
        )
        invalid = [json.dumps({**decision, **mutation}) for mutation in mutations]
        invalid.extend(('[]', '{"profile":"full","profile":"full"}', ' ' * 1025))
        for raw in invalid:
            with self.subTest(raw=raw[:120]), self.assertRaisesRegex(PublicationError, '[Vv]erification scope'):
                stage_artifacts(self.root, self.destination, {**env, 'SCOPEHUB_SCOPE_DECISION': raw})
            self.assertFalse(self.destination.exists())

        narrow = self.scope_env(profile='tooling', reason='reviewed_changes')
        with self.assertRaisesRegex(PublicationError, 'executed harness selection'):
            stage_artifacts(self.root, self.destination, narrow)
        self.assertFalse(self.destination.exists())

    def test_scheduled_job_does_not_require_a_scope_selector_output(self):
        report = self.hosted_report(event='schedule')
        self.save_report(report)
        env = {**self.env, 'SCOPEHUB_SCOPE_OUTCOME': 'skipped'}
        self.assertTrue(stage_artifacts(self.root, self.destination, env))
        self.assertFalse((self.destination / SCOPE_FILE).exists())

    def test_stage_job_reports_missing_harness_separately_from_evidence(self):
        prepare_reports(self.root)
        outputs, summary, _ = self.stage_job({
            'GITHUB_REPOSITORY': 'fixture/scopehub', 'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '2',
            'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_SHA': 'a' * 40, 'RUNNER_OS': 'Linux',
        })
        self.assertNotIn('path', outputs)
        self.assertTrue(Path(outputs['timing_path']).is_file())
        self.assertIn('harness', summary.read_text(encoding='utf-8').lower())

    def assert_core_artifacts_retained(self, outputs, report):
        staged = Path(outputs['path'])
        for relative in ('test-report.json', 'runs/fixture/test-report.json'):
            self.assertEqual(json_bytes(report), (staged / relative).read_bytes())
        for check in report['checks']:
            relative = check['log'].removeprefix('.reports/')
            self.assertEqual((self.reports / relative).read_bytes(), (staged / relative).read_bytes())

    def test_optional_summary_write_failure_keeps_core_and_timing_outputs(self):
        report = self.hosted_report()
        self.save_report(report)
        (self.destination.parent / 'step-summary.md').mkdir()

        outputs, _, messages = self.stage_job(self.env)

        self.assert_core_artifacts_retained(outputs, report)
        timing = Path(outputs['timing_path'])
        self.assertEqual(timing.read_bytes(), (Path(outputs['path']) / timing.name).read_bytes())
        self.assertIn('warning', messages.lower())
        self.assertIn('summary', messages.lower())

    def test_optional_compact_timing_failure_keeps_core_output(self):
        report = self.hosted_report()
        self.save_report(report)

        with patch.object(ci_artifacts, 'stage_timing', side_effect=OSError('Synthetic timing write failure')):
            outputs, _, messages = self.stage_job(self.env)

        self.assert_core_artifacts_retained(outputs, report)
        self.assertNotIn('timing_path', outputs)
        self.assertFalse((Path(outputs['path']) / 'verification-timing.json').exists())
        self.assertIn('warning', messages.lower())
        self.assertIn('timing', messages.lower())

    def test_only_current_named_logs_report_and_synthetic_browser_outputs_are_published(self):
        browser_checks = (
            'chart-history', 'ubuntu-preview', 'market-live-stream', 'market-quality-states',
            'market-workflow', 'market-worker-interruption', 'market-recovery-ui', 'market-recovery',
            'workspace-timeframe', 'desktop-workspace-transport',
        )
        self.add_catalog_checks(*browser_checks)
        report = self.hosted_report()
        for check in browser_checks:
            self.add_result(report, check)
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
            'runs/fixture/market-quality-states/results.json': b'{}\n',
            'runs/fixture/market-quality-states/browser/market-quality-source/synthetic-market-quality.png': b'synthetic PNG',
            'runs/fixture/market-workflow/results.json': b'{}\n',
            'runs/fixture/workspace-timeframe/results.json': b'{}\n',
            'runs/fixture/desktop-workspace-transport/results.json': b'{}\n',
            'runs/fixture/market-workflow/browser/market-workflow-symbols/synthetic-market-workflow.png': b'synthetic PNG',
            'runs/fixture/market-worker-interruption/results.json': b'{}',
            'runs/fixture/market-recovery-ui/results.json': b'{}',
            'runs/fixture/market-recovery/results.json': b'{}',
            'runs/fixture/market-recovery/browser/market-closed-bar-repair-verified/verified-closed-bar-repair.png': b'synthetic PNG',
            'runs/fixture/market-recovery/browser/market-closed-bar-repair-gap/unresolved-closed-bar-gap.png': b'synthetic PNG',
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
            'runs/fixture/market-quality-states/browser/market-quality-source/trace.zip',
            'runs/fixture/market-workflow/browser/market-workflow-symbols/private.png',
            'runs/fixture/market-worker-interruption/browser/worker-interruption-market/worker-data.json',
            'runs/fixture/market-recovery/browser/market-closed-bar-repair-gap/worker-data.json',
            'runs/fixture/market-recovery/browser/other-case/verified-closed-bar-repair.png',
            'runs/fixture/workspace-timeframe/browser/workspace-restore/workspace.sqlite3',
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

    def test_downloaded_report_requires_its_isolated_checkout_artifacts(self):
        report = self.hosted_report()
        self.save_report(report)
        self.assertIn('path', archive_evidence(self.root, 'M0-001', self.reports / 'test-report.json'))
        self.assertTrue(stage_artifacts(self.root, self.destination, self.env))
        with self.assertRaisesRegex(ProjectError, 'CI evidence requires a checkout-local report'):
            archive_evidence(self.root, 'M0-001', self.destination / 'test-report.json')
        imported = self.reports / 'ci/10/linux'
        shutil.copytree(self.destination, imported)
        with self.assertRaisesRegex(ProjectError, 'Specify the downloaded CI artifact root'):
            archive_evidence(self.root, 'M0-001', imported / 'test-report.json')
        self.assertIn('path', archive_evidence(self.root, 'M0-001', imported / 'test-report.json', imported))

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

    def test_observed_session_rejects_a_shadowed_private_value(self):
        self.add_catalog_checks('configured-model-session')
        report = self.hosted_report()
        check = 'configured-model-session'
        path = self.record_session(report)
        original = path.read_bytes()
        result = next(item for item in report['checks'] if item['id'] == check)
        for prefix, contents in (
            (b'{', b'{"provider":"synthetic private body",'),
            (b'"billing":{', b'"billing":{"currency":"synthetic private body",'),
        ):
            with self.subTest(prefix=prefix):
                compact = json.dumps(json.loads(original), separators=(',', ':')).encode()
                raw = compact.replace(prefix, contents, 1)
                path.write_bytes(raw)
                result['observation']['evidence_sha256'] = digest(raw)
                self.save_report(report)
                with self.assertRaisesRegex(ProjectError, 'Duplicate fields'):
                    collect_session(path.parent, check, report['source'])
                with self.assertRaisesRegex(ProjectError, 'Duplicate fields'):
                    stage_artifacts(self.root, self.destination, self.env)
                self.assertFalse((self.destination / 'test-report.json').exists())
                shutil.rmtree(self.destination, ignore_errors=True)

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
        manifest_relative = f'{directory}/observation.json'
        local_locator = diagnostic_locator(self.reports / 'test-report.json')
        self.assertEqual([manifest_relative], resolve_diagnostic(local_locator, self.reports)['verified_observations'])

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
        missing_locator = diagnostic_locator(without_field / 'test-report.json')
        with self.assertRaisesRegex(DiagnosticArchiveError, 'Missing field observation'):
            resolve_diagnostic(missing_locator, without_field)
        self.assertEqual(redacted_check_log(0), (without_field / f'runs/fixture/{check}.log').read_bytes())
        self.assertTrue((without_field / 'runs/fixture/project-state.log').is_file())
        imported = self.reports / 'ci/10/linux'
        shutil.copytree(self.destination, imported)
        self.assertEqual([manifest_relative], resolve_diagnostic(local_locator, imported)['verified_observations'])
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
