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
    from .diagnostic_archive import resolve_diagnostic
    from .project import (ProjectError, committed_source_identity, compare_history, diagnostic_archive_root, digest, feedback_fingerprint,
                         json_bytes, load_project, project_definition_hash, read_json, read_review_report, source_identity,
                         task_definition_hash, windows_x64)
    from .protected_python import (PYTHON_CHECK, SERIAL_COMMAND, WINDOWS_DEADLINE_SECONDS,
                                    python_row, read_native, serial_result)
    from .protection import violations
    from .verification_evidence import hosted_artifact_path
    from .verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                     exact_commit, git, require_ancestor, require_complete_history)
else:
    import timing
    from ci import WORKFLOW, classify_runs, command, github_identity
    from diagnostic_archive import resolve_diagnostic
    from project import (ProjectError, committed_source_identity, compare_history, diagnostic_archive_root, digest, feedback_fingerprint,
                         json_bytes, load_project, project_definition_hash, read_json, read_review_report, source_identity,
                         task_definition_hash, windows_x64)
    from protected_python import (PYTHON_CHECK, SERIAL_COMMAND, WINDOWS_DEADLINE_SECONDS,
                                  python_row, read_native, serial_result)
    from protection import violations
    from verification_evidence import hosted_artifact_path
    from verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                    exact_commit, git, require_ancestor, require_complete_history)

ROOT = Path(__file__).resolve().parents[1]
PR_FIELDS = ('number,url,state,isDraft,headRefOid,headRefName,baseRefOid,baseRefName,'
             'mergeable,mergeStateStatus,reviewDecision')
REVISION = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
FULL_HARNESS_STEPS = {
    'verify (ubuntu-latest)': 'Run pnpm check',
    'verify (windows-latest)': 'Run uv run python scripts/check.py --require-windows',
}
SCOPED_HARNESS_STEPS = {f'Verify {profile} changes': profile for profile in SCOPED_PROFILES}
MAIN_PUSH_BUDGET_POLICY = 'post_merge_main_push_budget'
RECURRING_PUSH_BUDGET_POLICY = 'recurring_main_push_budget'
PYTHON_OBSERVATION_POLICY = 'protected_python_observation'
PYTHON_OBSERVATION_RECORDS = {
    'feedback_id': 'windows-main-python-discovery-process-deadline',
    'repair_task_id': 'M0-153',
    'follow_up_task_id': 'M0-160',
    'policy_task_id': 'M0-159',
}
# This exception belongs to one reviewed repair. The first policy PR cannot
# use its own configuration to choose which existing observation to govern.
MAIN_PUSH_BUDGET_BINDING = {
    'pull_request_feedback_id': 'verification-overrun-without-timely-remediation',
    'main_push_feedback_id': 'verification-main-push-feedback-budget-overrun',
    'repair_task_id': 'M0-087',
    'follow_up_task_id': 'M0-091',
}


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


def observed_budget_recovery(root, project, *findings):
    observed = timing.offline_budget_status(root, findings, project['tasks'])
    return {row['feedback_id']: row['recovery'] for row in observed.get('budget_recovery', [])}


def successful_budget_observation(sample):
    elapsed = sample.get('elapsed_seconds')
    return sample.get('conclusion') == 'success' and type(elapsed) in (int, float) and 0 <= elapsed <= timing.HOSTED_BUDGET


def budget_observations_support_handoff(root, project, pr, push, repair, source, workflow):
    run, checkout = workflow['run'], workflow['checkout']
    windows = next((job for job in workflow['required_checkouts'] if job['name'] == 'verify (windows-latest)'), None)
    if run['run_attempt'] != 1 or windows is None:
        return False
    # Timing owns recovery, comparability and clock validation. Here the latest
    # qualified observation must also belong to the PR attempt being integrated.
    recovery = observed_budget_recovery(root, project, pr, push)
    pr_recovery, push_recovery = (recovery.get(finding['id']) or {} for finding in (pr, push))
    if (pr_recovery.get('status') != 'recovered' or push_recovery.get('status') != 'pending'
            or pr_recovery.get('repair_commit') != repair or push_recovery.get('repair_commit') != repair):
        return False
    push_comparisons = push_recovery['comparisons']
    if (not push_comparisons or push_recovery.get('unavailable_reports')
            or any(comparison['latest_attempt'] or comparison['history_missing'] for comparison in push_comparisons)):
        return False
    for comparison in pr_recovery['comparisons']:
        latest = comparison['latest_attempt'] or {}
        if (successful_budget_observation(latest) and str(latest.get('hosted_run_id')) == str(run['id'])
                and latest.get('hosted_head') == checkout['head']
                and latest.get('source_commit') == checkout['commit']
                and latest.get('inputs_sha256') == source['sha256']
                and str(latest.get('job_id')) == str(windows['job_id'])):
            return True
    return False


def budget_regression_receipts(root, task, finding):
    observation = feedback_fingerprint(finding)
    required = set(finding['regression_checks'])
    definition = task_definition_hash(task)
    for ref in task['evidence']:
        receipt = read_json(root / ref['path'])
        if (receipt['task_definition_sha256'] == definition and observation in receipt.get('observed_feedback', [])
                and required.issubset(check['id'] for check in receipt['checks'])
                and not receipt['source']['working_tree_dirty']):
            yield ref['path'], receipt


