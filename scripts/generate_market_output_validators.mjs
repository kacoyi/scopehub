import { readFileSync } from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import standalone from 'ajv/dist/standalone/index.js';
import addFormats from 'ajv-formats';

const read = path => JSON.parse(readFileSync(new URL(`../${path}`, import.meta.url), 'utf8'));
const schema = read('contracts/schemas/protocol.schema.json');
const { operations } = read('contracts/contract.json');
const ajv = new Ajv2020({
  inlineRefs: false, ownProperties: true, messages: false,
  code: { source: true, esm: true, lines: true },
});
addFormats(ajv);
ajv.addSchema(schema);

const exports = new Map();
const declarations = [];
for (const name of ['instrument.search', 'market.readHistoryPage']) {
  const { output } = operations.find(operation => operation.name === name);
  const check = `is${output}`;
  exports.set(check, `${schema.$id}#/$defs/${output}`);
  declarations.push(`export function ${check}(value: unknown): value is ${output};`);
}

// Ajv's standalone ESM still emits CommonJS references for these helpers.
// Convert them at generation so neither the browser nor Tauri needs eval or require.
const imports = new Map([
  ['ajv/dist/runtime/ucs2length', 'unicodeLengthModule'],
  ['ajv-formats/dist/formats', 'formatsModule'],
]);
const used = new Set();
const body = standalone(ajv, Object.fromEntries(exports)).replace(/\brequire\("([^"]+)"\)/g, (_, path) => {
  const binding = imports.get(path);
  if (!binding) throw new Error(`Unsupported standalone validator dependency ${path}`);
  used.add(path);
  return binding;
});
const moduleImports = [...used].map(path => `import ${imports.get(path)} from '${path}.js';`);
const types = [...exports.keys()].map(check => check.slice(2)).join(', ');
const header = '// Generated from canonical market operation outputs. Do not edit.';
process.stdout.write(JSON.stringify({
  'market-output-validators.mjs': [header, ...moduleImports, body.trimEnd(), ''].join('\n'),
  'market-output-validators.d.mts': [header, `import type { ${types} } from './protocol';`, ...declarations, ''].join('\n'),
}));
