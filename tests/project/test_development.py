"""Independent navigation, ownership and evidence regressions for AI development."""

import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.check_architecture import declared_source_boundaries
from scripts.development import product_brief, resolve_source, status_view
from scripts.project import (
    ProjectError, archive_evidence, digest, json_bytes, load_project, product_progress,
    read_json, task_context, task_definition_hash, workfront,
)
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


class MaintenanceHandoffTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict('os.environ', {'SCOPEHUB_ARCHIVE_ROOT': str(self.root / 'missing-archive')}))
        self.delivery_path = self.root / 'project/delivery.json'
        self.delivery = read_json(self.delivery_path)
        self.authority = {
            'outcomes': [
                {'id': 'repository-cleanup', 'status': 'open', 'owner': 'Audit coordinator',
                 'acceptance': ['Recheck the integrated inventory.'], 'next_action': 'Read the initial inventory.'},
                {'id': 'ongoing-maintenance', 'status': 'open', 'owner': 'Maintenance coordinator',
                 'acceptance': ['Observe a due review.'], 'next_action': 'Read the initial schedule.'},
            ],
            'engagement': {
                'complete_when': ['repository-cleanup', 'ongoing-maintenance'],
                'on_completion': 'Deliver the evidence and stop assigning work.',
                'later_implementation': 'Obtain a fresh owner instruction for later features.',
                'scheduled_checks': 'Keep the existing cadence.',
            },
            'resume': {'unavailable_private_archive': 'Missing originals leave their evidence obligations open.'},
            'evolution': {'trigger': 'Recurring failures.', 'action': 'Compare replacement and repair.',
                          'verify': 'Observe the next comparable change.'},
        }
        self.engagement = {
            'authority': self.retain('fixture-owner-scope', '/owner_scope', {'owner_scope': self.authority}),
            'workstreams': {
                'repository-cleanup': {'next_action': 'Assign the independently observed audit finding.'},
                'ongoing-maintenance': {'next_action': 'Reconcile producer originals before the next due review.'},
            },
        }

    def retain(self, name, pointer, record):
        path = f'contracts/reviews/{name}.json'
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = json_bytes(record)
        destination.write_bytes(raw)
        return {'path': path, 'pointer': pointer, 'sha256': digest(raw)}

    def save_engagement(self):
        self.delivery['scaffold_maintenance']['engagement'] = self.engagement
        self.delivery_path.write_bytes(json_bytes(self.delivery))

    def observe(self):
        overview = status_view(workfront(self.root), overview=True, read_only=True)
        return overview, task_context(self.root, phase='handoff')

    def follow_source(self, command):
        arguments = shlex.split(command)
        source = 'project:source' if 'project:source' in arguments else 'source'
        path, pointer = arguments[arguments.index(source) + 1:][:2]
        return resolve_source(self.root, {'path': path, 'pointer': pointer})

    def accept_workstream(self, identity):
        observation = {'workstream_id': identity, 'status': 'accepted',
                       'observation': f'Retained fixture acceptance of {identity}.'}
        closure = self.retain(f'fixture-closure-{identity}', '/closure', {'closure': observation})
        self.engagement['workstreams'][identity] = {'next_action': None, 'closure': closure}
        return closure, observation

    def test_public_handoff_recovers_commitments_after_a_closed_repair_transfers_focus(self):
        ledger = self.ledger()
        repair = task_by_id(ledger, 'M0-001')
        repair.update(title='Repair an interrupted handoff', status='active')
        audit = task_by_id(ledger, 'M0-002')
        audit.update(title='Audit a peer lock during an interrupted check', depends_on=['M0-001'],
                     next_action='Exercise the running peer and rejected released-lock control.',
                     plan=copy.deepcopy(repair['plan']))
        self.save_ledger(ledger)
        self.save_engagement()
        initial, _ = self.observe()
        evidence = archive_evidence(self.root, repair['id'], self.report(repair['id']))
        self.finish_first(evidence)
        ledger = self.ledger()
        task_by_id(ledger, audit['id'])['status'] = 'active'
        self.save_ledger(ledger)

        overview, handoff = self.observe()
        self.assertEqual('active', overview['focus']['status'])
        self.assertEqual(audit['id'], overview['focus']['id'])
        self.assertEqual('done', load_project(self.root)['tasks'][repair['id']]['status'])
        self.assertEqual(audit['next_action'], handoff['task']['next_action'])
        summary = overview['maintenance']['engagement']
        self.assertEqual(initial['maintenance']['engagement'], summary)
        self.assertEqual('active', summary['status'])
        self.assertEqual(2, summary['open_count'])
        self.assertEqual({'repository-cleanup': 'open', 'ongoing-maintenance': 'open'},
                         {row['id']: row['status'] for row in summary['workstreams']})
        self.assertIn('scripts/project.py context --phase handoff', summary['handoff_command'])
        self.assertNotIn(self.engagement['authority']['path'], json.dumps(summary))

        continuation = handoff['maintenance_engagement']
        current = {row['id']: row for row in continuation['workstreams']}
        source = self.follow_source(continuation['owner_source_command'])
        self.assertEqual(self.authority, source)
        self.assertEqual(source['engagement'], continuation['stop_when'])
        self.assertEqual(source['resume']['unavailable_private_archive'], continuation['unavailable_private_archive'])
        for original in source['outcomes']:
            row = current[original['id']]
            self.assertEqual('open', row['status'])
            self.assertEqual(original['owner'], row['owner'])
            self.assertTrue(original['acceptance'])
            self.assertEqual(self.engagement['workstreams'][row['id']]['next_action'], row['next_action'])
            self.assertNotIn('closure', row)

    def test_individual_closures_end_coordination_without_changing_the_feature_task(self):
        ledger = self.ledger()
        feature = task_by_id(ledger, 'M1-002')
        feature.update(title='Implement an unrelated product feature', next_action='Exercise the product interaction.')
        ledger['focus_task_id'] = feature['id']
        self.save_ledger(ledger)
        ordinary, ordinary_handoff = self.observe()
        self.assertNotIn('engagement', ordinary['maintenance'])
        self.assertNotIn('maintenance_engagement', ordinary_handoff)

        cleanup_closure, cleanup_observation = self.accept_workstream('repository-cleanup')
        self.save_engagement()
        overview, handoff = self.observe()
        self.assertEqual('active', overview['maintenance']['engagement']['status'])
        self.assertEqual(1, overview['maintenance']['engagement']['open_count'])
        first = {row['id']: row for row in handoff['maintenance_engagement']['workstreams']}
        self.assertEqual('open', first['ongoing-maintenance']['status'])
        cleanup = first['repository-cleanup']
        self.assertEqual('accepted', cleanup['status'])
        self.assertNotIn('next_action', cleanup)
        self.assertEqual(cleanup_closure, cleanup['closure'])
        self.assertEqual(cleanup_observation, self.follow_source(cleanup['closure_command']))

        self.accept_workstream('ongoing-maintenance')
        self.save_engagement()
        overview, handoff = self.observe()
        ended = overview['maintenance'].pop('engagement')
        self.assertEqual('ended', ended['status'])
        self.assertEqual(0, ended['open_count'])
        self.assertEqual(ordinary['maintenance'], overview['maintenance'])
        continuation = handoff.pop('maintenance_engagement')
        self.assertEqual('ended', continuation['status'])
        self.assertEqual(self.authority['engagement'], continuation['stop_when'])
        self.assertTrue(all(row['status'] == 'accepted' and 'next_action' not in row and row['closure']
                            for row in continuation['workstreams']))
        self.assertEqual(self.authority, self.follow_source(continuation['owner_source_command']))
        self.assertEqual(ordinary_handoff, handoff)
        self.assertEqual(ordinary_handoff['task'], task_context(self.root)['task'])

    def test_handoff_rejects_unverified_authority_or_closure(self):
        closure, _ = self.accept_workstream('repository-cleanup')
        authority = self.engagement['authority']
        for reference in (authority, closure):
            with self.subTest(path=reference['path']):
                destination = self.root / reference['path']
                original = destination.read_bytes()
                self.save_engagement()
                destination.write_bytes(original + b'\n')
                with self.assertRaisesRegex(ProjectError, 'Engagement source digest mismatch'):
                    task_context(self.root, phase='handoff')
                destination.write_bytes(original)
                pointer = reference['pointer']
                reference['pointer'] = '/missing'
                self.save_engagement()
                with self.assertRaisesRegex(ProjectError, 'Missing source pointer'):
                    workfront(self.root)
                reference['pointer'] = pointer

        wrong = {'workstream_id': 'ongoing-maintenance', 'status': 'accepted',
                 'observation': 'Fixture closure for a different workstream.'}
        self.engagement['workstreams']['repository-cleanup']['closure'] = self.retain('fixture-wrong-closure', '', wrong)
        self.save_engagement()
        with self.assertRaisesRegex(ProjectError, 'Engagement closure needs an observation for workstream repository-cleanup'):
            task_context(self.root, phase='handoff')

    def test_valid_digest_cannot_turn_rejected_or_incomplete_observation_into_acceptance(self):
        self.accept_workstream('repository-cleanup')
        identity = 'ongoing-maintenance'
        _, accepted = self.accept_workstream(identity)
        rejected = {**accepted, 'status': 'rejected', 'observation': 'The due review was missing.'}
        missing_status = {key: value for key, value in accepted.items() if key != 'status'}
        missing_observation = {key: value for key, value in accepted.items() if key != 'observation'}
        for observation, expected in (
            (rejected, 'explicit acceptance'), (missing_status, 'explicit acceptance'),
            (missing_observation, 'needs an observation'),
        ):
            with self.subTest(observation=observation):
                closure = self.retain('fixture-maintenance-observation', '', observation)
                self.engagement['workstreams'][identity]['closure'] = closure
                self.save_engagement()
                for view in (lambda: status_view(workfront(self.root), overview=True),
                             lambda: task_context(self.root, phase='handoff')):
                    with self.assertRaisesRegex(ProjectError, rf'{expected}.*{identity}'):
                        view()

    def test_read_only_handoff_commands_run_verbatim_with_an_unwritable_cache(self):
        scripts = Path(__file__).resolve().parents[2] / 'scripts'
        shutil.copytree(scripts, self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        with (self.root / '.gitignore').open('a', encoding='utf-8') as ignores:
            ignores.write('.venv/\n')
        interpreter_environment = self.root / '.venv'
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(sys.prefix, str(interpreter_environment))
            self.addCleanup(interpreter_environment.rmdir)
        else:
            interpreter_environment.symlink_to(sys.prefix, target_is_directory=True)
            self.addCleanup(interpreter_environment.unlink)
        unwritable_cache = self.root / 'cache-is-a-file'
        unwritable_cache.touch()
        env = {**os.environ, 'UV_CACHE_DIR': str(unwritable_cache), 'UV_OFFLINE': '1'}

        def cli(command):
            arguments = command if os.name == 'nt' else shlex.split(command)
            result = subprocess.run(arguments, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, f'{command}\n{result.stderr or result.stdout}')
            return json.loads(result.stdout)

        self.engagement['authority'] = self.retain('fixture owner scope', '', self.authority)
        _, observation = self.accept_workstream('repository-cleanup')
        self.save_engagement()
        ordinary = task_context(self.root, phase='handoff')['maintenance_engagement']
        self.assertTrue(ordinary['owner_source_command'].startswith('pnpm project:source '))
        executable = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
        overview = cli(f'{executable} -B scripts/project.py status --overview --read-only')
        summary = overview['maintenance']['engagement']
        handoff = cli(summary['handoff_command'])
        self.assertEqual(overview['focus']['id'], handoff['task']['id'])
        engagement = handoff['maintenance_engagement']
        self.assertEqual(self.authority, cli(engagement['owner_source_command'])['value'])
        accepted, = (row for row in engagement['workstreams'] if row['status'] == 'accepted')
        self.assertEqual(observation, cli(accepted['closure_command'])['value'])


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

    def test_engineering_index_resolves_sections_and_historical_nested_pointers(self):
        reference = {'path': 'engineering-contract-v1.json', 'pointer': '/runtime'}
        link = {'path': 'worker.json', 'pointer': '/settings'}
        value = {'scaled/value': [7]}
        self.save(reference['path'], {'runtime': {'$source': link}})
        self.save('worker.json', {'settings': value})
        self.assertEqual(resolve_source(self.root, reference), value)
        nested = dict(reference, pointer='/runtime/scaled~1value/0')
        self.assertEqual(resolve_source(self.root, nested), 7)
        for path in ['../outside.json', './engineering-contract-v1.json']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.save(reference['path'], {'runtime': {'$source': dict(link, path=path)}})
                resolve_source(self.root, nested)

    def test_engineering_operation_pointers_select_the_negotiated_command_and_reply(self):
        root = Path(__file__).resolve().parents[2]
        path = 'engineering-contract-v1.json'
        expected = {'input_spec': 'WorkspaceOpenInstrumentCommand11', 'output_spec': 'WorkspaceOpenInstrumentReply11'}
        row = resolve_source(root, {'path': path, 'pointer': '/operations/2'})
        self.assertEqual(row['name'], 'workspace.openInstrument')
        for field, value in expected.items():
            self.assertEqual(row[field], value)
            self.assertEqual(resolve_source(root, {'path': path, 'pointer': f'/operations/2/{field}'}), value)
        for field in ('roles', 'language', 'validation', 'packaging'):
            self.assertTrue(resolve_source(root, {'path': path, 'pointer': f'/stack/workers/{field}'}))

    def test_linked_workflow_and_nested_fixture_reach_navigation_and_project_checks(self):
        path = 'engineering-contract-v1.json'
        spec = read_json(self.root / path)
        spec['ai'] = {'$source': {'path': 'workflow.json', 'pointer': '/ai'}}
        self.save(path, spec)
        self.save('workflow.json', {'ai': {
            'case': {'$source': {'path': 'fixture.json', 'pointer': '/expected'}},
            'model_connections': {'primary_cloud_route': {'task': 'unknown', 'check': 'ai-workflow'}},
        }})
        self.save('fixture.json', {'expected': {'volume': None}})
        self.assertIsNone(resolve_source(self.root, {'path': path, 'pointer': '/ai/case/volume'}))
        self.assertEqual(resolve_source(self.root, {'path': path, 'pointer': '/ai'})['case'], {'volume': None})
        with self.assertRaisesRegex(ProjectError, 'primary cloud route'):
            load_project(self.root)

    def test_cycle_between_delegated_local_records_fails_with_the_offending_reference(self):
        path = 'engineering-contract-v1.json'
        self.save(path, {'ui': {'$source': {'path': 'workbench.json', 'pointer': ''}}})
        self.save('workbench.json', {'preview': {'$source': {'path': 'host.json', 'pointer': '/preview'}}})
        self.save('host.json', {'preview': {'$source': {'path': './workbench.json', 'pointer': ''}}})
        with self.assertRaisesRegex(ValueError, 'Cyclic engineering source link'):
            resolve_source(self.root, {'path': path, 'pointer': '/ui/preview'})

    def test_deep_and_shared_source_records_observe_the_view_budgets(self):
        index = {'path': 'engineering-contract-v1.json', 'pointer': ''}
        def link(name):
            return {'$source': {'path': name, 'pointer': ''}}

        self.save(index['path'], {'first': link('workflow.json'), 'second': link('workflow.json')})
        self.save('workflow.json', [link('sample.json'), link('sample.json')])
        self.save('sample.json', {'volume': None})
        read = Path.read_text
        with patch.object(Path, 'read_text', autospec=True, side_effect=read) as reads:
            self.assertEqual(resolve_source(self.root, index)['second'], [{'volume': None}] * 2)
        self.assertEqual([call.args[0].name for call in reads.call_args_list].count('sample.json'), 1)
        with patch('scripts.development.MAX_ENGINEERING_DEPTH', 3):
            with self.assertRaisesRegex(ValueError, 'expansion limit at .*sample.json#'):
                resolve_source(self.root, index)
        with patch('scripts.development.MAX_ENGINEERING_VALUES', 12):
            with self.assertRaisesRegex(ValueError, 'expansion limit at .*sample.json#'):
                resolve_source(self.root, index)

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

    def test_focused_causal_context_routes_retained_evidence_without_closing_an_unknown(self):
        task_id = 'M0-001'
        locator = {'run_id': 'original-1', 'source_commit': '1' * 40, 'report_sha256': '2' * 64}
        finding = {
            'id': 'synthetic-attribution-open', 'source': 'Synthetic first failure.',
            'observation': 'Admission was refused. The holder is unknown. Verification owner captures the next refusal.',
            'severity': 'minor', 'status': 'open', 'task_ids': [task_id], 'regression_checks': ['project-state'],
            'diagnostic_reports': [locator], 'resolution': None, 'deferral_reason': None,
        }
        self.save('project/feedback.json', {'schema_version': '1.0', 'items': [finding]})
        self.record['herdr'] = {
            'host': 'Herdr', 'profiles': {'implement': {'family': 'sol'}, 'supervise': {'family': 'astra'}},
            'reasoning_efforts': ['medium'], 'model_selection': ['Use the default family.'],
            'parallel': ['Keep review independent.'], 'tooling': ['Use synthetic fixtures.'],
        }
        self.save_record()
        for phase in ('implement', 'verify', 'handoff'):
            with self.subTest(phase=phase):
                context = task_context(self.root, task_id, phase=phase)
                self.assertEqual([finding], context['feedback'])
                investigation = context['causal_investigation']
                self.assertIn('task.next_action', investigation['next_observation'])
                self.assertIn('Keep unproven attribution open', investigation['next_observation'])
                self.assertEqual([{'feedback': finding['id'],
                                   'command': 'pnpm project:source project/feedback.json /items/0'}],
                                 investigation['original_records'])
                self.assertEqual([f"pnpm project:diagnostic resolve original-1 {'1' * 40} {'2' * 64}"],
                                 investigation['diagnostic_commands'])
                self.assertEqual(phase != 'implement', 'review_handoff' in context)
                if phase == 'verify':
                    launch = context['review_handoff']['launch']
                    self.assertLess(context['commands'].index(launch),
                                    context['commands'].index(f'pnpm project:record {task_id} --report <run-report>'))
        routine = task_context(self.root, 'M0-002')
        self.assertNotIn('causal_investigation', routine)
        self.assertNotIn('review_handoff', routine)

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
        for context in (focused, detailed):
            self.assertIn('pnpm project:impact', context['commands'])
            self.assertIn('pnpm check:changed', context['commands'])
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
