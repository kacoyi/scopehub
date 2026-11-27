import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { inspect } from 'node:util';
import { assertUnsuitableWindowsHandoffsRejected, assertWindowsLeafAclMatrix, assertWindowsPrivateHandoff, runHandoffProbe } from './workspace_test_handoff_acl.mjs';

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

function expectedLeafStages(profile) {
  // Expected results for these synthetic controls are independent of the
  // script's supplied expectedStage or diagnostic strings.
  const effective = {
    allow: 58, deny: 55, 'callback-allow': 54, 'callback-deny': 54,
    'object-allow': 54, 'object-deny': 54, 'object-callback-allow': 54, 'object-callback-deny': 54,
  };
  return new Map(Object.entries(effective).flatMap(([layout, stage]) => [
    [join(profile, `synthetic-acl-${layout}-zero-mask.txt`), 0],
    [join(profile, `synthetic-acl-${layout}-inherit-only.txt`), 0],
    [join(profile, `synthetic-acl-${layout}-effective.txt`), stage],
  ]));
}

async function verifyLeafCalibrationDiagnostics() {
  await withProbeMarker(async marker => {
    const profile = dirname(marker);
    const expected = expectedLeafStages(profile);
    const calls = [];
    const launch = (executable, args, options) => {
      const path = options.env.SCOPEHUB_TEST_HANDOFF;
      assert.ok(expected.has(path));
      calls.push(path);
      return runHandoffProbe(process.execPath, ['-e', `process.exit(${expected.get(path)})`], options);
    };
    const observe = [];
    const reportPreparation = observation => observe.push(observation);
    const prepare = (record, code, stdout = sid) => async (check, root, fixture, exitStages, observeStdout) => {
      assert.equal(root, profile);
      const { controls } = JSON.parse(fixture);
      assert.deepEqual(new Map(controls.map(({ path, expectedStage }) => [path, expectedStage])), expected);
      assert.equal(controls.length, 24);
      assert.equal((await readdir(profile)).length, controls.length);
      // The control requires the marker to reach the actual captured pipes.
      const body = "const fs = require('node:fs');"
        + 'fs.writeSync(1, process.argv[1]);fs.writeSync(2, process.argv[2]);'
        + 'process.exitCode = Number(process.argv[3]);';
      return runHandoffProbe(process.execPath, ['-e', body, stdout, record, String(code)], { exitStages, observeStdout });
    };

    const timing = 'SCOPEHUB_ACL_TIMING script 0\r\nSCOPEHUB_ACL_TIMING helper 12\nSCOPEHUB_ACL_TIMING matrix 34\n';
    await assertWindowsLeafAclMatrix(profile, { prepareFixture: prepare('', 0, timing), launch, reportPreparation });
    const { elapsed_ms, ...observation } = observe.pop();
    assert.ok(Number.isInteger(elapsed_ms) && elapsed_ms >= 0);
    assert.deepEqual(observation, { outcome: 'prepared', script_elapsed_ms: { script: 0, helper: 12, matrix: 34 } });
    assert.deepEqual(calls, [...expected.keys()]);
    assert.deepEqual(await readdir(profile), []);

    const unavailable = 'synthetic leaf ACL calibration diagnostic unavailable';
    const refused = [
      ['SCOPEHUB_ACL_FIXTURE 19 raw-revision\r\n', 'case object-callback-allow-inherit-only: stored ACL revision differs'],
      ['SCOPEHUB_ACL_FIXTURE 0 raw-flags\n', 'case allow-zero-mask: stored ACE flags differ',
        `SCOPEHUB_ACL_TIMING script 0\n${sid}\n`],
      ['SCOPEHUB_ACL_FIXTURE 0 fixture-baseline\n', 'case allow-zero-mask: starting handle and named DACL views could not be reconciled'],
      ['SCOPEHUB_ACL_FIXTURE 0 submitted-ace\n', 'case allow-zero-mask: submitted descriptor is missing the exact first fixture ACE'],
      ['SCOPEHUB_ACL_FIXTURE 0 submitted-inheritance\n', 'case allow-zero-mask: submitted descriptor changed the required inherited user grant'],
      ['SCOPEHUB_ACL_FIXTURE 0 raw-absent\n', 'case allow-zero-mask: stored DACL has no ACE for the fixture trustee'],
      ['SCOPEHUB_ACL_FIXTURE 0 raw-inheritance\n', 'case allow-zero-mask: stored DACL changed the required inherited user grant'],
      ['SCOPEHUB_ACL_FIXTURE 23 legacy-stage-0\n', 'case object-callback-deny-effective: legacy managed stage differs (expected 54, observed 0)'],
      ['SCOPEHUB_ACL_FIXTURE 24 raw-type\n', unavailable],
      ['SCOPEHUB_ACL_FIXTURE 0 unknown-reason\n', unavailable],
      [`SCOPEHUB_ACL_FIXTURE 0 raw-flags\n${marker} ${sid}\n`, unavailable],
      ['', unavailable, 'SCOPEHUB_ACL_FIXTURE 0 raw-absent\n'],
    ];
    for (const [record, disposition, stdout] of refused) {
      const blocked = assertWindowsLeafAclMatrix(profile, {
        prepareFixture: prepare(record, 4, stdout),
        launch: () => assert.fail('Failed preparation must stop before any witness launch.'),
        reportPreparation,
      });
      await assert.rejects(blocked, {
        message: `Could not verify the synthetic leaf ACL matrix (${disposition}).`,
      });
      assert.deepEqual(observe.pop().script_elapsed_ms, {});
      assert.deepEqual(await readdir(profile), []);
    }
  });
}

