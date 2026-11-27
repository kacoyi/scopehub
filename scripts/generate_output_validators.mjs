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

// Ajv's standalone ESM still emits CommonJS references for these helpers.
// Convert them at generation so neither the browser nor Tauri needs eval or require.
const imports = new Map([
  ['ajv/dist/runtime/ucs2length', 'unicodeLengthModule'],
  ['ajv-formats/dist/formats', 'formatsModule'],
]);

function generateValidators(moduleName, description, types) {
  const exports = Object.fromEntries(types.map(type => [`is${type}`, `${schema.$id}#/$defs/${type}`]));
  const declarations = types.map(type => `export function is${type}(value: unknown): value is ${type};`);
  const used = new Set();
  const body = standalone(ajv, exports).replace(/\brequire\("([^"]+)"\)/g, (_, path) => {
    const binding = imports.get(path);
    if (!binding) throw new Error(`Unsupported standalone validator dependency ${path}`);
    used.add(path);
    return binding;
  });
  const moduleImports = [...used].map(path => `import ${imports.get(path)} from '${path}.js';`);
  const header = `// Generated from ${description}. Do not edit.`;
  return {
    [`${moduleName}.mjs`]: [header, ...moduleImports, body.trimEnd(), ''].join('\n'),
    [`${moduleName}.d.mts`]: [header, `import type { ${types.join(', ')} } from './protocol';`, ...declarations, ''].join('\n'),
  };
}

const marketOutputs = ['instrument.search', 'market.readHistoryPage']
  .map(name => operations.find(operation => operation.name === name).output);
process.stdout.write(JSON.stringify({
  ...generateValidators('market-output-validators', 'canonical market operation outputs', marketOutputs),
  ...generateValidators('decision-note-output-validators', 'canonical committed decision-note deletion outcome',
    ['DecisionNoteDeleteCommitted11']),
}));
