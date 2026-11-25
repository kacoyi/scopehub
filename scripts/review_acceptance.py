"""Qualify independent review at task acceptance using local Git and retained decisions."""

from datetime import datetime, timezone
import hashlib
import json
import re

if __package__:
    from .review_observation import markdown_source, response_body, standalone_passage
    from .verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor
else:
    from review_observation import markdown_source, response_body, standalone_passage
    from verification_scope import EVIDENCE_FILE, TASK_REVIEW_FILE, ScopeError, changes, exact_commit, git, inspect_index, require_ancestor


DIRECTORY = 'contracts/reviews/task-decisions'
TASK_ID = re.compile(r'M[0-9]+-[0-9]{3}')
SUBJECT_TASK = re.compile(r'(?<![\w-])M[0-9]+-[0-9]{3}(?![\w-])')
BOUNDARY_TYPES = {'permissions', 'model-connection'}
DECISION = re.compile(r'^(?:• )?(\*\*(?:APPROVED?|CHANGES[_ ]REQUESTED)\*\*|(?:APPROVED?|CHANGES[_ ]REQUESTED)\b)')
COMMIT = re.compile(r'(?<![a-f0-9])(?:[a-f0-9]{64}|[a-f0-9]{40})(?![a-f0-9])')
TRANSCRIPT_LIMIT = 4 * 1024 * 1024
OBSERVATION_FILE_LIMIT = 4096
TASK_ADMIN = {'status', 'evidence', 'next_action', 'blocker', 'closure_reason'}
FEEDBACK_ADMIN = {'status', 'resolution', 'deferral_reason'}


class ReviewError(ValueError):
    """A consequential acceptance or its independent decision is unsupported."""


def heading_subject(heading):
    marker = DECISION.match(heading)
    source = COMMIT.search(heading)
    if not marker or not source:
        return None, None, []
    verdict = 'approved' if marker[1].strip('*').startswith('APPROVE') else 'changes_requested'
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


def entry_id(entry):
    return entry['request_id'] if entry['kind'] == 'request' else entry['response_id']


def event_time(value):
    time = value['requested_at'] if value['kind'] == 'request' else (value['event'] or {}).get('timestamp')
    if time is None:
        return None
    stamp = datetime.fromisoformat(time)
    if stamp.utcoffset() is None:
        raise ReviewError('Independent review timestamps require an offset.')
    return stamp


def validate_successor(previous, entry):
    expected = entry_id(previous) if previous else None
    if entry['supersedes'] != expected:
        raise ReviewError(f'Independent review entry must supersede {expected or "no previous entry"}.')
    new_time = event_time(entry)
    if previous is None:
        return
    old_time = event_time(previous)
    if old_time and new_time and new_time <= old_time:
        raise ReviewError('Independent review entry predates or equals the retained entry.')
    same_source = entry['source_commit'] == previous['source_commit']
    if previous['kind'] == 'request':
        if entry['kind'] == 'request':
            if same_source:
                raise ReviewError('An independent review request is already pending on this source.')
        elif not same_source or new_time is None:
            raise ReviewError('A pending request requires a later final assistant export on its exact source.')
    elif (entry['kind'] == 'decision' and same_source and previous['verdict'] == 'changes_requested'
          and entry['verdict'] == 'approved' and not (old_time and new_time)):
        raise ReviewError('Replacing a same-source rejection requires ordered final assistant exports.')


def response_content_match(earlier, candidate):
    # Old entries have only the folded fingerprint. A collision with one
    # cannot establish new content, and its original receipt stays immutable.
    if 'legacy_response_sha256' in earlier and 'legacy_response_sha256' in candidate:
        return earlier['response_sha256'] == candidate['response_sha256'], True
    return (earlier.get('legacy_response_sha256', earlier['response_sha256'])
            == candidate.get('legacy_response_sha256', candidate['response_sha256'])), False


