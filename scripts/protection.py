"""Read effective GitHub branch protection; never change server settings."""

import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

if __package__:
    from .ci import command, github_identity
    from .project import ProjectError, read_json
else:
    from ci import command, github_identity
    from project import ProjectError, read_json

ROOT = Path(__file__).resolve().parents[1]


def violations(actual, expected):
    errors = []
    status = actual.get('required_status_checks') or {}
    if status.get('strict') is not True:
        errors.append('The branch must be current with its base before integration.')
    observed = {(item.get('context'), item.get('app_id')) for item in status.get('checks', [])}
    for item in expected['required_status_checks']['checks']:
        if (item['context'], item['app_id']) not in observed:
            errors.append(f"Missing required check or trusted App binding: {item['context']}")
    for key, enabled in [('enforce_admins', True), ('allow_force_pushes', False),
                         ('allow_deletions', False), ('required_conversation_resolution', True)]:
        if (actual.get(key) or {}).get('enabled') is not enabled:
            errors.append(f'Unexpected enforcement for {key}.')
    reviews = actual.get('required_pull_request_reviews')
    if not isinstance(reviews, dict):
        errors.append('A pull request is required before integration.')
    else:
        if reviews.get('dismiss_stale_reviews') is not True:
            errors.append('Stale approvals must be dismissed.')
        if reviews.get('required_approving_review_count', -1) < expected['required_pull_request_reviews']['required_approving_review_count']:
            errors.append('The minimum review count is not enforced.')
        if any((reviews.get('bypass_pull_request_allowances') or {}).values()):
            errors.append('Pull-request bypass allowances are present.')
    return errors


def inspect(root=ROOT):
    policy = read_json(root / 'project/branch-protection.json')
    repository = github_identity(root)
    if repository != policy['repository']:
        raise ProjectError('The protection policy belongs to another repository')
    endpoint = f"repos/{repository}/branches/{policy['branch']}/protection"
    actual = json.loads(command(['gh', 'api', endpoint], root))
    errors = violations(actual, policy['configuration'])
    return {'status': 'failed' if errors else 'passed', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'repository': repository, 'branch': policy['branch'], 'errors': errors, 'observed': actual,
            'claim_scope': 'Live read of branch protection at the recorded time, not a merge authorization or permanent guarantee.'}


if __name__ == '__main__':
    try:
        result = inspect()
    except (ProjectError, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        result = {'status': 'failed', 'error': str(error)}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