def complete_task_receipt(project, task, receipt):
    # Task loading verifies the archived reference and evidence schema. This
    # handoff additionally requires every task check in the same current receipt.
    checks = {row['id']: row for row in receipt['checks']}
    if receipt['task_id'] != task['id'] or checks.keys() != set(task['checks']):
        return False
    python = receipt['environment'].get('python_executable')
    for key, row in checks.items():
        check = project['checks'][key]
        if check['status'] != 'implemented' or not check['command']:
            return False
        command = [python if arg == '{python}' else arg for arg in check['command']]
        if row['status'] != 'passed' or row['exit_code'] != 0 or row['command'] != command:
            return False
    return True


def budget_regression_receipt(root, project, task, finding, repair, source, *, complete=False, dispatch_repository=None):
    project_definition = project_definition_hash(project)
    for path, receipt in budget_regression_receipts(root, task, finding):
        observed = receipt['source']
        if receipt.get('project_definition_sha256') != project_definition or observed['sha256'] != source['sha256']:
            continue
        ci = receipt.get('ci') or {}
        if dispatch_repository and not python_dispatch_receipt(receipt, dispatch_repository):
            continue
        if complete and (not complete_task_receipt(project, task, receipt)
                         or observed.get('file_count') != source.get('file_count')
                         or observed.get('snapshot_kind') != source.get('snapshot_kind')
                         or (ci and ci['checkout_sha'] != observed['git_head'])):
            continue
        revision = ci.get('head_sha', observed['git_head'])
        try:
            exact_commit(root, revision)
            require_ancestor(root, repair, revision)
            require_ancestor(root, revision, source['git_head'])
        except ScopeError:
            continue
        return path
    return None


def main_push_budget_rule(root, project, base, completed, items, previous_feedback):
    candidate = project['delivery']['source_control'].get(MAIN_PUSH_BUDGET_POLICY)
    push_id = MAIN_PUSH_BUDGET_BINDING['main_push_feedback_id']
    push, previous = items.get(push_id, {}), previous_feedback.get(push_id, {})
    new_closure = push.get('status') == 'addressed' and previous.get('status') != 'addressed'
    required = candidate is not None or MAIN_PUSH_BUDGET_BINDING['repair_task_id'] in completed or new_closure
    try:
        delivery = json.loads(git(root, 'show', f'{base}:project/delivery.json')) if base else {}
    except ScopeError:
        return None, 'The protected main-push budget policy could not be read.' if required else None
    protected = delivery.get('source_control', {}).get(MAIN_PUSH_BUDGET_POLICY)
    if protected is not None and candidate != protected:
        return None, 'The PR changes or removes the protected main-push budget policy.'
    if not required and protected is None:
        return None, None
    if candidate is None or any(candidate.get(key) != value for key, value in MAIN_PUSH_BUDGET_BINDING.items()):
        return None, 'The main-push budget policy must name the governed M0-087 findings and M0-091.'
    missing = [candidate[key] for key in ('pull_request_feedback_id', 'main_push_feedback_id')
               if candidate[key] not in items]
    missing.extend(candidate[key] for key in ('repair_task_id', 'follow_up_task_id')
                   if candidate[key] not in project['tasks'])
    if missing:
        return None, f"The main-push budget policy references unavailable records: {', '.join(missing)}."
    return candidate, None


def recurring_push_budget_rule(root, project, base, affected, items, previous_feedback):
    candidate = project['delivery']['source_control'].get(RECURRING_PUSH_BUDGET_POLICY)
    try:
        delivery = json.loads(git(root, 'show', f'{base}:project/delivery.json')) if base else {}
    except ScopeError:
        return None, 'The protected recurring main-push budget binding could not be read.' if candidate else None
    accepted = delivery.get('source_control', {}).get(RECURRING_PUSH_BUDGET_POLICY)
    if accepted is not None:
        if candidate != accepted:
            return None, 'The PR changes or removes the protected recurring main-push budget binding.'
        return accepted, None
    if candidate:
        repair, follow_up = candidate['repair_task_id'], candidate['follow_up_task_id']
        finding = candidate['main_push_feedback_id']
        if (repair == follow_up or {repair, follow_up} & {'M0-087', 'M0-091'}
                or finding in {MAIN_PUSH_BUDGET_BINDING[key]
                               for key in ('main_push_feedback_id', 'pull_request_feedback_id')}):
            return None, 'The recurring main-push budget binding must name distinct delivery and observation records.'
        if affected.intersection((repair, follow_up)) or items.get(finding) != previous_feedback.get(finding):
            return None, 'The recurring main-push budget binding requires separate protected adoption.'
    return None, None


def verified_original_sample(archive, locator, sample):
    # Timing projections supply job clocks. The retained original binds the harness side of that join.
    original = resolve_diagnostic(locator, archive)
    raw = Path(original['report_path']).read_bytes()
    if digest(raw) != locator['report_sha256']:
        return False
    report = json.loads(raw)
    if report.get('native_requirement') != {'requested': sample['platform'] == 'Windows', 'satisfied': True}:
        return False
    projected = timing.report_sample(report, cache=sample['cache'])
    return all((all(sample[key].get(field) == number for field, number in value.items())
                if key == 'seconds' else sample.get(key) == value) for key, value in projected.items())


def eligible_native_sample(row, platform, repository, event):
    return (row['platform'] == platform and (platform != 'Windows' or windows_x64(
                {'system': platform, 'machine': row['arch']}))
            and row['ci'] and row['ci']['repository'] == repository
            and row['ci']['event'] == event and row['profile'] == 'full'
            and (row['report_status'] == 'passed' or (platform == 'Linux' and row['report_status'] == 'blocked'))
            and timing.sample_eligible(row) and row['review_exclusion'] is None and timing.has_hosted_inputs(row)
            and row['job']['runner_label'] == ('windows-latest' if platform == 'Windows' else 'ubuntu-latest')
            and row['workflow_conclusion'] == 'success' and row['seconds']['total'] is not None)


