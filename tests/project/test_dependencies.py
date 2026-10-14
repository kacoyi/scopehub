"""Dependency coverage, scan failures and time-bounded dispositions must be observable."""

from datetime import date
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator, FormatChecker
import yaml

from scripts import dependencies as review
from scripts.project import maintenance_status

ROOT = Path(__file__).resolve().parents[2]


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
        success = subprocess.CompletedProcess([], 0, stdout='fixture v1.0.0\n', stderr='')
        with patch.object(review.subprocess, 'run', return_value=success):
            review.validate_advisory_scope(self.root, self.policy)
        success.stdout = 'glib v0.18.5 (*)\n'
        with patch.object(review.subprocess, 'run', return_value=success):
            with self.assertRaisesRegex(ValueError, 'no longer applies'):
                review.validate_advisory_scope(self.root, self.policy)
        with patch.object(review.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['cargo'])):
            with self.assertRaises(subprocess.CalledProcessError):
                review.validate_advisory_scope(self.root, self.policy)

    def test_declared_schema_formats_remain_active_with_selected_dependencies(self):
        checker = FormatChecker()
        examples = {'uuid': ('123e4567-e89b-12d3-a456-426614174000', 'invalid'),
                    'date': ('2026-10-14', '2026-02-30'),
                    'date-time': ('2026-10-14T12:30:00Z', '2026-13-40T30:90:00Z'),
                    'uri': ('https://example.invalid/result', 'has spaces and no scheme')}
        def formats(value):
            if isinstance(value, dict):
                return ({value['format']} if 'format' in value else set()).union(*(formats(x) for x in value.values()))
            if isinstance(value, list):
                return set().union(*(formats(x) for x in value))
            return set()
        declared = set().union(*(formats(review.read(ROOT / path)) for path in
                                ['contracts/schemas/protocol.schema.json', 'contracts/schemas/project.schema.json']))
        for name in declared:
            with self.subTest(format=name):
                self.assertIn(name, checker.checkers)
                validator = Draft202012Validator({'type': 'string', 'format': name}, format_checker=checker)
                self.assertTrue(validator.is_valid(examples[name][0]))
                self.assertFalse(validator.is_valid(examples[name][1]))
