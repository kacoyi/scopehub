"""Qualify independent review at task acceptance using local Git and retained decisions."""

import hashlib
import json
import re

if __package__:
    from .verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor
else:
    from verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor


DIRECTORY = 'contracts/reviews/task-decisions'
TASK_ID = re.compile(r'M[0-9]+-[0-9]{3}')
DECISION = re.compile(r'^(?:• )?(APPROVED?|CHANGES[_ ]REQUESTED)\b')
COMMIT = re.compile(r'(?<![a-f0-9])(?:[a-f0-9]{64}|[a-f0-9]{40})(?![a-f0-9])')
TRANSCRIPT_LIMIT = 4 * 1024 * 1024
TASK_ADMIN = {'status', 'evidence', 'next_action', 'blocker', 'closure_reason'}
FEEDBACK_ADMIN = {'status', 'resolution', 'deferral_reason'}


class ReviewError(ValueError):
    """A consequential acceptance or its independent decision is unsupported."""


def task_decision(line, source):
    match = DECISION.match(line)
    subject = COMMIT.search(line)
    if not match or not subject or subject[0] != source:
        return None
    return 'approved' if match[1].startswith('APPROVE') else 'changes_requested'


def names_task(text, task):
    return re.search(rf'(?<![\w-]){task}(?![\w-])', text) is not None


def load_records(root, validate):
    directory = root / DIRECTORY
    if not directory.exists():
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise ReviewError('Expected an ordinary independent review decisions directory.')
    records = {}
    for path in sorted(directory.glob('*.json')):
        if not TASK_ID.fullmatch(path.stem) or path.is_symlink() or not path.is_file():
            raise ReviewError(f'Invalid independent review record path: {path.name}')
        try:
            record = json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            raise ReviewError(f'Cannot read independent review for {path.stem}.') from error
        validate('TaskReviewDecisions', record)
        if record['task_id'] != path.stem:
            raise ReviewError(f'Independent review file belongs to another task: {path.name}')
        for value in record['decisions']:
            if (task_decision(value['decision'], value['source_commit']) != value['verdict']
                    or not names_task(value['scope'], path.stem)
                    or value['observation'].strip() in {'', value['scope'].strip(), value['decision'].strip()}
                    or not value['reviewer'].strip()):
                raise ReviewError(f'Unusable independent decision for {path.stem}.')
        identities = {(entry['source_commit'], entry['original_sha256']) for entry in record['decisions']}
        if len(identities) != len(record['decisions']):
            raise ReviewError(f'The same original independent decision was recorded twice for {path.stem}.')
        records[path.stem] = record['decisions']
    return records


def read_at(root, commit, path):
    return json.loads(git(root, 'show', f'{commit}:{path}'))


def prior_records(root, commit, task):
    path = f'{DIRECTORY}/{task}.json'
    if git(root, 'ls-tree', '--name-only', commit, '--', path):
        return read_at(root, commit, path)['decisions']
    return []


def create_decision(root, task, source, transcript, reviewer, scope, observation):
    source = exact_commit(root, source)
    with transcript.open('rb') as stream:
        data = stream.read(TRANSCRIPT_LIMIT + 1)
    if len(data) > TRANSCRIPT_LIMIT:
        raise ReviewError('The independent transcript exceeds the intake limit.')
    lines = data.decode('utf-8').splitlines()
    herdr = any(line.startswith(('› ', '• ')) for line in lines)
    final = next((index for index in range(len(lines) - 1, -1, -1)
                  if (not herdr or lines[index].startswith('• ')) and DECISION.match(lines[index])), None)
    verdict = task_decision(lines[final], source) if final is not None else None
    if not verdict:
        raise ReviewError('The final independent verdict must name the exact full reviewed source.')
    end = next((index for index in range(final + 1, len(lines))
                if lines[index].startswith(('› ', '  Worked for '))), len(lines))
    response = '\n'.join(lines[final:end])
    decision = lines[final].strip()
    scope = scope or decision
    if not names_task(scope, task) or scope not in response:
        raise ReviewError('Select an excerpt from the final independent response identifying the reviewed task.')
    if observation not in response or observation.strip() in {'', scope.strip(), decision.strip()}:
        raise ReviewError('Select a distinct observation verbatim from the final independent response.')
    return dict(source_commit=source, verdict=verdict, decision=decision, scope=scope,
                observation=observation, reviewer=reviewer, original_sha256=hashlib.sha256(data).hexdigest())