def warm_main_sample(row, repository):
    return (eligible_native_sample(row, 'Windows', repository, 'push')
            and row['hosted_head_branch'] == 'main' and row['ci']['head_sha'] == row['source_commit']
            and row['cache'] == 'warm' and row.get('windows_caches') ==
            {'core': 'warm', 'browser': 'warm', 'recovery': 'warm', 'python': 'not_requested'})


def recurring_budget_evidence(root, project, base, rule, push):
    reports = push['diagnostic_reports']
    identities = sorted((row['run_id'], row['source_commit'], row['report_sha256']) for row in reports)
    if (len(identities) != 3 or digest(json_bytes(identities)) != rule['control_reports_sha256']
            or feedback_fingerprint(push) != rule['finding_sha256']):
        return None
    archive = diagnostic_archive_root(root)
    original_samples, clocks, issues = timing.retained_budget_inputs(root, [push])
    view = timing.timing_view(original_samples, hosted=clocks, issues=issues)
    keys = {timing.report_key(row) for row in reports}
    if any(row.get('linked_timing_conflict') and (row.get('run_id'), row.get('source_commit')) in keys
           for row in view['input_issues']):
        return None
    controls = []
    repository = push['verification_budget']['repository']
    for locator in reports:
        matches = [row for row in view['samples'] if timing.report_key(row) == timing.report_key(locator)]
        if len(matches) != 1 or not warm_main_sample(matches[0], repository):
            return None
        require_ancestor(root, locator['source_commit'], base)
        if not verified_original_sample(archive, locator, matches[0]):
            return None
        controls.append(matches[0])
    coverage = timing.coverage_key(controls[0])
    if (len(coverage) != rule['selected_check_count'] or len({row['ci']['run_id'] for row in controls}) != 3
            or any(timing.budget_cohort(row, timing.coverage_key(row)) != timing.budget_cohort(controls[0], coverage)
                   for row in controls)
            or sum(row['seconds']['total'] > timing.HOSTED_BUDGET for row in controls) < 2):
        return None
    follow_up = timing.budget_follow_up(view, [push], project['tasks'], root=root)
    recovery, = (row['recovery'] for row in follow_up['budget_recovery'] if row['feedback_id'] == push['id'])
    if (recovery['unavailable_reports'] or recovery['conflicting_reports'] or len(recovery['comparisons']) != 1
            or recovery['comparisons'][0]['history_missing']):
        return None
    return view['samples'], coverage, archive, recovery


def verified_pr_harness(root, checkout, sample):
    run = sample['ci']['run_id']
    platform = sample['platform'].lower()
    artifacts = hosted_artifact_path(root, f'.reports/ci/{run}/{platform}', directory=True)
    scope, _ = read_review_report(hosted_artifact_path(artifacts, 'verification-scope.json'), 4096)
    if (scope.get('kind') != 'scopehub-verification-scope-v1' or scope.get('profile') != 'full'
            or scope.get('base') != checkout['base'] or scope.get('checkout') != checkout['commit']):
        return False
    locator = {'run_id': sample['run_id'], 'source_commit': checkout['commit'],
               'report_sha256': scope['report_sha256']}
    # Both the downloaded entry and its per-run original must bind the same bytes.
    _, report_sha = read_review_report(hosted_artifact_path(artifacts, 'test-report.json'), 4 * 1024 * 1024)
    return report_sha == locator['report_sha256'] and verified_original_sample(artifacts, locator, sample)


def recurring_pr_evidence(root, project, base, rule, push, source, workflow, clock):
    try:
        evidence = recurring_budget_evidence(root, project, base, rule, push)
        if not evidence:
            return False
        samples, coverage, _, recovery = evidence
        if recovery['status'] != 'pending' or recovery['comparisons'][0]['latest_attempt'] is not None:
            return False
        checkout = workflow['checkout']
        run = workflow['run']
        repository = push['verification_budget']['repository']
        if (not timing.clock_complete(clock)
                or timing.ci_key(clock) != timing.actions_attempt_identity(repository, run)):
            return False
        clock_jobs = {job['platform']: job for job in clock['jobs']}
        selected = {key for key, check in project['checks'].items()
                    if check.get('cadence', 'change') == 'change' and check['status'] != 'retired'}
        if {key for key, _, _ in coverage} != selected:
            return False
        for platform, runner in (('Linux', 'verify (ubuntu-latest)'), ('Windows', 'verify (windows-latest)')):
            observed, = [job for job in workflow['required_checkouts'] if job['name'] == runner]
            exact = [row for row in samples if eligible_native_sample(row, platform, repository, 'pull_request')
                     and row['ci']['run_id'] == str(run['id']) and row['ci']['head_sha'] == checkout['head']
                     and row['source_commit'] == checkout['commit'] and row['inputs_sha256'] == source['sha256']
                     and str(row['job']['job_id']) == str(observed['job_id'])]
            expected = tuple(
                (key, 'blocked', 'unsupported_platform')
                if platform == 'Linux' and status == 'passed' and project['checks'][key]['platforms'] == 'windows-x64'
                else (key, status, reason) for key, status, reason in coverage)
            job = clock_jobs[platform]
            elapsed = job['seconds']['total']
            if (len(exact) != 1 or timing.coverage_key(exact[0]) != expected
                    or str(job['job_id']) != str(observed['job_id']) or exact[0]['seconds']['total'] != elapsed
                    or (platform == 'Windows' and elapsed > timing.HOSTED_BUDGET)
                    or not verified_pr_harness(root, checkout, exact[0])):
                return False
    except (OSError, ProjectError, ScopeError, ValueError, KeyError, TypeError, RuntimeError):
        return False
    return True


