"""Inspect hosted verification and preflight an explicitly selected task dispatch."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import quote
from uuid import uuid4

if __package__:
    from .project import (
        ProjectError, checkout_git_environment, committed_source_identity, digest, load_project, read_json,
        source_identity, task_definition_hash, validate_model,
    )
    from .timing import ci_budget_status
    from .verification_scope import ScopeError, exact_commit, git, oid, require_ancestor, require_complete_history, resolve_commit
else:
    from project import (
        ProjectError, checkout_git_environment, committed_source_identity, digest, load_project, read_json,
        source_identity, task_definition_hash, validate_model,
    )
    from timing import ci_budget_status
    from verification_scope import ScopeError, exact_commit, git, oid, require_ancestor, require_complete_history, resolve_commit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/verify.yml'


def command(argv, root=ROOT):
    result = subprocess.run(argv, cwd=root, env=checkout_git_environment(), capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Authentication failures belong to gh; never inspect credential files.
        raise ProjectError(f'{argv[0]} command failed: {result.stderr.strip()}')
    return result.stdout.strip()


def github_identity(root=ROOT):
    remote = command(['git', 'remote', 'get-url', 'origin'], root)
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?', remote)
    if not match:
        raise ProjectError('Expected a GitHub HTTPS origin without embedded credentials')
    marker = subprocess.run(['git', 'config', '--local', '--get', 'github.account'],
                            cwd=root, env=checkout_git_environment(), capture_output=True, text=True, check=False)
    if marker.returncode or not marker.stdout.strip():
        raise ProjectError('Set the owner-approved local github.account marker before authenticated operations')
    account = marker.stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', account):
        raise ProjectError('Invalid local GitHub account marker')
    command(['gh', 'auth', 'switch', '--hostname', 'github.com', '--user', account], root)
    if command(['gh', 'api', 'user', '--jq', '.login'], root).lower() != account.lower():
        raise ProjectError('The active GitHub account does not match the repository marker')
    return f'{match[1]}/{match[2]}'


def expected_integration_tree(root, base, reviewed):
    # A real three-way merge can write tree objects. Keep them outside the checkout's object database.
    objects = git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'objects').removesuffix(b'\n')
    if os.name == 'nt':
        objects = objects.removesuffix(b'\r')
    if b'\n' in objects:
        raise ProjectError('The Git object directory cannot be represented in the temporary merge.')
    with TemporaryDirectory(prefix='scopehub-task-tree-') as folder:
        info = Path(folder) / 'info'
        info.mkdir()
        (info / 'alternates').write_bytes(objects + b'\n')
        result = subprocess.run(
            ['git', '--no-replace-objects', '--no-optional-locks',
             'merge-tree', '--write-tree', '--no-messages', base, reviewed],
            cwd=root, capture_output=True, timeout=30,
            env={**checkout_git_environment(), 'GIT_OBJECT_DIRECTORY': folder,
                 'GIT_GRAFT_FILE': os.devnull, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''},
        )
    if result.returncode == 1:
        raise ProjectError('The reviewed source conflicts with the intended base. Review the resolved integration first.')
    if result.returncode:
        raise ProjectError('The expected integration is unavailable. Verify local Git history and objects.')
    return oid(result.stdout.strip().decode('ascii'))


def committed_tasks(root, commit):
    return {row['id']: row for row in json.loads(git(root, 'show', f'{commit}:project/tasks.json'))['tasks']}


def remote_branch_tip(root, repository, branch):
    endpoint = f'repos/{repository}/git/ref/heads/{quote(branch, safe="/")}'
    ref = json.loads(command(['gh', 'api', endpoint], root))
    target = ref.get('object') if isinstance(ref, dict) else None
    if not isinstance(target, dict) or ref.get('ref') != f'refs/heads/{branch}' or target.get('type') != 'commit':
        raise ProjectError(f'The remote branch {branch} could not be verified.')
    return oid(target.get('sha'))


def has_supplementary_dependency_receipt(root, base, intended, task, previous):
    # Accepted tasks may append receipts while retaining their record and prior evidence.
    prior_evidence = previous['evidence']
    evidence = task['evidence']
    if task != {**previous, 'evidence': evidence} or evidence[:len(prior_evidence)] != prior_evidence:
        return False
    prior_paths = {ref['path'] for ref in prior_evidence}
    definition = task_definition_hash(task)
    for ref in evidence[len(prior_evidence):]:
        path = ref['path']
        if path in prior_paths or not re.fullmatch(rf'project/evidence/{task["id"]}-[0-9a-f]{{16}}\.json', path):
            continue
        data = git(root, 'show', f'{intended}:{path}')
        if digest(data) != ref['sha256']:
            raise ProjectError(f'Evidence digest mismatch at {intended}: {path}')
        receipt = json.loads(data)
        validate_model(root, 'Evidence', receipt)
        source = receipt['source']
        if (receipt['task_id'] != task['id'] or receipt['task_definition_sha256'] != definition
                or source['working_tree_dirty'] or source['git_head'] == base
                or not set(task['checks']).issubset(check['id'] for check in receipt['checks'])):
            continue
        try:
            commit = exact_commit(root, source['git_head'])
            require_ancestor(root, base, commit)
            require_ancestor(root, commit, intended)
        except ScopeError:
            continue
        # Allow the commit that records the receipt. Any other tree change needs its own observation.
        changed = git(root, 'diff', '--no-ext-diff', '--no-renames', '--name-only', '-z', commit, intended, '--',
                      '.', ':(exclude)project/tasks.json', ':(exclude)project/evidence/')
        if not changed and committed_source_identity(root, commit) == source:
            return True
    return False


def dependency_binding(root, base, intended, dependency_id, reviewed_task):
    task = committed_tasks(root, intended).get(dependency_id)
    if not task or dependency_id == reviewed_task['id']:
        raise ProjectError(f'The named dependency {dependency_id} is absent from its base or is the selected task.')
    previous = committed_tasks(root, base).get(dependency_id)
    if task['status'] == 'cancelled' or (previous and previous['status'] == 'cancelled'):
        raise ProjectError(f'Cancelled dependency {dependency_id} cannot be used for dispatch.')
    # Shared ledger and feedback edits cannot by themselves attribute a stack to a task.
    paths = [path for path in task['scope'] if path not in {'project/tasks.json', 'project/feedback.json'}]
    if not paths or not git(root, '--literal-pathspecs', 'diff', '--no-ext-diff', '--no-renames',
                            '--name-only', '-z', base, intended, '--', *paths):
        raise ProjectError(f'Dependency {dependency_id} at {intended} contributes no changes in its recorded scope.')
    if previous and previous['status'] == 'done':
        if has_supplementary_dependency_receipt(root, base, intended, task, previous):
            return 'supplementary_receipt'
        raise ProjectError(f'Completed dependency {dependency_id} at {intended} needs a supplementary receipt '
                           'for that source. Use the task that owns the stack in the reviewed integration order.')
    if previous is None or task_definition_hash(task) != task_definition_hash(previous):
        return 'committed_definition'
    if dependency_id in reviewed_task['depends_on']:
        return 'declared_dependency'
    raise ProjectError(f'Dependency {dependency_id} at {intended} has no distinct task definition in the stack '
                       f'and is not declared by {reviewed_task["id"]}.')


def submit_reviewed_task(root, result, repository):
    task_id, head, base, ref = (result[key] for key in ('task_id', 'head_sha', 'protected_base', 'ref'))
    dispatch_id = uuid4().hex
    result.update(repository=repository, dispatch_id=dispatch_id, run_id=None,
                  workflow_url=f'https://github.com/{repository}/actions/workflows/verify.yml',
                  run_title=f'Verify task {task_id} [{dispatch_id}]',
                  attempted_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  base_observation='authenticated_remote', remote_refs_observed=True)
    try:
        response = command([
            'gh', 'api', '--method', 'POST', f'repos/{repository}/actions/workflows/verify.yml/dispatches',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            '-f', f'ref={ref}', '-F', 'return_run_details=true', '-f', f'inputs[task_id]={task_id}',
            '-f', f'inputs[expected_head]={head}', '-f', f'inputs[expected_base]={base}',
            '-f', f'inputs[dispatch_id]={dispatch_id}',
        ], root)
    except (ProjectError, OSError, subprocess.SubprocessError, UnicodeError, KeyboardInterrupt) as error:
        # A lost response can follow a successful POST. Never retry here.
        result.update(status='submission_unknown', error=str(error) or 'The dispatch request was interrupted.',
                      next_action='Look for run_title at workflow_url before resubmitting. '
                                  'Search regardless of the run head, which may differ after a ref move.')
        return result

    result.update(status='submitted', next_action='Check the run head against head_sha before using any evidence. '
                                                  'If run_id is missing, look for run_title at workflow_url.')
    try:
        details = json.loads(response) if response else None
    except ValueError:
        details = None
    run_id = details.get('workflow_run_id') if isinstance(details, dict) else None
    if type(run_id) is int and run_id > 0:
        result.update(run_id=run_id, run_url=f'https://github.com/{repository}/actions/runs/{run_id}')
    return result


def dispatch_task(root, task_id, *, base, reviewed, branch, dependency=None, submit=False):
    source = source_identity(root)
    if source['working_tree_dirty'] or not source['git_head']:
        raise ProjectError('Use a clean committed checkout for task dispatch.')
    head = source['git_head']
    try:
        require_complete_history(root)
        base, reviewed, head = (exact_commit(root, value) for value in (base, reviewed, head))
        policy = json.loads(git(root, 'show', f'{base}:project/branch-protection.json'))
        protected = policy['branch']
        tracked = resolve_commit(root, f'refs/remotes/origin/{protected}')
    except ScopeError as error:
        raise ProjectError(f'Cannot verify protected base {base} and reviewed source {reviewed} in local Git history: {error}') from error
    if tracked != base:
        raise ProjectError(f'The supplied protected base {base} differs from the locally tracked {protected} tip {tracked}.')
    try:
        git(root, 'check-ref-format', f'refs/heads/{branch}')
    except ScopeError:
        raise ProjectError('Pass an exact branch name with --ref.') from None

    reviewed_task = committed_tasks(root, reviewed).get(task_id)
    candidate_task = load_project(root)['tasks'].get(task_id)
    if not reviewed_task or not candidate_task or task_definition_hash(reviewed_task) != task_definition_hash(candidate_task):
        raise ProjectError(f'Task {task_id} does not have the same definition in the reviewed source and checkout.')
    intended = base
    if dependency:
        dependency_id, revision = dependency
        try:
            intended = exact_commit(root, revision)
            require_ancestor(root, base, intended)
        except ScopeError as error:
            raise ProjectError(f'Dependency {dependency_id} at {revision} is unavailable or unverified against base {base}: {error}') from error
        binding = dependency_binding(root, base, intended, dependency_id, reviewed_task)
    expected = expected_integration_tree(root, intended, reviewed)
    actual = git(root, 'rev-parse', f'{head}^{{tree}}').strip().decode('ascii')
    if actual != expected:
        raise ProjectError(
            f'Checkout {head} does not match reviewed source {reviewed} integrated with the intended base {intended}. '
            f'Use {reviewed} if already based there, or its reviewed integration with that base (expected tree {expected}).'
        )
    try:
        require_ancestor(root, intended, head)
    except ScopeError as error:
        raise ProjectError(f'Cannot verify intended base {intended} in checkout {head}: {error}') from error
    ref = f'refs/heads/{branch}'
    result = {'status': 'ready', 'task_id': task_id, 'head_sha': head, 'reviewed_sha': reviewed,
              'protected_base': base, 'intended_base': intended, 'expected_tree': expected, 'ref': ref,
              'base_observation': 'local_tracking', 'remote_refs_observed': False}
    if dependency:
        result['dependency'] = {'task_id': dependency_id, 'commit': intended, 'binding': binding}
    if submit:
        repository = github_identity(root)
        if repository != policy['repository']:
            raise ProjectError('The authenticated origin differs from the protected repository.')
        observed_base = remote_branch_tip(root, repository, protected)
        if observed_base != base:
            raise ProjectError(f'The remote protected {protected} tip is {observed_base}; reviewed base was {base}.')
        observed_head = remote_branch_tip(root, repository, branch)
        if observed_head != head:
            raise ProjectError(f'The remote branch tip for {ref} is {observed_head}; expected checkout {head}.')
        return submit_reviewed_task(root, result, repository)
    return result


def classify_runs(runs, head, *, dirty=False, selected=False):
    relevant = [run for run in runs if run.get('head_sha') == head and run.get('path') == WORKFLOW]
    latest = max(relevant, key=lambda run: (int(run['id']), int(run.get('run_attempt', 1))), default=None)
    if latest is None:
        return {'status': 'blocked', 'reason': 'No hosted verification exists for this commit.', 'run': None}
    observed = {key: latest.get(key) for key in ['id', 'head_sha', 'head_branch', 'status', 'conclusion',
                                               'run_attempt', 'html_url', 'event', 'created_at']}
    if dirty:
        return {'status': 'blocked', 'reason': 'The worktree has uncommitted changes; CI only covers the recorded commit.', 'run': observed}
    if latest['status'] != 'completed':
        return {'status': 'blocked', 'reason': 'Hosted verification is queued or running.', 'run': observed}
    scope = 'Selected' if selected else 'Latest'
    if latest.get('conclusion') != 'success':
        return {'status': 'failed', 'reason': f'{scope} run for this commit did not succeed.', 'run': observed}
    if selected:
        return {'status': 'blocked', 'reason': 'The selected run succeeded; inspect the latest run before treating hosted verification as current.', 'run': observed}
    return {'status': 'passed', 'reason': 'The latest workflow run for this clean commit succeeded.', 'run': observed}


def checkout_branch(root, protected):
    lines = command(['git', 'status', '--porcelain=v2', '--branch', '--untracked-files=no', '--no-ahead-behind'], root)
    metadata = dict(line[2:].split(' ', 1) for line in lines.splitlines() if line.startswith('# branch.'))
    head = metadata['branch.head']
    upstream = metadata.get('branch.upstream', '')
    if head == '(detached)':
        return None
    # A fresh task branch can track origin/main until its first push. Its
    # verification still comes from its own pull request.
    if head != protected and upstream.startswith('origin/') and upstream != 'origin/' + protected:
        return upstream.removeprefix('origin/')
    return head


def workflow_runs(root, repository, event, *, head=None, branch=None):
    # Bound the search to its purpose at the API. An unrelated busy workflow or
    # hundreds of manual runs on the same commit cannot displace the PR/push.
    endpoint = f'repos/{repository}/actions/workflows/verify.yml/runs'
    args = ['gh', 'api', '--method', 'GET', endpoint, '-f', f'event={event}']
    if branch:
        args.extend(['-f', f'branch={branch}'])
    if head:
        args.extend(['-f', f'head_sha={head}', '-f', 'per_page=100', '--paginate', '--slurp'])
        pages = json.loads(command(args, root))
    else:
        # The last completed weekly run remains visible while another is queued,
        # and while a new commit has only seen change-scoped verification.
        args.extend(['-f', 'status=completed', '-f', 'per_page=1'])
        pages = [json.loads(command(args, root))]
    return [run for page in pages for run in page['workflow_runs']
            if run.get('path') == WORKFLOW and run.get('event') == event
            and (head is None or run.get('head_sha') == head)
            and (branch is None or run.get('head_branch') == branch)
            and (head is not None or run.get('status') == 'completed')
            and (event != 'pull_request' or
                 (run.get('head_repository') or {}).get('full_name', '').casefold() == repository.casefold())]


def integration_status(root, repository, source, branch, protected):
    head = source['git_head']
    if branch in (protected, None):
        runs = workflow_runs(root, repository, 'push', head=head, branch=protected)
        if runs:
            return classify_runs(runs, head, dirty=source['working_tree_dirty'])
        if branch == protected:
            return {'status': 'blocked', 'reason': f'No {protected} push verification exists for this commit.', 'run': None}
    runs = workflow_runs(root, repository, 'pull_request', head=head, branch=branch)
    if not runs:
        return {'status': 'blocked', 'reason': 'No matching pull request verification exists for this commit.', 'run': None}
    if branch is None and len({run['head_branch'] for run in runs}) > 1:
        return {'status': 'blocked', 'reason': 'Multiple PR branches match this detached commit. Inspect a specific PR.', 'run': None}
    return classify_runs(runs, head, dirty=source['working_tree_dirty'])


def independent_status(root, repository, event, branch, head=None):
    try:
        runs = workflow_runs(root, repository, event, head=head, branch=branch)
    except (ProjectError, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as error:
        return {'status': 'unavailable', 'run': None, 'reason': str(error)}
    if not runs:
        return {'status': 'missing', 'run': None}
    observed = classify_runs(runs, head or runs[0]['head_sha'])
    return {'status': observed['status'], 'run': observed['run']}


def weekly_freshness(observation):
    created = (observation.get('run') or {}).get('created_at')
    if not isinstance(created, str):
        return 'unknown'
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(created.replace('Z', '+00:00'))
    except (ValueError, TypeError, OverflowError):
        return 'unknown'
    if age < timedelta(0):
        return 'unknown'
    return 'recent' if age <= timedelta(days=8) else 'overdue'


def status(root=ROOT, run_id=None):
    if run_id is not None and run_id < 1:
        raise ProjectError('Run ID must be positive')
    repository = github_identity(root)
    source = source_identity(root)
    head = source['git_head']
    if not head:
        raise ProjectError('Commit a source checkpoint before requesting hosted verification')
    if run_id:
        run = json.loads(command(['gh', 'api', f'repos/{repository}/actions/runs/{run_id}'], root))
        result = classify_runs([run], head, dirty=source['working_tree_dirty'], selected=True)
    else:
        protected = read_json(root / 'project/branch-protection.json')['branch']
        branch = checkout_branch(root, protected)
        result = integration_status(root, repository, source, branch, protected)
        result['manual_verification'] = independent_status(root, repository, 'workflow_dispatch', branch, head)
        weekly = independent_status(root, repository, 'schedule', protected)
        weekly['freshness'] = weekly_freshness(weekly)
        weekly['requires_review'] = weekly['status'] != 'passed' or weekly['freshness'] != 'recent'
        result['weekly_regression'] = weekly
    result.update(repository=repository, head_sha=source['git_head'],
                  observed_at=datetime.now(timezone.utc).isoformat(),
                  claim_scope='The run covers its reported check selection for this commit. Task and release acceptance have separate evidence.')
    if result['run']:
        if result['run']['event'] == 'workflow_dispatch':
            result['claim_scope'] = ('Workflow status for the selection recorded in this manual run (full, release or task). '
                                     'Task evidence and release acceptance are separate.')
        elif result['run']['event'] == 'schedule':
            result['claim_scope'] = 'Scheduled full change regression on this commit. Integration uses the pull request and main push runs.'
        run = result['run']['id']
        result['evidence_commands'] = [
            f'gh run download {run} --repo {repository} --name verification-windows-latest --dir .reports/ci/{run}/windows',
            f'pnpm project:record <task-id> --report .reports/ci/{run}/windows/test-report.json --artifact-root .reports/ci/{run}/windows',
        ]
    observed = result['run']
    timing_run = observed if (run_id is None and result['status'] in {'passed', 'failed'}
                              and observed['event'] in {'pull_request', 'push'}
                              and str(observed['run_attempt']) == '1') else None
    try:
        result['verification_budget'] = ci_budget_status(root, repository, timing_run)
    except (OSError, subprocess.SubprocessError, AttributeError, KeyError, TypeError, ValueError,
            OverflowError, RecursionError) as error:
        result['verification_budget'] = {'timing_available': False, 'intake': 'unavailable', 'error': str(error)}
    return result


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=int, help='Inspect an explicit run; a different commit is never accepted')
    subcommands = parser.add_subparsers(dest='operation')
    dispatch = subcommands.add_parser('dispatch-task', help='Compare reviewed task source before optional submission')
    dispatch.add_argument('task_id')
    dispatch.add_argument('--base', required=True, help='Expected current protected main commit')
    dispatch.add_argument('--reviewed', required=True, help='Independently reviewed task source commit')
    dispatch.add_argument('--ref', required=True, metavar='BRANCH', help='Exact remote branch name')
    dispatch.add_argument('--dependency', nargs=2, metavar=('TASK_ID', 'COMMIT'), help='Explicit planned dependent base')
    dispatch.add_argument('--submit', action='store_true', help='Verify live refs and submit the existing manual task')
    args = parser.parse_args(argv)
    if args.operation and args.run is not None:
        parser.error('--run is only for workflow inspection')
    try:
        if args.operation == 'dispatch-task':
            result = dispatch_task(root, args.task_id, base=args.base, reviewed=args.reviewed, branch=args.ref,
                                   dependency=args.dependency, submit=args.submit)
        else:
            result = status(root, run_id=args.run)
    except (ProjectError, ScopeError, OSError, subprocess.SubprocessError, KeyError, ValueError) as error:
        result = {'status': 'failed', 'error': str(error)}
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in {'passed', 'ready', 'submitted'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
