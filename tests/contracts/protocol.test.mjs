import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const schema = read('../../contracts/schemas/protocol.schema.json');
const policy = read('../../contracts/generated/runtime.json');
const ajv = new Ajv2020({ strict: true, allErrors: false });
addFormats(ajv);
const validate = ajv.compile(schema);

function finite(value) {
  if (typeof value === 'number') return Number.isFinite(value);
  if (value && typeof value === 'object') return Object.values(value).every(finite);
  return true;
}

function accepts(wire) {
  if (Buffer.byteLength(wire, 'utf8') > policy.max_message_bytes) return false;
  const body = wire.replace(/\r?\n$/, '');
  if (/[\r\n]/.test(body)) return false;
  try {
    const message = JSON.parse(body);
    return finite(message) && validate(message);
  } catch {
    return false;
  }
}

for (const example of read('../fixtures/protocol-cases.json')) {
  test(example.name, () => assert.equal(accepts(example.wire), example.valid));
}

test('reject oversized frames before parsing', () => {
  assert.equal(accepts(' '.repeat(policy.max_message_bytes + 1)), false);
});

test('operation schema projections resolve locally and match the registry', () => {
  const manifest = read('../../contracts/contract.json');
  for (const op of manifest.operations) {
    for (const direction of ['input', 'output']) {
      const projected = read(`../../contracts/schemas/operations/${op.name}.${direction}.schema.json`);
      assert.equal(projected.$ref, `${schema.$id}#/$defs/${op[direction]}`);
      assert.equal(typeof ajv.compile(projected), 'function');
    }
  }
});