def exact_full_pr_jobs(workflow, jobs):
    checkout, run = workflow['checkout'], workflow['run']
    if run['run_attempt'] != 1 or run.get('event') != 'pull_request' or run.get('head_sha') != checkout['head']:
        return False
    for name in FULL_HARNESS_STEPS:
        checked = [row for row in workflow['required_checkouts'] if row['name'] == name]
        if len(checked) != 1 or any(checked[0][key] != checkout[key] for key in ('pull_request', 'commit')):
            return False
        if not any(job['name'] == name and job['id'] == checked[0]['job_id']
                   and job['verification_profile'] == 'full' for job in jobs):
            return False
    return True


def protected_full_pr(base, source, policy, workflow, jobs):
    if not source or not policy or not workflow or source['working_tree_dirty']:
        return False
    if policy['branch'] != 'main' or workflow['status'] != 'passed' or workflow.get('expected_profile') != 'full':
        return False
    head = source['git_head']
    checkout = workflow.get('checkout', {})
    if checkout.get('base') != base or checkout.get('head') != head:
        return False
    contexts = {item['context'] for item in policy['configuration']['required_status_checks']['checks']}
    return FULL_HARNESS_STEPS.keys() <= contexts and all(required_job_succeeded(jobs, name, head) for name in contexts)


def main_push_budget_handoff(root, project, base, completed, items, rule, *, source, policy, workflow, jobs):
    if not rule or not protected_full_pr(base, source, policy, workflow, jobs):
        return None
    head = source['git_head']

    recurring = 'control_reports_sha256' in rule
    if recurring and not exact_full_pr_jobs(workflow, jobs):
        return None
    repair_task, follow_up = rule['repair_task_id'], rule['follow_up_task_id']
    task = project['tasks'].get(follow_up, {})
    if (repair_task not in completed or task.get('status') != 'planned' or not task.get('plan')
            or task.get('depends_on') != [repair_task] or task.get('next_action') != rule['next_action']):
        return None
    push = items.get(rule['main_push_feedback_id'], {})
    if (push.get('status') != 'open' or push.get('severity') != 'major'
            or set(push['task_ids']) != {repair_task, follow_up}):
        return None
    push_budget = push.get('verification_budget', {})
    repair = push_budget.get('repair_commit')
    common = {'repository': policy['repository'], 'profile': 'full', 'platform': 'Windows', 'repair_commit': repair}
    if not repair or push_budget != dict(common, event='push') or not push['regression_checks'] or not push.get('diagnostic_reports'):
        return None
    if recurring and (repair != rule['repair_commit'] or not set(push['regression_checks']) <= set(task['checks'])):
        return None
    if not recurring:
        pr = items.get(rule['pull_request_feedback_id'], {})
        if (pr.get('status') != 'addressed' or repair_task not in pr['task_ids'] or not pr.get('diagnostic_reports')
                or pr.get('verification_budget') != dict(common, event='pull_request')):
            return None

    change = committed_scope(root, base, head)
    if change['profile'] != 'full' or change['base'] != base or not change['change_count']:
        return None
    try:
        committed = json.loads(git(root, 'show', f'{head}:project/delivery.json'))
        name = RECURRING_PUSH_BUDGET_POLICY if recurring else MAIN_PUSH_BUDGET_POLICY
        if committed['source_control'].get(name) != rule:
            return None
        exact_commit(root, repair)
        require_ancestor(root, repair, head)
        if git(root, 'merge-base', base, repair).strip().decode('ascii') == repair:
            return None
    except ScopeError:
        return None

    receipt = budget_regression_receipt(root, project, project['tasks'][repair_task], push, repair, source,
                                        complete=recurring)
    if not receipt:
        return None
    if recurring:
        run = {**workflow['run'], 'repository': {'full_name': policy['repository']}}
        clock = timing.hosted_timing(run, jobs)
        if not recurring_pr_evidence(root, project, base, rule, push, source, workflow, clock):
            return None
    elif not budget_observations_support_handoff(root, project, pr, push, repair, source, workflow):
        return None
    return {'feedback_id': push['id'], 'status': 'pending', 'branch': policy['branch'],
            'repair_task_id': repair_task, 'repair_commit': repair, 'regression_receipt': receipt,
            'follow_up_task_id': follow_up, 'next_action': task['next_action']}


