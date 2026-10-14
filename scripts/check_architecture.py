"""Check concrete repository boundaries without requiring shared infrastructure."""

import ast
import json
from pathlib import Path
import re
import subprocess

from jsonschema import validators

if __package__:
    from .rust_workspace import workspace_packages
else:
    from rust_workspace import workspace_packages

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_INTERNAL = {
    'scopehub-domain': set(),
    'scopehub-application': {'scopehub-domain'},
    'scopehub-persistence': {'scopehub-domain', 'scopehub-application'},
    'scopehub-worker-host': {'scopehub-domain', 'scopehub-application'},
    'scopehub-platform': {'scopehub-domain', 'scopehub-application'},
    'scopehub-desktop': {'scopehub-domain', 'scopehub-application', 'scopehub-persistence', 'scopehub-worker-host', 'scopehub-platform'},
}


def rust_boundaries(root):
    errors = []
    try:
        packages = workspace_packages(root)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return [str(error)]
    members = {Path(package['manifest_path']).resolve().parent for package in packages}
    for package in packages:
        name = package['name']
        if name not in ALLOWED_INTERNAL:
            errors.append(f'Unregistered internal layer: {name}')
            continue
        for value in package['dependencies']:
            dependency = value['name']
            if 'path' in value:
                dependency_path = Path(value['path']).resolve()
                if not dependency_path.is_relative_to(root.resolve()):
                    errors.append(f'Local dependency outside the reviewed repository: {name} -> {dependency}')
                elif dependency_path not in members:
                    errors.append(f'Local dependency is not a reviewed workspace member: {name} -> {dependency}')
            if dependency.startswith('scopehub-') and dependency not in ALLOWED_INTERNAL[name]:
                errors.append(f'Forbidden internal dependency: {name} -> {dependency}')
            if name in {'scopehub-domain', 'scopehub-application'} and dependency in {'tauri', 'rusqlite', 'sqlx'}:
                errors.append(f'Infrastructure dependency in {name}: {dependency}')
    return errors


def desktop_capability_errors(root):
    errors = []
    directory = root / 'apps/desktop/src-tauri'
    capabilities = list((directory / 'capabilities').glob('*'))
    if {path.name for path in capabilities} != {'main.json'}:
        errors.append('Desktop capabilities must be limited to the reviewed main window')
    for path in capabilities:
        if path.suffix != '.json':
            errors.append(f'Unreviewed capability format: {path.name}')
            continue
        capability = json.loads(path.read_text())
        if (capability.get('remote') is not None or capability.get('windows') != ['main']
                or capability.get('webviews') or capability.get('local', True) is not True
                or capability.get('identifier') != 'main'
                or capability.get('permissions') != ['allow-execute-operation']):
            errors.append(f'Desktop capability exceeds the local operation registry: {path.name}')
    config = json.loads((directory / 'tauri.conf.json').read_text())
    if config['app']['security'].get('capabilities') != ['main']:
        errors.append('Desktop capability configuration must select only main')
    build = (directory / 'build.rs').read_text()
    if 'AppManifest::new().commands(&["execute_operation"])' not in build:
        errors.append('Desktop commands must use the reviewed application ACL')
    return errors


def main():
    errors = []
    spec = json.loads((ROOT / 'engineering-contract-v1.json').read_text())
    manifest = json.loads((ROOT / 'contracts/contract.json').read_text())
    schema = json.loads((ROOT / manifest['schema']).read_text())
    for path in spec['repository']['required_files']:
        if not (ROOT / path).is_file():
            errors.append(f'Missing required artifact: {path}')
    errors.extend(rust_boundaries(ROOT))
    source_root = ROOT / 'apps/desktop/ui/src'
    for path in source_root.rglob('*'):
        if path.suffix not in {'.ts', '.tsx', '.js', '.mjs'}:
            continue
        source = path.read_text()
        relative = path.relative_to(source_root)
        if 'lightweight-charts' in source and relative.parts[0] != 'chart-adapter':
            errors.append(f'Chart SDK outside adapter: {relative}')
        if re.search(r'(@tauri-apps/plugin-(shell|fs|sql)|node:(fs|child_process)|python/providers|python/ai)', source):
            errors.append(f'Forbidden UI capability or adapter dependency: {relative}')
    for path in (ROOT / 'python').rglob('*.py'):
        relative = path.relative_to(ROOT / 'python')
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                imports = [node.module or '']
            if any(name.split('.')[0] in {'sqlite3', 'sqlalchemy', 'psycopg'} for name in imports):
                errors.append(f'Worker database access: {relative}')
            if any(name.startswith('providers') for name in imports) and relative.parts[0] not in {'providers', 'worker_runtime'}:
                errors.append(f'Provider dependency outside data boundary: {relative}')
    expected = {op['name']: op for op in spec['operations']}
    actual = {op['name']: op for op in manifest['operations']}
    if set(actual) != set(expected) or set(expected) != set(schema['$defs']['OperationName']['enum']):
        errors.append('Operation catalog drift')
    for name in actual.keys() & expected.keys():
        for key in ['effect', 'confirmation', 'undoable', 'actors']:
            if actual[name][key] != expected[name][key]:
                errors.append(f'Operation policy drift: {name}.{key}')
        for direction in ['input', 'output']:
            if actual[name][direction] not in schema['$defs']:
                errors.append(f'Missing operation schema: {name}.{direction}')
    if schema['$defs']['ProtocolVersion']['const'] != spec['protocol']['version']:
        errors.append('Protocol version drift')
    for name, definition in schema['$defs'].items():
        if definition.get('type') == 'object' and name != 'Metadata' and definition.get('additionalProperties') is not False:
            errors.append(f'Unbounded structural fields: {name}')
    tauri = json.loads((ROOT / 'apps/desktop/src-tauri/tauri.conf.json').read_text())
    tauri_schema = json.loads((ROOT / 'node_modules/@tauri-apps/cli/config.schema.json').read_text())
    errors.extend(f'Tauri configuration: {error.message}' for error in validators.validator_for(tauri_schema)(tauri_schema).iter_errors(tauri))
    if not tauri['app']['security']['csp']:
        errors.append('Release CSP missing')
    errors.extend(desktop_capability_errors(ROOT))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(json.dumps({'status': 'passed', 'operations': len(actual), 'boundary_check': 'declared dependencies and source imports'}))


if __name__ == '__main__':
    main()
