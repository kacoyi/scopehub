"""Exercise verification selection against real commit trees and local Git states."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.verification_scope import SCOPED_PROFILES, committed_scope, repository_root


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'verification_scope.py'
ENGINEERING = 'engineering-contract-v1.json'
CONTRACT = 'contracts/contract.json'
DEVELOPMENT = 'project/development.json'
LEDGER = 'project/tasks.json'
CATALOG = 'project/checks.json'
HERDR = 'scripts/herdr.py'
HERDR_TEST = 'tests/project/test_herdr.py'
LOCALE = 'apps/desktop/ui/src/i18n/locales/zh-Hans/core.json'
APP = 'apps/desktop/ui/src/i18n/index.ts'
ADR = 'contracts/decisions/0001-scope.json'
REVIEW = 'contracts/reviews/2026-11-22-publication.json'
PR_NUMBER = 31
PR_REF = f'refs/pull/{PR_NUMBER}/merge'


class VerificationScopeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'fixture checkout'
        self.root.mkdir()
        self.event_file = self.root.parent / 'event.json'
        self.output = self.root.parent / 'outputs.txt'
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('GITHUB_', 'GIT_'))}
        self.env['GIT_CONFIG_NOSYSTEM'] = '1'
        self.env['GIT_CONFIG_GLOBAL'] = os.devnull
        self.git('init', '--initial-branch=main', '--object-format=sha1', '--template=')
        with (self.root / '.git' / 'config').open('ab') as config:
            config.write(
                b'\n[user]\n\tname = Scope Fixture\n\temail = scope@example.invalid\n'
                b'[core]\n\tautocrlf = false\n'
                b'[commit]\n\tgpgsign = false\n'
            )
        self.write('.gitignore', '*.ignored\n')
        self.write('AGENTS.md', '# Contributor guidance\n')
        self.write('README.md', '# Documentation\n')
        self.write(LEDGER, {'tasks': []})
        self.write(CATALOG, {
            'checks': [
                {'id': 'catalog-check', 'status': 'implemented'},
                {'id': 'fixture-localization', 'status': 'implemented', 'cadence': 'change'},
                {'id': 'planned-check', 'status': 'planned'},
                {'id': 'fixture-tooling', 'status': 'implemented', 'cadence': 'change'},
                {'id': 'task-check', 'status': 'implemented', 'cadence': 'task'},
                {'id': 'retired-check', 'status': 'retired'},
                {'id': 'release-check', 'status': 'implemented', 'cadence': 'release'},
            ],
            'profiles': {
                'records': ['catalog-check'],
                'tooling': ['fixture-tooling', 'catalog-check'],
                'localization': ['fixture-localization', 'catalog-check'],
                'full': ['catalog-check'],
            },
        })
        self.write(ENGINEERING, {
            'product': {'purpose': 'Initial purpose', 'design_principles': ['One', 'Two']},
            'authorization': {'required': True, 'limit': 9007199254740993.0},
        })
        self.write(CONTRACT, {'clarifications': ['Initial wording'], 'operations': ['read']})
        self.write(DEVELOPMENT, {
            'herdr': {
                'host': 'Herdr',
                'profiles': {'implement': {'family': 'sol'}, 'supervise': {'family': 'astra'}},
                'reasoning_efforts': ['medium', 'high'],
                'model_selection': ['Choose the profile for the assignment.'],
                'parallel': ['Start in the existing checkout.'],
                'tooling': ['Use approved development tools.'],
            },
            'quality': {'review': True},
        })
        self.write(HERDR, "APPROVAL_POLICY = 'auto-review'\n")
        self.write(HERDR_TEST, 'def test_fixture():\n    assert True\n')
        self.write(LOCALE, {'Status {status}': '状态 {status}', 'items': {'other': '{count} 项'}})
        self.write(APP, 'export {};\n')
        self.write(ADR, {'id': '0001', 'status': 'accepted', 'decision': 'Initial record'})
        self.write(REVIEW, {'publication_scope': 'draft PR', 'observation': 'Initial record'})
        self.base = self.commit('initial tree')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)

    def git(self, *args, cwd=None, input_data=None):
        result = subprocess.run(
            ['git', *args], cwd=cwd or self.root, env=self.env, input=input_data,
            capture_output=True, timeout=15, check=False,
        )
        if result.returncode:
            self.fail(f'Git {args!r} failed: {result.stderr!r}')
        return result.stdout

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        target.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)

    def edit(self, path, change):
        value = json.loads((self.root / path).read_text(encoding='utf-8'))
        change(value)
        self.write(path, value)

    def head(self):
        return self.git('rev-parse', '--verify', 'HEAD').strip().decode('ascii')

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-m', message)
        return self.head()

    def cli(self, *args, cwd=None, env=None, code=0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=cwd or self.root,
            env=self.env if env is None else env, capture_output=True,
            encoding='utf-8', timeout=20,
        )
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stderr, '')
        lines = result.stdout.splitlines()
        decision = json.loads(lines[0])
        expected = {'profile', 'reason', 'base', 'checkout', 'change_count'}
        if '--ci' not in args:
            expected.add('checks')
        self.assertEqual(set(decision), expected)
        self.assertIn(decision['profile'], SCOPED_PROFILES | {'full'})
        self.assertTrue(decision['reason'])
        return decision, lines[1:]

    def ci(self, event, *, checkout, name='push', cwd=None, extra_env=None):
        self.event_file.write_text(
            json.dumps(event) if isinstance(event, dict) else event, encoding='utf-8',
        )
        self.output.write_text('existing=value\n', encoding='utf-8')
        env = {
            **self.env, 'GITHUB_EVENT_NAME': name, 'GITHUB_SHA': checkout,
            'GITHUB_EVENT_PATH': str(self.event_file), 'GITHUB_OUTPUT': str(self.output),
            'GITHUB_REF': PR_REF if name == 'pull_request' else 'refs/heads/main',
            **(extra_env or {}),
        }
        env = {key: value for key, value in env.items() if value is not None}
        decision, extra = self.cli('--ci', cwd=cwd, env=env)
        self.assertEqual(extra, [])
        if decision['profile'] in SCOPED_PROFILES:
            code = 'reviewed_changes'
        elif decision['change_count'] is None:
            code = 'comparison_unavailable'
        elif decision['change_count'] == 0:
            code = 'no_changes'
        else:
            code = 'change_requires_full'
        provenance = dict(
            profile=decision['profile'], reason_code=code, base=decision['base'],
            checkout=decision['checkout'], change_count=decision['change_count'],
        )
        expected = json.dumps(provenance, separators=(',', ':'))
        self.assertEqual(self.output.read_text(encoding='utf-8'),
                         f"existing=value\nprofile={decision['profile']}\ndecision={expected}\n")
        return decision

    def push(self, checkout, **overrides):
        return {'ref': 'refs/heads/main', 'before': self.base, 'after': checkout, **overrides}

    def pull(self, base, head, merge, *, number=PR_NUMBER):
        return {'number': number, 'pull_request': {
            'base': {'sha': base}, 'head': {'sha': head}, 'merge_commit_sha': merge,
        }}

    def assert_local(self, profile, count, *args):
        decision, extra = self.cli(*args)
        self.assertEqual(extra, [])
        self.assertEqual((decision['profile'], decision['change_count']), (profile, count), decision)
        return decision

    def test_readme_and_non_authority_json_records(self):
        paths = {
            'README.md': '# Updated documentation\n',
            'contracts/references.json': {'references': []},
            LEDGER: {'tasks': ['fixture']},
            'project/feedback.json': {'feedback': []},
            'project/evidence/M0-060-0123456789abcdef.json': {'task_id': 'M0-060'},
        }
        for path, data in paths.items():
            self.write(path, data)
        self.assert_local('records', len(paths))
        head = self.commit('reviewed records')
        decision = self.ci(self.push(head), checkout=head)
        self.assertEqual((decision['profile'], decision['change_count']), ('records', len(paths)))
        self.assertEqual(decision, committed_scope(self.root, self.base, head))

    def test_prose_fields_allow_existing_strings_and_fixed_length_lists(self):
        self.edit(ENGINEERING, lambda doc: doc['product'].update(
            purpose='Refined purpose', design_principles=['First statement', 'Second statement'],
        ))
        self.edit(CONTRACT, lambda doc: doc.update(clarifications=['Clearer statement']))
        self.assert_local('records', 2)
        head = self.commit('wording only')
        self.assertEqual(self.ci(self.push(head), checkout=head)['profile'], 'records')

    def test_structure_or_executable_values_in_prose_files_require_full(self):
        changes = {
            'drop an entry': (ENGINEERING, lambda doc: doc['product']['design_principles'].pop()),
            'add an entry': (CONTRACT, lambda doc: doc['clarifications'].append('New statement')),
            'nested value': (CONTRACT, lambda doc: doc.update(clarifications=[{'text': 'New'}])),
            'type change': (ENGINEERING, lambda doc: doc['product'].update(purpose=['Different'])),
            'missing field': (ENGINEERING, lambda doc: doc['product'].pop('purpose')),
            'new field': (ENGINEERING, lambda doc: doc['product'].update(resource_policy='Review')),
            'operation': (CONTRACT, lambda doc: doc['operations'].append('write')),
            'bool vs number': (ENGINEERING, lambda doc: doc['authorization'].update(required=1)),
            'number spelling': (ENGINEERING, lambda doc: doc['authorization'].update(limit=9007199254740992)),
        }
        for label, (path, change) in changes.items():
            with self.subTest(change=label):
                self.git('restore', '--', path)
                self.edit(path, change)
                self.assert_local('full', 1)
                self.git('restore', '--', path)

    def test_prose_cannot_mask_equal_looking_executable_edits(self):
        for before, after in (
            ('true', '1'), ('100.0', '100'), ('9007199254740993.0', '9007199254740992.0'),
            ('{"first": 1, "second": 2}', '{"second": 2, "first": 1}'),
        ):
            with self.subTest(before=before, after=after):
                self.write(CONTRACT, '{"operations":' + before + ',"clarifications":["first"]}')
                base = self.commit('store executable baseline')
                self.write(CONTRACT, '{"operations":' + after + ',"clarifications":["second"]}')
                self.assert_local('full', 1, '--base', base)

    def test_json_ambiguity_requires_full(self):
        invalid = (
            b'{"clarifications":["changed"], "clarifications":["duplicate"], "operations":["read"]}',
            br'{"clarifications":["changed"], "operatio\u006es":["read"], "operations":["read"]}',
            br'{"clarifications":["\ud800"], "operations":["read"]}',
            b'{"clarifications":[NaN], "operations":["read"]}',
            b'{"clarifications":["changed"], "operations":["read"]} trailing',
            b'{"clarifications":["\xff"], "operations":["read"]}',
        )
        for data in invalid:
            with self.subTest(data=data):
                self.write(CONTRACT, data)
                decision = self.assert_local('full', 1)
                self.assertIn('ambiguous', decision['reason'])

    def test_tooling_and_localization_can_each_accompany_records(self):
        self.edit(LEDGER, lambda doc: doc['tasks'].append('records'))
        self.edit(DEVELOPMENT, lambda doc: doc['herdr']['reasoning_efforts'].append('xhigh'))
        self.write(HERDR_TEST, 'def test_fixture():\n    assert 2 + 2 == 4\n')
        self.assert_local('tooling', 3)
        self.git('add', '--', LEDGER, DEVELOPMENT, HERDR_TEST)
        self.edit(LOCALE, lambda doc: doc.update({'Status {status}': '{status} 为当前状态'}))
        self.assert_local('full', 4)
        self.git('restore', '--staged', '--worktree', '--', HERDR_TEST, DEVELOPMENT)
        self.assert_local('localization', 2)
        head = self.commit('localization and records')
        self.assertEqual(self.ci(self.push(head), checkout=head)['profile'], 'localization')

    def test_authority_and_launcher_changes_require_full(self):
        amendments = {
            'AGENTS.md': '# Updated contributor guidance\n',
            ADR: {'id': '0001', 'status': 'accepted', 'decision': 'Updated formal decision'},
            REVIEW: {'publication_scope': 'publish and merge', 'observation': 'Initial record'},
            HERDR: "APPROVAL_POLICY = 'never'\n",
        }
        self.write('README.md', '# Ordinary documentation change\n')
        for path, data in amendments.items():
            with self.subTest(path=path):
                self.write(path, data)
                self.assert_local('full', 2)
                self.git('restore', '--', path)
        self.assert_local('records', 1)
        self.write(REVIEW, {'publication_scope': 'publish and merge', 'observation': 'Initial record'})
        head = self.commit('change publication authorization')
        decision = self.ci(self.push(head), checkout=head)
        self.assertEqual((decision['profile'], decision['change_count']), ('full', 2))

    def test_development_only_allows_declarative_herdr_routing(self):
        routing = (
            lambda doc: doc['herdr']['profiles']['supervise'].update(family='sol'),
            lambda doc: doc['herdr']['reasoning_efforts'].append('xhigh'),
            lambda doc: doc['herdr']['model_selection'].append('Choose from the stable model catalog.'),
        )
        for change in routing:
            self.edit(DEVELOPMENT, change)
            self.assert_local('tooling', 1)
            self.git('restore', '--', DEVELOPMENT)
        controls = (
            lambda doc: doc['quality'].update(review=False),
            lambda doc: doc['herdr']['parallel'].append('Use a separate checkout.'),
            lambda doc: doc['herdr']['tooling'].append('Install tools without review.'),
            lambda doc: doc['herdr'].update(approvals={'policy': 'never'}),
            lambda doc: doc['herdr'].update(unknown_field='unknown'),
            lambda doc: doc['herdr']['profiles']['implement'].update(approval_policy='never'),
        )
        for change in controls:
            self.edit(DEVELOPMENT, routing[0])
            self.edit(DEVELOPMENT, change)
            self.assert_local('full', 1)
            self.git('restore', '--', DEVELOPMENT)
        self.edit(DEVELOPMENT, routing[0])
        head = self.commit('reviewed model routing')
        self.assertEqual(self.ci(self.push(head), checkout=head)['profile'], 'tooling')

    def test_malformed_herdr_efforts_require_full_for_explicit_commit_and_ci(self):
        for malformed in (['high'], {'effort': 'high'}):
            with self.subTest(malformed=malformed):
                self.git('restore', '--source', self.base, '--', DEVELOPMENT)
                self.edit(DEVELOPMENT, lambda doc: doc['herdr']['reasoning_efforts'].append(malformed))
                head = self.commit('malformed effort in Herdr routing')
                decision = self.ci(self.push(head), checkout=head)
                self.assertEqual((decision['profile'], decision['change_count']), ('full', 1))
                self.assertEqual(decision, committed_scope(self.root, self.base, head))

    def test_localization_structure_interpolation_and_unknown_catalog_require_full(self):
        changes = (
            lambda doc: doc.update(new='新键'),
            lambda doc: doc.pop('Status {status}'),
            lambda doc: doc.update({'Status {status}': '当前状态'}),
            lambda doc: doc.update({'Status {status}': '{value} 当前状态'}),
            lambda doc: doc.update({'Status {status}': '{status} 当前状态 {status}'}),
            lambda doc: doc.update({'Status {status}': {'other': '状态 {status}'}}),
            lambda doc: doc['items'].update(one='{count} 项'),
        )
        for change in changes:
            self.edit(LOCALE, change)
            self.assert_local('full', 1)
            self.git('restore', '--', LOCALE)
        self.edit(LOCALE, lambda doc: doc['items'].update(other='共 {count} 条'))
        self.assert_local('localization', 1)
        self.write(LOCALE.replace('core.json', 'other.json'), {'key': '文本'})
        self.assert_local('full', 2)

    def test_unknown_and_highest_risk_paths_override_records(self):
        self.write('README.md', '# Updated documentation\n')
        paths = (
            '.github/workflows/verify.yml', 'scripts/check.py', 'scripts/verification_scope.py',
            'project/checks.json', 'project/delivery.json', 'project/branch-protection.json',
            'project/dependency-inventory.json', 'contracts/schemas/protocol.schema.json',
            'scripts/storage.py', 'Cargo.toml', 'pnpm-lock.yaml', '.gitattributes',
            'apps/desktop/src-tauri/permissions/default.toml',
            'apps/desktop/ui/src/i18n/languages.json', APP, 'other/README.md', 'nested/AGENTS.md',
            'contracts/reviews/nested/2026-11-22-scope.json',
            'project/evidence/arbitrary.json', 'project/assessments/review.json',
        )
        for path in paths:
            with self.subTest(path=path):
                self.write(path, 'fixture\n')
                self.assert_local('full', 2)
                if path in (APP, CATALOG):
                    self.git('restore', '--', path)
                else:
                    (self.root / path).unlink()

    def test_staged_risk_survives_unstaged_revert_and_ignored_files_are_omitted(self):
        self.write(APP, 'export const changed = true;\n')
        self.git('add', '--', APP)
        self.git('restore', '--worktree', '--source=HEAD', '--', APP)
        self.write('README.md', '# Documentation edit\n')
        self.write('generated/code.ignored', 'ignored\n')
        self.assert_local('full', 2)
        self.git('restore', '--staged', '--', APP)
        self.assert_local('records', 1)
        self.write('untracked.py', 'untracked\n')
        self.assert_local('full', 2)

    def test_intent_to_add_uses_worktree_content(self):
        path = 'project/feedback.json'
        self.write(path, {'feedback': []})
        self.git('add', '-N', '--', path)
        self.assert_local('records', 1)
        self.write(path, b'not JSON\n')
        self.assert_local('full', 1)

    def test_rename_and_deletion_are_full_without_rename_heuristics(self):
        self.git('config', 'diff.renames', 'copies')
        self.git('mv', '--', 'README.md', 'other.md')
        self.assert_local('full', 2)
        head = self.commit('move documentation')
        self.assertEqual(self.ci(self.push(head), checkout=head)['change_count'], 2)
        self.assertEqual(self.ci(self.push(head), checkout=head)['profile'], 'full')
        self.git('rm', '--', LEDGER)
        self.assert_local('full', 1, '--base', 'HEAD')

    def test_unsafe_modes_and_binary_docs_are_full(self):
        self.git('update-index', '--chmod=+x', '--', 'README.md')
        self.assert_local('full', 1)
        self.git('restore', '--staged', '--worktree', '--', 'README.md')
        path = 'project/evidence/M0-060-0123456789abcdef.json'
        self.git('update-index', '--add', '--cacheinfo', f'160000,{self.base},{path}')
        self.assert_local('full', 1)
        self.git('update-index', '--force-remove', '--', path)
        self.write('README.md', b'not\0documentation')
        self.assert_local('full', 1)

    @unittest.skipIf(os.name == 'nt', 'Unprivileged symlinks are unavailable on some Windows hosts')
    def test_untracked_symlink_is_full(self):
        (self.root / 'project/feedback.json').symlink_to('tasks.json')
        decision, _ = self.cli()
        self.assertEqual(decision['profile'], 'full')
        self.assertIn('unsafe mode', decision['reason'])

    def test_conflict_hidden_entries_and_dirty_ci_require_full(self):
        self.write('README.md', '# Conflicting change\n')
        self.assertEqual(self.ci(self.push(self.base), checkout=self.base)['profile'], 'full')
        self.git('update-index', '--assume-unchanged', '--', 'README.md')
        decision, _ = self.cli()
        self.assertEqual(decision['profile'], 'full')
        self.assertIn('hidden', decision['reason'])
        self.git('update-index', '--no-assume-unchanged', '--', 'README.md')
        blob = self.git('rev-parse', 'HEAD:README.md').strip()
        self.git('update-index', '--index-info', input_data=b'0 ' + b'0' * 40 + b'\tREADME.md\n'
                 + b'100644 ' + blob + b' 1\tREADME.md\n'
                 + b'100644 ' + blob + b' 2\tREADME.md\n')
        decision, _ = self.cli()
        self.assertEqual(decision['profile'], 'full')
        self.assertIn('conflicts', decision['reason'])

    def test_pr_compares_the_exact_merge_against_the_current_event_base(self):
        self.git('switch', '-c', 'topic')
        self.write(APP, 'export const temporary = true;\n')
        self.commit('temporary executable change')
        self.git('restore', '--source', self.base, '--', APP)
        self.write('README.md', '# Prose at the merge endpoint\n')
        topic = self.commit('restore executable and edit prose')
        self.git('switch', 'main')
        self.write('base-only.py', 'this is already on main\n')
        base = self.commit('advance main')
        self.git('merge', '--no-ff', 'topic', '-m', 'test merge')
        merge = self.head()
        event = self.pull(base, topic, merge)
        decision = self.ci(event, checkout=merge, name='pull_request')
        self.assertEqual((decision['profile'], decision['base'], decision['checkout'], decision['change_count']),
                         ('records', base, merge, 1))
        self.assertEqual(decision, committed_scope(self.root, base, merge))
        event['pull_request']['merge_commit_sha'] = None
        self.assertEqual(self.ci(event, checkout=merge, name='pull_request'), decision)
        del event['pull_request']['merge_commit_sha']
        self.assertEqual(self.ci(event, checkout=merge, name='pull_request'), decision)
        for number in (None, True, 0, -PR_NUMBER, float(PR_NUMBER), str(PR_NUMBER), PR_NUMBER + 1):
            with self.subTest(number=number):
                bad_number = self.pull(base, topic, merge, number=number)
                result = self.ci(bad_number, checkout=merge, name='pull_request')
                self.assertEqual(result['profile'], 'full')
                self.assertIn('merge ref', result['reason'])
        for ref in (None, f'refs/pull/{PR_NUMBER}/head', 'refs/heads/main', f'refs/pull/{PR_NUMBER + 1}/merge'):
            with self.subTest(ref=ref):
                result = self.ci(event, checkout=merge, name='pull_request', extra_env={'GITHUB_REF': ref})
                self.assertEqual(result['profile'], 'full')
                self.assertIn('merge ref', result['reason'])

    def test_synchronize_classifies_actual_merge_and_rejects_conflicting_identities(self):
        self.git('switch', '-c', 'topic')
        self.write('README.md', '# First update\n')
        first_head = self.commit('first update')
        self.git('switch', 'main')
        self.git('merge', '--no-ff', 'topic', '-m', 'first preview')
        old_preview = self.head()

        for path, content, profile, count in (
                ('README.md', '# New wording\n', 'records', 1),
                (APP, 'export const changed = true;\n', 'full', 2)):
            self.git('switch', 'topic')
            self.write(path, content)
            topic = self.commit('synchronize topic')
            self.git('switch', '--detach', self.base)
            self.git('merge', '--no-ff', 'topic', '-m', 'current preview')
            checkout = self.head()
            event = self.pull(self.base, topic, old_preview)
            event.update(action='synchronize', after=topic)
            decision = self.ci(event, checkout=checkout, name='pull_request')
            self.assertEqual((decision['profile'], decision['base'], decision['checkout'], decision['change_count']),
                             (profile, self.base, checkout, count))
            for bad_event, extra in (
                    ({**event, 'after': first_head}, {}),
                    ({**event, 'after': None}, {}),
                    (event, {'GITHUB_SHA': old_preview}),
                    (event, {'GITHUB_REF': 'refs/pull/32/merge'}),
                    ({**event, 'pull_request': {**event['pull_request'], 'merge_commit_sha': 'invalid'}}, {}),
                    ({**event, 'after': first_head,
                      'pull_request': self.pull(self.base, first_head, checkout)['pull_request']}, {}),
                    ({**event, 'pull_request': {**event['pull_request'], 'base': {'sha': first_head}}}, {})):
                result = self.ci(bad_event, checkout=checkout, name='pull_request', extra_env=extra)
                self.assertEqual((result['profile'], result['change_count']), ('full', None))

    def test_pr_merge_only_changes_are_included_and_wrong_parents_are_rejected(self):
        self.git('switch', '-c', 'topic')
        self.write('README.md', '# PR text\n')
        topic = self.commit('edit PR text')
        self.git('switch', 'main')
        self.git('merge', '--no-ff', '--no-commit', 'topic')
        self.write(APP, 'export const mergeOnlyChange = true;\n')
        merge = self.commit('merge with executable difference')
        decision = self.ci(self.pull(self.base, topic, merge), checkout=merge, name='pull_request')
        self.assertEqual((decision['profile'], decision['change_count']), ('full', 2))
        for event in (self.pull(topic, self.base, merge), self.pull(self.base, merge, merge)):
            rejected = self.ci(event, checkout=merge, name='pull_request')
            self.assertEqual(rejected['profile'], 'full')
            self.assertIn('parents', rejected['reason'])
        stale = self.pull(self.base, topic, topic)
        self.assertIn('merge SHA', self.ci(stale, checkout=merge, name='pull_request')['reason'])
        self.git('switch', '--detach', topic)
        event = self.pull(self.base, topic, topic)
        self.assertIn('parents', self.ci(event, checkout=topic, name='pull_request')['reason'])

    def test_pr_requires_exact_commit_ids_and_exactly_two_parents(self):
        self.git('switch', '-c', 'topic')
        self.write('README.md', '# PR text\n')
        topic = self.commit('edit PR text')
        self.git('switch', 'main')
        self.git('merge', '--no-ff', 'topic', '-m', 'two-parent preview')
        merge = self.head()
        decision = self.ci(self.pull(self.base, topic, merge), checkout=merge, name='pull_request')
        self.assertEqual((decision['profile'], decision['change_count']), ('records', 1))

        for field, parent in (('base', self.base), ('head', topic)):
            with self.subTest(tag_for=field):
                tag = f'fixture-{field}'
                self.git('tag', '-am', f'annotated {field} tag', tag, parent)
                tag_oid = self.git('rev-parse', '--verify', tag).strip().decode('ascii')
                event = self.pull(self.base, topic, merge)
                event['pull_request'][field]['sha'] = tag_oid
                rejected = self.ci(event, checkout=merge, name='pull_request')
                self.assertEqual((rejected['profile'], rejected['change_count']), ('full', None))

        extra = self.git('commit-tree', f'{self.base}^{{tree}}', '-p', self.base, '-m',
                         'unrelated third parent').strip().decode('ascii')
        octopus = self.git('commit-tree', f'{merge}^{{tree}}', '-p', self.base, '-p', topic,
                           '-p', extra, '-m', 'three-parent preview').strip().decode('ascii')
        self.git('switch', '--detach', octopus)
        rejected = self.ci(self.pull(self.base, topic, octopus), checkout=octopus, name='pull_request')
        self.assertEqual((rejected['profile'], rejected['change_count']), ('full', None))
        self.assertIn('parents', rejected['reason'])

    def test_push_uses_full_before_to_after_range_and_requires_event_identity(self):
        self.write('Cargo.toml', '[workspace]\n')
        self.commit('build change earlier in push')
        self.write('README.md', '# Latest commit is prose\n')
        head = self.commit('edit prose')
        event = self.push(head)
        decision = self.ci(event, checkout=head)
        self.assertEqual((decision['profile'], decision['change_count']), ('full', 2))
        for overrides in (
            {'before': '0' * 40}, {'before': 'a' * 40}, {'before': 'HEAD'}, {'before': None},
            {'after': self.base}, {'ref': 'refs/heads/other'}, {'forced': True}, {'deleted': True},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.ci(self.push(head, **overrides), checkout=head)['profile'], 'full')
        self.assertIn('GITHUB_SHA', self.ci(event, checkout=self.base)['reason'])
        self.assertEqual(self.ci(event, checkout=head, extra_env={'GITHUB_SHA': ''})['profile'], 'full')
        for ref in (None, 'refs/heads/topic'):
            result = self.ci(event, checkout=head, extra_env={'GITHUB_REF': ref})
            self.assertEqual(result['profile'], 'full')
            self.assertIn('push to main', result['reason'])
        self.assertEqual(self.ci(event, checkout=head, name='schedule')['profile'], 'full')
        self.assertEqual(self.ci('{"ref":"bad","ref":"duplicate"}', checkout=head)['profile'], 'full')
        self.assertEqual(self.ci(event, checkout=head, extra_env={'GITHUB_EVENT_PATH': ''})['profile'], 'full')
        self.git('switch', '--detach', self.base)
        self.write('README.md', '# Text on divergent history\n')
        divergent = self.commit('divergent checkout')
        decision = self.ci(self.push(divergent, before=head), checkout=divergent)
        self.assertEqual(decision['profile'], 'full')
        self.assertIn('ancestor', decision['reason'])
        self.assertEqual(committed_scope(self.root, head, divergent)['profile'], 'full')

    def test_tracked_head_path_does_not_obscure_checkout(self):
        self.write('HEAD', 'A tracked file named HEAD\n')
        base = self.commit('track HEAD as a file')
        self.write('README.md', '# Documentation edit\n')
        head = self.commit('edit documentation')
        decision = self.ci(self.push(head, before=base), checkout=head)
        self.assertEqual((decision['profile'], decision['base'], decision['checkout'], decision['change_count']),
                         ('records', base, head, 1))

    def test_committed_scope_checks_commit_objects_independently_of_worktree(self):
        self.write('README.md', '# Committed documentation\n')
        head = self.commit('reviewed document')
        self.git('tag', '-am', 'fixture tag', 'fixture-tag')
        tag_sha = self.git('rev-parse', 'fixture-tag').strip().decode('ascii')
        self.write(APP, 'export const dirty = true;\n')
        self.assertEqual(self.ci(self.push(head), checkout=head)['profile'], 'full')
        self.assertEqual(committed_scope(self.root, self.base, head)['profile'], 'records')
        self.assertEqual(committed_scope(self.root, self.base, tag_sha)['profile'], 'full')

    def test_missing_base_empty_comparison_and_shallow_history_require_full(self):
        decision = self.assert_local('full', 0)
        self.assertIn('no changes', decision['reason'])
        decision = self.ci(self.push(self.base), checkout=self.base)
        self.assertEqual((decision['profile'], decision['change_count']), ('full', 0))
        decision = self.assert_local('full', None, '--base', 'missing')
        self.assertIsNone(decision['base'])
        self.assertIn('base ref', decision['reason'])
        self.write('README.md', '# Document change\n')
        head = self.commit('documentation')
        clone = self.root.parent / 'shallow'
        self.git('clone', '--depth=1', self.root.as_uri(), str(clone))
        self.assertIn('Shallow', self.ci(self.push(head), checkout=head, cwd=clone)['reason'])
        self.assertEqual(committed_scope(clone, self.base, head)['profile'], 'full')

    def test_opaque_paths_never_reach_action_outputs_or_decision(self):
        name = ('contracts/reviews/2026-11-22-不透明\nprofile=records.json' if os.name != 'nt'
                else 'contracts/reviews/2026-11-22-不透明 profile=records.json')
        self.write(name, {'reviewed_on': '2026-11-22'})
        head = self.commit('opaque path')
        decision = self.ci(self.push(head), checkout=head)
        self.assertEqual((decision['profile'], decision['change_count']), ('full', 1))
        self.assertNotIn('profile=records', json.dumps(decision))

    def test_local_preview_lists_catalog_selection_in_harness_order(self):
        self.write('README.md', '# Documentation change\n')
        self.assertEqual(self.assert_local('records', 1)['checks'], ['catalog-check'])
        self.write(HERDR_TEST, 'def test_fixture():\n    assert 2 + 2 == 4\n')
        self.assertEqual(self.assert_local('tooling', 2)['checks'], ['catalog-check', 'fixture-tooling'])
        self.git('restore', '--', HERDR_TEST)
        self.edit(LOCALE, lambda doc: doc.update({'Status {status}': '当前状态 {status}'}))
        self.assertEqual(self.assert_local('localization', 2)['checks'],
                         ['catalog-check', 'fixture-localization'])
        self.write('unknown.py', 'fixture\n')
        self.assertEqual(self.assert_local('full', 3)['checks'],
                         ['catalog-check', 'fixture-localization', 'planned-check', 'fixture-tooling'])

    def test_unavailable_catalog_and_invalid_selections_fall_back_to_full(self):
        self.write('README.md', '# Documentation change\n')
        catalog = self.root / CATALOG
        catalog.unlink()
        decision = self.assert_local('full', 2)
        self.assertIsNone(decision['checks'])
        self.assertIn('catalog is unavailable', decision['reason'])
        self.git('add', '--', CATALOG)
        self.git('commit', '-m', 'remove catalog from fixture')
        decision = self.assert_local('full', 1, '--base', 'HEAD')
        self.assertIsNone(decision['checks'])

        for ids in ([], ['unknown-check'], ['catalog-check', 'catalog-check']):
            with self.subTest(ids=ids):
                self.write(CATALOG, {
                    'checks': [{'id': 'catalog-check', 'status': 'implemented'}],
                    'profiles': {'records': ids},
                })
                self.git('add', '--', CATALOG)
                self.git('commit', '-m', 'install catalog with an invalid scoped selection')
                decision = self.assert_local('full', 1, '--base', 'HEAD')
                self.assertEqual(decision['checks'], ['catalog-check'])
                self.assertIn('invalid check IDs', decision['reason'])

    def test_git_root_line_endings_on_windows_and_posix(self):
        raw_root = os.fsencode(self.root)
        with patch('scripts.verification_scope.sys.platform', 'win32'):
            for ending in (b'\n', b'\r\n'):
                with patch('scripts.verification_scope.git', return_value=raw_root + ending):
                    self.assertEqual(repository_root(self.root / 'scripts'), self.root)
        if os.name != 'nt':
            with patch('scripts.verification_scope.git', return_value=raw_root + b'\r\n'):
                self.assertEqual(repository_root(self.root / 'scripts').name, self.root.name + '\r')

    def test_run_uses_exact_base_for_history_and_routes_harness_exit_status(self):
        stub = (
            'import json, os, sys\n'
            'from pathlib import Path\n'
            'name = Path(sys.argv[0]).name\n'
            'print(json.dumps([name, *sys.argv[1:]]))\n'
            'sys.exit(int(os.environ.get("HISTORY_EXIT" if name == "project.py" else "CHECK_EXIT", "0")))\n'
        )
        self.write('scripts/check.py', stub)
        self.write('scripts/project.py', stub)
        base = self.commit('install synthetic harness')
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.write('README.md', '# Local edit\n')
        nested = self.root / 'scripts'
        history = ['project.py', 'history', '--base', base]
        decision, lines = self.cli('--run', cwd=nested)
        self.assertEqual(decision['base'], base)
        self.assertEqual(list(map(json.loads, lines)), [history, ['check.py', '--profile', 'records']])

        self.git('restore', '--', 'README.md')
        self.write(HERDR_TEST, 'def test_fixture():\n    assert 2 + 2 == 4\n')
        decision, lines = self.cli('--run', '--base', 'origin/main', env={**self.env, 'CHECK_EXIT': '11'}, code=11)
        self.assertEqual(decision['profile'], 'tooling')
        self.assertEqual(list(map(json.loads, lines)), [history, ['check.py', '--profile', 'tooling']])

        self.git('restore', '--', HERDR_TEST)
        self.edit(LOCALE, lambda doc: doc.update({'Status {status}': '当前状态 {status}'}))
        decision, lines = self.cli('--run', cwd=nested)
        self.assertEqual(decision['profile'], 'localization')
        self.assertEqual(list(map(json.loads, lines)), [history, ['check.py', '--profile', 'localization']])

        self.write('unknown.py', 'fixture\n')
        decision, lines = self.cli('--run')
        self.assertEqual(decision['profile'], 'full')
        self.assertEqual(list(map(json.loads, lines)), [history, ['check.py']])
        _, lines = self.cli('--run', env={**self.env, 'HISTORY_EXIT': '7'}, code=7)
        self.assertEqual(list(map(json.loads, lines)), [history])
        decision, lines = self.cli('--run', '--base', 'unavailable')
        self.assertEqual((decision['profile'], decision['base']), ('full', None))
        self.assertEqual(list(map(json.loads, lines)), [['check.py']])

        self.git('restore', '--', LOCALE)
        (self.root / 'unknown.py').unlink()
        self.write('README.md', '# Prose committed to main\n')
        divergent_base = self.commit('advance main')
        self.git('switch', '-c', 'divergent-topic', base)
        self.write('README.md', '# Prose on topic\n')
        self.commit('commit on divergent topic')
        history = ['project.py', 'history', '--base', divergent_base]
        decision, lines = self.cli('--run', '--base', 'main')
        self.assertEqual((decision['profile'], decision['base']), ('full', divergent_base))
        self.assertIn('ancestor', decision['reason'])
        self.assertEqual(list(map(json.loads, lines)), [history, ['check.py']])
        _, lines = self.cli('--run', '--base', 'main', env={**self.env, 'HISTORY_EXIT': '7'}, code=7)
        self.assertEqual(list(map(json.loads, lines)), [history])

        shallow = self.root.parent / 'shallow with base'
        self.git('clone', '--depth=2', self.root.as_uri(), str(shallow))
        self.assertEqual(self.git('cat-file', '-t', base, cwd=shallow).strip(), b'commit')
        decision, lines = self.cli('--run', '--base', base, cwd=shallow)
        self.assertEqual((decision['profile'], decision['base']), ('full', base))
        self.assertIn('Shallow', decision['reason'])
        self.assertEqual(list(map(json.loads, lines)), [['project.py', 'history', '--base', base], ['check.py']])


if __name__ == '__main__':
    unittest.main()
