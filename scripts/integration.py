"""Observe integration gates for one PR; never authorize or perform a merge."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

if __package__:
    from .ci import WORKFLOW, classify_runs, command, github_identity
    from .project import ProjectError, load_project, read_json, review_history, source_identity
    from .protection import violations
else:
    from ci import WORKFLOW, classify_runs, command, github_identity
    from project import ProjectError, load_project, read_json, review_history, source_identity
    from protection import violations

ROOT = Path(__file__).resolve().parents[1]
PR_FIELDS = ('number,url,state,isDraft,headRefOid,headRefName,baseRefOid,baseRefName,'
             'mergeable,mergeStateStatus,reviewDecision')
REVISION = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'


def checked_pull_request(log):
    """Read the ref and actual HEAD printed by the pinned checkout step on Linux or Windows."""
    messages = (line.rstrip('\r\n').partition(' ')[2] for line in log)
    for message in messages:
        if re.fullmatch(r'##\[group\]Run actions/checkout@[0-9a-f]{40}', message):
            break
    number = None
    for message in messages:
        if message.startswith('##[group]Run '):
            break
        if match := re.fullmatch(r'\[command\].+\bcheckout [^\r\n]* refs/remotes/pull/([1-9][0-9]*)/merge', message):
            number = int(match[1])
        if number and message.startswith('[command]') and message.endswith(' log -1 --format=%H'):
            commit = next(messages, '')
            if re.fullmatch(REVISION, commit):
                return number, commit
            break
    raise ProjectError('The checkout log does not identify the checked pull request and merge commit.')


def job_checkout(root, repository, job_id):
    # A job log can be large and contain arbitrary text from later checks.
    # Keep its bytes off the terminal and read only the checkout prefix as UTF-8.
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as log:
        result = subprocess.run(['gh', 'api', '--allow-escape-sequences',
                                 f'repos/{repository}/actions/jobs/{job_id}/logs'],
                                cwd=root, stdout=log, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            detail = result.stderr.decode('utf-8', errors='replace').strip()
            raise ProjectError(f'Cannot read checkout evidence for GitHub job {job_id}: {detail}')
        log.seek(0)
        return checked_pull_request(log)


def required_checkouts(root, repository, jobs, contexts):
    observed = []
    for name in contexts:
        matches = [job for job in jobs if job.get('name') == name]
        if len(matches) != 1:
            raise ProjectError(f'Expected one required job named {name}.')
        job = matches[0]
        if not any(step.get('conclusion') == 'success' and
                   re.fullmatch(r'Run actions/checkout@[0-9a-f]{40}', step['name']) for step in job['steps']):
            raise ProjectError(f'Required job {name} has no successful pinned checkout.')
        number, commit = job_checkout(root, repository, job['id'])
        observed.append({'name': name, 'job_id': job['id'], 'pull_request': number, 'commit': commit})
    if not observed or len({item['job_id'] for item in observed}) != len(observed):
        raise ProjectError('Expected distinct required jobs for the pull request checkout.')
    if len({(item['pull_request'], item['commit']) for item in observed}) != 1:
        raise ProjectError('Required jobs checked different pull requests or merge commits.')
    return observed


def verified_pr_run(root, repository, pr, runs, dirty, contexts):
    # The REST pull_requests array describes matching open PRs *now*. It may
    # change when a PR moves, and is empty for some legitimate PR attempts.
    head, base = pr['headRefOid'], pr['baseRefOid']
    reason = 'No verification run exists for this pull request and head.'
    for run in runs:
        result = classify_runs([run], head, dirty=dirty)
        if run['status'] != 'completed':
            return result, []
        endpoint = f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
        pages = json.loads(command(['gh', 'api', '--paginate', '--slurp', endpoint], root))
        jobs = [job for page in pages for job in page['jobs']]
        try:
            observed = required_checkouts(root, repository, jobs, contexts)
        except ProjectError as error:
            result.update(status='blocked', reason=f"Run {run['id']}: {error}")
            return result, jobs
        number, commit = observed[0]['pull_request'], observed[0]['commit']
        if number != pr['number']:
            reason = f"Verification exists for this head, but no checked run matches PR #{pr['number']}."
            continue
        tested = json.loads(command(['gh', 'api', f'repos/{repository}/git/commits/{commit}'], root))
        if tested['sha'] != commit:
            raise ProjectError('The observed merge commit differs from the checkout log.')
        parents = [parent['sha'] for parent in tested['parents']]
        if parents != [base, head]:
            reason = f"Run {run['id']} checked PR #{number} at a different base or head."
            continue
        result['checkout'] = {'pull_request': number, 'commit': commit, 'base': base, 'head': head}
        result['required_checkouts'] = observed
        return result, jobs
    return {'status': 'blocked', 'reason': reason, 'run': None}, []


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
        if not re.fullmatch(REVISION, pr[key]):
            raise ProjectError(f'Invalid observed revision: {key}')
    base = pr['baseRefOid']
    command(['git', 'cat-file', '-e', f'{base}^{{commit}}'], root)
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', base, pr['headRefOid']],
                              cwd=root, capture_output=True, check=False)
    if ancestry.returncode not in {0, 1}:
        raise ProjectError('PR history is not available locally; fetch the reviewed head and base, then inspect again.')
    scope = delivery_scope(root, project, base)

    def workflow_runs():
        pages = json.loads(command(['gh', 'api', '--paginate', '--slurp', '--method', 'GET',
                                    f'repos/{repository}/actions/workflows/verify.yml/runs',
                                    '-f', f"head_sha={pr['headRefOid']}", '-f', f"branch={pr['headRefName']}",
                                    '-f', 'event=pull_request', '-f', 'per_page=100'], root))
        runs = [run for page in pages for run in page['workflow_runs']
                if run['event'] == 'pull_request' and run['path'] == WORKFLOW
                and run['head_branch'] == pr['headRefName'] and run['head_sha'] == pr['headRefOid']]
        fields = ('id', 'run_attempt', 'status', 'conclusion', 'head_sha', 'html_url', 'event', 'path')
        return sorted(({key: run.get(key) for key in fields} for run in runs),
                      key=lambda run: (run['id'], run['run_attempt']), reverse=True)

    runs = workflow_runs()
    contexts = [check['context'] for check in policy['configuration']['required_status_checks']['checks']]
    workflow, jobs = verified_pr_run(root, repository, pr, runs, source['working_tree_dirty'], contexts)
    protection = json.loads(command(['gh', 'api', f"repos/{repository}/branches/{policy['branch']}/protection"], root))
    stable = source_identity(root) == source and workflow_runs() == runs
    final_pr = pull_request()
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