async function verifyLeafPreparationDeadline() {
  await withProbeMarker(async marker => {
    const profile = dirname(marker);
    const prepareFixture = async (_check, _root, fixture, exitStages, observeStdout) => {
      const { controls } = JSON.parse(fixture);
      const body = `const fs = require('node:fs');
        fs.writeFileSync(process.env.SCOPEHUB_TEST_HANDOFF, 'started');
        fs.writeSync(1, 'SCOPEHUB_ACL_TIMING script 0\\nSCOPEHUB_ACL_TIMING helper ');
        setTimeout(() => {}, 30000);`;
      await runHandoffProbe(process.execPath, ['-e', body], {
        env: { ...env, SCOPEHUB_TEST_HANDOFF: controls[0].path }, timeout: 1500, exitStages, observeStdout,
      });
    };
    const observations = [];
    await assertSafeFixtureDiagnostic(assertWindowsLeafAclMatrix(profile, {
      prepareFixture,
      launch: () => assert.fail('Expired preparation must stop before any witness launch.'),
      reportPreparation: observation => observations.push(observation),
    }), profile, 'Could not verify the synthetic leaf ACL matrix (subprocess deadline exceeded).');
    assert.equal(observations.length, 1);
    const { outcome, script_elapsed_ms } = observations[0];
    assert.equal(outcome, 'failed');
    assert.deepEqual(script_elapsed_ms, { script: 0 });
    assert.deepEqual(await readdir(profile), []);
  });
}

async function assertSafeFixtureDiagnostic(request, profile, message) {
  const error = await request.then(() => null, failure => failure);
  assert.ok(error instanceof Error, 'Expected a fixture error.');
  assert.equal(error.message, message);
  assert.equal(error.cause, undefined);
  const exposed = inspect(error, { showHidden: true });
  assert.ok(!exposed.includes(profile) && !exposed.includes(sentinel) && !exposed.includes(sid));
  console.info('Synthetic handoff fixture diagnostic', JSON.stringify({ message: error.message, bytes: Buffer.byteLength(error.message) }));
}