def main_push_budget_closure(root, project, base, rule, push, previous, policy):
    budget = push.get('verification_budget', {})
    repair = budget.get('repair_commit')
    if (not policy or policy['branch'] != 'main' or not repair
            or budget != {'event': 'push', 'platform': 'Windows', 'profile': 'full',
                          'repository': policy['repository'], 'repair_commit': repair}):
        return False
    owners = {rule['repair_task_id'], rule['follow_up_task_id']}
    follow_up = project['tasks'].get(rule['follow_up_task_id'], {})
    if (not previous or previous['status'] != 'open' or previous.get('verification_budget') != budget
            or set(previous['task_ids']) != owners or set(push['task_ids']) != owners
            or feedback_fingerprint(previous) != feedback_fingerprint(push)
            or follow_up.get('status') != 'done' or follow_up.get('depends_on') != [rule['repair_task_id']]):
        return False
    try:
        require_complete_history(root)
        exact_commit(root, repair)
        require_ancestor(root, repair, base)
    except ScopeError:
        return False

    recurring = 'control_reports_sha256' in rule
    if recurring:
        if repair != rule['repair_commit']:
            return False
        try:
            evidence = recurring_budget_evidence(root, project, base, rule, push)
        except (OSError, ProjectError, ScopeError, ValueError, KeyError, TypeError, RuntimeError):
            return False
        if not evidence:
            return False
        samples, coverage, archive, recovery = evidence
    else:
        recovery = observed_budget_recovery(root, project, push).get(push['id']) or {}
    if recovery.get('status') != 'recovered' or recovery.get('repair_commit') != repair:
        return False
    latest = [comparison['latest_attempt'] or {} for comparison in recovery['comparisons']]
    if not latest:
        return False
    for sample in latest:
        revision = sample.get('source_commit')
        if not successful_budget_observation(sample) or sample.get('hosted_head') != revision:
            return False
        try:
            exact_commit(root, revision)
            require_ancestor(root, repair, revision)
            require_ancestor(root, revision, base)
        except ScopeError:
            return False
    for _, receipt in budget_regression_receipts(root, follow_up, push):
        ci, source = receipt.get('ci', {}), receipt['source']
        if (ci.get('event') != 'push' or ci['repository'] != budget['repository'] or ci['run_attempt'] != '1'
                or not windows_x64(receipt['environment']) or ci['head_sha'] != ci['checkout_sha']
                or ci['head_sha'] != source['git_head']):
            continue
        for sample in latest:
            if (ci['checkout_sha'], ci['run_id'], source['sha256']) != (
                    sample['source_commit'], str(sample['hosted_run_id']), sample['inputs_sha256']):
                continue
            if not recurring:
                return True
            locator = {'run_id': receipt['run_id'], 'source_commit': source['git_head'],
                       'report_sha256': receipt['report_sha256']}
            matches = [row for row in samples if timing.report_key(row) == timing.report_key(locator)
                       and row['ci'] and row['ci']['run_id'] == ci['run_id']]
            if (len(matches) != 1 or receipt['run_id'] != sample['run_id']
                    or not warm_main_sample(matches[0], budget['repository'])
                    or timing.coverage_key(matches[0]) != coverage):
                continue
            try:
                if verified_original_sample(archive, locator, matches[0]):
                    return True
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                continue
    return False


def python_finding_identity(finding):
    return digest(json_bytes([feedback_fingerprint(finding), finding['source'], finding['diagnostic_reports']]))


def python_repair_in_source(root, source, binding):
    if not source or not source.get('git_head'):
        return False
    try:
        require_ancestor(root, binding['repair_commit'], source['git_head'])
    except ScopeError:
        return False
    return True


def python_observation_rule(root, project, base, affected, completed, items, previous_feedback, source):
    candidate = project['delivery']['source_control'].get(PYTHON_OBSERVATION_POLICY)
    try:
        delivery = json.loads(git(root, 'show', f'{base}:project/delivery.json')) if base else {}
    except ScopeError:
        return None, 'The protected Windows Python observation binding could not be read.'
    protected = delivery.get('source_control', {}).get(PYTHON_OBSERVATION_POLICY)
    if protected is not None:
        if candidate != protected:
            return None, 'The PR changes or removes the protected Windows Python observation binding.'
        return protected, None
    if candidate:
        if any(candidate.get(key) != value for key, value in PYTHON_OBSERVATION_RECORDS.items()):
            return None, 'The Windows Python observation binding must name the separately owned delivery records.'
        repair, follow_up, finding = (candidate[key] for key in ('repair_task_id', 'follow_up_task_id', 'feedback_id'))
        if (candidate['policy_task_id'] not in completed or affected.intersection({repair, follow_up})
                or items.get(finding) != previous_feedback.get(finding)
                or python_repair_in_source(root, source, candidate)):
            return None, 'The Windows Python observation binding requires separate protected adoption.'
    finding = PYTHON_OBSERVATION_RECORDS['feedback_id']
    newly_addressed = (items.get(finding, {}).get('status') == 'addressed'
                       and previous_feedback.get(finding, {}).get('status') != 'addressed')
    if PYTHON_OBSERVATION_RECORDS['repair_task_id'] in completed or newly_addressed:
        return None, 'Windows Python repair delivery requires a separately adopted protected observation binding.'
    return None, None


def python_dispatch_receipt(receipt, repository):
    ci = receipt.get('ci') or {}
    return (ci.get('provider') == 'github-actions' and ci.get('repository') == repository
            and ci.get('event') == 'workflow_dispatch' and ci.get('run_attempt') == '1'
            and ci.get('head_sha') == ci.get('checkout_sha') == receipt['source']['git_head']
            and windows_x64(receipt['environment']))


