import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspect } from 'node:util';
import { runHandoffProbe } from './workspace_test_handoff_acl.mjs';

const sentinel = 'synthetic-handoff-private-marker';
const sid = 'S-1-5-21-123-456-789-1011';
const env = { ...process.env, SCOPEHUB_TEST_PROBE_MARKER: sentinel };
const script = body => ['-e', body, sentinel, sid];

async function fails(executable, args, expected, options = {}) {
  const failure = await runHandoffProbe(executable, args, { env, ...options }).then(() => null, error => error);
  assert.ok(failure instanceof Error, 'Expected a subprocess failure.');
  const diagnostic = inspect(failure, { showHidden: true });
  assert.ok(!diagnostic.includes(sentinel) && !diagnostic.includes(sid) && diagnostic.length < 2048,
    'A subprocess failure exposed synthetic inputs or emitted an unbounded diagnostic.');
  assert.ok(failure.message === expected, 'Expected the safe subprocess disposition: ' + expected);
}

const childFails = (body, expected, options) => fails(process.execPath, script(body), expected, options);

export const handoffProbeCases = [
  { name: 'success', run: () => runHandoffProbe(process.execPath, script('process.exit(0)')) },
  { name: 'startup failure', run: () => fails(join(tmpdir(), `${sentinel}-${randomUUID()}`), [],
    'subprocess startup failed (ENOENT)') },
  { name: 'deadline expiry', run: () => childFails('setTimeout(() => {}, 30000)',
    'subprocess deadline exceeded', { timeout: 400 }) },
  { name: 'termination by signal', unixOnly: true, run: () => childFails("process.kill(process.pid, 'SIGTERM')",
    'subprocess terminated by SIGTERM') },
  { name: 'stdout buffer exhaustion', run: () => childFails("process.stdout.write(process.argv[1].repeat(4096))",
    'subprocess output limit exceeded', { maxBuffer: 256 }) },
  { name: 'stderr buffer exhaustion', run: () => childFails("process.stderr.write(process.argv[2].repeat(4096))",
    'subprocess output limit exceeded', { maxBuffer: 256 }) },
  { name: 'a declared ACL-stage exit', run: () => childFails("console.error(process.argv[1]); process.exit(58)",
    'unexpected ACL trustee') },
  { name: 'an undeclared nonzero exit', run: () => childFails("console.error(process.env.SCOPEHUB_TEST_PROBE_MARKER); process.exit(7)",
    'subprocess exited with code 7') },
];
