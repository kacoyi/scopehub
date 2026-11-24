"""Observe integration gates for one PR; never authorize or perform a merge."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

if __package__:
    from . import timing
    from .ci import WORKFLOW, classify_runs, command, github_identity
    from .project import (ProjectError, feedback_fingerprint, load_project, project_definition_hash, read_json,
                          review_history, source_identity, task_definition_hash)
    from .protection import violations
    from .verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                     exact_commit, git, require_ancestor)
else:
    import timing
    from ci import WORKFLOW, classify_runs, command, github_identity
    from project import (ProjectError, feedback_fingerprint, load_project, project_definition_hash, read_json,
                         review_history, source_identity, task_definition_hash)
    from protection import violations
    from verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                    exact_commit, git, require_ancestor)

ROOT = Path(__file__).resolve().parents[1]
PR_FIELDS = ('number,url,state,isDraft,headRefOid,headRefName,baseRefOid,baseRefName,'
             'mergeable,mergeStateStatus,reviewDecision')
REVISION = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
FULL_HARNESS_STEPS = {
    'verify (ubuntu-latest)': 'Run pnpm check',
    'verify (windows-latest)': 'Run uv run python scripts/check.py --require-windows',
}
SCOPED_HARNESS_STEPS = {f'Verify {profile} changes': profile for profile in SCOPED_PROFILES}


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
                                cwd=root, env=checkout_git_environment(), stdout=log, stderr=subprocess.PIPE, timeout=30)
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


def job_verification_profile(job):
    """Read successful harness coverage for the protected runner's job."""
    full_step = FULL_HARNESS_STEPS.get(job['name'])
    if full_step is None:
        return None
    successful = {step['name'] for step in job.get('steps', [])
                  if step.get('status') == 'completed' and step.get('conclusion') == 'success'}
    if full_step in successful:
        return 'full'
    scoped = successful.intersection(SCOPED_HARNESS_STEPS)
    if len(scoped) == 1:
        return SCOPED_HARNESS_STEPS[scoped.pop()]
    return None


def expected_verification_profile(root, base, head, tested):
    """Use the head comparison only when it represents the checked merge tree."""
    tree = tested.get('tree')
    merge_tree = tree.get('sha') if isinstance(tree, dict) else None
    if not isinstance(merge_tree, str) or not re.fullmatch(REVISION, merge_tree):
        return 'full', 'The checked merge tree was unavailable.'
    try:
        head_tree = command(['git', '--no-replace-objects', 'rev-parse', '--verify', '--end-of-options',
                             f'{head}^{{tree}}'], root)
    except (ProjectError, OSError, subprocess.SubprocessError, UnicodeError):
        return 'full', 'The local PR head tree could not be verified.'
    if not re.fullmatch(REVISION, head_tree):
        return 'full', 'The local PR head tree could not be verified.'
    if head_tree != merge_tree:
        return 'full', 'The checked merge tree differs from the local PR head tree.'
    scope = committed_scope(root, base, head)
    return scope['profile'], scope.get('reason')


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
        if len(parents) != 2 or parents[1] != run['head_sha']:
            result.update(status='blocked', reason=f"Run {run['id']} checked a merge that does not match its declared head.")
            return result, jobs
        if parents[0] != base:
            reason = f"Run {run['id']} checked PR #{number} at a different base."
            continue
        result['checkout'] = {'pull_request': number, 'commit': commit, 'base': base, 'head': head}
        result['required_checkouts'] = observed
        expected, scope_reason = expected_verification_profile(root, base, head, tested)
        result['expected_profile'] = expected
        result['expected_profile_reason'] = scope_reason
        checked_ids = {item['job_id'] for item in observed}
        jobs = [dict(job, verification_profile=job_verification_profile(job)) if job['id'] in checked_ids else job
                for job in jobs]
        insufficient = [f"{job['name']} ({job['verification_profile'] or 'no successful harness'})"
                        for job in jobs if job['id'] in checked_ids
                        and job['verification_profile'] not in {expected, 'full'}]
        if result['status'] == 'passed' and insufficient:
            result.update(status='blocked', reason=f"Run {run['id']} requires {expected} verification. "
                          f"Required jobs with insufficient coverage: {', '.join(insufficient)}.")
        return result, jobs
    return {'status': 'blocked', 'reason': reason, 'run': None}, []