def python_receipt_original(root, receipt):
    ci, source = receipt['ci'], receipt['source']
    locator = {'run_id': receipt['run_id'], 'source_commit': source['git_head'],
               'report_sha256': receipt['report_sha256']}
    artifacts = root / '.reports' / 'ci' / ci['run_id'] / 'windows'
    if artifacts.is_dir():
        archive = hosted_artifact_path(root, f".reports/ci/{ci['run_id']}/windows", directory=True)
    else:
        archive = diagnostic_archive_root(root)
    report, log = read_native(archive, locator)
    if (report['ci'] != ci or report['source'] != source or report['status'] != 'passed'
            or report['selection'] != {'profile': 'task', 'task_id': receipt['task_id'],
                                      'checks': sorted({'environment', *(row['id'] for row in receipt['checks'])})}
            or report['task_definitions'].get(receipt['task_id']) != receipt['task_definition_sha256']
            or report['project_definition_sha256'] != receipt['project_definition_sha256']
            or report.get('observed_feedback') != receipt.get('observed_feedback')
            or any(report.get(key) is not True for key in ('source_unchanged', 'project_unchanged'))):
        raise ProjectError('The guarded Windows original differs from the repair receipt.')
    rows = {row['id']: row for row in report['checks']}
    if any(any(rows[row['id']].get(key) != value for key, value in row.items()) for row in receipt['checks']):
        raise ProjectError('The guarded Windows check results differ from the repair receipt.')
    return report, log


def python_attempt_jobs(root, ci, *, branch=None, conclusion=None):
    endpoint = f"repos/{ci['repository']}/actions/runs/{ci['run_id']}/attempts/1"
    run = json.loads(command(['gh', 'api', endpoint], root))
    if (run['path'] != WORKFLOW or str(run['id']) != ci['run_id'] or run['run_attempt'] != 1
            or run['head_sha'] != ci['head_sha'] or run['event'] != ci['event'] or run['status'] != 'completed'
            or (branch and run['head_branch'] != branch) or (conclusion and run['conclusion'] != conclusion)):
        raise ProjectError('The authenticated workflow attempt differs from the retained Windows original.')
    return json.loads(command(['gh', 'api', f'{endpoint}/jobs?per_page=100'], root))['jobs']


def python_pr_inventory(root, project, policy, source, workflow, receipt_path):
    repository = policy['repository']
    run, checkout = workflow['run'], workflow['checkout']
    try:
        receipt = read_json(root / receipt_path)
        ci = receipt['ci']
        guard_jobs = python_attempt_jobs(root, ci, conclusion='success')
        if not all(required_job_succeeded(guard_jobs, job, ci['head_sha']) for job in (
                'Check reviewed task dispatch', 'verify task (windows-latest)')):
            return None
        guard, guard_log = python_receipt_original(root, receipt)
        artifacts = hosted_artifact_path(root, f".reports/ci/{run['id']}/windows", directory=True)
        staged, report_sha = read_review_report(hosted_artifact_path(artifacts, 'test-report.json'), 4 * 1024 * 1024)
        scope, _ = read_review_report(hosted_artifact_path(artifacts, 'verification-scope.json'), 4096)
        if (scope.get('kind') != 'scopehub-verification-scope-v1' or scope.get('profile') != 'full'
                or scope.get('base') != checkout['base'] or scope.get('checkout') != checkout['commit']
                or scope.get('report_sha256') != report_sha):
            return None
        ordinary, ordinary_log = read_native(artifacts, {
            'run_id': staged['run_id'], 'source_commit': checkout['commit'], 'report_sha256': report_sha})
        if (not python_hosted_report(ordinary, repository, 'pull_request', checkout['head'], checkout['commit'])
                or ordinary['ci']['run_id'] != str(run['id']) or ordinary['status'] != 'passed'
                or ordinary['source'] != {**source, 'git_head': checkout['commit']}
                or ordinary['project_definition_sha256'] != guard['project_definition_sha256']):
            return None
        check = project['checks'][PYTHON_CHECK]
        inventory = serial_result(ordinary, ordinary_log, check)
        if inventory == serial_result(guard, guard_log, check):
            return {'inventory_sha256': digest(json_bytes(inventory))}
    except (OSError, ValueError, ScopeError, TypeError, KeyError, RuntimeError):
        pass
    return None


def python_hosted_report(report, repository, event, head, checkout, *, profile='full'):
    ci = report['ci']
    environment = report['environment']
    native = report['native_requirement']
    return (ci['provider'] == 'github-actions' and ci['repository'] == repository
            and ci['event'] == event and ci['run_attempt'] == '1'
            and ci['head_sha'] == head and ci['checkout_sha'] == report['source']['git_head'] == checkout
            and report['source']['working_tree_dirty'] is False and report['source_end'] == report['source']
            and report['source_unchanged'] is True and report['project_unchanged'] is True
            and isinstance(environment, dict) and isinstance(environment.get('machine'), str) and windows_x64(environment)
            and native['requested'] is (profile == 'full') and native['satisfied'] is True
            and report['selection']['profile'] == profile and report['selection']['task_id'] is None)