def repeated_response(decisions, candidate):
    for earlier in decisions:
        if earlier['kind'] != 'decision':
            continue
        if earlier['original_sha256'] == candidate['original_sha256']:
            return earlier
        same_content, exact = response_content_match(earlier, candidate)
        if earlier['response_id'] == candidate['response_id']:
            if not same_content:
                raise ReviewError('The retained assistant response identity has different content.')
            return earlier
        # A proved content replay stays inert even if exported in a new
        # envelope. With only folded hashes, two structured messages still
        # have independently comparable stable event identities.
        if same_content and (exact or earlier['event'] is None or candidate['event'] is None):
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
        for value in record['events']:
            if evidence_digest(path.stem, value) != value['evidence_sha256']:
                raise ReviewError(f'Independent public evidence digest mismatch for {path.stem}.')
            if value['kind'] == 'request':
                if (request_identity(path.stem, value) != value['request_id']
                        or not value['scope'].strip()):
                    raise ReviewError(f'Unusable independent review request for {path.stem}.')
                repeated = any(entry_id(earlier) == value['request_id'] for earlier in verified)
            else:
                if (response_identity(value['event'], value['response_sha256']) != value['response_id']
                        or bound_decision(value['decision'], path.stem, value['source_commit'], value['scope'])
                        != value['verdict']
                        or not observed_statement(value['observation'], value['decision'], value['scope'])
                        or not value['reviewer'].strip()):
                    raise ReviewError(f'Unusable independent decision or observation for {path.stem}.')
                repeated = repeated_response(verified, value)
            if repeated:
                raise ReviewError(f'The same independent review entry was recorded twice for {path.stem}.')
            validate_successor(verified[-1] if verified else None, value)
            verified.append(value)
        records[path.stem] = verified
    return records


def read_at(root, commit, path):
    return json.loads(git(root, 'show', f'{commit}:{path}'))


def prior_records(root, commit, task):
    path = f'{DIRECTORY}/{task}.json'
    if git(root, 'ls-tree', '--name-only', commit, '--', path):
        return read_at(root, commit, path)['events']
    return []


def final_response(text):
    if text.lstrip().startswith('{'):
        try:
            event = json.loads(text)
            message = event['payload']
            metadata = event.get('metadata', {})
            if (not isinstance(metadata, dict) or event['type'] != 'response_item' or message['type'] != 'message'
                    or message['role'] != 'assistant' or message['phase'] != 'final_answer'
                    or message['status'] != 'completed'
                    or metadata.get('client_authored') is True):
                raise ValueError('Expected a completed final assistant message.')
            parts = message['content']
            if not parts or any(part['type'] != 'output_text' for part in parts):
                raise ValueError('Expected final assistant text.')
            identity = message['id']
            if not isinstance(identity, str) or not identity.startswith('msg_'):
                raise ValueError('Expected a stable final assistant message identity.')
            return '\n'.join(part['text'] for part in parts), {'id': identity, 'timestamp': event['timestamp']}, False
        except (KeyError, TypeError, ValueError) as error:
            raise ReviewError('Expected one retained completed final assistant export.') from error

    lines = markdown_source(text).split('\n')
    if not any(line.startswith(('› ', '• ')) for line in lines):
        return text, None, False
    turns = [index for index, line in enumerate(lines)
             if line.startswith('› ') and line.strip() != '› Ask Codex to do anything']
    start = turns[-1] + 1 if turns else 0
    final = next((index for index in range(len(lines) - 1, start - 1, -1) if lines[index].startswith('• ')), None)
    if final is None:
        raise ReviewError('The last terminal exchange has no final independent verdict.')
    end = next((index for index in range(final + 1, len(lines))
                if lines[index].startswith(('› ', '  Worked for '))), len(lines))
    return '\n'.join([lines[final].removeprefix('• '), *lines[final + 1:end]]), None, True


def selected_observation(text, heading, scope):
    passage = standalone_passage(text)
    if passage is None or passage == scope:
        return None
    plain = passage.strip(' `*_#>•.!?。！？')
    if plain.casefold() in heading.casefold() or COMMIT.fullmatch(plain) or SUBJECT_TASK.fullmatch(plain):
        return None
    return passage


def observed_statement(text, heading, scope):
    return text == selected_observation(text, heading, scope)


