import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import branches, ci
from scripts.project import ProjectError


REPO = {'id': 10, 'full_name': 'fixture/scopehub'}
FORK = {'id': 20, 'full_name': 'contributor/scopehub'}
ENDPOINT = f"repos/{REPO['full_name']}"
BRANCHES = f'{ENDPOINT}/branches?per_page=100'
PULLS = f'{ENDPOINT}/pulls?state=open&per_page=100'


def branch(name, sha, *, protected=False):
    return {'name': name, 'commit': {'sha': sha}, 'protected': protected}


def pull(number, *, base, head, repo=REPO):
    return {'number': number, 'state': 'open',
            'base': {'ref': base[0], 'sha': base[1], 'repo': REPO},
            'head': {'ref': head[0], 'sha': head[1], 'repo': repo}}


class BranchReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git('init', '--initial-branch=local', '--object-format=sha1')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'github.account', 'fixture')
        self.origin = f"https://github.com/{REPO['full_name']}.git"
        self.git('remote', 'add', 'origin', self.origin)
        self.root_sha = self.commit('root')
        self.git('checkout', '-b', 'feature')
        self.feature = self.commit('feature')
        self.git('checkout', 'local')
        self.commit('default work')
        self.git('merge', '--no-ff', '-m', 'integrate feature', 'feature')
        self.default = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-b', 'unique', self.root_sha)
        self.unique = self.commit('unique')
        self.git('update-ref', 'refs/remotes/origin/trunk', self.root_sha)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, text=True, capture_output=True,
                              check=True, timeout=30).stdout.strip()

    def commit(self, message):
        self.git('commit', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def inventory(self, branch_pages=None, pull_pages=None):
        return {ENDPOINT: {**REPO, 'default_branch': 'trunk', 'delete_branch_on_merge': True},
                BRANCHES: [[branch('trunk', self.default, protected=True)]] if branch_pages is None else branch_pages,
                PULLS: [[]] if pull_pages is None else pull_pages}

    def inspect(self, first, final=None):
        # Stub the official CLI calls while keeping the shared identity check and Git graph real.
        identity_calls = iter([
            (['git', 'remote', 'get-url', 'origin'], self.origin),
            (['gh', 'auth', 'switch', '--hostname', 'github.com', '--user', 'fixture'], ''),
            (['gh', 'api', 'user', '--jq', '.login'], 'fixture'),
        ])
        observations = iter([*first.items(), *(final if final is not None else first).items()])

        def identity(argv, root):
            expected, response = next(identity_calls)
            self.assertEqual((expected, self.root), (argv, root))
            return response

        def api(argv, root):
            endpoint, payload = next(observations)
            expected = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET']
            if endpoint != ENDPOINT:
                expected.extend(['--paginate', '--slurp'])
            self.assertEqual(([*expected, endpoint], self.root), (argv, root))
            if isinstance(payload, Exception):
                raise payload
            return payload if isinstance(payload, str) else json.dumps(payload)

        with patch.object(ci, 'command', side_effect=identity), patch.object(branches, 'command', side_effect=api):
            return branches.inspect(self.root)

    def test_exact_remote_tips_stacked_bases_and_colliding_fork_head(self):
        inventory = self.inventory([[
            branch('trunk', self.default, protected=True),
            branch('protected', self.feature, protected=True),
            branch('stack/base', self.root_sha),
            branch('stack/head', self.feature),
            branch('fork-base', self.root_sha),
            branch('same-name', self.feature),
            branch('backup', self.unique),
        ]], [[
            pull(8, base=('stack/base', self.root_sha), head=('stack/head', self.feature)),
            pull(3, base=('fork-base', self.root_sha), head=('same-name', '8' * 40), repo=FORK),
            pull(12, base=('trunk', self.default), head=('backup', self.unique)),
        ]])
        refs_before = self.git('show-ref', '--head')
        report = self.inspect(inventory)
        self.assertEqual('passed', report['status'], report['errors'])
        self.assertEqual({'name': 'trunk', 'sha': self.default}, report['default_branch'])
        self.assertIs(report['delete_branch_on_merge'], True)
        self.assertEqual([], report['findings'])
        rows = {row['name']: row for row in report['branches']}
        self.assertEqual('default', rows['trunk']['status'])
        self.assertEqual('protected', rows['protected']['status'])
        self.assertEqual(self.feature, rows['protected']['sha'])
        self.assertEqual(('in_use', [8], []), (rows['stack/base']['status'], rows['stack/base']['open_pr_bases'],
                                              rows['stack/base']['open_pr_heads']))
        self.assertEqual(('in_use', [8]), (rows['stack/head']['status'], rows['stack/head']['open_pr_heads']))
        self.assertEqual(('in_use', [3]), (rows['fork-base']['status'], rows['fork-base']['open_pr_bases']))
        self.assertEqual('reachable_unreferenced', rows['same-name']['status'])
        self.assertEqual([], rows['same-name']['open_pr_heads'])
        self.assertFalse(rows['backup']['reachable_from_default'])
        prs = {row['number']: row for row in report['open_pull_requests']}
        self.assertEqual(('fork_head', False), (prs[3]['status'], prs[3]['head']['same_repository']))
        self.assertEqual('already_integrated', prs[8]['status'])
        self.assertEqual(self.root_sha, prs[8]['base']['sha'])
        self.assertEqual(self.feature, prs[8]['head']['sha'])
        self.assertEqual('outside_default_history', prs[12]['status'])
        self.assertEqual(refs_before, self.git('show-ref', '--head'))

    def test_unprotected_default_blocks_review_and_keeps_branch_classifications(self):
        inventory = self.inventory([[
            branch('trunk', self.default, protected=False),
            branch('feature', self.feature, protected=False),
            branch('backup', self.unique, protected=False),
        ]])

        report = self.inspect(inventory)

        self.assertEqual('blocked', report['status'])
        self.assertEqual([], report['errors'])
        self.assertEqual({'name': 'trunk', 'sha': self.default}, report['default_branch'])
        rows = {row['name']: row for row in report['branches']}
        self.assertEqual({'trunk': 'default', 'feature': 'reachable_unreferenced', 'backup': 'unique_history'},
                         {name: row['status'] for name, row in rows.items()})
        self.assertIs(rows['trunk']['protected'], False)
        finding, = report['findings']
        self.assertEqual('default_branch_unprotected', finding['code'])
        self.assertEqual('trunk', finding['branch'])
        self.assertIn('pnpm project:protection', finding['action'])

    def test_recorded_pr_base_can_lag_the_live_stacked_base(self):
        inventory = self.inventory([[
            branch('trunk', self.default, protected=True),
            branch('stack/base', self.feature),
            branch('stack/head', self.unique),
        ]], [[pull(26, base=('stack/base', self.root_sha), head=('stack/head', self.unique))]])

        report = self.inspect(inventory)

        self.assertEqual('passed', report['status'], report['errors'])
        rows = {row['name']: row for row in report['branches']}
        base = rows['stack/base']
        self.assertEqual(('in_use', [26], self.feature), (base['status'], base['open_pr_bases'], base['sha']))
        pr = report['open_pull_requests'][0]
        self.assertEqual((self.root_sha, self.feature), (pr['base']['sha'], pr['base']['live_sha']))
        self.assertEqual('outside_default_history', pr['status'])

    def test_same_repository_head_conflict_holds_the_live_branch(self):
        inventory = self.inventory([[
            branch('trunk', self.default, protected=True),
            branch('feature', self.feature),
        ]], [[pull(26, base=('trunk', self.default), head=('feature', self.unique))]])

        report = self.inspect(inventory)

        self.assertEqual('blocked', report['status'])
        self.assertIn('PR #26 head feature', report['errors'][0])
        feature = report['branches'][0]
        self.assertEqual(('in_use', [26], True), (feature['status'], feature['open_pr_heads'],
                                                 feature['reachable_from_default']))
        pr = report['open_pull_requests'][0]
        self.assertEqual((self.unique, self.feature), (pr['head']['sha'], pr['head']['live_sha']))
        self.assertEqual('uncertain', pr['status'])
        self.assertIsNone(pr['reachable_from_default'])

    def test_paginated_open_prs_include_later_branch_holds(self):
        first_heads = [branch('trunk', self.default, protected=True)]
        first_heads.extend(branch(f'feature-{n}', self.feature) for n in range(1, 100))
        first_pulls = [pull(n, base=('trunk', self.default), head=(f'feature-{n}', self.feature))
                       for n in range(1, 100)]
        first_pulls.append(pull(100, base=('trunk', self.default), head=('other', '8' * 40), repo=FORK))
        report = self.inspect(self.inventory(
            [first_heads, [branch('late-base', self.feature), branch('late-head', self.unique)]],
            [first_pulls, [pull(101, base=('late-base', self.feature), head=('late-head', self.unique))]],
        ))
        self.assertEqual('passed', report['status'], report['errors'])
        self.assertEqual(101, len(report['open_pull_requests']))
        self.assertEqual(102, len(report['branches']))
        rows = {row['name']: row for row in report['branches']}
        self.assertEqual(('in_use', [101]), (rows['late-base']['status'], rows['late-base']['open_pr_bases']))
        self.assertEqual(('in_use', [101]), (rows['late-head']['status'], rows['late-head']['open_pr_heads']))

    def test_unique_history_and_missing_commit_are_distinct(self):
        absent = '9' * 40
        inventory = self.inventory([[branch('trunk', self.default, protected=True), branch('backup', self.unique),
                                     branch('missing', absent)]])
        # A promisor configuration must not turn an ancestry check into an implicit fetch.
        self.git('config', 'remote.origin.promisor', 'true')
        trace = self.root / 'git-trace.log'
        with patch.dict('os.environ', {'GIT_TRACE': str(trace)}):
            report = self.inspect(inventory)
        git_trace = trace.read_text(encoding='utf-8')
        self.assertIn('cat-file', git_trace)
        self.assertNotIn(' fetch ', git_trace)
        self.assertEqual('blocked', report['status'])
        self.assertIn(absent, report['errors'][0])
        self.assertEqual({'backup': 'unique_history', 'missing': 'uncertain', 'trunk': 'default'},
                         {row['name']: row['status'] for row in report['branches']})

    def test_shallow_checkout_cannot_prove_unique_history(self):
        git_dir = Path(self.git('rev-parse', '--absolute-git-dir'))
        (git_dir / 'shallow').write_text(f'{self.default}\n', encoding='utf-8')
        report = self.inspect(self.inventory([[branch('trunk', self.default, protected=True),
                                               branch('feature', self.feature)]]))
        self.assertEqual('blocked', report['status'])
        self.assertIn('non-shallow', report['errors'][0])
        self.assertEqual('uncertain', report['branches'][0]['status'])

    def test_local_commit_replacements_do_not_create_reachability(self):
        self.git('replace', '--graft', self.default, self.unique)
        self.git('merge-base', '--is-ancestor', self.unique, self.default)
        report = self.inspect(self.inventory([[branch('trunk', self.default, protected=True),
                                               branch('unique', self.unique)]]))
        self.assertEqual('passed', report['status'], report['errors'])
        self.assertEqual('unique_history', report['branches'][1]['status'])

    def test_remote_change_withholds_classifications(self):
        original = self.inventory([[branch('trunk', self.default, protected=True), branch('feature', self.feature)]])
        for endpoint, revised in [
            (BRANCHES, [[branch('trunk', self.unique, protected=True), branch('feature', self.feature)]]),
            (BRANCHES, [[branch('trunk', self.default, protected=True), branch('feature', self.feature, protected=True)]]),
            (BRANCHES, [[branch('trunk', self.default, protected=False), branch('feature', self.feature)]]),
            (PULLS, [[pull(3, base=('trunk', self.default), head=('feature', self.feature))]]),
            (ENDPOINT, {**original[ENDPOINT], 'delete_branch_on_merge': False}),
        ]:
            with self.subTest(endpoint=endpoint, revised=revised):
                final = copy.deepcopy(original)
                final[endpoint] = revised
                report = self.inspect(original, final)
                self.assertEqual('blocked', report['status'])
                self.assertIn('changed during inspection', report['errors'][0])
                self.assertEqual([], report['branches'])
                self.assertIsNone(report['delete_branch_on_merge'])
                self.assertNotIn('default_branch_unprotected', {finding['code'] for finding in report['findings']})

    def test_disabled_or_unknown_auto_deletion_has_an_actionable_finding(self):
        for metadata, observed, reason in [
            ({'delete_branch_on_merge': False}, False, 'delete_branch_on_merge_disabled'),
            ({}, None, 'delete_branch_on_merge_unknown'),
            ({'delete_branch_on_merge': 1}, None, 'delete_branch_on_merge_unknown'),
        ]:
            with self.subTest(metadata=metadata):
                inventory = self.inventory()
                inventory[ENDPOINT] = {**REPO, 'default_branch': 'trunk', **metadata}
                report = self.inspect(inventory)
                self.assertEqual('blocked', report['status'])
                self.assertIs(report['delete_branch_on_merge'], observed)
                self.assertEqual([], report['errors'])
                self.assertEqual(reason, report['findings'][0]['code'])
                self.assertIn('GitHub Settings', report['findings'][0]['action'])

    def test_invalid_or_unavailable_remote_input_blocks_review(self):
        trunk = branch('trunk', self.default, protected=True)
        for endpoint, payload, reason in [
            (BRANCHES, {'message': 'unexpected response'}, 'paginated response'),
            (BRANCHES, [[[trunk]]], 'branch record'),
            (BRANCHES, [[trunk, trunk]], 'Duplicate branch'),
            (BRANCHES, [[branch('trunk', self.default, protected=None)]], 'protection status'),
            (BRANCHES, [[branch('trunk', self.default[:12], protected=True)]], 'commit SHA'),
            (BRANCHES, [[branch('other', self.default)]], 'Default branch'),
            (PULLS, [[pull(5, base=('trunk', 'short'), head=('other', self.unique), repo=FORK)]], 'PR #5 base'),
            (PULLS, [[pull(5, base=('trunk', self.default), head=('trunk', 'short'))]], 'PR #5 head'),
            (PULLS, [[pull(5, base=('trunk', self.default), head=('unknown', self.feature), repo=None)]],
             'repository for PR #5 head'),
            (PULLS, 'not JSON', 'Expecting value'),
            (PULLS, ProjectError('gh command failed: rate limit'), 'rate limit'),
        ]:
            with self.subTest(payload=payload):
                inventory = self.inventory()
                inventory[endpoint] = payload
                report = self.inspect(inventory)
                self.assertEqual('blocked', report['status'])
                self.assertIn(reason, report['errors'][0])
                self.assertEqual([], report['branches'])
                self.assertEqual([], report['open_pull_requests'])
