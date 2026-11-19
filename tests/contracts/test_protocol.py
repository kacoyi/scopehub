import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from worker_runtime.protocol import MAX_MESSAGE_BYTES, SCHEMA, ProtocolError, decode_line, encode_message

ROOT = Path(__file__).resolve().parents[2]


class ProtocolTests(unittest.TestCase):
    def test_shared_vectors(self):
        for example in json.loads((ROOT / 'tests/fixtures/protocol-cases.json').read_text()):
            with self.subTest(name=example['name']):
                if example['valid']:
                    value = decode_line(example['wire'].encode())
                    self.assertEqual(decode_line(encode_message(value)), value)
                    self.assertEqual(value, json.loads(example['wire']))
                    if 'expected_timestamp' in example:
                        self.assertIs(type(value['timestamp_ms']), int)
                        self.assertEqual(value['timestamp_ms'], example['expected_timestamp'])
                else:
                    with self.assertRaises(ProtocolError):
                        decode_line(example['wire'].encode())

    def test_oversize_and_invalid_utf8(self):
        for wire in [b'x' * (MAX_MESSAGE_BYTES + 1), b'\xff\n']:
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
