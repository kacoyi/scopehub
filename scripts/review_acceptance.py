"""Qualify independent review at task acceptance using local Git and retained decisions."""

from datetime import datetime
import hashlib
import json
import re

if __package__:
    from .verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor
else:
    from verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor


DIRECTORY = 'contracts/reviews/task-decisions'
TASK_ID = re.compile(r'M[0-9]+-[0-9]{3}')
SUBJECT_TASK = re.compile(r'(?<![\w-])M[0-9]+-[0-9]{3}(?![\w-])')
BOUNDARY_TYPES = {'permissions', 'model-connection'}
DECISION = re.compile(r'^(?:• )?(APPROVED?|CHANGES[_ ]REQUESTED)\b')
COMMIT = re.compile(r'(?<![a-f0-9])(?:[a-f0-9]{64}|[a-f0-9]{40})(?![a-f0-9])')
TRANSCRIPT_LIMIT = 4 * 1024 * 1024
TASK_ADMIN = {'status', 'evidence', 'next_action', 'blocker', 'closure_reason'}
FEEDBACK_ADMIN = {'status', 'resolution', 'deferral_reason'}


class ReviewError(ValueError):
    """A consequential acceptance or its independent decision is unsupported."""


def heading_subject(heading):
    marker = DECISION.match(heading)
    source = COMMIT.search(heading)
    if not marker or not source:
        return None, None, []
    verdict = 'approved' if marker[1].startswith('APPROVE') else 'changes_requested'
    return verdict, source[0], SUBJECT_TASK.findall(heading[marker.end():source.start()])


def bound_decision(heading, task, source, scope):
    verdict, exact_source, subjects = heading_subject(heading)
    if exact_source != source:
        return None
    if subjects == [task]:
        return verdict
    # Some retained terminal rejections predate task-qualified headings. A scope
    # excerpt from their final response can retain the blocker without granting approval.
    if not subjects and verdict == 'changes_requested' and task in SUBJECT_TASK.findall(scope):
        return verdict
    return None


def compact(text):
    return ' '.join(text.split())


def response_identity(event, response_hash):
    return hashlib.sha256(event['id'].encode('utf-8')).hexdigest() if event else response_hash


