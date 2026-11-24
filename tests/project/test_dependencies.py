"""Dependency coverage, scan failures and time-bounded dispositions must be observable."""

from datetime import date
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from jsonschema import Draft202012Validator, FormatChecker
from referencing.jsonschema import DRAFT202012
import yaml

from scripts import dependencies as review
from scripts import publisher_advisory as publisher
from scripts.project import maintenance_status

ROOT = Path(__file__).resolve().parents[2]


class PublisherAdvisoryTests(unittest.TestCase):
    token = 'synthetic-job-credential'
    response = {
        'ghsa_id': publisher.ADVISORY, 'state': 'published', 'withdrawn_at': None,
        'vulnerabilities': [{
            'package': {'ecosystem': 'rust', 'name': 'block_buffer'},
            'vulnerable_version_range': '>=0.11.0,<0.12.1',
        }],
    }

    def test_credential_is_only_sent_to_the_exact_tls_get_and_redirects_stop(self):
        with patch.object(publisher, 'HTTPSConnection') as connect:
            for url in [publisher.URL.replace('https:', 'http:'), publisher.URL + '?query=1',
                        publisher.URL.replace(publisher.HOST, 'api.github.com.example.invalid'),
                        publisher.URL.replace(publisher.HOST, 'api.github.com:443'),
                        publisher.URL.replace('RustCrypto', 'other-publisher'),
                        'https://api.osv.dev/v1/querybatch', 'https://registry.npmjs.org/picomatch/latest']:
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'approved publisher endpoint'):
                    publisher.fetch(url, self.token)
            connect.assert_not_called()

            reply = MagicMock(status=302)
            reply.__enter__.return_value = reply
            reply.headers = {'Location': 'https://example.invalid/credential-redirect'}
            connection = connect.return_value
            connection.getresponse.return_value = reply
            with self.assertRaisesRegex(ValueError, 'HTTP 302'):
                publisher.fetch(publisher.URL, self.token)
            connect.assert_called_once_with('api.github.com', timeout=8)
            connection.request.assert_called_once_with(
                'GET', '/repos/RustCrypto/utils/security-advisories/GHSA-qwgh-2vcv-g2f7',
                headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ScopeHub-dependency-review',
                         'Authorization': f'Bearer {self.token}'},
            )
            reply.read.assert_not_called()
            connection.close.assert_called_once_with()

    def test_auth_refusal_only_falls_back_to_a_fresh_public_same_endpoint_get(self):
        reply = (200, json.dumps(self.response).encode())
        for status in (403, 404):
            with self.subTest(status=status), patch.object(publisher, 'request_once', side_effect=[(status, b''), reply]) as get:
                self.assertEqual(self.response, publisher.fetch(publisher.URL, self.token))
                self.assertEqual([call(self.token), call(None)], get.call_args_list)
        with patch.object(publisher, 'request_once', side_effect=[(403, b''), (403, b'')]) as get:
            with self.assertRaisesRegex(ValueError, 'HTTP 403'):
                publisher.fetch(publisher.URL, self.token)
            self.assertEqual([call(self.token), call(None)], get.call_args_list)

    def test_transient_errors_allow_one_retry_and_terminal_errors_disclose_no_credential(self):
        reply = (200, json.dumps(self.response).encode())
        for failure in ((429, b''), (503, b''), TimeoutError(self.token)):
            with self.subTest(failure=failure), patch.object(publisher, 'request_once', side_effect=[failure, reply]) as get:
                self.assertEqual(self.response, publisher.fetch(publisher.URL, self.token))
                self.assertEqual([call(self.token), call(self.token)], get.call_args_list)
        with patch.object(publisher, 'request_once', side_effect=TimeoutError(self.token)) as get:
            with self.assertRaisesRegex(ValueError, '^Publisher advisory network request failed$') as error:
                publisher.fetch(publisher.URL, self.token)
            self.assertTrue(error.exception.__suppress_context__)
            self.assertEqual(2, get.call_count)
        for body in (b'broken JSON', b'x' * (publisher.MAX_BYTES + 1),
                     json.dumps(dict(self.response, state='draft')).encode(),
                     json.dumps(dict(self.response, withdrawn_at='2026-10-14T00:00:00Z')).encode()):
            with self.subTest(size=len(body)), patch.object(publisher, 'request_once', return_value=(200, body)) as get:
                with self.assertRaises(ValueError):
                    publisher.fetch(publisher.URL, self.token)
                get.assert_called_once_with(self.token)

    def test_hosted_prefetch_retains_only_public_fields_for_the_exact_run(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {key: f'fixture-{key}' for key in publisher.RUN_KEYS}
            env.update(GITHUB_ACTIONS='true', RUNNER_TEMP=directory)
            data = json.dumps(dict(self.response, private_fixture=self.token)).encode()
            with patch.dict(os.environ, env, clear=True), patch.object(publisher, 'request_once') as get:
                with self.assertRaisesRegex(ValueError, 'missing or invalid'):
                    publisher.read_publisher(publisher.URL)
                get.assert_not_called()
                get.return_value = (200, data)
                publisher.prefetch(os.environ, self.token)
                get.assert_called_once_with(self.token)
                evidence = next(Path(directory).iterdir())
                self.assertNotIn(self.token, evidence.read_text(encoding='utf-8'))
                self.assertEqual(self.response, publisher.read_publisher(publisher.URL))
                for key in publisher.RUN_KEYS:
                    with self.subTest(key=key), patch.dict(os.environ, {key: 'another-job'}):
                        with self.assertRaisesRegex(ValueError, 'another job or endpoint'):
                            publisher.read_publisher(publisher.URL)
                evidence.write_text('{truncated JSON', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'missing or invalid'):
                    publisher.read_publisher(publisher.URL)
                get.assert_called_once_with(self.token)

    def test_local_online_review_does_not_reuse_a_publisher_job_credential(self):
        with (patch.dict(os.environ, {publisher.TOKEN_ENV: self.token}, clear=True),
              patch.object(publisher, 'request_once', return_value=(200, json.dumps(self.response).encode())) as get):
            self.assertEqual(self.response, publisher.read_publisher(publisher.URL))
            get.assert_called_once_with(None)

    def test_workflow_grants_the_existing_token_to_only_the_isolated_prefetch(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/verify.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['verify']
        self.assertEqual({'contents': 'read'}, workflow['permissions'])
        self.assertNotIn('permissions', job)
        self.assertNotIn(publisher.TOKEN_ENV, job['env'])
        credential_steps = [step for step in job['steps'] if publisher.TOKEN_ENV in step.get('env', {})]
        self.assertEqual(1, len(credential_steps))
        self.assertEqual({publisher.TOKEN_ENV: '${{ github.token }}'}, credential_steps[0]['env'])
        self.assertIn('-I -S scripts/publisher_advisory.py', credential_steps[0]['run'])


class HostedCargoCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load((ROOT / '.github/workflows/verify.yml').read_text(encoding='utf-8'))
        cls.steps = workflow['jobs']['verify']['steps']
        cls.key = next(step for step in cls.steps if step.get('id') == 'cargo_keys')

    def test_only_successful_full_cache_owners_can_save_builds(self):
        caches = {step['uses'].split('@')[0]: step for step in self.steps
                  if step.get('uses', '').startswith('actions/cache')}
        self.assertEqual({'actions/cache/restore', 'actions/cache/save'}, set(caches))
        restore, save = caches['actions/cache/restore'], caches['actions/cache/save']
        self.assertEqual(restore['uses'].split('@')[1], save['uses'].split('@')[1])
        self.assertEqual(restore['with']['path'], save['with']['path'])
        self.assertEqual("${{ steps.cargo_cache.outputs['cache-primary-key'] }}", save['with']['key'])
        self.assertEqual("steps.build_setup.outputs.skip != 'true' && steps.lane.outputs.skip != 'true'", restore['if'])
        self.assertEqual(restore['if'], self.key['if'])
        self.assertEqual(
            "success() && !inputs.task_id && steps.build_setup.outputs.profile == '' "
            "&& (matrix.lane == '' || (github.event_name == 'push' && steps.lane.outputs.split == 'true' && matrix.lane != 'python')) "
            "&& steps.cargo_cache.outputs['cache-primary-key'] != '' "
            "&& steps.cargo_cache.outputs['cache-hit'] != 'true'", save['if'],
        )
        self.assertEqual("${{ steps.lane.outputs.split == 'true' && matrix.lane || '' }}", self.key['env']['SCOPEHUB_WINDOWS_LANE'])
        self.assertEqual('${{ steps.cargo_keys.outputs.primary }}', restore['with']['key'])
        self.assertGreater(self.steps.index(save), next(i for i, step in enumerate(self.steps) if step.get('id') == 'artifacts'))

    @unittest.skipUnless(sys.platform == 'linux', 'Run the hosted cache-key script with Bash')
    def test_browser_and_python_restore_the_same_key_with_legacy_fallback(self):
        legacy = 'Windows-X64-cargo-toolchain-'
        for lane, owner in (('python', 'browser'), ('browser', 'browser'), ('core', 'core'), ('', '')):
            with self.subTest(lane=lane), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'outputs.txt'
                subprocess.run(
                    [self.key['shell'], '-c', self.key['run']], capture_output=True, check=True, timeout=10,
                    env=dict(os.environ, SCOPEHUB_WINDOWS_LANE=lane, RUNNER_OS='Windows', RUNNER_ARCH='X64',
                             TOOLCHAIN_HASH='toolchain', LOCK_HASH='lock', GITHUB_OUTPUT=str(output)),
                )
                primary = f'Windows-X64-cargo-{owner}-toolchain-' if owner else legacy
                expected = [f'primary={primary}lock', f'legacy={legacy}lock', 'fallback<<CACHE_KEYS']
                if owner:
                    expected.extend((f'{legacy}lock', primary))
                expected.extend((legacy, 'CACHE_KEYS'))
                self.assertEqual(expected, output.read_text(encoding='utf-8').splitlines())


class DependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in [*review.LOCKS, 'project/dependency-inventory.json', 'project/dependencies.json',
                     'THIRD_PARTY_NOTICES', '.github/workflows/verify.yml', 'apps/desktop/src-tauri/Cargo.toml']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.packages = review.lock_inventory(self.root)
        self.policy = review.read(self.root / 'project/dependencies.json')

    def test_inventory_includes_platform_optional_and_development_dependencies(self):
        ids = {review.key(item)[:2] for item in self.packages}
        for item in [('npm', '@rolldown/binding-win32-x64-msvc'), ('crates.io', 'glib'), ('PyPI', 'mypy')]:
            self.assertIn(item, ids)
        self.assertNotIn(('PyPI', 'rfc3987'), ids)
        review.validate_inventory(self.root, self.policy, self.packages)

    def test_mutated_lock_missing_notice_and_unknown_license_cannot_pass(self):
        lock = self.root / 'uv.lock'
        original = lock.read_bytes()
        lock.write_bytes(original + b'\n# Changed lock input\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.validate_inventory(self.root, self.policy, self.packages)
        lock.write_bytes(original)
        notice = self.root / 'THIRD_PARTY_NOTICES'
        original = notice.read_bytes()
        notice.write_text('Missing upstream declarations.\n')
        with self.assertRaisesRegex(ValueError, 'notice inventory'):
            review.validate_inventory(self.root, self.policy, self.packages)
        notice.write_bytes(original)
        policy = dict(self.policy, reviewed_license_expressions=[])
        with self.assertRaisesRegex(ValueError, 'Unreviewed license'):
            review.validate_inventory(self.root, policy, self.packages)

    def test_unsupported_lock_versions_and_nonregistry_sources_require_support(self):
        path = self.root / 'pnpm-lock.yaml'
        data = yaml.safe_load(path.read_text())
        data['lockfileVersion'] = 'future'
        path.write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(ValueError, 'Unsupported pnpm'):
            review.lock_inventory(self.root)
        data['lockfileVersion'] = '9.0'
        data['packages']['fixture@1.0.0'] = {'resolution': {'tarball': 'https://example.invalid/fixture'}}
        path.write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(ValueError, 'unsupported npm'):
            review.lock_inventory(self.root)

    def test_new_or_floating_actions_require_review(self):
        path = self.root / '.github/workflows/verify.yml'
        text = path.read_text()
        sha = self.policy['actions'][0]['sha']
        path.write_text(text.replace(sha, 'v999'))
        with self.assertRaisesRegex(ValueError, 'not pinned'):
            review.actions(self.root)
        path.write_text(text.replace(sha, 'a' * 40))
        with self.assertRaisesRegex(ValueError, 'reviewed immutable'):
            review.validate_inventory(self.root, self.policy, self.packages)

    def test_network_and_partial_or_malformed_osv_results_fail_closed(self):
        with self.assertRaises(OSError):
            review.query_advisories(self.packages[:1], Mock(side_effect=OSError('offline')))
        for response in [[], None, {}, {'results': []}, {'results': [None]}, {'results': [{'error': 'unavailable'}]},
                         {'results': [{'vulns': None}]}, {'results': [{'vulns': [{}]}]}]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                review.query_advisories(self.packages[:1], Mock(return_value=response))

    def test_offline_inventory_is_explicitly_blocked_and_does_not_claim_a_scan(self):
        with (patch.object(review, 'ROOT', self.root), patch.object(sys, 'argv', ['dependencies.py', '--offline']),
              patch.object(review, 'query_advisories') as queried, redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                review.main()
        self.assertEqual(1, stopped.exception.code)
        queried.assert_not_called()
        result = review.read(next((self.root / '.reports/dependencies').glob('*.json')))
        self.assertEqual(('blocked', 'not_queried'), (result['status'], result['advisories']))

    def test_all_batches_and_paginated_findings_are_observed(self):
        request = Mock(side_effect=[{'results': [{'next_page_token': 'next'}]},
                                    {'results': [{'vulns': [{'id': 'CVE-fixture'}]}]}])
        result = review.query_advisories(self.packages[:1], request)
        self.assertEqual('CVE-fixture', result[0]['advisory'])
        self.assertEqual('next', request.call_args.args[1]['queries'][0]['page_token'])
        request = Mock(return_value={'results': [{'next_page_token': 'same'}]})
        with self.assertRaisesRegex(ValueError, 'pagination'):
            review.query_advisories(self.packages[:1], request)
        request = Mock(side_effect=lambda url, body: {'results': [{} for _ in body['queries']]})
        self.assertEqual([], review.query_advisories(self.packages, request))
        self.assertEqual(len(self.packages), sum(len(call.args[1]['queries']) for call in request.call_args_list))

    def test_actions_are_queried_by_commit(self):
        request = Mock(return_value={'results': [{}]})
        review.query_advisories([{'ecosystem': 'GitHub Actions', 'name': 'fixture/action', 'version': 'a' * 40}], request)
        self.assertEqual({'commit': 'a' * 40}, request.call_args.args[1]['queries'][0])

    def test_due_maintenance_is_visible_without_a_background_or_auto_merge_claim(self):
        due = date.fromisoformat(self.policy['maintenance']['next_review_by'])
        self.assertTrue(maintenance_status(self.root, due)['review_due'])
        self.assertFalse(maintenance_status(self.root, date(2026, 10, 14))['review_due'])

    def test_advisories_need_exact_identity_and_unexpired_disposition(self):
        item = {'ecosystem': 'crates.io', 'name': 'fixture', 'version': '1.0.0', 'advisory': 'CVE-fixture'}
        exception = {**item, 'advisories': ['CVE-fixture'], 'reason': 'Isolated test.', 'task_id': 'M5-002',
                     'evidence': ['Synthetic target graph.'], 'expires': '2026-11-01'}
        policy = {'advisory_dispositions': [exception]}
        self.assertEqual([], review.dispositions([item], policy, date(2026, 10, 14))[1])
        self.assertEqual(1, len(review.dispositions([dict(item, version='2.0.0')], policy, date(2026, 10, 14))[1]))
        with self.assertRaisesRegex(ValueError, 'expired'):
            review.dispositions([item], policy, date(2026, 11, 1))
        self.assertEqual([item], review.dispositions([item], {'advisory_dispositions': []})[1])

    def test_target_scope_change_or_failed_graph_cannot_keep_an_exception(self):
        # Isolate target-absence guards from independently tested primary ranges.
        policy = {**self.policy, 'advisory_dispositions': [item for item in self.policy['advisory_dispositions']
                                                        if item['scope_guard'] == 'absent-from-supported-builds']}
        success = subprocess.CompletedProcess([], 0, stdout='fixture v1.0.0\n', stderr='')
        with patch.object(review.subprocess, 'run', return_value=success):
            review.validate_advisory_scope(self.root, policy)
        success.stdout = 'glib v0.18.5 (*)\n'
        with patch.object(review.subprocess, 'run', return_value=success):
            with self.assertRaisesRegex(ValueError, 'no longer applies'):
                review.validate_advisory_scope(self.root, policy)
        with patch.object(review.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['cargo'])):
            with self.assertRaises(subprocess.CalledProcessError):
                review.validate_advisory_scope(self.root, policy)

    def test_primary_range_discrepancy_is_exact_live_and_fail_closed(self):
        item = {'ecosystem': 'crates.io', 'name': 'fixture-buffer', 'version': '0.10.4',
                'advisories': ['GHSA-fixture'],
                'upstream_source': 'https://api.github.com/repos/fixture/upstream/security-advisories/GHSA-fixture',
                'reviewed_upstream_range': '>=0.11.0,<0.12.1'}
        primary = {'ghsa_id': 'GHSA-fixture', 'withdrawn_at': None, 'vulnerabilities': [
            {'package': {'ecosystem': 'rust', 'name': 'fixture_buffer'},
             'vulnerable_version_range': '>=0.11.0,<0.12.1'}]}
        review.validate_upstream_range(item, Mock(return_value=primary))
        with self.assertRaisesRegex(ValueError, 'Locked version is affected'):
            review.validate_upstream_range({**item, 'version': '0.11.0'}, Mock(return_value=primary))
        changed = {**primary, 'vulnerabilities': [
            {**primary['vulnerabilities'][0], 'vulnerable_version_range': '>=0.10.0,<0.12.1'}]}
        for response in [changed, {}, [], {**primary, 'ghsa_id': 'GHSA-other'},
                         {**primary, 'vulnerabilities': None}]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                review.validate_upstream_range(item, Mock(return_value=response))
        with self.assertRaises(OSError):
            review.validate_upstream_range(item, Mock(side_effect=OSError('unavailable')))

    def test_picomatch_disposition_requires_the_reviewed_patch_for_every_resolution(self):
        identifier = 'picomatch@4.0.7'
        relative = f'patches/{identifier}.patch'
        local_patch = self.root / relative
        local_patch.parent.mkdir()
        shutil.copyfile(ROOT / relative, local_patch)
        shutil.copyfile(ROOT / 'pnpm-workspace.yaml', self.root / 'pnpm-workspace.yaml')
        item = next(d for d in self.policy['advisory_dispositions'] if d['scope_guard'] == 'picomatch-build-patch')
        release = Mock(return_value={'name': 'picomatch', 'version': '4.0.7'})
        probe = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with patch.object(review.subprocess, 'run', return_value=probe) as installed:
            review.validate_picomatch_patch(self.root, item, release)
            self.assertEqual(installed.call_args.args[0], ['node', '--test', 'tests/project/picomatch.test.mjs'])
            release.assert_called_once_with('https://registry.npmjs.org/picomatch/latest')
            with self.assertRaisesRegex(ValueError, 'Unexpected package'):
                review.validate_picomatch_patch(self.root, {**item, 'version': '4.0.6'}, release)
            with self.assertRaisesRegex(ValueError, 'latest npm release'):
                review.validate_picomatch_patch(self.root, item, Mock(return_value={'name': 'picomatch', 'version': '5.0.0'}))
            probe.returncode = 1
            with self.assertRaisesRegex(ValueError, 'behavior check failed'):
                review.validate_picomatch_patch(self.root, item, release)

        lock = self.root / 'pnpm-lock.yaml'
        original = lock.read_bytes()
        data = yaml.safe_load(original)
        data['snapshots'][identifier] = {}
        lock.write_text(yaml.safe_dump(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'reviewed picomatch patch'):
            review.validate_picomatch_patch(self.root, item, release)
        lock.write_bytes(original)
        local_patch.write_bytes(b'Altered patch\n')
        with self.assertRaisesRegex(ValueError, 'reviewed picomatch patch'):
            review.validate_picomatch_patch(self.root, item, release)

    def test_declared_schema_formats_remain_active_with_selected_dependencies(self):
        checker = FormatChecker()
        examples = {'uuid': ('123e4567-e89b-12d3-a456-426614174000', 'invalid'),
                    'date': ('2026-10-14', '2026-02-30'),
                    'date-time': ('2026-10-14T12:30:00Z', '2026-13-40T30:90:00Z'),
                    'uri': ('https://example.invalid/result', 'has spaces and no scheme')}

        def formats(resource):
            schema = resource.contents
            declared = {schema['format']} if isinstance(schema, dict) and 'format' in schema else set()
            for child in resource.subresources():
                declared.update(formats(child))
            return declared

        declared = set()
        for filename in ['protocol.schema.json', 'project.schema.json']:
            schema = review.read(ROOT / 'contracts' / 'schemas' / filename)
            Draft202012Validator.check_schema(schema)
            declared.update(formats(DRAFT202012.create_resource(schema)))
        for name in declared:
            with self.subTest(format=name):
                self.assertIn(name, checker.checkers)
                validator = Draft202012Validator({'type': 'string', 'format': name}, format_checker=checker)
                self.assertTrue(validator.is_valid(examples[name][0]))
                self.assertFalse(validator.is_valid(examples[name][1]))
