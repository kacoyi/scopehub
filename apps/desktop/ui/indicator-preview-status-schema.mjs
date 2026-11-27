import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';
import standaloneCode from 'ajv/dist/standalone/index.js';
import addFormats from 'ajv-formats';

const moduleId = 'virtual:indicator-preview-status';
const resolvedId = `\0${moduleId}`;
const protocolPath = fileURLToPath(new URL('../../../contracts/schemas/protocol.schema.json', import.meta.url));
const unicodeLengthRequire = 'require("ajv/dist/runtime/ucs2length").default';

function compileStatusSchema(protocol) {
  const reference = name => ({ $ref: `${protocol.$id}#/$defs/${name}` });
  const nullable = name => ({ anyOf: [reference(name), { type: 'null' }] });
  const branch = (status, fields = {}) => {
    const properties = { status: { const: status }, task_id: reference('Uuid'), ...fields };
    return { type: 'object', properties, required: Object.keys(properties), additionalProperties: false };
  };
  const status = {
    oneOf: [
      branch('running'),
      branch('ready', {
        event: reference('PreviewReady11'),
        source_snapshot: reference('FrozenSnapshot'),
        resolved_parameters: reference('Metadata'),
        warmup_from_ms: nullable('Timestamp'),
        calendar_hash: nullable('Hash'),
      }),
      branch('terminal', { event: reference('TaskTerminalEvent11') }),
    ],
  };
  const ajv = new Ajv2020({ strict: true, ownProperties: true, messages: false, code: { source: true, esm: true } });
  addFormats(ajv);
  ajv.addSchema(protocol);
  const validator = standaloneCode(ajv, ajv.compile(status)).replaceAll(unicodeLengthRequire, 'unicodeLength');
  if (/\brequire\s*\(/.test(validator)) throw new Error('Unsupported runtime import in the indicator preview status validator.');
  // Ajv emits this CommonJS helper reference even in standalone ESM mode.
  return `import unicodeLengthModule from 'ajv/dist/runtime/ucs2length.js';
const unicodeLength = unicodeLengthModule.default ?? unicodeLengthModule;
${validator}`;
}

export function indicatorPreviewStatusSchema() {
  return {
    name: 'scopehub-indicator-preview-status-schema',
    resolveId(id) {
      if (id === moduleId) return resolvedId;
    },
    load(id) {
      if (id !== resolvedId) return;
      this.addWatchFile(protocolPath);
      return compileStatusSchema(JSON.parse(readFileSync(protocolPath, 'utf8')));
    },
  };
}
