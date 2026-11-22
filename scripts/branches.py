"""Review live GitHub branches and open pull requests using local Git ancestry."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import re
import subprocess

if __package__:
    from .ci import ROOT, command, github_identity
    from .project import ProjectError
else:
    from ci import ROOT, command, github_identity
    from project import ProjectError


PAGE_SIZE = 100
SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


def api(root, endpoint, *, paginate=False):
    argv = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET']
    if paginate:
        argv.extend(['--paginate', '--slurp'])
    try:
        return json.loads(command([*argv, endpoint], root))
    except (ProjectError, OSError, subprocess.SubprocessError, ValueError) as error:
        raise ProjectError(f'{endpoint}: {error}') from error


def records(root, endpoint):
    pages = api(root, endpoint, paginate=True)
    if (not isinstance(pages, list) or not pages
            or any(not isinstance(page, list) or len(page) > PAGE_SIZE for page in pages)
            or any(len(page) != PAGE_SIZE for page in pages[:-1])):
        raise ProjectError(f'Invalid paginated response: {endpoint}')
    for page in pages:
        yield from page


def record(value, label):
    if not isinstance(value, dict):
        raise ProjectError(f'Missing or invalid {label}')
    return value


def positive_id(value, label):
    if type(value) is not int or value < 1:
        raise ProjectError(f'Missing or invalid {label}')
    return value


def branch_name(value, label):
    if (not isinstance(value, str) or not value or value == '@' or '..' in value or '@{' in value
            or re.search(r'[\x00-\x20\x7f~^:?*\[\\]', value) or value.endswith('.')
            or any(not part or part.startswith('.') or part.endswith('.lock') for part in value.split('/'))):
        raise ProjectError(f'Missing or invalid branch name for {label}')
    return value


def revision(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value) or not value.strip('0'):
        raise ProjectError(f'Missing or invalid commit SHA for {label}')
    return value


def repository_identity(value, label):
    repo = record(value, f'repository for {label}')
    name = repo.get('full_name')
    if not isinstance(name, str) or not REPOSITORY.fullmatch(name):
        raise ProjectError(f'Missing or invalid repository name for {label}')
    return positive_id(repo.get('id'), f'repository ID for {label}'), name.lower()


def pull_ref(raw, label, repository, branches, *, base=False):
    raw = record(raw, label)
    repo_id, repo_name = repository_identity(raw.get('repo'), label)
    name = branch_name(raw.get('ref'), label)
    sha = revision(raw.get('sha'), label)
    same_repository = repo_id == repository[0]
    if same_repository != (repo_name == repository[1]) or (base and not same_repository):
        raise ProjectError(f'Inconsistent repository identity for {label}')
    # GitHub can retain the PR's base snapshot after the base branch advances.
    live_sha = branches.get(name, {}).get('sha') if same_repository else None
    return {'repository': repo_name, 'repository_id': repo_id, 'name': name, 'sha': sha,
            'live_sha': live_sha, 'same_repository': same_repository}


def snapshot(root, repository):
    endpoint = f'repos/{repository}'
    metadata = record(api(root, endpoint), 'repository metadata')
    identity = repository_identity(metadata, 'the observed repository')
    if identity[1] != repository.lower():
        raise ProjectError('GitHub repository does not match the pinned origin')
    default = branch_name(metadata.get('default_branch'), 'the default branch')
    delete_on_merge = metadata.get('delete_branch_on_merge')
    if type(delete_on_merge) is not bool:
        delete_on_merge = None

    branches = {}
    for raw in records(root, f'{endpoint}/branches?per_page={PAGE_SIZE}'):
        raw = record(raw, 'branch record')
        name = branch_name(raw.get('name'), 'the branch inventory')
        if name in branches:
            raise ProjectError(f'Duplicate branch in paginated inventory: {name}')
        protected = raw.get('protected')
        if type(protected) is not bool:
            raise ProjectError(f'Missing or invalid protection status for {name}')
        sha = revision(record(raw.get('commit'), f'commit for {name}').get('sha'), name)
        branches[name] = {'name': name, 'sha': sha, 'protected': protected}
    if default not in branches:
        raise ProjectError(f'Default branch {default} is missing from the live branch inventory')

    pulls = {}
    for raw in records(root, f'{endpoint}/pulls?state=open&per_page={PAGE_SIZE}'):
        raw = record(raw, 'pull request record')
        number = positive_id(raw.get('number'), 'pull request number')
        if raw.get('state') != 'open' or number in pulls:
            raise ProjectError(f'Unexpected state or duplicate open pull request: #{number}')
        pulls[number] = {
            'number': number,
            'base': pull_ref(raw.get('base'), f'PR #{number} base', identity, branches, base=True),
            'head': pull_ref(raw.get('head'), f'PR #{number} head', identity, branches),
        }
    return identity, default, delete_on_merge, branches, pulls


def local_git(root, *args, input_text=None):
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_GRAFT_FILE=os.devnull)
    return subprocess.run(['git', '--no-replace-objects', '-c', 'core.commitGraph=false', *args],
                          cwd=root, env=env, input=input_text, text=True, capture_output=True, timeout=30)


def local_ancestry(root, tips, default):
    """Compare exact commit objects. An unavailable comparison has value None."""
    ancestry = dict.fromkeys(tips)
    shallow = local_git(root, 'rev-parse', '--is-shallow-repository')
    if shallow.returncode or shallow.stdout.strip() != 'false':
        return ancestry, ['Ancestry requires an available, non-shallow local Git checkout.']

    shas = sorted(tips)
    objects = local_git(root, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                        input_text='\n'.join(shas) + '\n')
    lines = objects.stdout.splitlines()
    if objects.returncode or len(lines) != len(shas):
        return ancestry, ['Unable to verify exact commit objects in the local Git checkout.']

    commits = {sha for sha, line in zip(shas, lines) if line == f'{sha} commit'}
    errors = [f'Exact commit unavailable locally: {sha}' for sha in sorted(tips - commits)]
    if default in commits:
        for sha in sorted(commits):
            result = local_git(root, 'merge-base', '--is-ancestor', sha, default)
            if result.returncode in {0, 1}:
                ancestry[sha] = result.returncode == 0
            else:
                errors.append(f'Unable to compare local commit {sha} with default tip {default}.')
    return ancestry, errors


def classify(default, branches, pulls, ancestry):
    bases = defaultdict(list)
    heads = defaultdict(list)
    errors = []
    for pr in pulls:
        number = pr['number']
        base = pr['base']
        head = pr['head']
        bases[base['name']].append(number)
        if base['live_sha'] is None:
            errors.append(f"PR #{number} base {base['name']} is missing from the live branch inventory.")
        pr['reachable_from_default'] = None
        if not head['same_repository']:
            pr['status'] = 'fork_head'
            continue

        heads[head['name']].append(number)
        if head['live_sha'] != head['sha']:
            live_sha = head['live_sha'] or 'missing'
            errors.append(f"PR #{number} head {head['name']} records {head['sha']}, live tip {live_sha}. Reinspect.")
            pr['status'] = 'uncertain'
            continue

        reachable = ancestry[head['sha']]
        pr['reachable_from_default'] = reachable
        pr['status'] = {True: 'already_integrated', False: 'outside_default_history', None: 'uncertain'}[reachable]

    for branch in branches:
        name = branch['name']
        reachable = ancestry[branch['sha']]
        branch.update(open_pr_bases=bases[name], open_pr_heads=heads[name], reachable_from_default=reachable)
        if name == default:
            branch['status'] = 'default'
        elif branch['protected']:
            branch['status'] = 'protected'
        elif bases[name] or heads[name]:
            branch['status'] = 'in_use'
        else:
            branch['status'] = {True: 'reachable_unreferenced', False: 'unique_history', None: 'uncertain'}[reachable]
    return errors


def inspect(root=ROOT):
    report = {
        'status': 'blocked',
        'repository': None,
        'default_branch': None,
        'delete_branch_on_merge': None,
        'branches': [],
        'open_pull_requests': [],
        'claim_scope': ('Point-in-time review. PR ref sha is the recorded revision and live_sha is the current '
                        'same-repository branch tip. Ancestry is relative to the observed default tip. '
                        'Reinspect before any cleanup.'),
    }
    try:
        repository = github_identity(root)
        report['repository'] = repository
        first = snapshot(root, repository)
        identity, default, delete_on_merge, branches, pulls = first
        default_sha = branches[default]['sha']
        ancestry, errors = local_ancestry(root, {item['sha'] for item in branches.values()}, default_sha)
        if snapshot(root, repository) != first:
            raise ProjectError('Repository metadata, branches or open pull requests changed during inspection. Reinspect.')

        branch_rows = [branches[name] for name in sorted(branches)]
        pull_rows = [pulls[number] for number in sorted(pulls)]
        errors.extend(classify(default, branch_rows, pull_rows, ancestry))
        report.update(status='passed' if not errors and delete_on_merge is True else 'blocked',
                      repository=identity[1], repository_id=identity[0],
                      default_branch={'name': default, 'sha': default_sha}, delete_branch_on_merge=delete_on_merge,
                      branches=branch_rows,
                      open_pull_requests=pull_rows, errors=errors,
                      summary={'branches': dict(Counter(row['status'] for row in branch_rows)),
                               'open_pull_requests': dict(Counter(row['status'] for row in pull_rows))})
    except (ProjectError, OSError, subprocess.SubprocessError, ValueError) as error:
        report['errors'] = [str(error)]

    setting = report['delete_branch_on_merge']
    report['findings'] = []
    if setting is False:
        report['findings'].append({
            'code': 'delete_branch_on_merge_disabled',
            'action': ('Enable Automatically delete head branches in GitHub Settings > General > Pull Requests, '
                       'then rerun project:branches.'),
        })
    elif setting is None:
        report['findings'].append({
            'code': 'delete_branch_on_merge_unknown',
            'action': ('Resolve any observation errors, confirm the setting in GitHub Settings > General > '
                       'Pull Requests and rerun project:branches with the pinned account.'),
        })
    report['observed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = inspect()
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
