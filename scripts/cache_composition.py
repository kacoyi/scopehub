"""Report bounded metadata totals for the paths used by ordinary Cargo caching."""

import ctypes
from ctypes import wintypes
from functools import cache
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
SCHEMA_VERSION = 2
_IS_WINDOWS = os.name == 'nt'
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_ALL = 0x0007
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@cache
def _windows_file_api():
    class FileInformation(ctypes.Structure):
        _fields_ = [
            ('attributes', wintypes.DWORD),
            ('creation_time', wintypes.FILETIME),
            ('access_time', wintypes.FILETIME),
            ('write_time', wintypes.FILETIME),
            ('volume_serial', wintypes.DWORD),
            ('size_high', wintypes.DWORD),
            ('size_low', wintypes.DWORD),
            ('link_count', wintypes.DWORD),
            ('file_index_high', wintypes.DWORD),
            ('file_index_low', wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_information.restype = wintypes.BOOL
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    return create_file, get_information, close_handle, FileInformation


def _windows_file_metadata(path):
    # NTFS can leave stale sizes in other directory entries after a hardlink
    # is modified. Query the file object without reading data or opening a
    # replacement reparse target.
    create_file, get_information, close_handle, info_type = _windows_file_api()
    handle = create_file(
        os.fspath(path), _FILE_READ_ATTRIBUTES, _FILE_SHARE_ALL, None,
        _OPEN_EXISTING, _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = info_type()
        if not get_information(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.attributes, (info.size_high << 32) | info.size_low
    finally:
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _check_root(root):
    # Check every ancestor before touching its child. Path.resolve() would
    # follow cache-provided links and junctions, so keep the lexical paths.
    for directory in (*reversed(root.parents), root):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return 'missing'
        except OSError:
            return 'error'
        if _is_link(info):
            return 'skipped'
        if not stat.S_ISDIR(info.st_mode):
            return 'error'
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
    root_paths = {label: cargo_home / path for path, label in CARGO_COMPONENTS.items()}
    root_paths['target'] = target
    counters = ('entries', 'skipped_links', 'skipped_other', 'errors')
    roots = {label: {'status': 'not_observed', **dict.fromkeys(counters, 0)} for label in root_paths}
    labels = (*CARGO_COMPONENTS.values(), *DEBUG_COMPONENTS.values(), 'target_other')
    components = {label: {'files': 0, 'logical_bytes': 0} for label in labels}
    entries_seen, limit = 0, None
    start = clock()
    deadline = start + max_seconds

    for label, root in root_paths.items():
        if clock() >= deadline:
            limit = 'time'
            break
        observation = roots[label]
        excluded = _check_root(root)
        if clock() >= deadline:
            observation['status'] = 'limit'
            limit = 'time'
            break
        if excluded:
            observation['status'] = excluded
            continue

        observation['status'] = 'scanned'
        pending = [root]
        while pending and limit is None:
            if clock() >= deadline:
                limit = 'time'
                break
            directory = pending.pop()
            try:
                if directory != root and _is_link(directory.lstat()):
                    observation['skipped_links'] += 1
                    continue
                if clock() >= deadline:
                    limit = 'time'
                    break
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if clock() >= deadline:
                            limit = 'time'
                            break
                        if entries_seen >= max_entries:
                            limit = 'entries'
                            break
                        entries_seen += 1
                        observation['entries'] += 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if _is_link(info):
                                observation['skipped_links'] += 1
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(Path(entry.path))
                                continue
                            if not stat.S_ISREG(info.st_mode):
                                observation['skipped_other'] += 1
                                continue
                            if clock() >= deadline:
                                limit = 'time'
                                break
                            attributes, logical_bytes = (
                                _windows_file_metadata(entry.path) if _IS_WINDOWS else (0, info.st_size)
                            )
                        except OSError:
                            observation['errors'] += 1
                            continue
                        if clock() >= deadline:
                            limit = 'time'
                            break
                        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            observation['skipped_links'] += 1
                            continue
                        if attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                            observation['errors'] += 1
                            continue
                        bucket = (
                            _target_component(Path(entry.path).relative_to(target)) if label == 'target' else label
                        )
                        components[bucket]['files'] += 1
                        components[bucket]['logical_bytes'] += logical_bytes
            except OSError:
                if directory != root:
                    observation['errors'] += 1
                observation['status'] = 'error'

        if limit is None and clock() >= deadline:
            limit = 'time'
        if limit is not None:
            observation['status'] = 'limit'
            break
        if observation['errors']:
            observation['status'] = 'error'

    complete = (
        limit is None and roots['target']['status'] == 'scanned'
        and all(root['status'] in {'scanned', 'missing'} for root in roots.values())
    )
    return {
        'status': 'complete' if complete else 'incomplete', 'limit': limit,
        'elapsed_ms': round((clock() - start) * 1000),
        **{key: sum(root[key] for root in roots.values()) for key in counters},
        'missing_roots': sum(root['status'] == 'missing' for root in roots.values()),
        'roots': roots, 'components': components,
    }


def provenance(env, phase):
    """Accept only the expected comparable observation and bounded identity fields."""
    lane = env.get('SCOPEHUB_CACHE_LANE')
    match = env.get('SCOPEHUB_CACHE_MATCH')
    job = env.get('GITHUB_JOB')
    if (
        phase not in {'restored', 'post-check'}
        or lane not in {'core', 'browser'}
        or job != f'windows_{lane}'
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
        'schema_version': SCHEMA_VERSION, 'event': 'pull_request', 'profile': 'full',
        'platform': 'Windows', 'architecture': 'X64', 'producer_job': job, 'lane': lane, 'phase': phase,
        'run_id': run_id, 'run_attempt': 1, 'checkout_sha': checkout,
        'source_head': head, 'base_sha': base, 'cache_match': match,
        'accounting': 'regular_file_paths_logical_bytes_including_hardlink_aliases',
        'observation_scope': 'runner_directory_state', 'elapsed_scope': 'scan_only',
    }


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError('Expected one fixed observation phase.')
        record = provenance(os.environ, sys.argv[1])
        record.update(measure(Path.home() / '.cargo', Path.cwd() / 'target'))
    except Exception:
        # Do not serialize exception text or traceback paths from the producer.
        unavailable = {'schema_version': SCHEMA_VERSION, 'status': 'unavailable'}
        print(RECORD_PREFIX + json.dumps(unavailable, separators=(',', ':')))
        return 1
    print(RECORD_PREFIX + json.dumps(record, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
