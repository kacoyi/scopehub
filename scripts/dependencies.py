"""Review exact lockfile inventory, upstream license metadata and fresh OSV results."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ['Cargo.lock', 'uv.lock', 'pnpm-lock.yaml']
OSV = 'https://api.osv.dev/v1/querybatch'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def key(package):
    return package['ecosystem'], package['name'], package['version']


def lock_inventory(root=ROOT):
    packages = []
    cargo = tomllib.loads((root / 'Cargo.lock').read_text(encoding='utf-8'))
    if cargo.get('version') != 4:
        raise ValueError('Unsupported Cargo lockfile version')
    for package in cargo['package']:
        if 'source' not in package:
            continue
        if package['source'] != 'registry+https://github.com/rust-lang/crates.io-index' or not package.get('checksum'):
            raise ValueError('Review support for a non-crates.io or unchecked Cargo dependency first')
        packages.append({'ecosystem': 'crates.io', 'name': package['name'], 'version': package['version']})
    uv = tomllib.loads((root / 'uv.lock').read_text(encoding='utf-8'))
    if uv.get('version') != 1:
        raise ValueError('Unsupported uv lockfile version')
    for package in uv['package']:
        source = package['source']
        if source == {'virtual': '.'}:
            continue
        if source != {'registry': 'https://pypi.org/simple'}:
            raise ValueError('Review support for a non-PyPI Python dependency first')
        packages.append({'ecosystem': 'PyPI', 'name': package['name'], 'version': package['version']})
    pnpm = yaml.safe_load((root / 'pnpm-lock.yaml').read_text(encoding='utf-8'))
    if str(pnpm.get('lockfileVersion')) != '9.0':
        raise ValueError('Unsupported pnpm lockfile version')
    for identifier, package in pnpm['packages'].items():
        name, separator, version = identifier.rpartition('@')
        if not separator or not re.fullmatch(r'\d[^():/]*', version) or not package.get('resolution', {}).get('integrity'):
            raise ValueError(f'Review unsupported npm source or version: {identifier}')
        packages.append({'ecosystem': 'npm', 'name': name, 'version': version})
    if len({key(item) for item in packages}) != len(packages):
        raise ValueError('Duplicate package identities in the dependency inventory')
    return sorted(packages, key=key)


def actions(root=ROOT):
    found = set()
    for path in sorted((root / '.github/workflows').glob('*.y*ml')):
        workflow = yaml.safe_load(path.read_text(encoding='utf-8'))
        uses = []
        for job in workflow.get('jobs', {}).values():
            if 'uses' in job:
                uses.append(job['uses'])
            uses.extend(step['uses'] for step in job.get('steps', []) if 'uses' in step)
        for value in uses:
            if value.startswith('./'):
                continue
            match = re.fullmatch(r'([\w.-]+/[\w./-]+)@([0-9a-f]{40})', value)
            if not match:
                raise ValueError(f'External workflow source is not pinned to a full commit: {value}')
            found.add((match[1], match[2]))
    return sorted(found)


def fetch_json(url, body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'ScopeHub-dependency-review'})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def query_advisories(packages, request=fetch_json):
    findings = []
    for start in range(0, len(packages), 100):
        pending = [(package, ({'commit': package['version']} if package['ecosystem'] == 'GitHub Actions' else
                              {'package': {'name': package['name'], 'ecosystem': package['ecosystem']},
                               'version': package['version']}), set()) for package in packages[start:start + 100]]
        while pending:
            response = request(OSV, {'queries': [query for _, query, _ in pending]})
            if not isinstance(response, dict):
                raise ValueError('OSV returned an invalid response envelope')
            results = response.get('results')
            if not isinstance(results, list) or len(results) != len(pending):
                raise ValueError('OSV returned incomplete query coverage')
            following = []
            for (package, query, seen), result in zip(pending, results, strict=True):
                if not isinstance(result, dict) or set(result) - {'vulns', 'next_page_token'}:
                    raise ValueError('OSV returned an unexpected package result')
                vulnerabilities = result.get('vulns', [])
                if not isinstance(vulnerabilities, list):
                    raise ValueError('OSV returned malformed vulnerabilities')
                for vulnerability in vulnerabilities:
                    if not isinstance(vulnerability, dict) or not isinstance(vulnerability.get('id'), str):
                        raise ValueError('OSV returned a vulnerability without an identifier')
                    findings.append({**package, 'advisory': vulnerability['id']})
                token = result.get('next_page_token')
                if token:
                    if not isinstance(token, str) or token in seen or len(seen) >= 20:
                        raise ValueError('OSV pagination did not converge')
                    following.append((package, {**query, 'page_token': token}, seen | {token}))
            pending = following
    return findings


def dispositions(findings, policy, today=None):
    today = today or date.today()
    exceptions = policy['advisory_dispositions']
    for item in exceptions:
        if (not item.get('reason') or not item.get('task_id') or not item.get('evidence')
                or date.fromisoformat(item['expires']) <= today):
            raise ValueError('An advisory disposition is incomplete or expired')
    reviewed, unresolved = [], []
    for finding in findings:
        matching = [item for item in exceptions if key(item) == key(finding) and finding['advisory'] in item['advisories']]
        if len(matching) > 1:
            raise ValueError('Ambiguous advisory disposition')
        if matching:
            reviewed.append({**finding, 'disposition': matching[0]})
        else:
            unresolved.append(finding)
    return reviewed, unresolved


def validate_advisory_scope(root, policy):
    if not policy['advisory_dispositions']:
        return
    desktop = tomllib.loads((root / 'apps/desktop/src-tauri/Cargo.toml').read_text(encoding='utf-8'))
    if desktop['package']['metadata']['scopehub']['check_platform'] != 'windows-x64':
        raise ValueError('Reassess the advisory disposition before enabling another desktop platform')
    # Resolve both the supported native product and all targets of portable crates.
    graphs = []
    for selection in [['--workspace', '--target', 'x86_64-pc-windows-msvc'],
                      ['--workspace', '--exclude', desktop['package']['name'], '--target', 'all']]:
        result = subprocess.run(['cargo', 'tree', '--locked', *selection, '--prefix', 'none', '--format', '{p}'],
                                cwd=root, capture_output=True, text=True, encoding='utf-8', check=True, timeout=180)
        graphs.extend(result.stdout.splitlines())
    for item in policy['advisory_dispositions']:
        if item.get('scope_guard') != 'absent-from-supported-builds' or item['ecosystem'] != 'crates.io':
            raise ValueError('Unsupported advisory scope guard; explicit implementation and review are needed')
        if any(line.split()[:2] == [item['name'], 'v' + item['version']] for line in graphs):
            raise ValueError(f"A disposition no longer applies to supported builds: {item['name']}")


def notice_text(inventory):
    lines = [
        'ScopeHub dependency license inventory',
        '',
        'This inventory covers all locked packages, including development and non-shipped target dependencies.',
        'License declarations come from version-specific upstream metadata. The original package licenses and',
        'notices govern use and must be preserved when applicable. This file is not the packaged release notice bundle.',
        'Before distribution, release-artifacts must retain the actual bundled license/copyright/NOTICE texts',
        'and any required covered source offers. No packaged release has been accepted.',
        '',
    ]
    for package in inventory['packages']:
        lines.extend([f"{package['ecosystem']}: {package['name']} {package['version']}",
                      f"Declared license: {package['license']}", f"Upstream metadata: {package['metadata_url']}", ''])
    return '\n'.join(lines)


def validate_inventory(root, policy, packages):
    inventory = read(root / 'project/dependency-inventory.json')
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in LOCKS}
    if inventory['lock_sha256'] != hashes or [key(item) for item in inventory['packages']] != [key(item) for item in packages]:
        raise ValueError('Dependency inventory is stale; refresh and review the changed upstream metadata')
    reviewed_licenses = set(policy['reviewed_license_expressions'])
    for package in inventory['packages']:
        if not package.get('license') or package['license'] not in reviewed_licenses or not package.get('metadata_url', '').startswith('https://'):
            raise ValueError(f'Unreviewed license metadata: {key(package)}')
    if (root / 'THIRD_PARTY_NOTICES').read_text(encoding='utf-8') != notice_text(inventory):
        raise ValueError('Dependency notice inventory is missing or stale')
    observed_actions = actions(root)
    reviewed_actions = sorted((item['repository'], item['sha']) for item in policy['actions'])
    if observed_actions != reviewed_actions:
        raise ValueError('Workflow sources differ from reviewed immutable Actions')
    return inventory


def refresh_inventory(root=ROOT):
    packages = lock_inventory(root)
    overrides = read(root / 'project/dependencies.json').get('license_metadata_overrides', [])
    result = subprocess.run(['cargo', 'metadata', '--locked', '--format-version', '1'], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=300)
    cargo = {(item['name'], item['version']): item for item in json.loads(result.stdout)['packages'] if item['source']}

    def metadata(package):
        ecosystem, name, version = key(package)
        quoted = urllib.parse.quote(name, safe='')
        if ecosystem == 'crates.io':
            info = cargo[(name, version)]
            license_name = info.get('license')
            url = f'https://crates.io/crates/{quoted}/{version}'
        else:
            url = (f'https://pypi.org/pypi/{quoted}/{version}/json' if ecosystem == 'PyPI'
                   else f'https://registry.npmjs.org/{quoted}/{version}')
            info = fetch_json(url)
            if ecosystem == 'PyPI':
                info = info['info']
                license_name = info.get('license_expression') or info.get('license')
                if not license_name:
                    license_name = ' OR '.join(value for value in info.get('classifiers', [])
                                               if value.startswith('License :: ') and value != 'License :: OSI Approved')
            else:
                license_name = info.get('license')
                if isinstance(license_name, dict):
                    license_name = license_name.get('type')
        override = next((item for item in overrides if key(item) == key(package)), None)
        if override:
            license_name, url = override['license'], override['source']
        if not isinstance(license_name, str) or not license_name.strip():
            raise ValueError(f'Upstream license metadata missing: {key(package)}')
        return {**package, 'license': license_name.strip(), 'metadata_url': url}

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(metadata, packages))
    inventory = {'schema_version': '1.0', 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'lock_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in LOCKS},
                 'packages': records}
    write(root / 'project/dependency-inventory.json', inventory)
    (root / 'THIRD_PARTY_NOTICES').write_text(notice_text(inventory), encoding='utf-8')
    return {'status': 'review_required', 'packages': len(records),
            'license_expressions': sorted({item['license'] for item in records})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh-inventory', action='store_true', help='Fetch metadata; never approve licenses or advisories automatically')
    parser.add_argument('--offline', action='store_true', help='Validate inventory only; does not establish advisory acceptance')
    args = parser.parse_args()
    output = ROOT / '.reports/dependencies' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.json')
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
              'claim_scope': 'Dependency metadata and known advisories at query time; not proof of absence of defects or release-license compliance.'}
    try:
        if args.refresh_inventory:
            print(json.dumps(refresh_inventory(), indent=2))
            return
        policy = read(ROOT / 'project/dependencies.json')
        packages = lock_inventory(ROOT)
        inventory = validate_inventory(ROOT, policy, packages)
        report.update(packages=len(packages), lock_sha256=inventory['lock_sha256'], inventory='passed')
        if args.offline:
            report.update(status='blocked', advisories='not_queried')
        else:
            validate_advisory_scope(ROOT, policy)
            action_packages = [{'ecosystem': 'GitHub Actions', 'name': name, 'version': sha} for name, sha in actions(ROOT)]
            findings = query_advisories(packages + action_packages)
            reviewed, unresolved = dispositions(findings, policy)
            report.update(status='failed' if unresolved else 'passed', advisories='queried', actions=len(action_packages),
                          reviewed_findings=reviewed, unresolved_findings=unresolved)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report.update(error=str(error), status='failed')
    write(output, report)
    print(json.dumps({**report, 'report': output.relative_to(ROOT).as_posix()}, indent=2))
    raise SystemExit(0 if report['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
