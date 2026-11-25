import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspect } from 'node:util';
import { assertWindowsPrivateHandoff, runHandoffProbe } from './workspace_test_handoff_acl.mjs';

const sentinel = 'synthetic-handoff-private-marker';
const sid = 'S-1-5-21-123-456-789-1011';
const env = { ...process.env, SCOPEHUB_TEST_PROBE_MARKER: sentinel };
const script = (body, ...args) => ['-e', body, sentinel, sid, ...args];

async function fails(executable, args, expected, options = {}) {
  const failure = await runHandoffProbe(executable, args, { env, ...options }).then(() => null, error => error);
  assert.ok(failure instanceof Error, 'Expected a subprocess failure.');
  const diagnostic = inspect(failure, { showHidden: true });
  assert.ok(!diagnostic.includes(sentinel) && !diagnostic.includes(sid),
    'A subprocess failure exposed synthetic inputs.');
  // Playwright adds its own JavaScript stack frames when it runs this case.
  // Check the bound of the emitted subprocess message independently.
  assert.ok(failure.message.length <= 120, 'The subprocess diagnostic exceeded its message bound.');
  assert.ok(failure.message === expected, 'Expected the safe subprocess disposition: ' + expected);
}

const childFails = (body, expected, options) => fails(process.execPath, script(body), expected, options);

async function withProbeMarker(run) {
  const directory = await mkdtemp(join(tmpdir(), 'scopehub-handoff-probe-'));
  try {
    await run(join(directory, 'witness'));
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function exitsWithHeldOutput(code) {
  await withProbeMarker(async marker => {
    // The direct child exits while a short-lived descendant still owns both
    // pipes. The witness makes the ordering observable without a wall-clock assertion.
    const holder = `setTimeout(() => {
      try { require('node:fs').writeFileSync(process.argv[1], 'finished'); } catch {}
    }, 1300)`;
    const body = `const holder = require('node:child_process').spawn(process.execPath,
      ['-e', ${JSON.stringify(holder)}, process.argv[3]], { stdio: ['ignore', 1, 2], windowsHide: true });
      holder.once('spawn', () => { holder.unref(); process.exit(${code}); });`;
    const args = script(body, marker);
    if (code === 0) {
      await runHandoffProbe(process.execPath, args, { timeout: 400 });
    } else {
      await fails(process.execPath, args, 'unexpected ACL trustee', { timeout: 400 });
    }
    assert.ok(!existsSync(marker), 'The finished child outcome waited for descendant stream closure.');
  });
}

async function reapsRunningChild() {
  await withProbeMarker(async marker => {
    const body = `process.on('SIGTERM', () => {});
      setTimeout(() => {}, 2000);
      require('node:fs').writeFileSync(process.argv[3], String(process.pid));`;
    await fails(process.execPath, script(body, marker), 'subprocess deadline exceeded', { timeout: 800 });
    const pid = Number(await readFile(marker, 'utf8').catch(() => ''));
    assert.ok(Number.isSafeInteger(pid) && pid > 0, 'The actual child must start before the deadline control.');
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, 'The timed-out child must be gone when the probe settles.');
  });
}

async function runPrivateHandoffWitness(exit) {
  await withProbeMarker(async handoff => {
    let calls = 0;
    const check = async (executable, args, options) => {
      calls += 1;
      assert.match(executable, /[\\/]target[\\/]debug[\\/]examples[\\/]windows_handoff_probe\.exe$/);
      assert.deepEqual(args, [], 'The handoff name must stay out of the command line.');
      assert.equal(options.env.SCOPEHUB_TEST_HANDOFF, handoff);
      assert.equal(options.timeout, undefined, 'Use the shared ten-second deadline.');
      // Portable launch control. The Windows workspace journey exercises the
      // compiled descriptor reader against the actual handoff and ACL fixtures.
      const body = "const fs = require('node:fs');"
        + "fs.writeFileSync(process.env.SCOPEHUB_TEST_HANDOFF, 'called');"
        + "process.exit(" + exit + ");";
      return runHandoffProbe(process.execPath, ['-e', body], options);
    };
    if (exit === 0) {
      await assertWindowsPrivateHandoff(handoff, check);
    } else {
      await assert.rejects(assertWindowsPrivateHandoff(handoff, check), {
        message: 'Private handoff ACL verification failed (unexpected ACL trustee).',
      });
    }
    assert.equal(calls, 1);
    assert.equal(await readFile(handoff, 'utf8'), 'called');
  });
}

export const handoffProbeCases = [
  { name: 'the independent private handoff witness accepts and rejects through the bounded launcher', run: async () => {
    await runPrivateHandoffWitness(0);
    await runPrivateHandoffWitness(58);
  } },
  { name: 'success with retained output pipes', run: () => exitsWithHeldOutput(0) },
  { name: 'a declared exit with retained output pipes', run: () => exitsWithHeldOutput(58) },
  { name: 'reaping a live child at its deadline', run: reapsRunningChild },
  { name: 'success', run: () => runHandoffProbe(process.execPath, script('process.exit(0)')) },
  { name: 'startup failure', run: () => fails(join(tmpdir(), `${sentinel}-${randomUUID()}`), [],
    'subprocess startup failed (ENOENT)') },
  { name: 'deadline expiry', run: () => childFails('setTimeout(() => {}, 30000)',
    'subprocess deadline exceeded', { timeout: 400 }) },
  { name: 'termination by signal', unixOnly: true, run: () => childFails("process.kill(process.pid, 'SIGTERM')",
    'subprocess terminated by SIGTERM') },
  { name: 'stdout buffer exhaustion', run: () => childFails("require('node:fs').writeSync(1, process.argv[1].repeat(4096))",
    'subprocess output limit exceeded', { maxBuffer: 256 }) },
  { name: 'stderr buffer exhaustion', run: () => childFails("require('node:fs').writeSync(2, process.argv[2].repeat(4096))",
    'subprocess output limit exceeded', { maxBuffer: 256 }) },
  { name: 'a declared ACL-stage exit', run: () => childFails("console.error(process.argv[1]); process.exit(58)",
    'unexpected ACL trustee') },
  { name: 'an undeclared nonzero exit', run: () => childFails("console.error(process.env.SCOPEHUB_TEST_PROBE_MARKER); process.exit(7)",
    'subprocess exited with code 7') },
];
