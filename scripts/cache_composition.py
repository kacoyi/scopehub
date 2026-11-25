"""Report bounded metadata totals for the paths used by ordinary Cargo caching."""

import json
import os
from pathlib import Path
import re
import stat
import sys
import time

if __package__:
    from .verification_scope import JsonNumber, json_object, oid
else:
    from verification_scope import JsonNumber, json_object, oid


CARGO_COMPONENTS = {
    'registry/index': 'cargo_registry_index',
    'registry/cache': 'cargo_registry_cache',
    'git/db': 'cargo_git_db',
}
DEBUG_COMPONENTS = {
    'deps': 'target_deps',
    'build': 'target_build',
    'incremental': 'target_incremental',
    '.fingerprint': 'target_fingerprints',
    'examples': 'target_examples',
}
PROBE_BRANCH = 'codex/audit-native-cache-composition'
RECORD_PREFIX = 'SCOPEHUB_CACHE_COMPOSITION '


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _check_root(root):
    # Check every ancestor before touching its child. Path.resolve() would
    # follow cache-provided links and junctions, so keep the lexical paths.
    for directory in (*reversed(root.parents), root):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return 'missing_roots'
        if _is_link(info):
            return 'skipped_links'
        if not stat.S_ISDIR(info.st_mode):
            return 'errors'
    return None


def _target_component(relative):
    parts = relative.parts
    if len(parts) > 2 and parts[0].lower() == 'debug':
        return DEBUG_COMPONENTS.get(parts[1].lower(), 'target_other')
    return 'target_other'


def measure(cargo_home, target, *, max_entries=100_000, max_seconds=10.0, clock=time.monotonic):
    """Count regular paths and logical bytes, with each hardlink alias counted."""
    if not cargo_home.is_absolute() or not target.is_absolute():
        raise ValueError('Cache roots must be absolute.')
    roots = [(cargo_home / path, label) for path, label in CARGO_COMPONENTS.items()]
    roots.append((target, None))
    labels = (*CARGO_COMPONENTS.values(), *DEBUG_COMPONENTS.values(), 'target_other')
    components = {label: {'files': 0, 'logical_bytes': 0} for label in labels}
    result = {
        'status': 'complete', 'limit': None, 'entries': 0, 'skipped_links': 0,
        'skipped_other': 0, 'errors': 0, 'missing_roots': 0, 'components': components,
    }
    start = clock()
    deadline = start + max_seconds

    for root, label in roots:
        if clock() >= deadline:
            result['limit'] = 'time'
            break
        try:
            excluded = _check_root(root)
        except OSError:
            excluded = 'errors'
        if excluded:
            result[excluded] += 1
            continue
        pending = [root]
        while pending and result['limit'] is None:
            if clock() >= deadline:
                result['limit'] = 'time'
                break
            try:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        if clock() >= deadline:
                            result['limit'] = 'time'
                            break
                        if result['entries'] >= max_entries:
                            result['limit'] = 'entries'
                            break
                        result['entries'] += 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            result['errors'] += 1
                            continue
                        if _is_link(info):
                            result['skipped_links'] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode):
                            bucket = label or _target_component(Path(entry.path).relative_to(target))
                            components[bucket]['files'] += 1
                            components[bucket]['logical_bytes'] += info.st_size
                        else:
                            result['skipped_other'] += 1
            except OSError:
                result['errors'] += 1
        if result['limit'] is not None:
            break

    end = clock()
    if result['limit'] is None and end >= deadline:
        result['limit'] = 'time'
    result['elapsed_ms'] = round((end - start) * 1000)
    if result['limit'] is not None or result['errors']:
        result['status'] = 'incomplete'
    return result


def provenance(env, phase):
    """Accept only the expected comparable observation and bounded identity fields."""
    lane = env.get('SCOPEHUB_CACHE_LANE')
    match = env.get('SCOPEHUB_CACHE_MATCH')
    if (
        phase not in {'restored', 'post-check'}
        or lane not in {'core', 'browser'}
        or match not in {'primary_exact', 'legacy_equivalent', 'prefix'}
        or env.get('RUNNER_OS') != 'Windows'
        or env.get('RUNNER_ARCH') != 'X64'
        or env.get('GITHUB_EVENT_NAME') != 'pull_request'
        or env.get('GITHUB_RUN_ATTEMPT') != '1'
        or env.get('GITHUB_BASE_REF') != 'main'
        or env.get('GITHUB_HEAD_REF') != PROBE_BRANCH
    ):
        raise ValueError('Unexpected cache observation scope.')

    run_id = env['GITHUB_RUN_ID']
    checkout = oid(env.get('GITHUB_SHA'))
    head = oid(env.get('SCOPEHUB_SOURCE_HEAD'))
    base = oid(env.get('SCOPEHUB_SCOPE_BASE'))
    raw = env['SCOPEHUB_SCOPE_DECISION']
    if not re.fullmatch(r'[1-9][0-9]{0,19}', run_id) or len(raw) > 1024:
        raise ValueError('Invalid observation identity.')
    decision = json_object(raw.encode('utf-8'))
    count = decision.get('change_count')
    if (
        decision.keys() != {'profile', 'base', 'checkout', 'change_count', 'reason_code'}
        or decision['profile'] != 'full'
        or decision['reason_code'] != 'change_requires_full'
        or decision['base'] != base
        or decision['checkout'] != checkout
        or not isinstance(count, JsonNumber)
        or not re.fullmatch(r'[1-9][0-9]{0,9}', count.token)
    ):
        raise ValueError('Unverified ordinary full selection.')

    return {
        'schema_version': 1, 'event': 'pull_request', 'profile': 'full',
        'platform': 'Windows', 'architecture': 'X64', 'lane': lane, 'phase': phase,
        'run_id': run_id, 'run_attempt': 1, 'checkout_sha': checkout,
        'source_head': head, 'base_sha': base, 'cache_match': match,
        'accounting': 'regular_file_paths_logical_bytes_including_hardlink_aliases',
    }


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError('Expected one fixed observation phase.')
        record = provenance(os.environ, sys.argv[1])
        record.update(measure(Path.home() / '.cargo', Path.cwd() / 'target'))
    except Exception:
        # Do not serialize exception text or traceback paths from the producer.
        print(RECORD_PREFIX + '{"schema_version":1,"status":"unavailable"}')
        return 1
    print(RECORD_PREFIX + json.dumps(record, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
