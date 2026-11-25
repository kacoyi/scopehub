"""Check concrete repository boundaries without requiring shared infrastructure."""

import ast
import json
from pathlib import Path
import re
import subprocess

from jsonschema import validators

if __package__:
    from .development import expand_engineering_sources
    from .rust_workspace import workspace_packages
else:
    from development import expand_engineering_sources
    from rust_workspace import workspace_packages

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_INTERNAL = {
    'scopehub-domain': set(),
    'scopehub-application': {'scopehub-domain'},
    'scopehub-persistence': {'scopehub-domain', 'scopehub-application', 'scopehub-platform'},
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
    allowed = ALLOWED_INTERNAL
    development = root / 'project/development.json'
    if development.is_file():
        modules = json.loads(development.read_text(encoding='utf-8'))['modules']
        rust_modules = {item['id']: item for item in modules
                        if any(path.startswith('crates/') or path == 'apps/desktop/src-tauri' for path in item['paths'])}
        allowed = {f'scopehub-{key}': {f'scopehub-{dependency}' for dependency in item['dependencies']}
                   for key, item in rust_modules.items()}
    members = {Path(package['manifest_path']).resolve().parent for package in packages}
    for package in packages:
        name = package['name']
        if name not in allowed:
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
            if dependency.startswith('scopehub-') and dependency not in allowed[name]:
                errors.append(f'Forbidden internal dependency: {name} -> {dependency}')
            if name in {'scopehub-domain', 'scopehub-application'} and dependency in {'tauri', 'rusqlite', 'sqlx'}:
                errors.append(f'Infrastructure dependency in {name}: {dependency}')
    return errors


def declared_source_boundaries(root):
    """Check statically visible TS/Python module crossings and public entries.

    This intentionally does not claim to detect dynamic imports or all semantic
    coupling. Existing capability checks and code review remain necessary.
    """
    root = root.resolve()
    record = root / 'project/development.json'
    if not record.is_file():
        return []  # Small synthetic workspaces exercise independent Rust rules.
    modules = json.loads(record.read_text(encoding='utf-8'))['modules']
    errors = []

    def owner(path):
        relative = path.resolve().relative_to(root)
        candidates = [(len(Path(base).parts), item) for item in modules for base in item['paths']
                      if relative.is_relative_to(Path(base))]
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def crossing(source, target):
        target = target.resolve()
        if not target.is_relative_to(root):
            errors.append(f'Import outside repository: {source.relative_to(root)}')
            return
        caller = owner(source)
        relative = target.relative_to(root).as_posix()
        if relative.startswith('contracts/generated/'):
            dependency, public = 'domain', True
        else:
            callee = owner(target)
            if callee is None:
                errors.append(f'Unowned internal import: {relative}')
                return
            dependency = callee['id']
            public = relative in callee['public_entries']
        if caller is None:
            errors.append(f'Unowned source: {source.relative_to(root)}')
        elif caller['id'] != dependency:
            if dependency not in caller['dependencies']:
                errors.append(f'Forbidden module dependency: {caller["id"]} -> {dependency}')
            elif not public:
                errors.append(f'Private module entry: {caller["id"]} -> {relative}')

    ui = root / 'apps/desktop/ui/src'
    for path in ui.rglob('*'):
        if path.suffix not in {'.ts', '.tsx', '.js', '.mjs'}:
            continue
        # Include re-exports and static/dynamic literal imports. Computed imports
        # remain a review concern, not a claimed parser capability.
        pattern = r'''(?:from\s*|import\s*\(\s*|import\s*)['"]([^'"]+)['"]'''
        for imported in re.findall(pattern, path.read_text(encoding='utf-8')):
            if not imported.startswith('.'):
                continue
            target = (path.parent / imported).resolve()
            if target.suffix == '.css':
                continue
            candidates = [target, *[Path(str(target) + suffix) for suffix in ['.ts','.tsx','.js','.mjs']], target / 'index.ts']
            target = next((item for item in candidates if item.is_file()), target)
            crossing(path, target)
    python = root / 'python'
    known = {item.name for item in python.iterdir() if item.is_dir()} if python.exists() else set()
    for path in python.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = path.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    target = base / (node.module or '').replace('.', '/')
                    target = target.with_suffix('.py') if target.with_suffix('.py').is_file() else target / '__init__.py'
                    crossing(path, target)
                else:
                    imported = [node.module or '']
            for name in imported:
                if name.startswith('contracts.generated.'):
                    crossing(path, root / (name.replace('.', '/') + '.py'))
                elif name.split('.')[0] in known:
                    target = python / name.replace('.', '/')
                    target = target.with_suffix('.py') if target.with_suffix('.py').is_file() else target / '__init__.py'
                    crossing(path, target)
    return errors


def desktop_capability_errors(root):
    errors = []
    commands = ['execute_operation', 'configure_openai_credential', 'execute_market_subscription', 'cancel_market_subscription',
                'prepare_market_subscription', 'cancel_market_preparation', 'cancel_local_import_preview',
                'choose_local_csv_folder']
    permissions = ['allow-' + command.replace('_', '-') for command in commands]
    directory = root / 'apps/desktop/src-tauri'
    capabilities = list((directory / 'capabilities').glob('*'))
    if {path.name for path in capabilities} != {'main.json'}:
        errors.append('Desktop capabilities must be limited to the reviewed main window')
    for path in capabilities:
        if path.suffix != '.json':
            errors.append(f'Unreviewed capability format: {path.name}')
            continue
        capability = json.loads(path.read_text(encoding='utf-8'))
        if (capability.get('remote') is not None or capability.get('windows') != ['main']
                or capability.get('webviews') or capability.get('local', True) is not True
                or capability.get('identifier') != 'main'
                or capability.get('permissions') != permissions):
            errors.append(f'Desktop capability exceeds the local operation registry: {path.name}')
    config = json.loads((directory / 'tauri.conf.json').read_text(encoding='utf-8'))
    if config['app']['security'].get('capabilities') != ['main']:
        errors.append('Desktop capability configuration must select only main')
    build = (directory / 'build.rs').read_text(encoding='utf-8')
    manifest = re.search(r'AppManifest::new\(\)\.commands\(&\[([^\]]*)\]\)', build)
    if manifest is None or re.findall(r'"([^"]+)"', manifest[1]) != commands:
        errors.append('Desktop commands must use the reviewed application ACL')
    return errors


def valid_operation_actors(operation, declared_actors):
    actors = operation['actors']
    if not isinstance(actors, list) or not actors or any(actor not in declared_actors for actor in actors):
        return False
    if operation.get('transport') == 'private_workspace':
        return actors == ['user']
    return len(set(actors)) == len(actors)


def main():
    errors = []
    index = json.loads((ROOT / 'engineering-contract-v1.json').read_text(encoding='utf-8'))
    spec = expand_engineering_sources(ROOT, index)
    manifest = json.loads((ROOT / 'contracts/contract.json').read_text(encoding='utf-8'))
    schema = json.loads((ROOT / manifest['schema']).read_text(encoding='utf-8'))
    for path in spec['repository']['required_files']:
        if not (ROOT / path).is_file():
            errors.append(f'Missing required artifact: {path}')
    errors.extend(rust_boundaries(ROOT))
    errors.extend(declared_source_boundaries(ROOT))
    source_root = ROOT / 'apps/desktop/ui/src'
    for path in source_root.rglob('*'):
        if path.suffix not in {'.ts', '.tsx', '.js', '.mjs'}:
            continue
        source = path.read_text(encoding='utf-8')
        relative = path.relative_to(source_root)
        if 'lightweight-charts' in source and relative.parts[0] != 'chart-adapter':
            errors.append(f'Chart SDK outside adapter: {relative}')
        if re.search(r'(@tauri-apps/plugin-(shell|fs|sql)|node:(fs|child_process)|python/providers|python/ai)', source):
            errors.append(f'Forbidden UI capability or adapter dependency: {relative}')
    for path in (ROOT / 'python').rglob('*.py'):
        relative = path.relative_to(ROOT / 'python')
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                imports = [node.module or '']
            if any(name.split('.')[0] in {'sqlite3', 'sqlalchemy', 'psycopg'} for name in imports):
                errors.append(f'Worker database access: {relative}')
            if any(name.startswith('providers') for name in imports) and relative.parts[0] not in {'providers', 'worker_runtime'}:
                errors.append(f'Provider dependency outside data boundary: {relative}')
    catalog = manifest['operations']
    actual = {op['name']: op for op in catalog}
    if (index['operations'] != {'$source': {'path': 'contracts/contract.json', 'pointer': '/operations'}}
            or len(catalog) != len(actual)
            or set(actual) != set(schema['$defs']['OperationName']['enum'])):
        errors.append('Operation catalog drift')
    for name, op in actual.items():
        if (op['effect'] not in {'read', 'workspace', 'draft', 'compute', 'persist', 'control'}
                or type(op['undoable']) is not bool
                or not (type(op['confirmation']) is bool or op['confirmation'] == 'python_only')
                or not valid_operation_actors(op, spec['authorization']['actors'])
                or op.get('transport') not in (None, 'private_workspace')):
            errors.append(f'Invalid operation authority: {name}')
        versions = op.get('wire_bindings', {})
        if not set(versions).issubset(manifest['compatible_protocol_versions']):
            errors.append(f'Unsupported wire binding: {name}')
        for binding in [op, *versions.values()]:
            for direction in ['input', 'output']:
                if binding[direction] not in schema['$defs']:
                    errors.append(f'Missing operation schema: {name}.{direction}')
    version = spec['protocol']['version']
    if (schema['$defs']['ProtocolVersion11']['const'] != version
            or schema['$defs']['ProtocolVersion']['const'] != '1.0'
            or manifest['protocol_version'] != version):
        errors.append('Protocol version drift')
    for name, definition in schema['$defs'].items():
        if definition.get('type') == 'object' and name != 'Metadata' and definition.get('additionalProperties') is not False:
            errors.append(f'Unbounded structural fields: {name}')
    tauri = json.loads((ROOT / 'apps/desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    tauri_schema = json.loads((ROOT / 'node_modules/@tauri-apps/cli/config.schema.json').read_text(encoding='utf-8'))
    errors.extend(f'Tauri configuration: {error.message}' for error in validators.validator_for(tauri_schema)(tauri_schema).iter_errors(tauri))
    if not tauri['app']['security']['csp']:
        errors.append('Release CSP missing')
    errors.extend(desktop_capability_errors(ROOT))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(json.dumps({'status': 'passed', 'operations': len(actual), 'boundary_check': 'declared dependencies and source imports'}))


if __name__ == '__main__':
    main()
