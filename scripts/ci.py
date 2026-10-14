"""Inspect hosted verification for this repository's exact commit using official gh."""

import argparse
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .project import ProjectError, source_identity
else:
    from project import ProjectError, source_identity

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/verify.yml'


def command(argv, root=ROOT):
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=30)
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
                            cwd=root, capture_output=True, text=True, check=False)
    if marker.returncode or not marker.stdout.strip():
        raise ProjectError('Set the owner-approved local github.account marker before authenticated operations')
    account = marker.stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', account):
        raise ProjectError('Invalid local GitHub account marker')
    command(['gh', 'auth', 'switch', '--hostname', 'github.com', '--user', account], root)
    if command(['gh', 'api', 'user', '--jq', '.login'], root).lower() != account.lower():
        raise ProjectError('The active GitHub account does not match the repository marker')
    return f'{match[1]}/{match[2]}'


def classify_runs(runs, head, *, dirty=False, selected=False):
    relevant = [run for run in runs if run.get('head_sha') == head and run.get('path') == WORKFLOW]
    latest = max(relevant, key=lambda run: (int(run['id']), int(run.get('run_attempt', 1))), default=None)
    if latest is None:
        return {'status': 'blocked', 'reason': 'No hosted verification exists for this commit.', 'run': None}
    observed = {key: latest.get(key) for key in ['id', 'head_sha', 'status', 'conclusion', 'run_attempt', 'html_url', 'event']}
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


def status(root=ROOT, run_id=None):
    if run_id is not None and run_id < 1:
        raise ProjectError('Run ID must be positive')
    repository = github_identity(root)
    source = source_identity(root)
    if not source['git_head']:
        raise ProjectError('Commit a source checkpoint before requesting hosted verification')
    if run_id:
        runs = [json.loads(command(['gh', 'api', f'repos/{repository}/actions/runs/{run_id}'], root))]
    else:
        response = json.loads(command(['gh', 'api', '--method', 'GET',
                                       f'repos/{repository}/actions/runs',
                                       '-f', f"head_sha={source['git_head']}", '-f', 'per_page=100'], root))
        runs = response['workflow_runs']
    result = classify_runs(runs, source['git_head'], dirty=source['working_tree_dirty'], selected=run_id is not None)
    result.update(repository=repository, head_sha=source['git_head'],
                  claim_scope='Workflow success for this commit; task evidence and release acceptance are separate.')
    if result['run']:
        run = result['run']['id']
        result['evidence_commands'] = [
            f'gh run download {run} --repo {repository} --name verification-windows-latest --dir .reports/ci/{run}/windows',
            f'pnpm project:record <task-id> --report .reports/ci/{run}/windows/test-report.json --artifact-root .reports/ci/{run}/windows',
        ]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=int, help='Inspect an explicit run; a different commit is never accepted')
    args = parser.parse_args()
    try:
        result = status(run_id=args.run)
    except (ProjectError, OSError, subprocess.SubprocessError, KeyError, ValueError) as error:
        result = {'status': 'failed', 'error': str(error)}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