def required_job_succeeded(jobs, name, head):
    matches = [job for job in jobs if job.get('name') == name]
    return len(matches) == 1 and matches[0].get('status') == 'completed' and (
        matches[0].get('conclusion') == 'success' and matches[0].get('head_sha') == head)


def budget_observations_support_handoff(root, project, pr, push, repair, source, workflow):
    run, checkout = workflow['run'], workflow['checkout']
    windows = next((job for job in workflow['required_checkouts'] if job['name'] == 'verify (windows-latest)'), None)
    if run['run_attempt'] != 1 or windows is None:
        return False
    # Timing owns recovery, comparability and clock validation. Here the latest
    # qualified observation must also belong to the PR attempt being integrated.
    observed = timing.offline_budget_status(root, [pr, push], project['tasks'])
    recovery = {row['feedback_id']: row['recovery'] for row in observed.get('budget_recovery', [])}
    pr_recovery, push_recovery = (recovery.get(finding['id']) or {} for finding in (pr, push))
    if (pr_recovery.get('status') != 'recovered' or push_recovery.get('status') != 'pending'
            or pr_recovery.get('repair_commit') != repair or push_recovery.get('repair_commit') != repair):
        return False
    push_comparisons = push_recovery['comparisons']
    if not push_comparisons or any(comparison['latest_attempt'] or comparison['history_missing']
                                  for comparison in push_comparisons):
        return False
    for comparison in pr_recovery['comparisons']:
        latest = comparison['latest_attempt'] or {}
        elapsed = latest.get('elapsed_seconds')
        if (str(latest.get('hosted_run_id')) == str(run['id']) and latest.get('hosted_head') == checkout['head']
                and latest.get('source_commit') == checkout['commit']
                and latest.get('inputs_sha256') == source['sha256']
                and str(latest.get('job_id')) == str(windows['job_id']) and latest.get('conclusion') == 'success'
                and type(elapsed) in (int, float) and 0 <= elapsed <= timing.HOSTED_BUDGET):
            return True
    return False


def budget_regression_receipt(root, project, task, finding, repair, source):
    observation = feedback_fingerprint(finding)
    required = set(finding['regression_checks'])
    definition = task_definition_hash(task)
    project_definition = project_definition_hash(project)
    for ref in task['evidence']:
        receipt = read_json(root / ref['path'])
        if (receipt['task_definition_sha256'] != definition or receipt['project_definition_sha256'] != project_definition
                or observation not in receipt.get('observed_feedback', [])
                or not required.issubset(check['id'] for check in receipt['checks'])):
            continue
        observed = receipt['source']
        if observed['working_tree_dirty'] or observed['sha256'] != source['sha256']:
            continue
        revision = (receipt.get('ci') or {}).get('head_sha', observed['git_head'])
        try:
            exact_commit(root, revision)
            require_ancestor(root, repair, revision)
            require_ancestor(root, revision, source['git_head'])
        except ScopeError:
            continue
        return ref['path']
    return None