def read_observation_file(path):
    with path.open('rb') as stream:
        data = stream.read(OBSERVATION_FILE_LIMIT + 1)
    if len(data) > OBSERVATION_FILE_LIMIT:
        raise ReviewError('The observation file exceeds the one-sentence input limit.')
    try:
        return data.decode('utf-8-sig')
    except UnicodeError as error:
        raise ReviewError('Save the observation file as UTF-8.') from error


def create_decision(root, task, source, transcript, reviewer, scope, observation, supersedes=None):
    source = exact_commit(root, source)
    with transcript.open('rb') as stream:
        data = stream.read(TRANSCRIPT_LIMIT + 1)
    if len(data) > TRANSCRIPT_LIMIT:
        raise ReviewError('The independent response exceeds the intake limit.')
    response, event, rendered = final_response(data.decode('utf-8'))
    lines = markdown_source(response).strip().split('\n')
    heading = lines[0].strip() if lines else ''
    consumed = 1
    for line in lines[1:4]:
        if COMMIT.search(heading) or not line.startswith('  ') or not line.strip() or line.lstrip().startswith('•'):
            break
        heading = compact(heading + ' ' + line)
        consumed += 1
    body = '\n'.join(lines[consumed:])
    scope = compact(scope) if scope else heading
    observation = selected_observation(observation, heading, scope)
    # Keep the historical folded fingerprint for old receipts and scope lookup.
    whole = compact(response)
    verdict = bound_decision(heading, task, source, scope)
    if not verdict:
        raise ReviewError('The final independent verdict must identify this task and exact full source in its heading.')
    projected_body, observations = response_body(body, rendered=rendered)
    if scope not in whole or observation is None or observation not in observations:
        raise ReviewError('Select a complete factual observation from the post-verdict body, separate from the heading.')
    projected_heading, _ = response_body('\n'.join(lines[:consumed]), rendered=rendered)
    response_text = json.dumps([projected_heading, projected_body], ensure_ascii=False, separators=(',', ':'))
    response_hash = hashlib.sha256(response_text.encode('utf-8')).hexdigest()
    decision = dict(kind='decision', source_commit=source, verdict=verdict, decision=heading, scope=scope,
                    observation=observation, reviewer=reviewer, original_sha256=hashlib.sha256(data).hexdigest(),
                    response_id=response_identity(event, response_hash), response_sha256=response_hash,
                    legacy_response_sha256=hashlib.sha256(whole.encode('utf-8')).hexdigest(),
                    observation_body_sha256=hashlib.sha256(projected_body.encode('utf-8')).hexdigest(),
                    event=event, supersedes=supersedes)
    decision['evidence_sha256'] = evidence_digest(task, decision)
    return decision


