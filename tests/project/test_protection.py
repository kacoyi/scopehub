"""Detect remote protection drift without modifying a repository."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.protection import inspect, violations
from scripts.project import ProjectError, read_json

ROOT = Path(__file__).resolve().parents[2]


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.expected = read_json(ROOT / 'project/branch-protection.json')['configuration']
        self.actual = deepcopy(self.expected)
        for key in ['enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution']:
            self.actual[key] = {'enabled': self.actual[key]}

    def test_required_checks_are_bound_to_the_observed_actions_app(self):
        self.assertEqual([], violations(self.actual, self.expected))
        for checks in [[], [{'context': item['context'], 'app_id': -1}
                           for item in self.expected['required_status_checks']['checks']]]:
            self.actual['required_status_checks']['checks'] = checks
            self.assertTrue(violations(self.actual, self.expected))

    def test_bypass_missing_pr_and_stale_base_are_rejected(self):
        for key, value in [('enforce_admins', {'enabled': False}), ('allow_force_pushes', {'enabled': True}),
                           ('allow_deletions', {'enabled': True}), ('required_pull_request_reviews', None),
                           ('required_status_checks', {'strict': False})]:
            with self.subTest(key=key):
                self.assertTrue(violations(dict(self.actual, **{key: value}), self.expected))
        self.actual['required_pull_request_reviews']['bypass_pull_request_allowances'] = {'users': [{'login': 'fixture'}]}
        self.assertTrue(violations(self.actual, self.expected))

    def test_read_only_observation_checks_identity_and_does_not_claim_missing_access(self):
        with (patch('scripts.protection.github_identity', return_value='kacoyi/scopehub'),
              patch('scripts.protection.command', return_value=json.dumps(self.actual)) as command):
            self.assertEqual('passed', inspect(ROOT)['status'])
            self.assertEqual(['gh', 'api', 'repos/kacoyi/scopehub/branches/main/protection'], command.call_args.args[0])
        with patch('scripts.protection.github_identity', return_value='unrelated/repository'):
            with self.assertRaisesRegex(ProjectError, 'another repository'):
                inspect(ROOT)
        with (patch('scripts.protection.github_identity', return_value='kacoyi/scopehub'),
              patch('scripts.protection.command', side_effect=ProjectError('API unavailable'))):
            with self.assertRaisesRegex(ProjectError, 'API unavailable'):
                inspect(ROOT)
