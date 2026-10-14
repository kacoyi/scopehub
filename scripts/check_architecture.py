"""Check concrete repository boundaries without requiring shared infrastructure."""

import ast
import json
from pathlib import Path
import re
import tomllib

from jsonschema import validators

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_INTERNAL = {
    'scopehub-domain': set(),
    'scopehub-application': {'scopehub-domain'},
    'scopehub-persistence': {'scopehub-domain', 'scopehub-application'},
    'scopehub-worker-host': {'scopehub-domain', 'scopehub-application'},
    'scopehub-platform': {'scopehub-domain', 'scopehub-application'},
    'scopehub-desktop': {'scopehub-domain', 'scopehub-application', 'scopehub-persistence', 'scopehub-worker-host', 'scopehub-platform'},
}


def dependency_tables(manifest):
    for key in ['dependencies', 'dev-dependencies', 'build-dependencies']:
        yield manifest.get(key, {})
    for target in manifest.get('target', {}).values():
        yield from dependency_tables(target)


def rust_boundaries(root):
    errors = []
    workspace = tomllib.loads((root / 'Cargo.toml').read_text()).get('workspace', {}).get('dependencies', {})
    for path in [*root.glob('crates/*/Cargo.toml'), root / 'apps/desktop/src-tauri/Cargo.toml']:
        if not path.is_file():
            continue
        cargo = tomllib.loads(path.read_text())
        name = cargo['package']['name']
        if name not in ALLOWED_INTERNAL:
            errors.append(f'Unregistered internal layer: {name}')
            continue
        for table in dependency_tables(cargo):
            for alias, value in table.items():
                base = path.parent
                if isinstance(value, dict) and value.get('workspace'):
                    if alias not in workspace:
                        errors.append(f'Missing workspace dependency: {name} -> {alias}')
                        continue
                    value, base = workspace[alias], root
                dependency = value.get('package', alias) if isinstance(value, dict) else alias
                if isinstance(value, dict) and 'path' in value:
                    dependency_path = (base / value['path'] / 'Cargo.toml').resolve()
                    if not dependency_path.is_relative_to(root.resolve()):
                        errors.append(f'Local dependency outside the reviewed repository: {name} -> {alias}')
                        continue
                    if not dependency_path.is_file():
                        errors.append(f'Missing local dependency: {name} -> {alias}')
                        continue
                    dependency = tomllib.loads(dependency_path.read_text())['package']['name']
                if dependency.startswith('scopehub-') and dependency not in ALLOWED_INTERNAL[name]:
                    errors.append(f'Forbidden internal dependency: {name} -> {dependency}')
                if name in {'scopehub-domain', 'scopehub-application'} and dependency in {'tauri', 'rusqlite', 'sqlx'}:
                    errors.append(f'Infrastructure dependency in {name}: {dependency}')
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
    for path in (ROOT / 'apps/desktop/src-tauri/capabilities').glob('*.json'):
        capability = json.loads(path.read_text())
        if capability.get('remote'):
            errors.append(f'Remote Tauri capability: {path.name}')
        if capability.get('permissions'):
            errors.append(f'M0 must expose no commands: {path.name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(json.dumps({'status': 'passed', 'operations': len(actual), 'boundary_check': 'declared dependencies and source imports'}))


if __name__ == '__main__':
    main()
