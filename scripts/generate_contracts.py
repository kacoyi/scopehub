"""Generate structural types and schema projections; validators enforce refinements."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'contracts/schemas/protocol.schema.json'
GENERATED = ROOT / 'contracts/generated'


def pascal(value):
    return ''.join(part[:1].upper() + part[1:] for part in re.split(r'[^a-zA-Z0-9]+', value))


def nullable(schema):
    choices = schema.get('anyOf', [])
    if len(choices) == 2 and choices[1] == {'type': 'null'}:
        return choices[0]
    return None


def kind(schema, language):
    if '$ref' in schema:
        return schema['$ref'].split('/')[-1]
    inner = nullable(schema)
    if inner is not None:
        rendered = kind(inner, language)
        return {'ts': f'({rendered} | null)', 'py': f'{rendered} | None', 'rs': f'Option<{rendered}>'}[language]
    if language != 'rs' and ('const' in schema or 'enum' in schema):
        values = schema.get('enum', [schema.get('const')])
        return ' | '.join(json.dumps(v) for v in values) if language == 'ts' else 'Literal[' + ', '.join(repr(v) for v in values) + ']'
    if schema.get('type') == 'array':
        item = kind(schema['items'], language)
        return {'ts': f'Array<{item}>', 'py': f'list[{item}]', 'rs': f'Vec<{item}>'}[language]
    if schema.get('type') == 'integer' and language == 'rs':
        return 'u64' if schema.get('minimum', -1) >= 0 else 'i64'
    primitive = {
        'string': {'ts': 'string', 'py': 'str', 'rs': 'String'},
        'integer': {'ts': 'number', 'py': 'int', 'rs': 'u64'},
        'number': {'ts': 'number', 'py': 'float', 'rs': 'f64'},
        'boolean': {'ts': 'boolean', 'py': 'bool', 'rs': 'bool'},
        'null': {'ts': 'null', 'py': 'None', 'rs': '()'},
        'object': {'ts': 'Record<string, JsonValue>', 'py': 'dict[str, JsonValue]', 'rs': 'serde_json::Map<String, serde_json::Value>'},
    }
    if schema.get('type') in primitive:
        return primitive[schema['type']][language]
    raise ValueError(f'Unsupported anonymous structural type: {schema}')


def generate(schema):
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    header = f'Generated from contracts/schemas/protocol.schema.json; sha256={digest}. Do not edit.'
    ts = [f'// {header}', 'export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };']
    py = [f'# {header}', 'from __future__ import annotations', 'from typing import Literal, NotRequired, TypedDict', 'JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]']
    rs = [f'// {header}', '// Structural types only: validate JSON Schema before deserializing.']
    derive = '#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]'
    for name, definition in schema['$defs'].items():
        variants = definition.get('oneOf', definition.get('anyOf'))
        if variants and nullable(definition) is None:
            if any('$ref' not in value for value in variants):
                raise ValueError(f'Name union members before generation: {name}')
            ts.append(f'export type {name} = ' + ' | '.join(kind(v, 'ts') for v in variants) + ';')
            py.append(f'{name} = ' + ' | '.join(kind(v, 'py') for v in variants))
            rs.extend([derive, '#[serde(untagged)]', f'pub enum {name} {{'])
            for value in variants:
                member = kind(value, 'rs')
                rs.append(f'    {member}(Box<{member}>),')
            rs.append('}')
        elif 'properties' in definition:
            required = set(definition['required'])
            ts.append(f'export type {name} = {{')
            py.append(f'class {name}(TypedDict):')
            rs.extend([derive, '#[serde(deny_unknown_fields)]', f'pub struct {name} {{'])
            for key, value in definition['properties'].items():
                ts.append(f'  {key}{"" if key in required else "?"}: {kind(value, "ts")};')
                pytype = kind(value, 'py')
                py.append(f'    {key}: {pytype if key in required else f"NotRequired[{pytype}]"}')
                rstype = kind(value, 'rs')
                if key not in required:
                    rstype = f'Option<{rstype}>'
                rs.append(f'    pub {"r#type" if key == "type" else key}: {rstype},')
            ts.append('};')
            if not definition['properties']:
                py.append('    pass')
            rs.append('}')
        elif 'enum' in definition:
            ts.append(f'export type {name} = {kind(definition, "ts")};')
            py.append(f'{name} = {kind(definition, "py")}')
            rs.extend([derive, f'pub enum {name} {{'])
            for value in definition['enum']:
                rs.extend([f'    #[serde(rename = {json.dumps(value)})]', f'    {pascal(value)},'])
            rs.append('}')
        else:
            ts.append(f'export type {name} = {kind(definition, "ts")};')
            py.append(f'{name} = {kind(definition, "py")}')
            rs.append(f'pub type {name} = {kind(definition, "rs")};')
    rust = subprocess.run(['rustfmt', '--emit', 'stdout', '--edition', '2024'], input='\n'.join(rs) + '\n', text=True, check=True, capture_output=True).stdout
    return {'protocol.ts': '\n'.join(ts) + '\n', 'protocol.py': '\n'.join(py) + '\n', 'protocol.rs': rust}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    schema = json.loads(SOURCE.read_text())
    outputs = {GENERATED / name: contents for name, contents in generate(schema).items()}
    spec = json.loads((ROOT / 'engineering-contract-v1.json').read_text())
    policy = {'protocol_version': spec['protocol']['version'], **spec['runtime']['defaults']}
    outputs[GENERATED / 'runtime.json'] = json.dumps(policy, indent=2) + '\n'
    manifest = json.loads((ROOT / 'contracts/contract.json').read_text())
    for operation in manifest['operations']:
        for direction in ['input', 'output']:
            projection = {'$schema': schema['$schema'], '$id': f'urn:scopehub:{operation["name"]}:{direction}:1.0',
                          '$comment': 'Generated projection; edit protocol.schema.json.',
                          '$ref': f'{schema["$id"]}#/$defs/{operation[direction]}'}
            path = ROOT / 'contracts/schemas/operations' / f'{operation["name"]}.{direction}.schema.json'
            outputs[path] = json.dumps(projection, indent=2) + '\n'
    stale = [str(path.relative_to(ROOT)) for path, content in outputs.items() if not path.exists() or path.read_text() != content]
    existing = set(GENERATED.glob('*')) | set((ROOT / 'contracts/schemas/operations').glob('*.json'))
    orphaned = {p for p in existing if p.is_file() and p not in outputs}
    if args.check:
        if stale or orphaned:
            raise SystemExit('Generated contract drift: ' + ', '.join(stale + [str(p.relative_to(ROOT)) for p in orphaned]))
    else:
        for path in orphaned:
            path.unlink()
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8', newline='\n')
    print(json.dumps({'generated_files': len(outputs), 'check': args.check, 'status': 'passed'}))


if __name__ == '__main__':
    main()
