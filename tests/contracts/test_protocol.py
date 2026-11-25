import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from worker_runtime.protocol import (
    MAX_MESSAGE_BYTES,
    SCHEMA,
    ProtocolError,
    decode_extended_line,
    decode_line,
    encode_extended_message,
    encode_message,
)

ROOT = Path(__file__).resolve().parents[2]


class ProtocolTests(unittest.TestCase):
    def test_shared_vectors(self):
        review_envelopes = set()
        for example in json.loads((ROOT / 'tests/fixtures/protocol-cases.json').read_text()):
            with self.subTest(name=example['name']):
                wire = example['wire'].encode()
                if not example['valid']:
                    for decode in (decode_line, decode_extended_line):
                        with self.assertRaises(ProtocolError):
                            decode(wire)
                    continue

                expected = json.loads(wire)
                value = decode_extended_line(wire)
                self.assertEqual(value, expected)
                self.assertEqual(decode_extended_line(encode_extended_message(value)), value)
                if 'expected_timestamp' in example:
                    self.assertIs(type(value['timestamp_ms']), int)
                    self.assertEqual(value['timestamp_ms'], example['expected_timestamp'])

                if ('review_declarations' in (expected.get('payload') or {})
                        or 'declaration_conflicts' in (expected.get('result') or {})):
                    review_envelopes.add((expected['operation'], expected['type']))
                    with self.assertRaisesRegex(ProtocolError, '^INVALID_ARGUMENT$'):
                        decode_line(wire)
                else:
                    original = decode_line(wire)
                    self.assertEqual(original, expected)
                    self.assertEqual(decode_line(encode_message(original)), original)

        self.assertEqual(review_envelopes, {
            (operation, kind)
            for operation in ('market.readLocalImportSourceRow', 'market.readLocalFolderSourceRow')
            for kind in ('request', 'response')
        })

    def test_extended_review_retains_newline_inclusive_frame_limit(self):
        examples = json.loads((ROOT / 'tests/fixtures/protocol-cases.json').read_text())
        wire = next(example['wire'].encode() for example in examples
                    if example['name'] == '1.1 frozen import source row attributes a declaration conflict')
        body = wire.removesuffix(b'\n')
        exact = body.ljust(MAX_MESSAGE_BYTES - 2) + b'\r\n'
        self.assertEqual(decode_extended_line(exact), decode_extended_line(wire))
        with self.assertRaisesRegex(ProtocolError, '^RESOURCE_LIMIT$'):
            decode_extended_line(exact + b' ')
        unknown = wire.replace(b'market.readLocalImportSourceRow', b'market.unknown')
        for invalid in (unknown, wire + b'\n', b'\xff\n'):
            with self.assertRaisesRegex(ProtocolError, '^INVALID_ARGUMENT$'):
                decode_extended_line(invalid)

    def test_oversize_and_invalid_utf8(self):
        for wire in [b'x' * (MAX_MESSAGE_BYTES + 1), b'\xff\n']:
            with self.assertRaises(ProtocolError):
                decode_line(wire)

    def test_private_workspace_vectors_and_worker_separation(self):
        registry = Registry().with_resource(SCHEMA['$id'], Resource.from_contents(SCHEMA))
        fixtures = ROOT / 'tests/fixtures'
        names = json.loads((fixtures / 'private-workspace-protocol-index.json').read_text(encoding='utf-8'))
        examples = [case for name in names for case in json.loads((fixtures / name).read_text(encoding='utf-8'))]
        for example in examples:
            with self.subTest(name=example['name']):
                file = ROOT / 'contracts/schemas/operations' / f'{example["operation"]}.v1.1.{example["direction"]}.schema.json'
                projection = json.loads(file.read_text())
                validator = Draft202012Validator(projection, registry=registry, format_checker=FormatChecker())
                self.assertEqual(validator.is_valid(example['message']), example['valid'])
                wire = json.dumps(example['message']).encode()
                if example.get('shared_fault'):
                    self.assertEqual(decode_line(wire), example['message'])
                else:
                    with self.assertRaises(ProtocolError):
                        decode_line(wire)

    def test_every_schema_compiles_without_network(self):
        registry = Registry().with_resource(SCHEMA['$id'], Resource.from_contents(SCHEMA))
        manifest = json.loads((ROOT / 'contracts/contract.json').read_text())
        for operation in manifest['operations']:
            for direction in ['input', 'output']:
                path = ROOT / 'contracts/schemas/operations' / f'{operation["name"]}.{direction}.schema.json'
                projection = json.loads(path.read_text())
                Draft202012Validator.check_schema(projection)
                self.assertEqual(projection['$ref'], f'{SCHEMA["$id"]}#/$defs/{operation[direction]}')
                validator = Draft202012Validator(projection, registry=registry)
                list(validator.iter_errors(None))
        Draft202012Validator.check_schema(SCHEMA)

    def test_versioned_discovery_retains_legacy_payloads_and_selects_the_exact_command(self):
        registry = Registry().with_resource(SCHEMA['$id'], Resource.from_contents(SCHEMA))
        manifest = json.loads((ROOT / 'contracts/contract.json').read_text())
        examples = {case['name']: json.loads(case['wire']) for case in json.loads(
            (ROOT / 'tests/fixtures/protocol-cases.json').read_text()) if case.get('valid')}
        for operation in manifest['operations']:
            for version, binding in operation.get('wire_bindings', {}).items():
                for direction, definition in binding.items():
                    file = ROOT / 'contracts/schemas/operations' / f'{operation["name"]}.v{version}.{direction}.schema.json'
                    self.assertEqual(json.loads(file.read_text())['$ref'], f'{SCHEMA["$id"]}#/$defs/{definition}')
        def validates(name, direction, message):
            file = ROOT / 'contracts/schemas/operations' / f'{name}.v1.1.{direction}.schema.json'
            return Draft202012Validator(json.loads(file.read_text()), registry=registry).is_valid(message)
        self.assertTrue(validates('chart.addAnnotation','input',examples['1.1 scoped annotation']))
        self.assertTrue(validates('chart.addAnnotation','output',examples['1.1 committed annotation supplies undo identity']))
        self.assertFalse(validates('chart.setStyle','input',examples['1.1 scoped annotation']))
        self.assertFalse(validates('chart.setStyle','output',examples['1.1 committed annotation supplies undo identity']))
        self.assertTrue(validates('indicator.runPreview','input',examples['1.1 frozen indicator input']))
        self.assertTrue(validates('indicator.acceptVersion','input',examples['1.1 immutable preview acceptance']))
        self.assertTrue(validates('workspace.readChartProjection','output',examples['1.1 only permitted quote field exported']))
        self.assertFalse(validates('chart.addAnnotation','input',examples['1.1 human confirms a core-issued challenge']))


if __name__ == '__main__':
    unittest.main()