def python_push_observation(root, project, base, binding, follow_up, policy):
    missing = {'status': 'missing', 'first_push_commit': None,
               'next_action': 'Verify the first protected main push, its Windows Python producer and the guarded original.'}
    try:
        repair = binding['repair_commit']
        require_complete_history(root)
        exact_commit(root, repair)
        require_ancestor(root, repair, base)
        descendants = set(git(root, 'rev-list', '--ancestry-path', f'{repair}..{base}').decode('ascii').splitlines())
        descendants.add(repair)
        first_parent = git(root, 'rev-list', '--first-parent', '--reverse', base).decode('ascii').splitlines()
        first = next(revision for revision in first_parent if revision in descendants)
        missing['first_push_commit'] = first
        locators = [row for row in follow_up.get('diagnostic_reports', []) if row['source_commit'] == first]
        if len(locators) != 1 or not policy or policy['branch'] != 'main':
            return missing
        locator, = locators
        report, log = read_native(diagnostic_archive_root(root), locator)
        repository = policy['repository']
        profile = report['selection']['profile']
        selected = report['selection']['checks']
        if (profile not in {'full', 'ci-windows-python'}
                or not python_hosted_report(report, repository, 'push', first, first, profile=profile)
                or not isinstance(selected, list) or selected.count(PYTHON_CHECK) != 1
                or (profile == 'ci-windows-python' and selected != [PYTHON_CHECK])
                or report['source'] != committed_source_identity(root, first)):
            return missing
        jobs = python_attempt_jobs(root, report['ci'], branch='main')
        producers = [job for job in jobs if job['name'] == timing.WINDOWS_PRODUCERS['python']]
        if len(producers) != 1 or producers[0]['head_sha'] != first or producers[0]['status'] != 'completed':
            return missing
        conclusion = producers[0]['conclusion']
        ledger = json.loads(git(root, 'show', f'{first}:project/tasks.json'))
        task = next(row for row in ledger['tasks'] if row['id'] == binding['repair_task_id'])
        finding = next(row for row in project['feedback']['items'] if row['id'] == binding['feedback_id'])
        if task['status'] != 'done':
            return missing
        observed = {**missing, 'diagnostic': locator}
        failed = {**observed, 'status': 'failed',
                  'next_action': 'Keep the finding open, retain the first push, and assign a corrective task.'}
        check = project['checks'][PYTHON_CHECK]
        row = python_row(report)
        python = report['environment']['python_executable']
        if (conclusion in {'failure', 'timed_out'} and report['status'] == row['status'] == 'failed'
                and check['status'] == 'implemented' and check['command'] == SERIAL_COMMAND
                and check['windows_timeout_seconds'] == WINDOWS_DEADLINE_SECONDS
                and isinstance(python, str) and python and row['command'] == [python, *SERIAL_COMMAND[1:]]):
            return {**failed, 'reason_code': row.get('reason_code', 'python_check_failed')}
        if profile != 'full' or conclusion != 'success':
            return missing
        inventory = serial_result(report, log, check)
        compared = False
        for _, receipt in budget_regression_receipts(root, task, finding):
            if (not python_dispatch_receipt(receipt, repository)
                    or receipt['source']['sha256'] != report['source']['sha256']):
                continue
            try:
                guard, guard_log = python_receipt_original(root, receipt)
                if guard['project_definition_sha256'] != report['project_definition_sha256']:
                    continue
                comparison = serial_result(guard, guard_log, check)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            compared = True
            if inventory == comparison:
                return {**observed, 'status': 'passed',
                        'next_action': 'Complete the observation task and record closure of the Python deadline finding. '
                                       'Evaluate the separate 900-second whole-workflow objective.'}
        if compared:
            return {**failed, 'reason_code': 'inventory_mismatch'}
        return missing
    except (OSError, ValueError, ScopeError, TypeError, KeyError, RuntimeError, StopIteration, subprocess.SubprocessError):
        return missing


def python_observation_scope(root, project, base, completed, previous_tasks, items, previous_feedback,
                             binding, *, source, policy, workflow, jobs):
    repair, follow_up = binding['repair_task_id'], binding['follow_up_task_id']
    repair_on_base = previous_tasks.get(repair, {}).get('status') == 'done'
    failure = 'Windows Python repair delivery requires a verified native comparison and an open protected handoff.'
    if repair not in completed and not repair_on_base:
        return None, failure if python_repair_in_source(root, source, binding) else None
    finding = items.get(binding['feedback_id'], {})
    task = project['tasks'].get(follow_up, {})
    prior_finding = previous_feedback.get(binding['feedback_id'], {})
    prior_task = previous_tasks.get(follow_up, {})
    if repair_on_base and task == prior_task and finding == prior_finding:
        return None, None
    matches = (finding.get('severity') == 'major' and finding.get('diagnostic_reports')
               and python_finding_identity(finding) == binding['finding_sha256']
               and set(finding['task_ids']) == {repair, follow_up}
               and task.get('depends_on') == [repair] and task.get('plan')
               and set(finding['regression_checks']) <= set(task['checks'])
               and task.get('status') in {'planned', 'active', 'blocked', 'done'})
    details = {'feedback_id': binding['feedback_id'], 'branch': 'main', 'repair_task_id': repair,
               'repair_commit': binding['repair_commit'], 'follow_up_task_id': follow_up}
    if not matches:
        return None, 'Windows Python delivery requires the original open finding and its protected observation owner.'
    if repair_on_base:
        observation = python_push_observation(root, project, base, binding, task, policy)
        prior_locators = prior_task.get('diagnostic_reports', [])
        retained = all(locator in task.get('diagnostic_reports', []) for locator in prior_locators)
        if (not retained or finding['status'] not in {'open', 'addressed'}
                or (finding['status'] == 'addressed') != (task['status'] == 'done')
                or (finding['status'] == 'addressed' and observation['status'] != 'passed')):
            return {**details, **observation}, 'Windows Python deadline closure requires the verified first protected push.'
        if (observation['status'] == 'failed' and finding == prior_finding and task['status'] == 'planned'
                and project['tasks'][repair] == previous_tasks[repair]
                and observation.get('diagnostic') in task.get('diagnostic_reports', [])
                and task == {**prior_task, 'diagnostic_reports': task['diagnostic_reports'],
                              'next_action': task['next_action']}):
            observation['recording_failed_observation'] = True
        return {**details, **observation}, None
    if (finding['status'] != 'open' or task['status'] != 'planned' or not task.get('next_action')
            or task.get('diagnostic_reports') or not protected_full_pr(base, source, policy, workflow, jobs)
            or not exact_full_pr_jobs(workflow, jobs)):
        return None, failure
    head = source['git_head']
    try:
        require_ancestor(root, base, head)
        exact_commit(root, binding['repair_commit'])
        require_ancestor(root, binding['repair_commit'], head)
        committed = json.loads(git(root, 'show', f'{head}:project/delivery.json'))
        if committed['source_control'].get(PYTHON_OBSERVATION_POLICY) != binding:
            return None, failure
    except ScopeError:
        return None, failure
    receipt = budget_regression_receipt(root, project, project['tasks'][repair], finding, binding['repair_commit'],
                                        source, complete=True, dispatch_repository=policy['repository'])
    inventory = python_pr_inventory(root, project, policy, source, workflow, receipt) if receipt else None
    if inventory is None:
        return None, failure
    return {**details, **inventory, 'status': 'pending', 'regression_receipt': receipt,
            'next_action': task['next_action']}, None


