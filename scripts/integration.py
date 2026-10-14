"""Observe integration gates for one PR; never authorize or perform a merge."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .ci import classify_runs, command, github_identity
    from .project import ProjectError, load_project, read_json, review_history, source_identity
    from .protection import violations
else:
    from ci import classify_runs, command, github_identity
    from project import ProjectError, load_project, read_json, review_history, source_identity
    from protection import violations

ROOT = Path(__file__).resolve().parents[1]
PR_FIELDS = ('number,url,state,isDraft,headRefOid,baseRefOid,baseRefName,'
             'mergeable,mergeStateStatus,reviewDecision')


def delivery_scope(root, project, base):
    """Use actual base history and validated receipts, not a supplied task list."""
    history = review_history(root, base)
    previous_file = subprocess.run(['git', 'show', f'{base}:project/tasks.json'], cwd=root,
                                   capture_output=True, check=False)
    previous = {task['id']: task for task in json.loads(previous_file.stdout)['tasks']} if previous_file.returncode == 0 else {}
    changed = [task for key, task in project['tasks'].items() if task != previous.get(key)]
    completed = [task['id'] for task in changed if task['status'] == 'done']
    unfinished = [task['id'] for task in changed if task['status'] in {'active', 'blocked'}]
    blockers = []
    if not completed:
        blockers.append('The PR has no completed delivery task with archived evidence.')
    blockers.extend(f'Unfinished delivery task: {key}' for key in unfinished)
    affected = {task['id'] for task in changed}
    blockers.extend(f"Unresolved {item['severity']} feedback: {item['id']}"
                    for item in project['feedback']['items'] if item['status'] != 'addressed'
                    and (item['severity'] == 'critical' or
                         item['severity'] == 'major' and affected.intersection(item['task_ids'])))
    return {'completed_task_ids': completed, 'unfinished_task_ids': unfinished,
            'blockers': blockers, 'history': history,
            'claim_scope': 'Completed tasks and accepted history; review must still check the diff against the declared scope.'}


def assess(*, source, pr, final_pr, base_current, workflow, jobs, policy, protection, scope, observations_stable):
    blockers = []
    if not source['git_head'] or source['working_tree_dirty']:
        blockers.append('Preserve a clean committed revision before integration.')
    if source['git_head'] != pr['headRefOid']:
        blockers.append('The local commit differs from the live PR head.')
    if pr['state'] != 'OPEN':
        blockers.append('The PR is not open.')
    if pr['baseRefName'] != policy['branch']:
        blockers.append('The PR targets a branch outside the integration policy.')
    if not base_current:
        blockers.append('The PR head does not contain the current base revision; update it and rerun verification.')
    if pr['mergeable'] != 'MERGEABLE':
        blockers.append('GitHub has not confirmed a conflict-free merge.')
    if pr['mergeStateStatus'] != 'CLEAN' and not (pr['isDraft'] and pr['mergeStateStatus'] == 'DRAFT'):
        blockers.append(f"GitHub merge state is {pr['mergeStateStatus']}; resolve it and inspect again.")
    if pr['reviewDecision'] not in {'', 'APPROVED'}:
        blockers.append(f"GitHub review decision is {pr['reviewDecision']}.")
    if workflow['status'] != 'passed':
        blockers.append(workflow['reason'])
    for required in policy['configuration']['required_status_checks']['checks']:
        matches = [job for job in jobs if job.get('name') == required['context']]
        if len(matches) != 1 or not all(job.get('status') == 'completed' and job.get('conclusion') == 'success'
                                        and job.get('head_sha') == pr['headRefOid'] for job in matches):
            blockers.append(f"Required job did not succeed for this PR revision: {required['context']}")
    blockers.extend(violations(protection, policy['configuration']))
    blockers.extend(scope['blockers'])
    if pr != final_pr or not observations_stable:
        blockers.append('The PR, source or latest workflow changed during inspection; inspect again.')
    review_candidate = not blockers
    if pr['isDraft']:
        blockers.append('The PR is a draft; complete review preparation before integration.')
    return {
        'status': 'blocked' if blockers else 'passed', 'technical_ready': not blockers,
        'review_candidate': review_candidate, 'blockers': blockers,
        'recommendation': 'present_merge_proposal' if review_candidate else 'resolve_integration_blockers',
        'decision_requirements': [
            'Review the diff, task scope, test evidence, unresolved conversations and recovery plan for the observed head and base.',
            'Use explicit owner merge authorization covering this PR and revision or an already granted applicable scope; this command does not evaluate or grant it.',
            'Immediately before an authorized merge, inspect again and bind the operation to the expected PR head; a changed base also requires renewed assessment.',
            'After merging, inspect the resulting main commit and its push CI before starting a release candidate.'
        ],
        'authorization': 'not_evaluated',
        'claim_scope': 'Point-in-time technical assessment. Review, owner authorization, merge execution and release acceptance are separate.',
    }


def inspect(root=ROOT, pr_number=None):
    if not isinstance(pr_number, int) or pr_number < 1:
        raise ProjectError('Supply a positive PR number with --pr')
    project = load_project(root)
    policy = read_json(root / 'project/branch-protection.json')
    source = source_identity(root)
    repository = github_identity(root)
    if repository != policy['repository']:
        raise ProjectError('The integration policy belongs to another repository')

    def pull_request():
        return json.loads(command(['gh', 'pr', 'view', str(pr_number), '--repo', repository,
                                   '--json', PR_FIELDS], root))

    pr = pull_request()
    for key in ['headRefOid', 'baseRefOid']:
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', pr[key]):
            raise ProjectError(f'Invalid observed revision: {key}')
    base = pr['baseRefOid']
    command(['git', 'cat-file', '-e', f'{base}^{{commit}}'], root)
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', base, pr['headRefOid']],
                              cwd=root, capture_output=True, check=False)
    if ancestry.returncode not in {0, 1}:
        raise ProjectError('PR history is not available locally; fetch the reviewed head and base, then inspect again.')
    scope = delivery_scope(root, project, base)

    def workflow_status():
        response = json.loads(command(['gh', 'api', '--method', 'GET', f'repos/{repository}/actions/runs',
                                       '-f', f"head_sha={pr['headRefOid']}", '-f', 'per_page=100'], root))
        return classify_runs(response['workflow_runs'], pr['headRefOid'], dirty=source['working_tree_dirty'])

    workflow = workflow_status()
    jobs = []
    if workflow['run']:
        run = workflow['run']
        endpoint = f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
        pages = json.loads(command(['gh', 'api', '--paginate', '--slurp', endpoint], root))
        jobs = [job for page in pages for job in page['jobs']]
    protection = json.loads(command(['gh', 'api', f"repos/{repository}/branches/{policy['branch']}/protection"], root))
    final_pr = pull_request()
    stable = source_identity(root) == source and workflow_status() == workflow
    result = assess(source=source, pr=pr, final_pr=final_pr, base_current=ancestry.returncode == 0,
                    workflow=workflow, jobs=jobs, policy=policy, protection=protection,
                    scope=scope, observations_stable=stable)
    result.update(observed_at=datetime.now(timezone.utc).isoformat(), repository=repository,
                  pull_request=pr, source=source, workflow=workflow, delivery_scope=scope,
                  required_jobs=[{key: job.get(key) for key in ['id', 'name', 'head_sha', 'status', 'conclusion']}
                                 for job in jobs],
                  protection={'status': 'failed' if violations(protection, policy['configuration']) else 'passed',
                              'branch': policy['branch']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pr', type=int, required=True)
    args = parser.parse_args()
    try:
        result = inspect(pr_number=args.pr)
    except (ProjectError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
        result = {'status': 'blocked', 'technical_ready': False, 'review_candidate': False,
                  'authorization': 'not_evaluated', 'blockers': [str(error)],
                  'recommendation': 'restore_missing_observation_and_inspect_again'}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['technical_ready'] else 1)


if __name__ == '__main__':
    main()