function leafMatrixMustStop() {
  assert.fail('A bootstrap or readback failure must stop before any further operation.');
}

async function verifyLeafBootstrapDiagnostics() {
  await withProbeMarker(async profile => {
    const hooks = { prepareFixture: leafMatrixMustStop, launch: leafMatrixMustStop };
    await assertSafeFixtureDiagnostic(assertWindowsLeafAclMatrix(profile, hooks), profile,
      'Synthetic ACL fixture allow-zero-mask failed (fixture creation).');

    await assertSafeFixtureDiagnostic(assertUnsuitableWindowsHandoffsRejected(profile), profile,
      'Synthetic ACL fixture broad failed (fixture creation).');

    // The second exclusive create fails. Matrix cleanup owns only the first
    // path and must leave the pre-existing file with its original bytes.
    const existing = join(dirname(profile), 'synthetic-acl-allow-inherit-only.txt');
    await writeFile(existing, sentinel);
    await assertSafeFixtureDiagnostic(assertWindowsLeafAclMatrix(dirname(profile), hooks), dirname(profile),
      'Synthetic ACL fixture allow-inherit-only failed (fixture creation).');
    assert.deepEqual(await readdir(dirname(profile)), ['synthetic-acl-allow-inherit-only.txt']);
    assert.equal(await readFile(existing, 'utf8'), sentinel);
  });
}

async function verifyLeafCleanupDiagnostics(failReadback) {
  await withProbeMarker(async marker => {
    const profile = dirname(marker);
    const first = 'synthetic-acl-allow-zero-mask.txt';
    const firstPath = join(profile, first);
    const expected = expectedLeafStages(profile);
    const calls = [];
    const obstructRemoval = async () => {
      // An owned empty fixture replaced by an owned directory makes the
      // ordinary nonrecursive removal fail on every supported host.
      await rm(firstPath);
      await mkdir(firstPath);
    };
    const prepareFixture = async (_check, _profile, _fixture, exitStages) => {
      if (!failReadback) return;
      await obstructRemoval();
      const log = 'SCOPEHUB_ACL_FIXTURE 19 raw-revision\r\n';
      return runHandoffProbe(process.execPath, ['-e',
        'require("node:fs").writeSync(2, process.argv[1]); process.exitCode = 4;', log], { exitStages });
    };
    const launch = async (_executable, _args, options) => {
      const path = options.env.SCOPEHUB_TEST_HANDOFF;
      assert.ok(expected.has(path));
      calls.push(path);
      try {
        await runHandoffProbe(process.execPath, ['-e', `process.exit(${expected.get(path)})`], options);
      } finally {
        if (calls.length === expected.size) await obstructRemoval();
      }
    };

    const primary = failReadback
      ? 'Could not verify the synthetic leaf ACL matrix (case object-callback-allow-inherit-only: stored ACL revision differs). '
      : '';
    await assertSafeFixtureDiagnostic(assertWindowsLeafAclMatrix(profile, { prepareFixture, launch }), profile,
      primary + 'Synthetic ACL fixture allow-zero-mask failed (fixture cleanup).');
    assert.deepEqual(calls, failReadback ? [] : [...expected.keys()]);
    assert.deepEqual(await readdir(profile), [first], 'Every other owned fixture must be removed before rejection.');
  });
}

export const handoffProbeCases = [
  { name: 'bounded synthetic fixture creation and exclusive ownership', run: verifyLeafBootstrapDiagnostics },
  { name: 'synthetic fixture cleanup after a matching matrix', run: () => verifyLeafCleanupDiagnostics(false) },
  { name: 'first synthetic readback failure when cleanup also fails', run: () => verifyLeafCleanupDiagnostics(true) },
  { name: 'bounded synthetic leaf matrix calibration and fixture cleanup', run: verifyLeafCalibrationDiagnostics },
  { name: 'an interrupted leaf matrix before native witness comparison', run: verifyLeafPreparationDeadline },
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