def delivery_scope(root, project, base, *, source=None, policy=None, workflow=None, jobs=()):
    """Use actual base history and validated receipts, not a supplied task list."""
    history, previous_feedback = compare_history(root, base)
    previous_file = subprocess.run(['git', 'show', f'{base}:project/tasks.json'], cwd=root, env=checkout_git_environment(),
                                   capture_output=True, check=False)
    previous_tasks = ({task['id']: task for task in json.loads(previous_file.stdout)['tasks']}
                      if previous_file.returncode == 0 else {})
    changed = [task for key, task in project['tasks'].items() if task != previous_tasks.get(key)]
    completed = [task['id'] for task in changed if task['status'] == 'done']
    unfinished = [task['id'] for task in changed if task['status'] in {'active', 'blocked'}]
    blockers = []
    if not completed:
        blockers.append('The PR has no completed delivery task with archived evidence.')
    blockers.extend(f'Unfinished delivery task: {key}' for key in unfinished)
    affected = {task['id'] for task in changed}
    items = {item['id']: item for item in project['feedback']['items']}
    rule, policy_blocker = main_push_budget_rule(root, project, history.get('base_commit'), completed,
                                                items, previous_feedback)
    recurring, recurring_blocker = recurring_push_budget_rule(
        root, project, history.get('base_commit'), affected, items, previous_feedback)
    blockers.extend(error for error in (policy_blocker, recurring_blocker) if error)
    handoffs, reviewed_budgets = {}, set()
    for name, binding in ((MAIN_PUSH_BUDGET_POLICY, rule), (RECURRING_PUSH_BUDGET_POLICY, recurring)):
        if not binding:
            continue
        handoff = main_push_budget_handoff(root, project, base, completed, items, binding,
                                          source=source, policy=policy, workflow=workflow, jobs=jobs)
        if handoff:
            handoffs[name] = handoff
        push_id = binding['main_push_feedback_id']
        repair_task = binding['repair_task_id']
        if (name == RECURRING_PUSH_BUDGET_POLICY and repair_task in completed
                and previous_tasks.get(repair_task, {}).get('status') != 'done' and not handoff):
            blockers.append(f'Recurring repair delivery requires the verified open handoff: {push_id}')
        if binding['repair_task_id'] in affected:
            reviewed_budgets.add(push_id)
            if 'pull_request_feedback_id' in binding:
                reviewed_budgets.add(binding['pull_request_feedback_id'])
        push, prior_finding = items.get(push_id), previous_feedback.get(push_id)
        if push and push['status'] == 'addressed':
            recorded = (prior_finding and prior_finding['status'] == 'addressed'
                        and prior_finding.get('verification_budget') == push.get('verification_budget')
                        and set(prior_finding['task_ids']) == set(push['task_ids'])
                        and feedback_fingerprint(prior_finding) == feedback_fingerprint(push))
            if not recorded and not main_push_budget_closure(root, project, base, binding, push, prior_finding, policy):
                blockers.append(f'Main-push timing closure lacks a verified main-push observation: {push_id}')
    handed_off = {handoff['feedback_id'] for handoff in handoffs.values()}
    python_rule, python_blocker = python_observation_rule(
        root, project, history.get('base_commit'), affected, completed, items, previous_feedback, source)
    if python_rule:
        observation, python_blocker = python_observation_scope(
            root, project, base, completed, previous_tasks, items, previous_feedback, python_rule,
            source=source, policy=policy, workflow=workflow, jobs=jobs)
        if observation:
            handoffs[PYTHON_OBSERVATION_POLICY] = observation
            if not python_blocker and (observation['status'] == 'pending' or observation.get('recording_failed_observation')):
                handed_off.add(observation['feedback_id'])
    if python_blocker:
        blockers.append(python_blocker)
    blockers.extend(f"Unresolved {item['severity']} feedback: {item['id']}"
                    for item in items.values() if item['status'] != 'addressed'
                    and item['id'] not in handed_off
                    and (item['severity'] == 'critical' or item['id'] in reviewed_budgets
                         or item['severity'] == 'major' and affected.intersection(item['task_ids'])))
    return {'completed_task_ids': completed, 'unfinished_task_ids': unfinished,
            'blockers': blockers, 'history': history, **handoffs,
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
        fields = ('id', 'run_attempt', 'status', 'conclusion', 'head_sha', 'html_url', 'event', 'path',
                  'head_branch', 'created_at')
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