def evidence_digest(task, value):
    fields = {key: item for key, item in value.items() if key != 'evidence_sha256'}
    data = json.dumps([task, fields], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def event_time(value):
    if value['event'] is None:
        return None
    stamp = datetime.fromisoformat(value['event']['timestamp'])
    if stamp.utcoffset() is None:
        raise ReviewError('Independent response timestamps require an offset.')
    return stamp


def validate_successor(previous, decision):
    expected = previous['response_id'] if previous else None
    if decision['supersedes'] != expected:
        raise ReviewError(f'Independent response must supersede {expected or "no previous response"}.')
    new_time = event_time(decision)
    if previous:
        old_time = event_time(previous)
        if old_time and new_time and new_time <= old_time:
            raise ReviewError('Independent response predates or equals the retained decision.')
        if (decision['source_commit'] == previous['source_commit']
                and previous['verdict'] == 'changes_requested' and decision['verdict'] == 'approved'
                and not (old_time and new_time)):
            raise ReviewError('Replacing a same-source rejection requires ordered final assistant exports.')


def repeated_response(decisions, candidate):
    for earlier in decisions:
        if earlier['response_id'] == candidate['response_id']:
            if earlier['response_sha256'] != candidate['response_sha256']:
                raise ReviewError('The retained assistant response identity has different content.')
            return earlier
        if (earlier['response_sha256'] == candidate['response_sha256']
                and (earlier['event'] is None or candidate['event'] is None)):
            return earlier
    return None


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
        verified = []
        for value in record['decisions']:
            if evidence_digest(path.stem, value) != value['evidence_sha256']:
                raise ReviewError(f'Independent public evidence digest mismatch for {path.stem}.')
            if (response_identity(value['event'], value['response_sha256']) != value['response_id']
                    or bound_decision(value['decision'], path.stem, value['source_commit'], value['scope']) != value['verdict']
                    or value['observation'].strip() in {'', value['scope'].strip(), value['decision'].strip()}
                    or not value['reviewer'].strip()):
                raise ReviewError(f'Unusable independent decision for {path.stem}.')
            if repeated_response(verified, value):
                raise ReviewError(f'The same independent response was recorded twice for {path.stem}.')
            validate_successor(verified[-1] if verified else None, value)
            verified.append(value)
        records[path.stem] = verified
    return records


def read_at(root, commit, path):
    return json.loads(git(root, 'show', f'{commit}:{path}'))


def prior_records(root, commit, task):
    path = f'{DIRECTORY}/{task}.json'
    if git(root, 'ls-tree', '--name-only', commit, '--', path):
        return read_at(root, commit, path)['decisions']
    return []


def final_response(text):
    if text.lstrip().startswith('{'):
        try:
            event = json.loads(text)
            message = event['payload']
            if (event['type'] != 'response_item' or message['type'] != 'message'
                    or message['role'] != 'assistant' or message['phase'] != 'final_answer'
                    or message['status'] != 'completed'
                    or event.get('metadata', {}).get('client_authored') is True):
                raise ValueError('Expected a completed final assistant message.')
            parts = message['content']
            if not parts or any(part['type'] != 'output_text' for part in parts):
                raise ValueError('Expected final assistant text.')
            identity = message['id']
            if not isinstance(identity, str) or not identity.startswith('msg_'):
                raise ValueError('Expected a stable final assistant message identity.')
            return '\n'.join(part['text'] for part in parts), {'id': identity, 'timestamp': event['timestamp']}
        except (KeyError, TypeError, ValueError) as error:
            raise ReviewError('Expected one retained completed final assistant export.') from error

    lines = text.splitlines()
    if not any(line.startswith(('› ', '• ')) for line in lines):
        return text, None
    turns = [index for index, line in enumerate(lines)
             if line.startswith('› ') and line.strip() != '› Ask Codex to do anything']
    start = turns[-1] + 1 if turns else 0
    final = next((index for index in range(len(lines) - 1, start - 1, -1) if lines[index].startswith('• ')), None)
    if final is None:
        raise ReviewError('The last terminal exchange has no final independent verdict.')
    end = next((index for index in range(final + 1, len(lines))
                if lines[index].startswith(('› ', '  Worked for '))), len(lines))
    return '\n'.join([lines[final].removeprefix('• '), *lines[final + 1:end]]), None


def create_decision(root, task, source, transcript, reviewer, scope, observation, supersedes=None):
    source = exact_commit(root, source)
    with transcript.open('rb') as stream:
        data = stream.read(TRANSCRIPT_LIMIT + 1)
    if len(data) > TRANSCRIPT_LIMIT:
        raise ReviewError('The independent response exceeds the intake limit.')
    response, event = final_response(data.decode('utf-8'))
    lines = response.strip().splitlines()
    # Only the first paragraph can state the reviewed subject. Terminal wrapping
    # may move the full SHA onto a continuation line.
    heading = lines[0].strip() if lines else ''
    for line in lines[1:4]:
        if COMMIT.search(heading) or not line.startswith('  ') or not line.strip() or line.lstrip().startswith('•'):
            break
        heading = compact(heading + ' ' + line)
    scope = compact(scope) if scope else heading
    observation = compact(observation)
    whole = compact(response)
    verdict = bound_decision(heading, task, source, scope)
    if not verdict:
        raise ReviewError('The final independent verdict must identify this task and exact full source in its heading.')
    if scope not in whole or observation not in whole or observation in {'', scope, heading}:
        raise ReviewError('Select a distinct observation and any scope excerpt from the final independent response.')
    response_hash = hashlib.sha256(whole.encode('utf-8')).hexdigest()
    identity = response_identity(event, response_hash)
    decision = dict(source_commit=source, verdict=verdict, decision=heading, scope=scope,
                    observation=observation, reviewer=reviewer, original_sha256=hashlib.sha256(data).hexdigest(),
                    response_id=identity, response_sha256=response_hash, event=event, supersedes=supersedes)
    decision['evidence_sha256'] = evidence_digest(task, decision)
    return decision


def append_decision(root, decisions, decision):
    if previous := repeated_response(decisions, decision):
        return previous, False
    latest = decisions[-1] if decisions else None
    validate_successor(latest, decision)
    if (latest and latest['source_commit'] != decision['source_commit']
            and not (event_time(latest) and event_time(decision))):
        try:
            require_ancestor(root, latest['source_commit'], decision['source_commit'])
        except ScopeError as error:
            raise ReviewError('A rendered response can only supersede a decision on an ancestor source.') from error
    return decision, True


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


def review_view(key, status, finding, decisions):
    result = {'status': status, 'feedback_ids': sorted(finding), 'reviewed_source': None}
    if decisions:
        decision = decisions[-1]
        result.update(reviewed_source=decision['source_commit'], last_verdict=decision['verdict'],
                      reviewer=decision['reviewer'], observation=decision['observation'],
                      response_id=decision['response_id'],
                      source={'path': f'{DIRECTORY}/{key}.json', 'pointer': f'/decisions/{len(decisions) - 1}',
                              'evidence_sha256': decision['evidence_sha256']})
    else:
        result['reviewer'] = 'Independent review role assigned by the coordinator'
    if status == 'unavailable':
        result['review_next_action'] = 'Restore the local protected comparison history or the named review source, then recheck.'
    elif status in {'pending', 'stale', 'changes_requested'}:
        result['review_next_action'] = 'Keep the task unfinished and obtain an independent final decision on the clean source.'
    return result


def assess(root, project, records, head, bases, *, accepted, protected, trusted, enforce):
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
        finding = qualifying_items(items, key, (row for _, feedback in previous for row in feedback)) if automatic else {}
        plans = (record.get('plan', {}) for record in (task, *(old for old, _ in previous)))
        selected = any(plan.get('independent_review') or BOUNDARY_TYPES.intersection(plan.get('change_types', []))
                       for plan in plans)
        decisions = records.get(key, [])
        if not (selected or finding or decisions):
            continue
        original = accepted.get(key)
        if task['status'] == 'done' and original and original['status'] == 'done':
            same = dict(task, evidence=original['evidence']) == original
            prefix = task['evidence'][:len(original['evidence'])] == original['evidence']
            if same and prefix:
                if decisions:
                    states[key] = review_view(key, 'accepted_history', finding, decisions)
                continue
        if not trusted and task['status'] == 'done':
            state = 'unavailable'
        else:
            state = disposition(root, task, finding if automatic else None,
                                decisions[-1] if decisions else None, head, protected)
        states[key] = review_view(key, state, finding, decisions)
        if enforce and task['status'] == 'done' and state != 'approved':
            raise ReviewError(f'Independent review for {key} is {state}. '
                              + states[key]['review_next_action'] + ' Use pnpm project:review for decision intake.')
    return states
