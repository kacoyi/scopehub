"""Observe integration gates for one PR; never authorize or perform a merge."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

if __package__:
    from . import timing
    from .ci import WORKFLOW, classify_runs, command, github_identity
    from .diagnostic_archive import resolve_diagnostic
    from .project import (ProjectError, compare_history, diagnostic_archive_root, digest, feedback_fingerprint,
                         json_bytes, load_project, project_definition_hash, read_json, read_review_report, source_identity,
                         task_definition_hash, windows_x64)
    from .protection import violations
    from .verification_evidence import hosted_artifact_path
    from .verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                     exact_commit, git, require_ancestor, require_complete_history)
else:
    import timing
    from ci import WORKFLOW, classify_runs, command, github_identity
    from diagnostic_archive import resolve_diagnostic
    from project import (ProjectError, compare_history, diagnostic_archive_root, digest, feedback_fingerprint,
                         json_bytes, load_project, project_definition_hash, read_json, read_review_report, source_identity,
                         task_definition_hash, windows_x64)
    from protection import violations
    from verification_evidence import hosted_artifact_path
    from verification_scope import (SCOPED_PROFILES, ScopeError, checkout_git_environment, committed_scope,
                                    exact_commit, git, require_ancestor, require_complete_history)

ROOT = Path(__file__).resolve().parents[1]
PR_FIELDS = ('number,url,state,isDraft,headRefOid,headRefName,baseRefOid,baseRefName,'
             'mergeable,mergeStateStatus,reviewDecision')
REVISION = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
PINNED_CHECKOUT = r'actions/checkout@[0-9a-f]{40}'
FULL_HARNESS_STEPS = {
    'verify (ubuntu-latest)': 'Run pnpm check',
    'verify (windows-latest)': 'Run uv run python scripts/check.py --require-windows',
}
SCOPED_HARNESS_STEPS = {f'Verify {profile} changes': profile for profile in SCOPED_PROFILES}
WINDOWS_PUBLISHER = 'verify (windows-latest)'
WINDOWS_PRODUCER_LAYOUTS = {
    timing.WINDOWS_AGGREGATE_MARKER: timing.LEGACY_WINDOWS_LANES,
    timing.WINDOWS_RECOVERY_MARKER: timing.WINDOWS_RECOVERY_LANES,
    timing.WINDOWS_WORKSPACE_MARKER: timing.WINDOWS_WORKSPACE_LANES,
}
MAIN_PUSH_BUDGET_POLICY = 'post_merge_main_push_budget'
RECURRING_PUSH_BUDGET_POLICY = 'recurring_main_push_budget'
# This exception belongs to one reviewed repair. The first policy PR cannot
# use its own configuration to choose which existing observation to govern.
MAIN_PUSH_BUDGET_BINDING = {
    'pull_request_feedback_id': 'verification-overrun-without-timely-remediation',
    'main_push_feedback_id': 'verification-main-push-feedback-budget-overrun',
    'repair_task_id': 'M0-087',
    'follow_up_task_id': 'M0-091',
}


def checked_pull_request(log, checkout_step=None):
    """Read the ref and actual HEAD printed by the pinned checkout step on Linux or Windows."""
    messages = (line.rstrip('\r\n').partition(' ')[2] for line in log)
    for message in messages:
        if re.fullmatch(rf'##\[group\]Run {PINNED_CHECKOUT}', message):
            if checkout_step is not None and message != f'##[group]{checkout_step}':
                raise ProjectError('The checkout log uses a different pinned action than the job record.')
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


def job_checkout(root, repository, job_id, checkout_step=None):
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
        return checked_pull_request(log, checkout_step)


def required_checkouts(root, repository, jobs, contexts):
    observed = []
    for name in contexts:
        matches = [job for job in jobs if job.get('name') == name]
        if len(matches) != 1:
            raise ProjectError(f'Expected one required job named {name}.')
        job = matches[0]
        checkout = [step for step in job.get('steps', ()) if re.fullmatch(rf'Run {PINNED_CHECKOUT}', step['name'])]
        if (len(checkout) != 1 or checkout[0].get('status') != 'completed'
                or checkout[0].get('conclusion') != 'success'):
            raise ProjectError(f'Required job {name} has no successful pinned checkout.')
        step = checkout[0]['name']
        number, commit = job_checkout(root, repository, job['id'], step)
        observed.append({'name': name, 'job_id': job['id'], 'pull_request': number, 'commit': commit, 'step': step})
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


def verified_merge_source(root, commit, tested):
    if tested['sha'] != commit or not re.fullmatch(REVISION, commit):
        raise ProjectError('The observed merge commit differs from the checkout log.')
    parents = [parent['sha'] for parent in tested['parents']]
    record = tested.get('tree')
    reported_tree = record.get('sha') if isinstance(record, dict) else None
    if 'tree' in tested and (not isinstance(reported_tree, str) or not re.fullmatch(REVISION, reported_tree)):
        raise ProjectError('The tested merge reports an invalid Git tree.')
    try:
        kind = git(root, 'cat-file', '--batch-check=%(objecttype)', input=f'{commit}\n'.encode('ascii')).strip()
        if kind == b'commit':
            # Read the commit object directly so grafts, shallow parents and refs cannot change its lineage.
            headers = git(root, 'cat-file', 'commit', commit).partition(b'\n\n')[0].splitlines()
            tree, = (line[5:].decode('ascii') for line in headers if line.startswith(b'tree '))
            local_parents = [line[7:].decode('ascii') for line in headers if line.startswith(b'parent ')]
            authority = 'local_commit'
        elif kind == f'{commit} missing'.encode('ascii'):
            # An authenticated historical merge may have been collected locally. Its tree must still be verifiable.
            tree = reported_tree
            authority = 'hosted_commit_with_local_tree'
        else:
            raise ValueError('Expected a Git commit object or an absent object.')
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError) as error:
        raise ProjectError('The tested merge commit and tree could not be verified locally.') from error
    if kind == b'commit' and ((reported_tree is not None and tree != reported_tree) or parents != local_parents):
        raise ProjectError('The hosted tested merge tree or parents conflict with the local commit object.')
    try:
        if tree is None or git(root, 'cat-file', '-t', tree).strip() != b'tree':
            raise ScopeError('Expected a Git tree.')
    except (ScopeError, OSError, subprocess.SubprocessError) as error:
        raise ProjectError('The tested merge commit and tree could not be verified locally.') from error
    return parents, tree, authority


def expected_verification_profile(root, base, head, merge_tree):
    """Use the head comparison only when it represents the checked merge tree."""
    try:
        head_tree = git(root, 'rev-parse', '--verify', '--end-of-options', f'{head}^{{tree}}').strip().decode('ascii')
    except (ScopeError, OSError, subprocess.SubprocessError, UnicodeError):
        return 'full', 'The local PR head tree could not be verified.'
    if not re.fullmatch(REVISION, head_tree):
        return 'full', 'The local PR head tree could not be verified.'
    if head_tree != merge_tree:
        return 'full', 'The checked merge tree differs from the local PR head tree.'
    scope = committed_scope(root, base, head)
    return scope['profile'], scope.get('reason')


def declared_checkout(job):
    action, = (step.get('uses') for step in job['steps']
               if str(step.get('uses', '')).startswith('actions/checkout@'))
    if not re.fullmatch(PINNED_CHECKOUT, action):
        raise ValueError('Expected a pinned checkout action.')
    return f'Run {action}'


def committed_producers(root, tree):
    try:
        jobs = yaml.safe_load(git(root, 'cat-file', 'blob', f'{tree}:{WORKFLOW}'))['jobs']
        verify = jobs['verify']
        publisher = jobs.get('windows_change', verify)
        marker = None
        if 'windows_change' in jobs:
            marker, = (step.get('name') for step in publisher['steps']
                       if step.get('name') in WINDOWS_PRODUCER_LAYOUTS)
        lanes = {f'windows_{lane}': lane for lane in WINDOWS_PRODUCER_LAYOUTS.get(marker, ())}
        lane_ids = {job_id for job_id in jobs if job_id.startswith('windows_') and job_id != 'windows_change'}
        if lane_ids != lanes.keys():
            raise ValueError('The workflow declarations disagree with the Windows producer layout.')
        if marker:
            needs = publisher['needs']
            if not isinstance(needs, list) or len(needs) != len(lanes) or set(needs) != lanes.keys():
                raise ValueError('The publisher dependencies disagree with the Windows producer layout.')
        declarations = {'verify (ubuntu-latest)': verify, WINDOWS_PUBLISHER: publisher}
        for job_id, lane in lanes.items():
            producer = jobs[job_id]
            name = timing.WINDOWS_PRODUCERS[lane]
            if (producer['strategy']['matrix']['lane'] != [lane] or producer['runs-on'] != 'windows-latest'
                    or producer['name'] not in ('Windows change checks (${{ matrix.lane }})', name)):
                raise ValueError('The Windows producer declaration disagrees with its expected lane.')
            declarations[name] = producer
        declared = {name: (declared_checkout(job), job['steps']) for name, job in declarations.items()}
        # Historical workflows can report skipped administrative jobs outside the PR verification matrix.
        inactive = {job['name'] for job_id, job in jobs.items()
                    if job_id not in {'verify', 'windows_change', *lanes} and 'if' in job
                    and 'name' in job and '${{' not in job['name']}
    except (ScopeError, OSError, subprocess.SubprocessError, yaml.YAMLError, RecursionError,
            AttributeError, KeyError, TypeError, ValueError) as error:
        raise ProjectError('The Windows producer layout could not be verified from the tested merge tree.') from error
    return marker, declared, inactive


def verified_windows_producers(run, repository, jobs, marker):
    windows, = (row for row in jobs if row.get('name') == WINDOWS_PUBLISHER)
    guards = [step for step in windows.get('steps', ()) if step.get('name') in timing.WINDOWS_MARKERS]
    result = timing.hosted_timing({**run, 'repository': {'full_name': repository}}, jobs)
    workflow = result['workflow']
    if not (workflow['matrix_complete'] and workflow['identity_complete']):
        return False
    if marker is None:
        return not guards and not workflow.get('windows_split')
    if (not workflow.get('windows_split') or len(guards) != 1 or guards[0]['name'] != marker
            or guards[0].get('status') != 'completed' or guards[0].get('conclusion') != 'success'):
        return False
    profile = windows['verification_profile']
    for lane in WINDOWS_PRODUCER_LAYOUTS[marker]:
        job, = (row for row in jobs if row.get('name') == timing.WINDOWS_PRODUCERS[lane])
        if job['conclusion'] != 'success':
            return False
        harness = f'Verify the Windows {lane} lane'
        expected = (harness if profile == 'full' else
                    f'Verify {profile} changes' if lane == 'core' else 'Select the Windows lane work')
        successful = {step['name'] for step in job.get('steps', [])
                      if step.get('status') == 'completed' and step.get('conclusion') == 'success'}
        if expected not in successful or (profile != 'full' and lane != 'core' and harness in successful):
            return False
    return True


def declared_artifact_step(steps, action, field, value):
    step, = (step for step in steps
             if re.fullmatch(rf'actions/{action}-artifact@[0-9a-f]{{40}}', step.get('uses', ''))
             and step.get('with', {}).get(field) == value
             and step.get('continue-on-error', False) is False)
    return step


def declared_publication_step(steps, step_id):
    step, = (step for step in steps if step.get('id') == step_id and step.get('name')
             and step.get('continue-on-error', False) is False)
    return step


def workflow_step_name(step):
    if 'name' in step:
        return step['name']
    if 'uses' in step:
        return f"Run {step['uses']}"
    return None


def require_completed_steps(job, steps, checkout, required):
    checkout_index, = (index for index, step in enumerate(steps) if workflow_step_name(step) == checkout)
    checkout_observed, = (step for step in job['steps'] if step.get('name') == checkout)
    names = [workflow_step_name(step) for step in steps]
    for declaration in required:
        name = workflow_step_name(declaration)
        matches = [step for step in job['steps'] if step.get('name') == name]
        # GitHub gives identically named actions separate numbers, including when one is skipped.
        # Anchor their declared positions at the verified checkout to distinguish the required transfer.
        if names.count(name) > 1:
            number = checkout_observed.get('number')
            if type(number) is not int:
                raise ProjectError(f"Required job {job['name']} has no numbered evidence for {name}.")
            expected = number + steps.index(declaration) - checkout_index
            matches = [step for step in matches if step.get('number') == expected]
        if len(matches) != 1 or matches[0].get('status') != 'completed' or matches[0].get('conclusion') != 'success':
            raise ProjectError(f"Required job {job['name']} has no successful {name} evidence step.")


def require_windows_publication(jobs, declared, marker):
    by_name = {job['name']: job for job in jobs}
    profile = by_name[WINDOWS_PUBLISHER]['verification_profile']
    publishers = [WINDOWS_PUBLISHER]
    if marker:
        lanes = WINDOWS_PRODUCER_LAYOUTS[marker] if profile == 'full' else ('core',)
        publishers.extend(timing.WINDOWS_PRODUCERS[lane] for lane in lanes)
    for name in publishers:
        checkout, steps = declared[name]
        try:
            preparation = declared_publication_step(steps, 'reports')
            staging = declared_publication_step(steps, 'artifacts')
            upload = declared_artifact_step(steps, 'upload', 'path', '${{ steps.artifacts.outputs.path }}')
            required = [preparation, staging, upload]
            if marker and name == WINDOWS_PUBLISHER:
                field, value = ('pattern', 'verification-windows-${{ github.run_attempt }}-*') if profile == 'full' else (
                    'name', 'verification-windows-${{ github.run_attempt }}-core')
                required.append(declared_artifact_step(steps, 'download', field, value))
        except (AttributeError, TypeError, ValueError) as error:
            raise ProjectError(f"The tested workflow does not declare the required evidence steps for {name}.") from error
        require_completed_steps(by_name[name], steps, checkout, required)


def qualified_producer_checkouts(root, repository, jobs, run, tree, observed):
    marker, declared, inactive = committed_producers(root, tree)
    active = [job['name'] for job in jobs if not (
        job['name'] in inactive and job.get('status') == 'completed' and job.get('conclusion') == 'skipped')]
    protected = {row['name'] for row in observed}
    if (sorted(active) != sorted(declared) or not protected <= declared.keys()
            or not all(required_job_succeeded(jobs, name, run['head_sha']) for name in declared)
            or not verified_windows_producers(run, repository, jobs, marker)):
        raise ProjectError('The run lacks the committed Windows producer layout.')
    remaining = [name for name in declared if name not in protected]
    if remaining:
        observed = [*observed, *required_checkouts(root, repository, jobs, remaining)]
    if (len({row['job_id'] for row in observed}) != len(declared)
            or len({(row['pull_request'], row['commit']) for row in observed}) != 1):
        raise ProjectError('Required jobs checked different pull requests or merge commits.')
    for row in observed:
        if row['step'] != declared[row['name']][0]:
            raise ProjectError(f"Required job {row['name']} used a different pinned checkout than the tested workflow.")
    require_windows_publication(jobs, declared, marker)
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
        try:
            parents, tree, authority = verified_merge_source(root, commit, tested)
        except ProjectError as error:
            result.update(status='blocked', reason=f"Run {run['id']}: {error}")
            return result, jobs
        if len(parents) != 2 or parents[1] != run['head_sha']:
            result.update(status='blocked', reason=f"Run {run['id']} checked a merge that does not match its declared head.")
            return result, jobs
        if parents[0] != base:
            reason = f"Run {run['id']} checked PR #{number} at a different base."
            continue
        result['checkout'] = {'pull_request': number, 'commit': commit, 'base': base, 'head': head}
        result['tested_tree'] = {'sha': tree, 'authority': authority}
        result['required_checkouts'] = observed
        expected, scope_reason = expected_verification_profile(root, base, head, tree)
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
        if result['status'] == 'passed':
            try:
                result['required_checkouts'] = qualified_producer_checkouts(root, repository, jobs, run, tree, observed)
            except ProjectError as error:
                result.update(status='blocked', reason=f"Run {run['id']}: {error}")
                return result, jobs
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


def budget_regression_receipt(root, project, task, finding, repair, source, *, complete=False):
    project_definition = project_definition_hash(project)
    for path, receipt in budget_regression_receipts(root, task, finding):
        observed = receipt['source']
        if receipt.get('project_definition_sha256') != project_definition or observed['sha256'] != source['sha256']:
            continue
        ci = receipt.get('ci') or {}
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


def recurring_full_workflow(workflow, jobs):
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


def main_push_budget_handoff(root, project, base, completed, items, rule, *, source, policy, workflow, jobs):
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

    recurring = 'control_reports_sha256' in rule
    if recurring and not recurring_full_workflow(workflow, jobs):
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


def delivery_scope(root, project, base, *, source=None, policy=None, workflow=None, jobs=()):
    """Use actual base history and validated receipts, not a supplied task list."""
    history, previous_feedback = compare_history(root, base)
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
                and previous.get(repair_task, {}).get('status') != 'done' and not handoff):
            blockers.append(f'Recurring repair delivery requires the verified open handoff: {push_id}')
        if binding['repair_task_id'] in affected:
            reviewed_budgets.add(push_id)
            if 'pull_request_feedback_id' in binding:
                reviewed_budgets.add(binding['pull_request_feedback_id'])
        push, previous = items.get(push_id), previous_feedback.get(push_id)
        if push and push['status'] == 'addressed':
            recorded = (previous and previous['status'] == 'addressed'
                        and previous.get('verification_budget') == push.get('verification_budget')
                        and set(previous['task_ids']) == set(push['task_ids'])
                        and feedback_fingerprint(previous) == feedback_fingerprint(push))
            if not recorded and not main_push_budget_closure(root, project, base, binding, push, previous, policy):
                blockers.append(f'Main-push timing closure lacks a verified main-push observation: {push_id}')
    handed_off = {handoff['feedback_id'] for handoff in handoffs.values()}
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