def main_push_budget_handoff(root, project, base, completed, items, *, source, policy, workflow, jobs):
    rule = project['delivery']['source_control'].get('post_merge_main_push_budget')
    if not rule or not source or not policy or not workflow or source['working_tree_dirty']:
        return None
    if policy['branch'] != 'main' or workflow['status'] != 'passed' or workflow.get('expected_profile') != 'full':
        return None
    head = source['git_head']
    checkout = workflow.get('checkout', {})
    if checkout.get('base') != base or checkout.get('head') != head:
        return None
    contexts = {item['context'] for item in policy['configuration']['required_status_checks']['checks']}
    if not FULL_HARNESS_STEPS.keys() <= contexts or not all(required_job_succeeded(jobs, name, head) for name in contexts):
        return None

    repair_task, follow_up = rule['repair_task_id'], rule['follow_up_task_id']
    task = project['tasks'].get(follow_up, {})
    if (repair_task not in completed or task.get('status') != 'planned' or not task.get('plan')
            or task.get('depends_on') != [repair_task] or task.get('next_action') != rule['next_action']):
        return None
    push = items.get(rule['main_push_feedback_id'], {})
    pr = items.get(rule['pull_request_feedback_id'], {})
    if (push.get('status') != 'open' or push.get('severity') != 'major' or pr.get('status') != 'addressed'
            or set(push['task_ids']) != {repair_task, follow_up} or repair_task not in pr['task_ids']):
        return None
    push_budget, pr_budget = push.get('verification_budget', {}), pr.get('verification_budget', {})
    repair = push_budget.get('repair_commit')
    common = {'repository': policy['repository'], 'profile': 'full', 'platform': 'Windows', 'repair_commit': repair}
    if (not repair or push_budget != dict(common, event='push') or pr_budget != dict(common, event='pull_request')
            or not push['regression_checks'] or not push.get('diagnostic_reports') or not pr.get('diagnostic_reports')):
        return None

    change = committed_scope(root, base, head)
    if change['profile'] != 'full' or change['base'] != base or not change['change_count']:
        return None
    try:
        committed = json.loads(git(root, 'show', f'{head}:project/delivery.json'))
        if committed['source_control'].get('post_merge_main_push_budget') != rule:
            return None
        exact_commit(root, repair)
        require_ancestor(root, repair, head)
        if git(root, 'merge-base', base, repair).strip().decode('ascii') == repair:
            return None
    except ScopeError:
        return None

    receipt = budget_regression_receipt(root, project, project['tasks'][repair_task], push, repair, source)
    if not receipt or not budget_observations_support_handoff(root, project, pr, push, repair, source, workflow):
        return None
    return {'feedback_id': push['id'], 'status': 'pending', 'branch': policy['branch'],
            'repair_task_id': repair_task, 'repair_commit': repair, 'regression_receipt': receipt,
            'follow_up_task_id': follow_up, 'next_action': task['next_action']}


def delivery_scope(root, project, base, *, source=None, policy=None, workflow=None, jobs=()):
    """Use actual base history and validated receipts, not a supplied task list."""
    history = review_history(root, base)
    previous_file = subprocess.run(['git', 'show', f'{base}:project/tasks.json'], cwd=root, env=checkout_git_environment(),
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
    items = {item['id']: item for item in project['feedback']['items']}
    handoff = main_push_budget_handoff(root, project, base, completed, items,
                                      source=source, policy=policy, workflow=workflow, jobs=jobs)
    rule = project['delivery']['source_control'].get('post_merge_main_push_budget', {})
    reviewed_budgets = ({rule['main_push_feedback_id'], rule['pull_request_feedback_id']}
                        if rule.get('repair_task_id') in affected else set())
    handed_off = handoff['feedback_id'] if handoff else None
    blockers.extend(f"Unresolved {item['severity']} feedback: {item['id']}"
                    for item in items.values() if item['status'] != 'addressed'
                    and item['id'] != handed_off
                    and (item['severity'] == 'critical' or item['id'] in reviewed_budgets
                         or item['severity'] == 'major' and affected.intersection(item['task_ids'])))
    return {'completed_task_ids': completed, 'unfinished_task_ids': unfinished,
            'blockers': blockers, 'history': history,
            **({'post_merge_main_push_budget': handoff} if handoff else {}),
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
        if not required_job_succeeded(jobs, required['context'], pr['headRefOid']):
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
                              cwd=root, env=checkout_git_environment(), capture_output=True, check=False)
    if ancestry.returncode not in {0, 1}:
        raise ProjectError('PR history is not available locally; fetch the reviewed head and base, then inspect again.')
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
    scope = delivery_scope(root, project, base, source=source, policy=policy, workflow=workflow, jobs=jobs)
    protection = json.loads(command(['gh', 'api', f"repos/{repository}/branches/{policy['branch']}/protection"], root))
    stable = source_identity(root) == source and workflow_runs() == runs
    final_pr = pull_request()
    result = assess(source=source, pr=pr, final_pr=final_pr, base_current=ancestry.returncode == 0,
                    workflow=workflow, jobs=jobs, policy=policy, protection=protection,
                    scope=scope, observations_stable=stable)
    result.update(observed_at=datetime.now(timezone.utc).isoformat(), repository=repository,
                  pull_request=pr, source=source, workflow=workflow, delivery_scope=scope,
                  required_jobs=[{key: job.get(key) for key in
                                 ['id', 'name', 'head_sha', 'status', 'conclusion', 'verification_profile']}
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
