"""Exercise false completion, stale decisions and delivery feedback with isolated records."""

from contextlib import redirect_stdout
from io import StringIO
import json
import shutil
import base64
import subprocess
import sys
from unittest.mock import patch

from scripts import check as harness
from scripts.project import (
    ProjectError, archive_evidence, digest, json_bytes, load_project, product_progress, readiness,
    read_json, review_history, task_context, workfront, verified_main, source_identity,
)
from tests.project.test_continuity import ProjectFixture
from tests.project.fixtures import task_by_id
from scripts.verification_evidence import integration_validation, release_artifacts, collect_session, performance_verdict, field_observation


class DeliveryTests(ProjectFixture):
    def edit_decision(self):
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['decision'] += ' Changed after validation.'
        path.write_bytes(json_bytes(decision))

    def run_harness(self, args=(), before_check=None, system='Linux'):
        def execute(name, command, env, run_dir, timeout):
            if before_check:
                before_check(name)
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic harness fixture, not product acceptance.\n')
            return {'id': name, 'status': 'passed', 'command': command, 'exit_code': 0,
                    'log': log.relative_to(self.root).as_posix(), 'log_sha256': digest(log.read_bytes())}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=execute),
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(harness.platform, 'system', return_value=system),
              patch.object(harness.platform, 'machine', return_value='x86_64'),
              patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        return stopped.exception.code, read_json(self.root / '.reports/test-report.json')

    def commit_fixture(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, capture_output=True, check=True)
        subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve fixture history'],
                       cwd=self.root, capture_output=True, check=True)
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root,
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_decision_change_invalidates_status_and_archival(self):
        report = self.report()
        self.edit_decision()
        state = workfront(self.root)
        self.assertTrue(state['verification']['matches_current_execution_inputs'])
        self.assertFalse(state['verification']['matches_current_project_definitions'])
        self.assertIsNone(state['milestones'])
        with self.assertRaisesRegex(ProjectError, 'definitions or decisions changed'):
            archive_evidence(self.root, 'M0-001', report)

    def test_task_change_hides_stale_milestone_results(self):
        self.report()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['acceptance'].append('New observable outcome.')
        self.save_ledger(ledger)
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNone(state['milestones'])

    def test_malformed_report_cannot_appear_current_or_support_completion(self):
        path = self.report()
        report = read_json(path)
        report['source_unchanged'] = 'true'
        path.write_bytes(json_bytes(report))
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNotNone(state['verification']['report_error'])
        with self.assertRaisesRegex(ProjectError, 'HarnessReport'):
            archive_evidence(self.root, 'M0-001', path)

    def test_commit_after_dirty_verification_does_not_create_release_proof(self):
        self.report()
        self.commit_fixture()
        result = readiness(self.root)
        self.assertTrue(result['verification']['current'])
        self.assertIn('Release verification must name the same clean committed revision.', result['blockers'])

    def completed_release_fixture(self):
        # An isolated all-implemented project exercises the eventual release path.
        # Its synthetic logs and outcomes are never production acceptance evidence.
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        for check in catalog['checks']:
            check.update(status='implemented', command=['{python}', '-c', "print('synthetic acceptance')"])
        catalog['required_change_checks'] = [check['id'] for check in catalog['checks'] if check['cadence'] == 'change']
        path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        plan = task_by_id(ledger, 'M0-001')['plan']
        for task in ledger['tasks']:
            task['checks'] = [check['id'] for check in catalog['checks']]
            task['plan'] = plan
        self.save_ledger(ledger)
        report_path = self.report(system='Windows')
        completed = set()
        while len(completed) < len(ledger['tasks']):
            task = next(task for task in ledger['tasks'] if task['id'] not in completed
                        and set(task['depends_on']).issubset(completed))
            ref = archive_evidence(self.root, task['id'], report_path)
            task.update(status='done', evidence=[ref], next_action=None)
            completed.add(task['id'])
            ledger['focus_task_id'] = next((item['id'] for item in ledger['tasks'] if item['id'] not in completed), None)
            self.save_ledger(ledger)
        self.commit_fixture()
        report_path = self.report(system='Windows')
        report = read_json(report_path)
        report.update(
            selection={'profile': 'release', 'task_id': None, 'checks': [c['id'] for c in report['checks']]},
            native_requirement={'requested': True, 'satisfied': True},
            integration_validation=integration_validation(report['checks']),
        )
        run_dir = self.root / '.reports/runs/fixture'
        folder = run_dir / 'candidate'
        folder.mkdir()
        identity = {'commit': report['source']['git_head'], 'inputs_sha256': report['source']['sha256']}
        data = b'Synthetic installer data for tests.'
        (folder / 'fixture.exe').write_bytes(data)
        installer = {'path': 'fixture.exe', 'sha256': digest(data)}

        def save(name, value):
            content = json_bytes(value)
            (folder / name).write_bytes(content)
            return {'path': name, 'sha256': digest(content)}

        observations = []
        for name, build in [('Windows 10', 19045), ('Windows 11', 22631)]:
            host = {'family': name, 'product_type': 1, 'arch': 'x64', 'build': build, 'webview2': 'fixture'}
            observation = dict(
                identity,
                installer_sha256=installer['sha256'],
                host=host,
                operator='synthetic',
                observed_at='2026-01-01T12:00:00Z',
                clean_install='passed',
                upgrade='passed',
            )
            observations.append(save(str(build) + '.json', observation))
        host.update(cpu='fixture', ram='fixture', display_scale='fixture', refresh_rate='fixture')
        performance = save(
            'performance.json',
            dict(
                identity,
                host=host,
                installer_sha256=installer['sha256'],
                cold_first_frame_ms=[1000] * 20,
                warm_first_frame_ms=[1000] * 20,
                update_to_present_ms=[200] * 72000,
                private_bytes_per_minute=[1024] * 120,
                final_bar_presented=True,
            ),
        )
        save(
            'build-manifest.json',
            dict(identity, installer=installer, windows_observations=observations, performance=performance),
        )
        report['candidate_artifacts'] = release_artifacts(
            run_dir, report['source'], load_project(self.root)['spec']['acceptance']['performance']
        )
        report_path.write_bytes(json_bytes(report))
        return report_path, report

    def test_release_technical_pass_does_not_authorize_publication(self):
        self.completed_release_fixture()
        with patch('scripts.project.verified_main', return_value={'id': 'synthetic observed main run'}):
            result = readiness(self.root)
        self.assertEqual([], result['blockers'])
        self.assertTrue(result['technical_ready'])
        self.assertEqual('not_evaluated', result['authorization'])
        self.assertEqual('present_release_proposal', result['recommendation'])

    def test_bound_hosted_release_and_main_provenance(self):
        latest, report = self.completed_release_fixture()
        head = report['source']['git_head']
        base = self.root / '.reports/ci/402/windows'
        base.mkdir(parents=True)
        shutil.copytree(self.root / '.reports/runs', base / 'runs')
        report['ci'] = {
            'provider': 'github-actions',
            'repository': 'owner/repo',
            'run_id': '402',
            'run_attempt': '1',
            'event': 'workflow_dispatch',
            'head_sha': head,
            'checkout_sha': head,
            'run_url': 'https://github.com/owner/repo/actions/runs/402',
        }
        hosted = base / 'test-report.json'
        hosted.write_bytes(json_bytes(report))
        latest.write_bytes(json_bytes(dict(report, selection={'profile': 'fast', 'task_id': None})))
        with patch('scripts.project.verified_main', return_value={'id': 'main run'}):
            self.assertFalse(readiness(self.root)['technical_ready'])
            self.assertTrue(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate=head)[
                    'technical_ready'
                ]
            )
            self.assertFalse(readiness(self.root, report_path=hosted, candidate=head)['technical_ready'])
            self.assertFalse(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate='a' * 40)[
                    'technical_ready'
                ]
            )
            payload = base / 'runs/fixture/candidate/fixture.exe'
            payload.write_bytes(b'changed')
            self.assertFalse(
                readiness(self.root, report_path=hosted, artifact_root=base, candidate=head)[
                    'technical_ready'
                ]
            )
        self.assertEqual('fast', read_json(latest)['selection']['profile'])

    def test_release_requires_both_windows_families_and_a_measured_installer(self):
        path, report = self.completed_release_fixture()
        manifest_path = self.root / '.reports/runs/fixture/candidate/build-manifest.json'
        manifest = read_json(manifest_path)
        manifest['windows_observations'] = [manifest['windows_observations'][1]] * 2
        manifest_path.write_bytes(json_bytes(manifest))
        with patch('scripts.project.verified_main', return_value={'id': 'main run'}):
            self.assertFalse(readiness(self.root, report_path=path)['technical_ready'])

    def test_owner_rule_hash_includes_authority_and_excludes_supplementary_observations(self):
        path = self.root / 'contracts/reviews/example.json'
        path.parent.mkdir()
        path.write_bytes(json_bytes({'owner_direction': 'use A', 'screenshot': 1}))
        spec_path = self.root / 'engineering-contract-v1.json'
        spec = read_json(spec_path)
        spec['repository'] = {
            'owner_decision_inputs': [
                {'path': 'contracts/reviews/example.json', 'pointer': '/owner_direction'}
            ]
        }
        spec_path.write_bytes(json_bytes(spec))
        self.report()
        path.write_bytes(json_bytes({'owner_direction': 'use A', 'screenshot': 2}))
        self.assertTrue(workfront(self.root)['verification']['current'])
        path.write_bytes(json_bytes({'owner_direction': 'use B', 'screenshot': 2}))
        self.assertFalse(workfront(self.root)['verification']['current'])

    def test_modality_evidence_is_independent_of_mock_results_and_default_settings(self):
        directory = self.root / 'observations'
        directory.mkdir()
        source = {'git_head': 'b' * 40, 'sha256': 'a' * 64}
        from tests.contracts.test_qualification import observed_case

        original = {
            'qualification': observed_case(),
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'provider': 'fixture',
            'route': 'route',
            'data_revision': 'revision',
            'authorized_scope': 'fixture scope',
            'observed_at': '2026-11-18T12:00:00Z',
            'model': 'fixture',
            'deployment': 'cloud',
        }
        results = []
        self.assertEqual('not_evaluated', integration_validation(results)['cloud_models']['status'])
        results.append({'id': 'ai-replay-evaluations', 'status': 'passed'})
        mock = integration_validation(results)
        self.assertTrue(mock['mock_ai_evaluated'])
        self.assertEqual('not_evaluated', mock['cloud_models']['status'])
        for key, modality in [('configured-model-session', 'text'), ('configured-voice-session', 'voice')]:
            (directory / (key + '.observation.json')).write_bytes(
                json_bytes(dict(original, check=key, modality=modality))
            )
            observed = collect_session(directory, key, source)
            results.append({'id': key, 'status': 'passed', 'observation': observed})
            self.assertEqual(
                [r['observation'] for r in results if 'observation' in r],
                integration_validation(results)['cloud_models']['observations'],
            )
            with self.assertRaisesRegex(ProjectError, 'another revision'):
                collect_session(directory, key, dict(source, git_head='c' * 40))
        self.assertEqual('not_evaluated', integration_validation(results)['real_market_data']['status'])
        key = 'configured-model-session'
        filename = directory / (key + '.observation.json')
        filename.write_bytes(json_bytes(dict(original, check=key, modality='text', deployment='local')))
        local = integration_validation(
            [{'id': key, 'status': 'passed', 'observation': collect_session(directory, key, source)}]
        )
        self.assertEqual('observed', local['configured_models']['status'])
        self.assertEqual('not_evaluated', local['cloud_models']['status'])
        self.assertEqual('local', local['configured_models']['observations'][0]['deployment'])
        filename.write_bytes(json_bytes(dict(original, check=key, modality='text', deployment='unknown')))
        with self.assertRaisesRegex(ProjectError, 'local or cloud'):
            collect_session(directory, key, source)

    def test_client_family_name_does_not_qualify_a_server_build(self):
        from scripts.verification_evidence import windows_client
        host = {'family': 'Windows 11', 'build': 26100, 'arch': 'x64', 'webview2': 'fixture'}
        for wrong in (None, True, 2, 3):
            with self.subTest(product_type=wrong), self.assertRaises(ProjectError):
                windows_client(dict(host, product_type=wrong))
        self.assertEqual('Windows 11', windows_client(dict(host, product_type=1)))

    def test_memory_and_latency_verdicts(self):
        profile = {'initial_bars_to_chart_p95_ms': 1000, 'chart_update_p95_ms': 200}
        data = {
            'cold_first_frame_ms': [1000] * 20,
            'warm_first_frame_ms': [999] * 20,
            'update_to_present_ms': [200] * 72000,
            'private_bytes_per_minute': [40 * 1024**2] * 120,
            'final_bar_presented': True,
        }
        self.assertEqual('passed', performance_verdict(data, profile)['status'])
        data['private_bytes_per_minute'] = [i * 1024**2 for i in range(120)]
        self.assertEqual('failed', performance_verdict(data, profile)['status'])
        data['private_bytes_per_minute'] = [1024**2] * 110 + [100 * 1024**2] * 10
        self.assertEqual('inconclusive', performance_verdict(data, profile)['status'])
        data['cold_first_frame_ms'][-2:] = [1001, 1002]
        self.assertEqual('failed', performance_verdict(data, profile)['status'])

    def test_main_requires_current_official_main_push_and_candidate_release_run(self):
        path = self.root / 'project/branch-protection.json'
        path.write_bytes(json_bytes({'repository': 'owner/repo', 'branch': 'main'}))
        head = 'b' * 40
        run = {
            'head_branch': 'main',
            'head_sha': head,
            'id': 4,
            'run_attempt': 1,
            'event': 'push',
            'path': '.github/workflows/verify.yml',
            'status': 'completed',
            'conclusion': 'success',
        }

        def command(argv, root):
            if 'git/ref' in ' '.join(argv):
                return head
            if any(x.endswith('/attempts/1') for x in argv):
                return json.dumps(dict(run, id=5, event='workflow_dispatch'))
            return json.dumps({'workflow_runs': [run]})

        with (
            patch('scripts.ci.github_identity', return_value='owner/repo'),
            patch('scripts.ci.command', side_effect=command),
        ):
            self.assertEqual(
                4,
                verified_main(
                    self.root, head, {'repository': 'owner/repo', 'run_id': '5', 'run_attempt': '1'}
                )['id'],
            )
            with self.assertRaisesRegex(ProjectError, 'not the current main'):
                verified_main(self.root, 'c' * 40, None)
            run['event'] = 'pull_request'
            with self.assertRaisesRegex(ProjectError, 'push verification'):
                verified_main(self.root, head, None)

    def test_graphical_ubuntu_requires_retained_screenshots_and_reports_pending_modalities(self):
        self.commit_fixture()
        source = source_identity(self.root)
        key = 'ubuntu-workspace-observation'
        directory = self.root / '.reports/observations' / key
        directory.mkdir(parents=True)
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XxGCPgAAAABJRU5ErkJggg=='
        )
        (directory / 'fixture.png').write_bytes(image)
        names = [
            'history',
            'live',
            'reconnect',
            'folder-refresh',
            'undo-and-restore',
            'layout',
            'indicator',
            'mock-ai',
            'evidence-selection',
            'decision-review',
            'local-bridge',
        ]
        obs = {
            'kind': key,
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'operator': 'synthetic operator',
            'observed_at': '2026-01-01T12:00:00Z',
            'surface': 'ubuntu-graphical-workspace',
            'actions': dict.fromkeys(names, {'status': 'passed', 'observed': 'Observed synthetic test outcome.'}),
            'external_sessions': dict.fromkeys(['text', 'voice', 'cli', 'desktop'], 'pending'),
            'screenshots': [{'path': 'fixture.png', 'sha256': digest(image)}],
        }
        (directory / 'observation.json').write_bytes(json_bytes(obs))
        result = field_observation(self.root, key)
        self.assertEqual(obs['external_sessions'], result['external_sessions'])
        failed = dict(obs, actions={**obs['actions'], 'reconnect': {'status': 'failed', 'observed': 'No chart'}})
        (directory / 'observation.json').write_bytes(json_bytes(failed))
        with self.assertRaisesRegex(ProjectError, 'Field action did not pass: reconnect'):
            field_observation(self.root, key)
        (directory / 'observation.json').write_bytes(json_bytes(obs))
        (directory / 'fixture.png').unlink()
        with self.assertRaisesRegex(ProjectError, 'Missing or changed'):
            field_observation(self.root, key)

    def test_physical_trial_requires_matching_ubuntu_receipt_and_native_surface(self):
        self.commit_fixture()
        source = source_identity(self.root)
        key = 'physical-windows-workspace'
        directory = self.root / '.reports/observations' / key
        directory.mkdir(parents=True)
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XxGCPgAAAABJRU5ErkJggg=='
        )
        (directory / 'fixture.png').write_bytes(image)
        actions = [
            'unconfigured-entry',
            'sample',
            'instrument',
            'timeframe',
            'style',
            'resize',
            'crosshair',
            'data-inspection',
            'exit',
        ]
        win = {
            'kind': key,
            'commit': source['git_head'],
            'inputs_sha256': source['sha256'],
            'operator': 'test operator',
            'observed_at': '2026-11-18T12:01:00Z',
            'host': {'family': 'Windows 11', 'product_type': 1, 'build': 22631, 'arch': 'x64', 'webview2': 'fixture'},
            'surface': 'physical-dual-boot-native-webview2',
            'screenshots': [{'path': 'fixture.png', 'sha256': digest(image)}],
            'actions': dict.fromkeys(actions, {'status': 'passed', 'observed': 'Synthetic fixture outcome.'}),
            'ubuntu_reviewed_commit': 'c' * 40,
        }
        obs_path = directory / 'observation.json'
        obs_path.write_bytes(json_bytes(win))
        ubuntu_path = self.root / '.reports/ubuntu-receipt.json'
        receipt = {
            'source': dict(source, git_head='c' * 40),
            'field_observations': {'ubuntu-workspace-observation': {}},
        }
        ubuntu_path.write_bytes(json_bytes(receipt))
        ledger = {
            'tasks': {'M1-012': {'status': 'done', 'evidence': [{'path': '.reports/ubuntu-receipt.json'}]}}
        }
        with patch('scripts.verification_evidence.load_project', return_value=ledger):
            self.assertEqual(key, field_observation(self.root, key)['kind'])
            obs_path.write_bytes(json_bytes(dict(win, surface='hosted-build-only')))
            with self.assertRaisesRegex(ProjectError, 'physical native WebView2'):
                field_observation(self.root, key)
            obs_path.write_bytes(json_bytes(win))
            receipt['source']['sha256'] = 'd' * 64
            ubuntu_path.write_bytes(json_bytes(receipt))
            with self.assertRaisesRegex(ProjectError, 'matching Ubuntu'):
                field_observation(self.root, key)

    def test_task_only_observations_do_not_enter_change_or_release_selection(self):
        project = load_project(self.root)
        key = 'one-observed-session'
        project['checks'][key] = {'id': key, 'status': 'implemented', 'cadence': 'task'}
        project['tasks']['M0-001']['checks'].append(key)
        self.assertNotIn(key, harness.select_checks(project))
        self.assertNotIn(key, harness.select_checks(project, profile='release'))
        self.assertIn(key, harness.select_checks(project, task_id='M0-001'))

    def test_single_stream_and_quality_checks_cannot_pass_full_market_acceptance(self):
        project = load_project(self.root)
        project['catalog']['acceptance']=[{'requirement':'all instruments and timeframes','checks':['market-workflow']}]
        partial=[{'id':name,'status':'passed'} for name in ['market-live-stream','market-quality-states']]
        self.assertEqual('blocked',product_progress(project,partial)[1][0]['status'])
        self.assertEqual('passed',product_progress(project,partial+[{'id':'market-workflow','status':'passed'}])[1][0]['status'])

    def test_ordinary_or_non_native_report_cannot_become_release_acceptance(self):
        path, report = self.completed_release_fixture()
        for update in [
            {'selection': {'profile': 'full', 'task_id': None}},
            {'selection': {'profile': 'fast', 'task_id': None}},
            {'selection': {'profile': 'task', 'task_id': 'M5-001'}},
            {'selection': None},
            {'selection': ['release']},
            {'native_requirement': {'requested': False, 'satisfied': True}},
            {'native_requirement': {'requested': True, 'satisfied': False}},
            {'native_requirement': {'requested': 1, 'satisfied': 1}},
            {'environment': dict(report['environment'], system='Linux')},
            {'environment': dict(report['environment'], machine='aarch64')},
            {'status': 'blocked'},
        ]:
            with self.subTest(update=update):
                path.write_bytes(json_bytes(dict(report, **update)))
                self.assertFalse(readiness(self.root)['release_ready'])

    def test_decision_change_during_run_fails_the_report(self):
        code, report = self.run_harness(before_check=lambda name: self.edit_decision() if name == 'project-state' else None)
        self.assertEqual(1, code)
        self.assertTrue(report['source_unchanged'])
        self.assertFalse(report['project_unchanged'])
        self.assertEqual('failed', report['status'])
        self.assertFalse(report['product_complete'])

    def test_invalid_state_at_end_is_recorded_as_failure(self):
        def invalidate(name):
            if name == 'project-state':
                ledger = self.ledger()
                ledger['focus_task_id'] = None
                self.save_ledger(ledger)

        code, report = self.run_harness(before_check=invalidate)
        self.assertEqual(1, code)
        self.assertIn('focus task', report['project_state_error'])
        self.assertFalse(report['project_unchanged'])

    def test_fast_checks_cannot_complete_unexecuted_task_checks(self):
        code, report = self.run_harness(['--profile', 'fast'])
        self.assertEqual(0, code)
        self.assertEqual('passed', report['status'])
        results = {item['id']: item for item in report['checks']}
        self.assertEqual('not_selected', results['rust-lints']['reason_code'])
        self.assertFalse(report['product_complete'])
        with self.assertRaisesRegex(ProjectError, 'Required check did not pass'):
            archive_evidence(self.root, 'M0-001', self.root / '.reports/test-report.json')

    def test_selecting_planned_work_returns_blocked_and_nonzero(self):
        code, report = self.run_harness(['--task', 'M1-002'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertEqual({'environment', 'project-state', 'market-fixtures'}, set(report['selection']['checks']))

    def test_change_runs_exclude_expensive_release_checks_but_task_selection_can_include_them(self):
        project = load_project(self.root)
        self.assertNotIn('runtime-stability', harness.select_checks(project))
        self.assertIn('runtime-stability', harness.select_checks(project, profile='release'))
        self.assertIn('runtime-stability', harness.select_checks(project, task_id='M5-001'))
        code, report = self.run_harness(['--profile', 'release'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])

    def test_fast_run_stops_after_failure_and_preserves_the_omissions(self):
        def failed(name, command, env, run_dir, timeout):
            return {'id': name, 'status': 'failed', 'command': command, 'exit_code': 2}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=failed) as executed,
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(sys, 'argv', ['check.py', '--profile', 'fast']), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        self.assertEqual(1, stopped.exception.code)
        self.assertEqual(1, executed.call_count)
        report = read_json(self.root / '.reports/test-report.json')
        self.assertEqual('failed', report['status'])
        self.assertEqual('earlier_failure', next(item for item in report['checks'] if item['id'] == 'project-state')['reason_code'])

    def test_require_windows_cannot_be_satisfied_by_a_fast_linux_run(self):
        code, report = self.run_harness(['--profile', 'fast', '--require-windows'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertFalse(report['native_requirement']['satisfied'])
        self.assertFalse(report['product_complete'])

    def test_required_check_cannot_be_demoted_deleted_or_moved_to_release(self):
        path = self.root / 'project/checks.json'
        original = path.read_bytes()
        for mutation in ['demote', 'delete', 'release']:
            with self.subTest(mutation=mutation):
                path.write_bytes(original)
                catalog = read_json(path)
                check = next(item for item in catalog['checks'] if item['id'] == 'rust-lints')
                if mutation == 'demote':
                    check.update(status='planned', command=None)
                elif mutation == 'delete':
                    catalog['checks'].remove(check)
                else:
                    check['cadence'] = 'release'
                path.write_bytes(json_bytes(catalog))
                with self.assertRaisesRegex(ProjectError, 'Required change checks'):
                    self.run_harness(['--require-windows'], system='Windows')

    def test_integration_rejects_selection_omission_on_both_platforms(self):
        project = load_project(self.root)
        selected = harness.select_checks(project) - {'rust-lints'}
        for system, args in [('Linux', []), ('Windows', ['--require-windows'])]:
            with self.subTest(system=system), patch.object(harness, 'select_checks', return_value=selected):
                code, report = self.run_harness(args, system=system)
                self.assertEqual(1, code)
                self.assertEqual('blocked', report['status'])
                self.assertIn('rust-lints', report['integration_gates']['unverified'])

    def test_future_planned_checks_do_not_block_native_change_integration(self):
        code, report = self.run_harness(['--require-windows'], system='Windows')
        self.assertEqual(0, code)
        self.assertEqual('passed', report['integration_gates']['status'])
        self.assertFalse(report['product_complete'])
        code, report = self.run_harness(['--profile', 'fast'], system='Windows')
        self.assertEqual(0, code)
        self.assertEqual('not_requested', report['integration_gates']['status'])
        code, report = self.run_harness(['--profile', 'fast', '--require-windows'], system='Windows')
        self.assertEqual(1, code)

    def replace_gate(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        old = next(item for item in catalog['checks'] if item['id'] == 'rust-lints')
        replacement = dict(old, id='replacement-lints')
        old.update(status='retired', command=None)
        catalog['checks'].append(replacement)
        for ids in [catalog['required_change_checks'], *[gate['checks'] for gate in catalog['milestones']]]:
            ids[:] = ['replacement-lints' if key == 'rust-lints' else key for key in ids]
        path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task.update(status='active', checks=['project-state', 'replacement-lints'])
        self.save_ledger(ledger)
        return path, catalog

    def test_history_prevents_removing_both_a_gate_and_its_required_binding(self):
        base = self.commit_fixture()
        self.replace_gate()
        with self.assertRaisesRegex(ProjectError, 'Established integration gate removed'):
            review_history(self.root, base)

    def test_history_allows_task_bound_replacement_and_preserves_its_rationale(self):
        base = self.commit_fixture()
        path, catalog = self.replace_gate()
        catalog['gate_changes'].append({'removed_check': 'rust-lints', 'replacement_checks': ['replacement-lints'],
                                        'task_id': 'M0-001', 'reason': 'Synthetic replacement exercises the same failure boundary.'})
        path.write_bytes(json_bytes(catalog))
        self.assertEqual(1, len(review_history(self.root, base)['gate_replacements']))
        replaced = self.commit_fixture()
        catalog['gate_changes'][0]['reason'] = 'Rewrite the accepted rationale.'
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'Accepted gate replacement changed'):
            review_history(self.root, replaced)

    def test_split_gate_can_be_replaced_again_from_either_review_base(self):
        original = self.commit_fixture()
        path, catalog = self.replace_gate()
        first = {'removed_check': 'rust-lints', 'replacement_checks': ['replacement-lints', 'project-state'],
                 'task_id': 'M0-001', 'reason': 'Check source and project consistency separately.'}
        catalog['gate_changes'].append(first)
        path.write_bytes(json_bytes(catalog))
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        intermediate = self.commit_fixture()

        check = next(item for item in catalog['checks'] if item['id'] == 'replacement-lints')
        catalog['checks'].append(dict(check, id='final-lints'))
        check.update(status='retired', command=None)
        milestone = next(item for item in catalog['milestones'] if item['id'] == 'M0')
        for ids in [catalog['required_change_checks'], milestone['checks']]:
            ids[ids.index('replacement-lints')] = 'final-lints'
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-002')
        task.update(status='active', checks=['project-state', 'final-lints'],
                    plan=task_by_id(ledger, 'M0-001')['plan'])
        self.save_ledger(ledger)
        second = {'removed_check': 'replacement-lints', 'replacement_checks': ['final-lints'],
                  'task_id': 'M0-002', 'reason': 'Preserve the source checks through the next cutover.'}
        catalog['gate_changes'].append(second)
        path.write_bytes(json_bytes(catalog))
        self.assertEqual([first, second], review_history(self.root, original)['gate_replacements'])
        self.assertEqual([second], review_history(self.root, intermediate)['gate_replacements'])

        # A surviving branch of the split cannot cover a missing or unreviewed branch.
        task['status'] = 'planned'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'without a reviewed replacement'):
            review_history(self.root, original)
        catalog['gate_changes'].remove(second)
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'without a reviewed replacement'):
            review_history(self.root, original)

    def test_circular_gate_substitutions_fail_even_with_a_required_leaf(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        catalog['gate_changes'] = [
            {'removed_check': 'rust-lints', 'replacement_checks': ['project-state'],
             'task_id': 'M0-001', 'reason': 'Synthetic first step.'},
            {'removed_check': 'project-state', 'replacement_checks': ['rust-lints', 'environment'],
             'task_id': 'M0-001', 'reason': 'Synthetic return to an earlier gate.'},
        ]
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['checks'].append('environment')
        self.save_ledger(ledger)
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'cycle'):
            load_project(self.root)

    def test_retiring_a_work_classification_preserves_closed_plans_and_checks_open_ones(self):
        path = self.root / 'project/delivery.json'
        policy = read_json(path)
        legacy = dict(policy['change_types'][0], id='old-maintenance')
        policy['change_types'].append(legacy)
        path.write_bytes(json_bytes(policy))
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task['plan']['change_types'] = ['old-maintenance']
        task_by_id(ledger, 'M0-004').update(status='cancelled', closure_reason='Superseded.',
                                             plan=task['plan'])
        self.save_ledger(ledger)
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        accepted = self.commit_fixture()

        policy['change_types'].remove(legacy)
        path.write_bytes(json_bytes(policy))
        self.assertEqual('passed', review_history(self.root, accepted)['status'])
        ledger = self.ledger()
        task_by_id(ledger, 'M0-002')['plan'] = task_by_id(ledger, 'M0-001')['plan']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unknown references.*old-maintenance'):
            load_project(self.root)

    def test_cancelled_history_does_not_prevent_a_delivered_replacement_from_passing(self):
        project = load_project(self.root)
        # Synthetic states test aggregation; real task completion is validated separately.
        for task in project['tasks'].values():
            if task['milestone'] == 'M0':
                task['status'] = 'done' if task['id'] == 'M0-004' else 'cancelled'
        results = [{'id': key, 'status': 'passed'} for key in project['checks']]
        milestone = product_progress(project, results)[0][0]
        self.assertEqual('passed', milestone['status'])
        self.assertIn('M0-001', milestone['cancelled_task_ids'])
        project['tasks']['M0-004']['status'] = 'cancelled'
        self.assertEqual('blocked', product_progress(project, results)[0][0]['status'])

    def test_failed_run_cannot_archive_even_when_task_checks_pass(self):
        path = self.report()
        report = read_json(path)
        report['checks'].append({'id': 'environment', 'status': 'failed', 'command': [], 'exit_code': 1})
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'failed verification run'):
            archive_evidence(self.root, 'M0-001', path)

    def test_active_work_needs_a_plan_and_completed_dependencies(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['status'] = 'active'
        del task_by_id(ledger, 'M0-001')['plan']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'needs a delivery plan'):
            load_project(self.root)
        task_by_id(ledger, 'M0-001')['status'] = 'planned'
        task_by_id(ledger, 'M0-003')['status'] = 'active'
        ledger['focus_task_id'] = 'M0-003'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unfinished dependencies'):
            load_project(self.root)

    def test_proposed_decision_cannot_authorize_active_work(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['status'] = 'active'
        self.save_ledger(ledger)
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['status'] = 'proposed'
        path.write_bytes(json_bytes(decision))
        with self.assertRaisesRegex(ProjectError, 'unaccepted decision'):
            load_project(self.root)

    def test_uncovered_risks_cannot_be_hidden_in_a_task_plan(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['plan']['risks'].append({'failure': 'Unverified installation.', 'checks': ['windows-installation']})
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'uncovered risks'):
            load_project(self.root)

    def test_context_and_scenarios_identify_native_and_mock_boundaries(self):
        context = task_context(self.root, 'M0-003')
        self.assertTrue(any(check['platforms'] == 'windows-x64' for check in context['checks']))
        context = task_context(self.root, 'M4-001')
        self.assertTrue(any('mock evaluation' in scenario['evidence_boundary'] for scenario in context['scenarios']))

    def test_scenario_cannot_name_checks_without_a_delivery_task(self):
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['scenarios'][0]['checks'].append('release-artifacts')
        path.write_bytes(json_bytes(delivery))
        with self.assertRaisesRegex(ProjectError, 'without a delivery task'):
            load_project(self.root)

    def feedback(self, status='open'):
        item = {'id': 'fixture-defect', 'source': 'Synthetic test observation.', 'observation': 'A previous check missed a defect.',
                'severity': 'major', 'status': status, 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                'resolution': None, 'deferral_reason': None}
        if status == 'addressed':
            item['resolution'] = {'cause': 'Missing invariant.', 'correction': 'Validate the invariant.', 'prevention': 'Add regression evidence.'}
        return item

    def save_feedback(self, item):
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': [item]}))

    def test_feedback_cannot_be_closed_before_corrective_work(self):
        self.save_feedback(self.feedback('addressed'))
        with self.assertRaisesRegex(ProjectError, 'completed corrective work'):
            load_project(self.root)

    def test_resolved_feedback_has_regression_proof_without_invalidating_receipt(self):
        self.save_feedback(self.feedback())
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        item = self.feedback('addressed')
        self.save_feedback(item)
        self.assertTrue(workfront(self.root)['verification']['current'])
        item['regression_checks'] = ['environment']
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'lacks regression evidence'):
            load_project(self.root)

    def test_deferred_major_feedback_remains_a_release_blocker(self):
        item = self.feedback('deferred')
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'explicit reason'):
            load_project(self.root)
        item['deferral_reason'] = 'Needs a native target to investigate.'
        self.save_feedback(item)
        result = readiness(self.root)
        self.assertFalse(result['release_ready'])
        self.assertTrue(any('fixture-defect' in blocker for blocker in result['blockers']))

    def test_history_rejects_rewritten_decisions_and_reopened_completed_tasks(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit_fixture()
        self.assertEqual('passed', review_history(self.root, base)['status'])
        self.edit_decision()
        with self.assertRaisesRegex(ProjectError, 'Accepted decision history changed'):
            review_history(self.root, base)
        subprocess.run(['git', 'restore', 'contracts/decisions/0003-repository-harness.json'],
                       cwd=self.root, check=True, capture_output=True)
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='planned', next_action='Pretend the completed task is new.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history changed'):
            review_history(self.root, base)

    def test_history_json_is_read_as_utf8_under_a_legacy_windows_locale(self):
        base = self.commit_fixture()
        original_run = subprocess.run

        def legacy_locale(argv, **kwargs):
            if argv[:2] == ['git', 'show'] and kwargs.get('text') and not kwargs.get('encoding'):
                kwargs['encoding'] = 'cp1252'
            return original_run(argv, **kwargs)

        with patch('scripts.project.subprocess.run', side_effect=legacy_locale):
            self.assertEqual('passed', review_history(self.root, base)['status'])

    def append_native_receipt(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit_fixture()
        ref = archive_evidence(self.root, 'M0-001', self.report(system='Windows'))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['evidence'].append(ref)
        self.save_ledger(ledger)
        return base, ref

    def test_history_allows_validated_append_only_receipts_on_completed_task(self):
        base, _ = self.append_native_receipt()
        self.assertEqual('passed', review_history(self.root, base)['status'])

    def test_history_rejects_evidence_removal_reorder_replacement_and_state_changes(self):
        base, _ = self.append_native_receipt()
        original = self.ledger()
        for change in ['remove', 'reorder', 'replace', 'state']:
            with self.subTest(change=change):
                ledger = read_json(self.root / 'project/tasks.json')
                task = task_by_id(ledger, 'M0-001')
                evidence = task['evidence']
                if change == 'remove': evidence.pop(0)
                elif change == 'reorder': evidence.reverse()
                elif change == 'replace': evidence[0] = evidence[-1]
                else: task['closure_reason'] = 'Retroactive explanation'
                self.save_ledger(ledger)
                with self.assertRaisesRegex(ProjectError, 'Accepted task history'):
                    review_history(self.root, base)
                self.save_ledger(original)

    def test_history_rejects_appended_receipt_for_different_definition_or_missing_checks(self):
        base, ref = self.append_native_receipt()
        path = self.root / ref['path']
        original = path.read_bytes()
        for field in ['task_definition_sha256', 'checks']:
            with self.subTest(field=field):
                receipt = read_json(path)
                receipt[field] = '0' * 64 if field == 'task_definition_sha256' else receipt['checks'][:1]
                path.write_bytes(json_bytes(receipt))
                ledger = self.ledger()
                task_by_id(ledger, 'M0-001')['evidence'][-1]['sha256'] = digest(path.read_bytes())
                self.save_ledger(ledger)
                with self.assertRaisesRegex(ProjectError, 'Appended evidence'):
                    review_history(self.root, base)
                path.write_bytes(original)

    def test_history_keeps_cancelled_records_immutable(self):
        ref = archive_evidence(self.root, 'M0-001', self.report())
        extra = archive_evidence(self.root, 'M0-001', self.report(system='Windows'))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='cancelled', evidence=[ref], next_action=None,
                                         closure_reason='Superseded fixture scope.')
        ledger['focus_task_id'] = 'M0-002'
        self.save_ledger(ledger)
        base = self.commit_fixture()
        task_by_id(ledger, 'M0-001')['evidence'].append(extra)
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history'):
            review_history(self.root, base)


    def test_missing_history_base_does_not_claim_comparison(self):
        self.assertEqual('not_applicable', review_history(self.root)['status'])
        with self.assertRaises(subprocess.CalledProcessError):
            review_history(self.root, 'missing-base-385ab')

    def test_unknown_selection_is_an_error(self):
        project = load_project(self.root)
        for args in [{'profile': 'typo'}, {'task_id': 'M0-999'}]:
            with self.subTest(args=args), self.assertRaisesRegex(ProjectError, 'Unknown'):
                harness.select_checks(project, **args)
