"""Fetch the one reviewed public publisher advisory without sharing CI credentials."""

from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path

HOST = 'api.github.com'
ADVISORY = 'GHSA-qwgh-2vcv-g2f7'
ROUTE = f'/repos/RustCrypto/utils/security-advisories/{ADVISORY}'
URL = f'https://{HOST}{ROUTE}'
TOKEN_ENV = 'SCOPEHUB_PUBLISHER_ADVISORY_TOKEN'
RUN_KEYS = ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB', 'GITHUB_SHA', 'RUNNER_OS')
MAX_BYTES = 64 * 1024
TRANSIENT = {408, 429, 500, 502, 503, 504}


def require_exact_url(url):
    if url != URL:
        raise ValueError('Primary advisory URL differs from the approved publisher endpoint')


def public_record(body):
    if (not isinstance(body, dict) or body.get('ghsa_id') != ADVISORY
            or body.get('state') != 'published' or body.get('withdrawn_at') is not None):
        raise ValueError('Publisher advisory is not the approved public record')
    entries = body.get('vulnerabilities')
    if not isinstance(entries, list):
        raise ValueError('Invalid publisher advisory package ranges')
    vulnerabilities = []
    for item in entries:
        try:
            ecosystem = item['package']['ecosystem']
            name = item['package']['name']
            affected = item['vulnerable_version_range']
        except (KeyError, TypeError):
            raise ValueError('Invalid publisher advisory package range') from None
        if not all(isinstance(value, str) for value in (ecosystem, name, affected)):
            raise ValueError('Invalid publisher advisory package range')
        vulnerabilities.append({'package': {'ecosystem': ecosystem, 'name': name}, 'vulnerable_version_range': affected})
    return {'ghsa_id': ADVISORY, 'state': 'published', 'withdrawn_at': None, 'vulnerabilities': vulnerabilities}


def request_once(token):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'ScopeHub-dependency-review'}
    if token:
        if not token.isascii() or not token.isprintable() or any(char.isspace() for char in token):
            raise ValueError('Invalid hosted publisher credential')
        headers['Authorization'] = f'Bearer {token}'
    # Direct TLS to a fixed host and relative path. HTTPConnection never follows redirects.
    connection = HTTPSConnection(HOST, timeout=8)
    try:
        connection.request('GET', ROUTE, headers=headers)
        with connection.getresponse() as response:
            return response.status, response.read(MAX_BYTES + 1) if response.status == 200 else b''
    finally:
        connection.close()


def fetch(url, token=None):
    require_exact_url(url)
    for attempt in range(2):
        try:
            status, data = request_once(token)
        except (HTTPException, OSError):
            if attempt == 0:
                continue
            raise ValueError('Publisher advisory network request failed') from None
        if status == 200:
            if len(data) > MAX_BYTES:
                raise ValueError('Publisher advisory response is too large')
            try:
                return public_record(json.loads(data))
            except (json.JSONDecodeError, UnicodeError):
                raise ValueError('Publisher advisory response is invalid JSON') from None
        if attempt == 0 and token and status in {403, 404}:
            # A read-only installation token may lack access to another repository.
            # The same published advisory can be read anonymously if quota permits.
            token = None
            continue
        if attempt == 0 and status in TRANSIENT:
            continue
        raise ValueError(f'Publisher advisory unavailable (HTTP {status})')


def hosted_slot(env):
    run = {key: env.get(key) for key in RUN_KEYS}
    if env.get('GITHUB_ACTIONS') != 'true' or not all(run.values()) or not env.get('RUNNER_TEMP'):
        raise ValueError('Publisher advisory requires the current GitHub job identity')
    return Path(env['RUNNER_TEMP']) / 'scopehub-public-publisher-advisory.json', run


def prefetch(env, token):
    path, run = hosted_slot(env)
    if not token:
        raise ValueError('Hosted publisher credential is unavailable')
    body = fetch(URL, token)
    try:
        with path.open('x', encoding='utf-8') as output:
            json.dump({'run': run, 'url': URL, 'body': body}, output)
    except OSError:
        raise ValueError('Could not retain current publisher advisory evidence') from None


def read_publisher(url):
    require_exact_url(url)
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        return fetch(url)
    path, run = hosted_slot(os.environ)
    try:
        with path.open('rb') as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Oversized publisher advisory evidence')
        evidence = json.loads(data)
    except (OSError, ValueError):
        raise ValueError('Current publisher advisory evidence is missing or invalid') from None
    if not isinstance(evidence, dict) or evidence.get('run') != run or evidence.get('url') != url:
        raise ValueError('Publisher advisory evidence belongs to another job or endpoint')
    return public_record(evidence.get('body'))


if __name__ == '__main__':
    job_token = os.environ.pop(TOKEN_ENV, None)
    try:
        prefetch(os.environ, job_token)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