def qualifying_items(items, key, previous=()):
    earlier = {row['id'] for row in previous if row['severity'] in {'major', 'critical'} and row['status'] != 'deferred'}
    return {row['id']: {field: value for field, value in row.items() if field not in FEEDBACK_ADMIN}
            for row in items if key in row['task_ids']
            and (row['id'] in earlier or row['severity'] in {'major', 'critical'} and row['status'] != 'deferred')}


def material(path):
    return (path not in {b'project/tasks.json', b'project/feedback.json'}
            and not EVIDENCE_FILE.fullmatch(path)
            and not TASK_REVIEW_FILE.fullmatch(path))


def source_matches(root, reviewed, head, protected):
    require_ancestor(root, reviewed, head)
    untracked = inspect_index(root, include_untracked=True)
    if any(material(path) for path in untracked):
        return False

    def delta(*revisions):
        return {row.path for row in changes(root, *revisions) if material(row.path)}

    difference = delta(reviewed) | delta('--cached', reviewed)
    if not difference:
        return True
    if not protected or protected == head:
        return False
    require_ancestor(root, protected, head)
    common = git(root, 'merge-base', '--all', protected, reviewed).decode('ascii').splitlines()
    if len(common) != 1:
        return False
    reviewed_paths = delta(common[0], reviewed)
    protected_only = delta(common[0], protected) - reviewed_paths
    return difference <= protected_only and not difference & (delta(protected) | delta('--cached', protected))


def disposition(root, task, feedback, decision, head, protected):
    key = task['id']
    if not decision:
        return 'pending'
    try:
        reviewed = exact_commit(root, decision['source_commit'])
        earlier = read_at(root, reviewed, 'project/tasks.json')['tasks']
        original = next((item for item in earlier if item['id'] == key), None)
        if original is None or ({k: v for k, v in original.items() if k not in TASK_ADMIN}
                                != {k: v for k, v in task.items() if k not in TASK_ADMIN}):
            return 'stale'
        observed = read_at(root, reviewed, 'project/feedback.json')['items']
        if feedback is not None and qualifying_items(observed, key, observed) != feedback:
            return 'stale'
        return decision['verdict'] if source_matches(root, reviewed, head, protected) else 'stale'
    except (ScopeError, OSError, ValueError, KeyError, TypeError):
        return 'unavailable'


def assess(root, project, records, head, bases, *, accepted, protected, enforce):
    """Check only new acceptances and expose pending work through the same rules."""
    baselines = []
    for base, tasks, feedback in bases:
        baselines.append((base, tasks, feedback))
        receipts = git(root, 'ls-tree', '-r', '--name-only', base, '--', DIRECTORY + '/').decode('utf-8').splitlines()
        for path in receipts:
            key = path.rsplit('/', 1)[-1].removesuffix('.json')
            if not TASK_ID.fullmatch(key):
                continue
            original = prior_records(root, base, key)
            if records.get(key, [])[:len(original)] != original:
                raise ReviewError(f'Independent decision history changed or disappeared for {key}.')

    # Feedback qualification belongs to the existing development review policy.
    # Small consumers without that policy can still explicitly select a task review.
    automatic = bool(project.get('development', {}).get('quality', {}).get('review')) or any(
        git(root, 'ls-tree', '--name-only', base, '--', 'project/development.json') for base, _, _ in baselines
    )
    items = project['feedback']['items']
    states = {}
    for key, task in project['tasks'].items():
        if task['status'] == 'cancelled':
            continue
        previous = [(tasks.get(key, {}), feedback) for _, tasks, feedback in baselines]
        if task['status'] == 'done':
            original = accepted.get(key)
            if original and original['status'] == 'done':
                same = dict(task, evidence=original['evidence']) == original
                prefix = task['evidence'][:len(original['evidence'])] == original['evidence']
                if same and prefix:
                    continue
            if previous and all(old.get('status') == 'done' for old, _ in previous):
                continue
        finding = qualifying_items(items, key, (row for _, feedback in previous for row in feedback)) if automatic else {}
        selected = (task.get('plan', {}).get('independent_review', False)
                    or any(old.get('plan', {}).get('independent_review', False) for old, _ in previous))
        if not (selected or finding):
            continue
        decisions = records.get(key, [])
        decision = decisions[-1] if decisions else None
        state = disposition(root, task, finding if automatic else None, decision, head, protected)
        states[key] = {'status': state, 'feedback_ids': sorted(finding),
                       'reviewed_source': decision['source_commit'] if decision else None}
        if decision:
            states[key]['last_verdict'] = decision['verdict']
        if enforce and task['status'] == 'done' and state != 'approved':
            raise ReviewError(f'Independent review for {key} is {state}. Keep the task unfinished until a matching '
                              'approval is recorded with pnpm project:review.')
    return states
