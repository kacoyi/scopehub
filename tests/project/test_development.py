"""Independent navigation, ownership and evidence regressions for AI development."""

import copy
import json

from scripts.check_architecture import declared_source_boundaries
from scripts.development import product_brief, resolve_source, status_view
from scripts.project import (
    ProjectError, archive_evidence, json_bytes, load_project, product_progress,
    read_json, task_context, task_definition_hash, workfront,
)
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


class DevelopmentTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        spec = read_json(self.root / 'engineering-contract-v1.json')
        spec.update(repository={'development': 'project/development.json'}, product={'purpose': 'Synthetic research purpose.'})
        self.save('engineering-contract-v1.json', spec)
        self.save('contracts/contract.json', {'operations': [
            {'name': 'read.synthetic', 'implementation': 'implemented', 'implementation_scope': 'Fixture only.'},
            {'name': 'write.synthetic', 'implementation': 'planned'},
        ]})
        self.record = {
            'schema_version': '1.0',
            'collaboration': {key: [f'Synthetic {key}.'] for key in
                              ['owner','development_ai','ask_owner_when','question_protocol','product_ai_separation']},
            'quality': {key: [f'Synthetic {key}.'] for key in
                        ['module_design','state_ownership','verification','evolution','review','feedback_loop']},
            'modules': [{'id': 'fixture', 'status': 'implemented', 'paths': ['implementation.py'],
                         'responsibility': 'Synthetic implementation.', 'public_entries': ['implementation.py'],
                         'dependencies': [], 'owns': ['Synthetic state'], 'invariants': ['Synthetic rule'],
                         'checks': ['project-state']}],
            'capabilities': [{'id': 'fixture-capability',
                              'source': {'path': 'engineering-contract-v1.json', 'pointer': '/product/purpose'},
                              'task_ids': ['M0-001'], 'delivery': 'current-contract', 'boundary': 'Not product evidence.'}],
            'improvement_metrics': ['Synthetic lookup measurement.'],
            'data_ownership': [{'id': 'fixture-state', 'owner': 'fixture', 'writer': 'fixture',
                                'status': 'implemented', 'rule': 'Synthetic state mutation.'}],
        }
        self.save_record()

    def save(self, path, value):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(json_bytes(value))

    def save_record(self):
        self.save('project/development.json', self.record)

    def test_active_work_requires_navigation_and_evolution(self):
        ledger = self.ledger()
        task = task_by_id(ledger, 'M0-001')
        task['status'] = 'active'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'implementation and evolution'):
            load_project(self.root)
        task['plan'].update(
            implementation={'modules': ['fixture'], 'entrypoints': ['implementation.py'],
                            'rules': [self.record['capabilities'][0]['source']], 'non_goals': [], 'open_questions': []},
            evolution={'kind': 'refactor', 'preserve': ['Fixture output'], 'change': ['Fixture internals'],
                       'data_plan': 'No persistent state.', 'cleanup': 'Remove replaced fixture code.'})
        self.save_ledger(ledger)
        self.assertEqual('active', load_project(self.root)['tasks']['M0-001']['status'])
        task['plan']['implementation']['entrypoints'] = ['missing.py']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Missing task entrypoint'):
            load_project(self.root)

    def test_module_records_reject_drift_cycles_and_private_entry_escape(self):
        baseline = copy.deepcopy(self.record)
        for mutation in ['duplicate', 'missing', 'unknown-check', 'cycle', 'entry-escape']:
            with self.subTest(mutation=mutation):
                self.record = copy.deepcopy(baseline)
                module = self.record['modules'][0]
                if mutation == 'duplicate':
                    self.record['modules'].append(copy.deepcopy(module))
                elif mutation == 'missing':
                    module['paths'] = ['absent']
                elif mutation == 'unknown-check':
                    module['checks'] = ['unknown']
                elif mutation == 'cycle':
                    (self.root / 'other.py').write_text('value = 2\n')
                    other = {**copy.deepcopy(module), 'id': 'other', 'dependencies': ['fixture'],
                             'paths': ['other.py'], 'public_entries': ['other.py']}
                    module['dependencies'] = ['other']
                    self.record['modules'].append(other)
                else:
                    module['public_entries'] = ['outside.py']
                self.save_record()
                with self.assertRaises(ProjectError):
                    load_project(self.root)

    def test_capabilities_need_real_sources_and_non_cancelled_delivery(self):
        item = self.record['capabilities'][0]
        item['source']['pointer'] = '/product/missing'
        self.save_record()
        with self.assertRaisesRegex(ProjectError, 'Missing source pointer'):
            load_project(self.root)
        item['source']['pointer'] = '/product/purpose'
        item['task_ids'] = []
        self.save_record()
        with self.assertRaisesRegex(ProjectError, 'lacks tracked delivery'):
            load_project(self.root)
        item['task_ids'] = ['M0-002']
        self.save_record()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-002').update(status='cancelled', closure_reason='Synthetic cancellation.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'only cancelled'):
            load_project(self.root)
        item['task_ids'] = ['M0-001']
        self.save_record()
        task_by_id(ledger, 'M0-001')['delivery'] = 'later'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'only later delivery'):
            load_project(self.root)

    def test_state_ownership_and_current_release_scope_are_explicit(self):
        self.record['data_ownership'].append(copy.deepcopy(self.record['data_ownership'][0]))
        self.save_record()
        with self.assertRaisesRegex(ProjectError, 'multiple ownership'):
            load_project(self.root)
        self.record['data_ownership'].pop()
        self.record['data_ownership'][0]['writer'] = 'unknown'
        self.save_record()
        with self.assertRaisesRegex(ProjectError, 'Unknown state owner'):
            load_project(self.root)
        self.record['data_ownership'][0]['writer'] = 'fixture'
        self.save_record()
        project = load_project(self.root)
        baseline = project['tasks']['M0-001']
        later = {**copy.deepcopy(baseline), 'id': 'M0-999', 'delivery': 'later'}
        self.assertNotEqual(task_definition_hash(baseline), task_definition_hash({**baseline, 'delivery': 'later'}))
        for task in project['tasks'].values():
            task['status'] = 'done'  # Pure progress calculation, not completion evidence.
        project['tasks'][later['id']] = later
        results = [{'id': key, 'status': 'passed'} for key in project['checks']]
        milestone = next(item for item in product_progress(project, results)[0] if item['id'] == 'M0')
        self.assertEqual('passed', milestone['status'])
        self.assertEqual(['M0-999'], milestone['later_task_ids'])

    def test_cancelled_current_work_cannot_cover_a_capability_delivered_only_later(self):
        capability = self.record['capabilities'][0]
        capability['task_ids'] = ['M0-002', 'M0-004']
        self.save_record()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-004')['delivery'] = 'later'
        self.save_ledger(ledger)
        load_project(self.root)
        task_by_id(ledger, 'M0-002').update(status='cancelled', closure_reason='Superseded.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'only later delivery'):
            load_project(self.root)
        capability['task_ids'].append('M0-001')
        self.save_record()
        load_project(self.root)

    def test_json_pointers_handle_escapes_and_reject_escape_or_bad_index(self):
        self.save('reference.json', {'a/b': {'~key': ['expected']}})
        self.assertEqual('expected', resolve_source(self.root, {'path': 'reference.json', 'pointer': '/a~1b/~0key/0'}))
        for pointer in ['/a~2b', '/a~1b/~0key/-1', '/a~1b/~0key/00']:
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                resolve_source(self.root, {'path': 'reference.json', 'pointer': pointer})
        with self.assertRaisesRegex(ValueError, 'repository-relative'):
            resolve_source(self.root, {'path': '../outside.json', 'pointer': ''})

    def test_task_context_exposes_the_role_families_and_shared_quality_floor(self):
        self.record['herdr'] = {
            'host': 'Herdr', 'profiles': {'implement': {'family': 'sol'}, 'supervise': {'family': 'astra'}},
            'reasoning_efforts': ['medium', 'high'], 'model_selection': ['Raise effort for a risky assignment.'],
            'parallel': ['Keep review independent.'], 'tooling': ['Use synthetic fixtures.'],
        }
        self.save_record()
        host = task_context(self.root, 'M0-001')['development_host']
        self.assertEqual(host['profiles'], {'implement': {'family': 'sol'}, 'supervise': {'family': 'astra'}})
        self.assertEqual(host['default_reasoning_effort'], 'medium')
        for efforts in (['low', 'medium'], ['medium', 'low'], ['high'], []):
            with self.subTest(efforts=efforts):
                self.record['herdr']['reasoning_efforts'] = efforts
                self.save_record()
                with self.assertRaisesRegex(ProjectError, 'reasoning_efforts'):
                    load_project(self.root)

        self.record['herdr']['reasoning_efforts'] = ['medium']
        self.record['herdr']['profiles']['supervise']['family'] = 'astra\n'
        self.save_record()
        with self.assertRaisesRegex(ProjectError, 'profiles/supervise/family'):
            load_project(self.root)

    def test_context_omits_dependency_history_but_keeps_effective_rule_sources(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-002')['acceptance'] = ['Large historical prerequisite. ' * 1000]
        task_by_id(ledger, 'M0-001')['depends_on'] = ['M0-002']
        self.save_ledger(ledger)
        decision = read_json(self.root / 'contracts/decisions/0003-repository-harness.json')
        decision.update(id='0007', supersedes=['0003'], decision='Current synthetic decision.')
        self.save('contracts/decisions/0007-successor.json', decision)
        focused = task_context(self.root, 'M0-001')
        detailed = task_context(self.root, 'M0-001', detail=True)
        self.assertLess(len(json.dumps(focused)), len(json.dumps(detailed)) / 2)
        self.assertNotIn('acceptance', focused['dependencies'][0])
        successor = next(item for item in focused['decisions'] if item['id'] == '0007')
        self.assertEqual('contracts/decisions/0007-successor.json', successor['path'])
        self.assertEqual('Current synthetic decision.', successor['decision'])
        self.assertIn('quality_review', task_context(self.root, 'M0-001', phase='verify'))
        self.assertIn('handoff', task_context(self.root, 'M0-001', phase='handoff'))
        with self.assertRaisesRegex(ProjectError, 'Unknown context phase'):
            task_context(self.root, 'M0-001', phase='unknown')

    def test_context_shows_revisit_conditions_of_current_applicable_decisions(self):
        decision = read_json(self.root / 'contracts/decisions/0003-repository-harness.json')
        self.save('contracts/decisions/0007-intermediate.json', {
            **decision, 'id': '0007', 'supersedes': ['0003'], 'revisit_when': 'Intermediate condition.',
        })
        current_conditions = ['The source changes.', 'The recovery path changes.']
        self.save('contracts/decisions/0008-current.json', {
            **decision, 'id': '0008', 'supersedes': ['0007'], 'revisit_when': current_conditions,
        })
        text_condition = 'The supported format changes.'
        self.save('contracts/decisions/0009-applicable.json', {
            **decision, 'id': '0009', 'revisit_when': text_condition,
        })
        self.save('contracts/decisions/0010-proposed.json', {
            **decision, 'id': '0010', 'status': 'proposed', 'supersedes': ['0008'],
            'revisit_when': 'Proposed condition.',
        })
        self.save('contracts/decisions/0011-rejected.json', {
            **decision, 'id': '0011', 'status': 'rejected', 'revisit_when': 'Rejected condition.',
        })
        self.save('contracts/decisions/0012-unrelated.json', {
            **decision, 'id': '0012', 'status': 'accepted', 'revisit_when': 'Unrelated condition.',
        })
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['decision_refs'] = ['0003', '0009', '0010', '0011']
        self.save_ledger(ledger)

        for phase in ('implement', 'verify', 'handoff'):
            with self.subTest(phase=phase):
                decisions = {item['id']: item for item in task_context(self.root, 'M0-001', phase=phase)['decisions']}
                self.assertEqual(
                    {'0003': ('accepted', '0007'), '0007': ('accepted', '0008'),
                     '0008': ('accepted', None), '0009': ('accepted', None),
                     '0010': ('proposed', None), '0011': ('rejected', None)},
                    {key: (item['status'], item['superseded_by']) for key, item in decisions.items()},
                )
                self.assertEqual(
                    {'0008': current_conditions, '0009': text_condition},
                    {key: item['revisit_when'] for key, item in decisions.items() if 'revisit_when' in item},
                )

    def test_brief_derives_product_and_implementation_instead_of_copying_summary(self):
        brief = product_brief(load_project(self.root))
        self.assertEqual('Synthetic research purpose.', brief['purpose'])
        self.assertEqual(['read.synthetic'], [item['name'] for item in brief['implemented_operations']])
        self.assertEqual(self.record['collaboration'], brief['collaboration'])
        spec = read_json(self.root / 'engineering-contract-v1.json')
        spec['product']['purpose'] = 'Revised requirement.'
        self.save('engineering-contract-v1.json', spec)
        self.assertEqual('Revised requirement.', product_brief(load_project(self.root))['purpose'])

    def test_historical_receipts_do_not_hide_stale_or_failed_latest_checks(self):
        path = self.report()
        self.finish_first(archive_evidence(self.root, 'M0-001', path))
        report = read_json(path)
        report['status'] = 'failed'
        report['checks'][0].update(status='failed', exit_code=1)
        self.save('.reports/test-report.json', report)
        state = workfront(self.root)
        check = next(item for item in state['evidence_overview']['checks'] if item['id'] == 'project-state')
        self.assertEqual('failed', check['explanation'])
        self.assertEqual('failed', state['verification']['recorded_status'])
        self.assertEqual('M0-001', state['evidence_overview']['historical_task_evidence'][0]['task_id'])
        (self.root / 'implementation.py').write_text('changed = True\n')
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNone(state['milestones'])
        self.assertFalse(state['evidence_overview']['historical_task_evidence'][0]['matches_execution_inputs'])
        self.assertTrue(all(item['explanation'] == 'stale_report' for item in state['evidence_overview']['checks']))

    def test_current_omissions_are_explained_without_becoming_passes(self):
        path = self.report()
        report = read_json(path)
        report['checks'][0].update(status='blocked', reason_code='not_selected')
        report['selection'] = {'profile': 'fast', 'task_id': None}
        self.save('.reports/test-report.json', report)
        state = workfront(self.root)
        checks = {item['id']: item for item in state['evidence_overview']['checks']}
        self.assertEqual('not_selected', checks['project-state']['explanation'])
        self.assertEqual('not_implemented', checks['ai-workflow']['explanation'])
        self.assertEqual('fast', state['evidence_overview']['latest_selection']['profile'])
        compact = status_view(state)
        self.assertIn('project-state', compact['evidence_overview']['check_groups']['not_selected'])
        self.assertNotIn('plan', compact['focus'])
        self.assertIn('plan', state['focus'])
        self.assertIs(state, status_view(state, detail=True))

    def test_malformed_latest_report_is_explained_without_crashing_status(self):
        path = self.report()
        baseline = read_json(path)
        for broken in [{**baseline, 'checks': 'invalid'}, {**baseline, 'source': []}, None]:
            with self.subTest(broken=type(broken).__name__):
                self.save('.reports/test-report.json', broken)
                state = workfront(self.root)
                self.assertFalse(state['verification']['current'])
                self.assertIsNotNone(state['verification']['report_error'])
                self.assertTrue(all(item['explanation'] == 'invalid_report'
                                    for item in state['evidence_overview']['checks']))
        path.write_text('{broken JSON')
        self.assertIsNotNone(workfront(self.root)['verification']['report_error'])

    def source_fixture(self):
        root = 'apps/desktop/ui/src'
        for name in ['main.ts', 'adapter/public.ts', 'adapter/private.ts']:
            path = self.root / root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('export const value = 1;\n')
        base = self.record['modules'][0]
        self.record['modules'] = [
            {**base, 'id': 'ui', 'paths': [root], 'public_entries': [root + '/main.ts'], 'dependencies': ['adapter']},
            {**base, 'id': 'adapter', 'paths': [root + '/adapter'], 'public_entries': [root + '/adapter/public.ts'], 'dependencies': []},
        ]
        self.save_record()
        return self.root / root / 'main.ts'

    def test_declared_dependencies_and_private_entries_reject_real_imports(self):
        caller = self.source_fixture()
        caller.write_text("import { value } from './adapter/public';\n")
        self.assertEqual([], declared_source_boundaries(self.root))
        caller.write_text("export { value } from './adapter/private';\n")
        self.assertTrue(any('Private module entry' in item for item in declared_source_boundaries(self.root)))
        caller.write_text("import('./adapter/public');\n")
        self.record['modules'][0]['dependencies'] = []
        self.save_record()
        self.assertTrue(any('Forbidden module dependency' in item for item in declared_source_boundaries(self.root)))

    def test_equivalent_checkout_root_preserves_import_boundaries(self):
        caller = self.source_fixture()
        alias = self.root / '..' / self.root.name
        caller.write_text("import './adapter/public';\n")
        self.assertEqual([], declared_source_boundaries(alias))
        caller.write_text("import './adapter/private';\nimport '../../../../../outside';\n")
        self.assertEqual([
            'Private module entry: ui -> apps/desktop/ui/src/adapter/private.ts',
            f'Import outside repository: {caller.relative_to(self.root)}',
        ], declared_source_boundaries(alias))

    def test_python_import_crossing_uses_declared_public_entry(self):
        base = self.record['modules'][0]
        for name in ['caller/main.py', 'adapter/public.py', 'adapter/private.py']:
            path = self.root / 'python' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('value = 1\n')
        self.record['modules'] = [
            {**base, 'id': 'caller', 'paths': ['python/caller'], 'public_entries': ['python/caller/main.py'], 'dependencies': ['adapter']},
            {**base, 'id': 'adapter', 'paths': ['python/adapter'], 'public_entries': ['python/adapter/public.py'], 'dependencies': []},
        ]
        self.save_record()
        caller = self.root / 'python/caller/main.py'
        caller.write_text('from adapter.public import value\n')
        self.assertEqual([], declared_source_boundaries(self.root))
        caller.write_text('from adapter.private import value\n')
        self.assertTrue(any('Private module entry' in item for item in declared_source_boundaries(self.root)))
        caller.write_text('from ..adapter.private import value\n')
        self.assertTrue(any('Private module entry' in item for item in declared_source_boundaries(self.root)))