def request_identity(task, request):
    subject = [task, request['source_commit'], request['scope'], request['supersedes']]
    raw = json.dumps(subject, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def create_request(task, source, scope, supersedes):
    request = dict(kind='request', source_commit=source, scope=compact(scope), supersedes=supersedes,
                   requested_at=datetime.now(timezone.utc).isoformat())
    request['request_id'] = request_identity(task, request)
    request['evidence_sha256'] = evidence_digest(task, request)
    return request


def append_entry(root, entries, entry):
    if entry['kind'] == 'request':
        previous = next((row for row in entries if entry_id(row) == entry['request_id']), None)
    else:
        previous = repeated_response(entries, entry)
    if previous:
        return previous, False
    latest = entries[-1] if entries else None
    validate_successor(latest, entry)
    if latest and entry['kind'] == 'decision' and latest['source_commit'] != entry['source_commit']:
        try:
            require_ancestor(root, latest['source_commit'], entry['source_commit'])
        except ScopeError as error:
            raise ReviewError('Open a new independent review request before importing a decision on a replacement source.') from error
    return entry, True


def selected_plan(task):
    plan = task.get('plan', {})
    types = set(plan.get('change_types', []))
    effect = plan.get('evolution', {}).get('data_effect')
    return bool(plan.get('independent_review') or BOUNDARY_TYPES.intersection(types)
                or effect == 'destructive' or 'persistence' in types and effect not in {'preserved', 'additive'})


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
    if reviewed != head and git(root, 'merge-base', '--all', reviewed, head).decode('ascii').splitlines() != [reviewed]:
        return False
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


def disposition(root, task, feedback, entry, head, protected):
    key = task['id']
    if not entry:
        return 'pending'
    try:
        reviewed = exact_commit(root, entry['source_commit'])
        earlier = read_at(root, reviewed, 'project/tasks.json')['tasks']
        original = next((item for item in earlier if item['id'] == key), None)
        if original is None or ({k: v for k, v in original.items() if k not in TASK_ADMIN}
                                != {k: v for k, v in task.items() if k not in TASK_ADMIN}):
            return 'stale'
        observed = read_at(root, reviewed, 'project/feedback.json')['items']
        if feedback is not None and qualifying_items(observed, key, observed) != feedback:
            return 'stale'
        if not source_matches(root, reviewed, head, protected):
            return 'stale'
        return 'pending' if entry['kind'] == 'request' else entry['verdict']
    except (ScopeError, OSError, ValueError, KeyError, TypeError):
        return 'unavailable'


def review_view(key, status, finding, entries, head):
    path = f'{DIRECTORY}/{key}.json'
    result = {'status': status, 'feedback_ids': sorted(finding), 'reviewed_source': None,
              'reviewer': 'Independent review role assigned by the coordinator', 'candidate_source': head}
    for index in range(len(entries) - 1, -1, -1):
        decision = entries[index]
        if decision['kind'] != 'decision':
            continue
        result.update(reviewed_source=decision['source_commit'], last_verdict=decision['verdict'],
                      reviewer=decision['reviewer'], observation=decision['observation'],
                      response_id=decision['response_id'],
                      source={'path': path, 'pointer': f'/events/{index}',
                              'evidence_sha256': decision['evidence_sha256']})
        break
    if entries:
        latest = entries[-1]
        result['current_entry_id'] = entry_id(latest)
        result['current_entry_source'] = latest['source_commit']
        if latest['kind'] == 'request':
            result['pending_request'] = {**latest, 'path': path, 'pointer': f'/events/{len(entries) - 1}'}
    if status == 'unavailable':
        result['review_next_action'] = 'Restore the local protected comparison history or the named review source, then recheck.'
    elif status in {'pending', 'stale', 'changes_requested'}:
        result['review_next_action'] = 'Keep the task unfinished and obtain an independent final decision on the clean source.'
    return result


def assess(root, project, records, head, bases, *, accepted, protected, trusted, enforce):
    """Check only new acceptances and expose pending work through the same rules."""
    baselines = []
    protected_entries = {}
    for base, tasks, feedback in bases:
        baselines.append((base, tasks, feedback))
        receipts = git(root, 'ls-tree', '-r', '--name-only', base, '--', DIRECTORY + '/').decode('utf-8').splitlines()
        for path in receipts:
            key = path.rsplit('/', 1)[-1].removesuffix('.json')
            if not TASK_ID.fullmatch(key):
                continue
            original = prior_records(root, base, key)
            if base == protected:
                protected_entries[key] = original
            if records.get(key, [])[:len(original)] != original:
                raise ReviewError(f'Independent review history changed or disappeared for {key}.')

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
        selected = any(selected_plan(record) for record in (task, *(old for old, _ in previous)))
        entries = records.get(key, [])
        if not (selected or finding or entries):
            continue
        original = accepted.get(key)
        if task['status'] == 'done' and original and original['status'] == 'done':
            same = dict(task, evidence=original['evidence']) == original
            prefix = task['evidence'][:len(original['evidence'])] == original['evidence']
            if same and prefix and entries == protected_entries.get(key, []):
                if entries:
                    states[key] = review_view(key, 'accepted_history', finding, entries, head)
                continue
        if not trusted and task['status'] == 'done':
            state = 'unavailable'
        else:
            state = disposition(root, task, finding if automatic else None,
                                entries[-1] if entries else None, head, protected)
        states[key] = review_view(key, state, finding, entries, head)
        if enforce and task['status'] == 'done' and state != 'approved':
            raise ReviewError(f'Independent review for {key} is {state}. '
                              + states[key]['review_next_action'] + ' Use pnpm project:review for decision intake.')
    return states
