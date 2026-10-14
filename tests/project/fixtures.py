"""Small synthetic project records, independent of the product's task history."""

def task_by_id(ledger, task_id):
    return next(task for task in ledger['tasks'] if task['id'] == task_id)


def records():
    def task(key, checks, dependencies=(), plan=False):
        value = {
            'id': key, 'title': f'Synthetic lifecycle case {key}', 'milestone': key.split('-')[0],
            'status': 'planned', 'depends_on': list(dependencies), 'scope': ['implementation.py'],
            'decision_refs': ['0003'], 'acceptance': ['Observe the synthetic outcome.'],
            'checks': checks, 'evidence': [], 'next_action': 'Exercise the synthetic case.',
            'blocker': None, 'closure_reason': None,
        }
        if plan:
            value['plan'] = {
                'outcome': 'Exercise repository controls with synthetic records.',
                'change_types': ['tooling'], 'context': ['engineering-contract-v1.json'],
                'hypotheses': [], 'risks': [{'failure': 'Unsupported completion.', 'checks': ['project-state']}],
                'rollback': 'Discard the isolated synthetic repository.',
            }
        return value

    tasks = [
        task('M0-004', ['project-state']),
        task('M1-002', ['market-fixtures']),
        task('M0-003', ['project-state', 'windows-native-build'], ['M0-001', 'M0-002'], plan=True),
        task('M0-002', ['project-state']),
        task('M0-001', ['project-state', 'rust-lints'], plan=True),
        task('M4-001', ['ai-workflow']),
        task('M5-001', ['runtime-stability', 'windows-installation', 'release-artifacts']),
    ]
    implemented = ['environment', 'project-state', 'rust-lints', 'windows-native-build']
    planned = ['market-fixtures', 'ai-workflow', 'runtime-stability', 'windows-installation', 'release-artifacts']
    checks = [{
        'id': key, 'status': 'implemented' if key in implemented else 'planned',
        'purpose': 'Synthetic observation; never product acceptance.',
        'platforms': 'windows-x64' if key == 'windows-native-build' else 'all',
        'command': ['{python}', '-c', "print('synthetic fixture')"] if key in implemented else None,
        'timeout_seconds': 10,
        'cadence': 'release' if key in {'runtime-stability', 'windows-installation', 'release-artifacts'} else 'change',
    } for key in implemented + planned]
    milestone_checks = {'M0': implemented, 'M1': ['market-fixtures'],
                        'M4': ['ai-workflow'], 'M5': ['windows-installation']}
    delivery = {
        'schema_version': '1.0', 'context': ['engineering-contract-v1.json'],
        'iteration_policy': {key: ['Synthetic iteration policy.'] for key in ['start', 'during', 'finish', 'adaptation']},
        'change_types': [{'id': 'tooling', 'triggers': ['Synthetic tooling change.'], 'checks': ['project-state']}],
        'scenarios': [{
            'id': 'mock-collaboration', 'task_ids': ['M4-001'], 'user_goal': 'Exercise a synthetic scenario.',
            'given': 'A synthetic model.', 'when': 'A synthetic request.', 'then': ['Observe the result.'],
            'failure_cases': ['Missing fixture.'], 'checks': ['ai-workflow'],
            'evidence_boundary': 'Synthetic mock evaluation, not real model acceptance.',
        }],
        'release_policy': {'required_milestones': list(milestone_checks), 'required_checks': ['release-artifacts'],
                           'artifact_requirements': ['Synthetic retained artifact.'], 'product_review': ['Synthetic owner review.']},
        'source_control': {key: ['Synthetic source-control policy.'] for key in
                           ['checkpoint', 'push', 'verification', 'integration', 'failure_recovery', 'scaling', 'authorization']},
        'scaffold_maintenance': {key: ['Synthetic maintenance policy.'] for key in
                                 ['triggers', 'workflow', 'guardrails', 'bootstrap']},
    }
    result = {
        'engineering-contract-v1.json': {'milestones': [{'id': key} for key in milestone_checks],
                                         'acceptance': {'required': ['Synthetic installed outcome', 'Synthetic model outcome']}},
        'project/tasks.json': {'schema_version': '1.0', 'focus_task_id': 'M0-001', 'tasks': tasks},
        'project/checks.json': {'schema_version': '1.0', 'checks': checks,
                                'milestones': [{'id': key, 'checks': values} for key, values in milestone_checks.items()],
                                'acceptance': [{'requirement': 'Synthetic installed outcome', 'checks': ['windows-installation']},
                                               {'requirement': 'Synthetic model outcome', 'checks': ['ai-workflow']}],
                                'profiles': {'fast': ['environment', 'project-state']}},
        'project/delivery.json': delivery,
        'project/feedback.json': {'schema_version': '1.0', 'items': []},
    }
    for key, filename in [('0003', 'repository-harness'), ('0004', 'project-continuity')]:
        result[f'contracts/decisions/{key}-{filename}.json'] = {
            'schema_version': '1.0', 'id': key, 'title': 'Synthetic decision', 'status': 'accepted',
            'recorded_at': '2026-01-01', 'context': 'Synthetic lifecycle tests.',
            'decision': 'Preserve synthetic records across changes.', 'authority': 'Test fixture only.',
            'alternatives': [], 'consequences': ['Only synthetic evidence.'], 'supersedes': [],
            'revisit_when': ['The synthetic scenario changes.'],
        }
    return result
