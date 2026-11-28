import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { chmod, open, readFile, rm } from 'node:fs/promises';
import { constants } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const witness = fileURLToPath(new URL('../../target/debug/examples/windows_handoff_probe.exe', import.meta.url));
const fixtureHelper = 'handoff-fixture-acl.exe';
const deadlineExceeded = 'subprocess deadline exceeded';
const stages = {
  51: 'file type',
  52: 'directory owner',
  53: 'file owner and DACL',
  54: 'inherited access',
  55: 'effective read/write',
  56: 'directory descriptor read',
  57: 'file descriptor read',
  58: 'unexpected ACL trustee',
  59: 'current user identity',
};

const startupCodes = new Set(['EACCES', 'EAGAIN', 'EINVAL', 'EMFILE', 'ENFILE', 'ENOENT', 'ENOEXEC', 'ENOMEM', 'ENOTDIR', 'EPERM']);

function probeFailure(error, child, exitStages) {
  if (error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') return 'subprocess output limit exceeded';
  if (Number.isInteger(error.code)) {
    const stage = exitStages[error.code];
    return typeof stage === 'function' ? stage(error.stderr) : stage ?? `subprocess exited with code ${error.code}`;
  }
  // Node can set error.killed when a deadline only closes inherited pipes.
  // child.killed means a signal was actually sent to the direct child.
  if (child?.killed) return deadlineExceeded;
  if (error.signal && Object.hasOwn(constants.signals, error.signal)) {
    return `subprocess terminated by ${error.signal}`;
  }
  if (startupCodes.has(error.code)) return `subprocess startup failed (${error.code})`;
  return 'subprocess startup failed';
}

export async function runHandoffProbe(executable, args, {
  timeout = 10_000, maxBuffer = 8192, env, exitStages = stages, readProgress,
  incompleteProgress = 'synthetic leaf ACL preparation progress incomplete',
} = {}) {
  let child;
  let stdout;
  try {
    // Let execFile close the pipes at its deadline and wait for the direct child.
    const execution = exec(executable, args, { timeout, killSignal: 'SIGKILL', maxBuffer, windowsHide: true, env });
    child = execution.child;
    ({ stdout } = await execution);
  } catch (error) {
    // execFile errors can carry the command, stdout and stderr. Retain only
    // the enumerated outcome, without chaining the original error.
    const failure = probeFailure(error, child, exitStages);
    const progress = readProgress?.(error.stdout ?? '', true);
    if (failure === deadlineExceeded && readProgress) {
      const detail = progress ? `last confirmed ${progress.last}` : 'no preparation stage confirmed';
      throw new Error(`${failure}, ${detail}`);
    }
    throw new Error(failure);
  }
  if (!readProgress) return;
  const progress = readProgress(stdout);
  if (!progress?.complete) throw new Error(incompleteProgress);
  return progress;
}

function prepareAclFixture(check, handoff, fixture, exitStages, readProgress) {
  const systemRoot = process.env.SystemRoot;
  if (!systemRoot) throw new Error('subprocess startup failed (missing system root)');
  const powershell = join(systemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  return runHandoffProbe(powershell, ['-NoProfile', '-NonInteractive', '-NoLogo', '-Command', check], {
    env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff, SCOPEHUB_TEST_ACL_FIXTURE: fixture },
    exitStages,
    readProgress,
  });
}

export async function buildWindowsAclFixtureHelper(run) {
  const systemRoot = process.env.SystemRoot;
  if (!systemRoot) throw new Error('Could not build the synthetic ACL fixture helper (missing system root).');
  const compiler = join(systemRoot, 'Microsoft.NET/Framework64/v4.0.30319/csc.exe');
  const sources = ['workspace_test_handoff_prepare_acl.cs', 'workspace_test_handoff_leaf_acl.cs']
    .map(name => fileURLToPath(new URL(name, import.meta.url)));
  const started = performance.now();
  try {
    await runHandoffProbe(compiler, [
      '/nologo', '/target:exe', '/platform:anycpu', '/out:' + join(run, fixtureHelper), ...sources,
    ], { exitStages: {} });
  } catch (error) {
    throw new Error(`Could not build the synthetic ACL fixture helper (${error.message}).`);
  }
  console.info('Synthetic ACL fixture helper build', JSON.stringify({ elapsedMs: Math.round(performance.now() - started) }));
}

export async function assertWindowsPrivateHandoff(handoff, launch = runHandoffProbe) {
  try {
    await launch(witness, [], { env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff } });
  } catch (error) {
    throw new Error(`Private handoff ACL verification failed (${error.message}).`);
  }
}

function assertRejectedHandoff(path, stage, launch) {
  return assert.rejects(assertWindowsPrivateHandoff(path, launch), {
    message: `Private handoff ACL verification failed (${stages[stage]}).`,
  });
}

function aclFixtureFailure(name, stage) {
  return new Error(`Synthetic ACL fixture ${name} failed (${stage}).`);
}

async function cleanAclFixture({ name, path, restoreWrite }) {
  let failed = false;
  if (restoreWrite) {
    try { await chmod(path, 0o600); }
    catch { failed = true; }
  }
  try { await rm(path); }
  catch { failed = true; }
  if (failed) return aclFixtureFailure(name, 'fixture cleanup');
}

async function withAclFixtures(fixtures, verify) {
  const owned = [];
  let primary;
  try {
    for (const fixture of fixtures) {
      try {
        const file = await open(fixture.path, 'wx', 0o600);
        // Only the successful exclusive open establishes ownership. Claim
        // it before closing so a close error still sends it through cleanup.
        owned.push(fixture);
        await file.close();
      } catch {
        throw aclFixtureFailure(fixture.name, 'fixture creation');
      }
    }
    await verify();
  } catch (error) {
    primary = error;
  }

  const cleanup = (await Promise.all(owned.map(cleanAclFixture))).find(Boolean);
  if (primary && cleanup) throw new Error(`${primary.message} ${cleanup.message}`);
  if (primary || cleanup) throw primary ?? cleanup;
}

async function prepareUnsuitableAcl(path, fixture, stages, launchFixture) {
  const started = performance.now();
  let progress;
  let failure;
  try {
    const run = process.env.SCOPEHUB_WORKSPACE_TEST_RUN;
    if (!run) throw new Error('subprocess startup failed (missing test run directory)');
    await launchFixture(join(run, fixtureHelper), [], {
      env: { ...process.env, SCOPEHUB_TEST_HANDOFF: path, SCOPEHUB_TEST_ACL_FIXTURE: fixture },
      exitStages: { 1: 'fixture preparation' },
      incompleteProgress: 'synthetic ACL preparation progress incomplete',
      readProgress: (stdout, interrupted) => {
        progress = aclProgress(stdout, interrupted, stages.length, index => [undefined, stages[index]]);
        return progress;
      },
    });
  } catch (error) {
    failure = error.message;
    throw new Error(`Could not prepare the synthetic ${fixture} ACL fixture (${failure}).`);
  } finally {
    const elapsedMs = progress?.times ?? [];
    console.info('Synthetic unsuitable ACL fixture preparation', JSON.stringify({
      fixture, subprocessMs: Math.round(performance.now() - started), stages: stages.slice(0, elapsedMs.length),
      elapsedMs, failure: failure ?? null,
    }));
  }
}

export async function assertUnsuitableWindowsHandoffsRejected(profile, {
  launchFixture = runHandoffProbe, launch = runHandoffProbe,
} = {}) {
  const cases = [['broad', 'baseline-read', 58], ['explicit-only', 'explicit-dacl-ready', 54]];
  for (const [fixture, descriptorReady, stage] of cases) {
    const path = join(profile, `synthetic-acl-${fixture}.txt`);
    await withAclFixtures([{ name: fixture, path }], async () => {
      await prepareUnsuitableAcl(path, fixture,
        ['helper-entered', 'input-ready', descriptorReady, 'rule-added', 'dacl-written'], launchFixture);
      await assertRejectedHandoff(path, stage, launch);
    });
  }

  // Keep the inherited descriptor intact and make the effective write open fail.
  const readOnly = join(profile, 'synthetic-acl-read-only.txt');
  await withAclFixtures([{ name: 'read-only', path: readOnly, restoreWrite: true }], async () => {
    try { await chmod(readOnly, 0o400); }
    catch { throw aclFixtureFailure('read-only', 'fixture preparation'); }
    await assertRejectedHandoff(readOnly, 55, launch);
  });
}

const leafFailureReasons = {
  'fixture-start': 'could not read or construct the starting descriptor',
  'fixture-baseline': 'starting handle and named DACL views could not be reconciled',
  'fixture-inheritance': 'starting descriptor has no required inherited user grant',
  'submitted-ace': 'submitted descriptor is missing the exact first fixture ACE',
  'submitted-inheritance': 'submitted descriptor changed the required inherited user grant',
  'fixture-write': 'could not write the synthetic DACL',
  'raw-read': 'could not read the stored descriptor',
  'raw-parse': 'could not parse the stored descriptor',
  'raw-dacl': 'stored descriptor has no DACL',
  'raw-revision': 'stored ACL revision differs',
  'raw-compare': 'could not compare the stored ACE',
  'raw-duplicate': 'stored DACL contains more than one exact fixture ACE',
  'raw-absent': 'stored DACL has no ACE for the fixture trustee',
  'raw-ambiguous': 'stored ACE could not be isolated from the fixture trustee candidates',
  'raw-type': 'stored ACE type differs',
  'raw-flags': 'stored ACE flags differ',
  'raw-mask': 'stored ACE mask differs',
  'raw-object': 'stored ACE object fields differ',
  'raw-bytes': 'stored ACE binary form differs',
  'raw-inheritance': 'stored DACL changed the required inherited user grant',
  'legacy-read': 'could not compare the legacy managed descriptor',
};

function leafFailure(controls, stderr) {
  // Decode only the entire bounded line. Never include the child's text or
  // accept a case label from the child.
  const marker = /^SCOPEHUB_ACL_FIXTURE ([0-9]{1,2}) ([a-z0-9-]{1,24})\r?\n?$/u.exec(stderr);
  const fallback = 'synthetic leaf ACL calibration diagnostic unavailable';
  if (!marker || marker[0] !== stderr) return fallback;
  const control = controls[Number(marker[1])];
  if (!control) return fallback;
  const reason = marker[2];
  if (Object.hasOwn(leafFailureReasons, reason)) return `case ${control.name}: ${leafFailureReasons[reason]}`;
  const legacyStage = /^legacy-stage-(0|53|54|55|58)$/u.exec(reason);
  if (!legacyStage) return fallback;
  return `case ${control.name}: legacy managed stage differs (expected ${control.expectedStage}, observed ${legacyStage[1]})`;
}

const setupStages = ['script-entered', 'helper-loaded', 'matrix-ready'];
const rowStages = ['handle-read', 'baseline-matched', 'submission-checked', 'dacl-written', 'readback-checked', 'legacy-matched'];

function aclProgress(stdout, interrupted, count, positionAt) {
  // A deadline may cut the final write short. Accept only complete records
  // in order, and derive display names from the parent-owned matrix.
  const lines = stdout.split('\n');
  const tail = lines.pop();
  if ((!interrupted && tail !== '') || lines.length === 0 || lines.length > count) return;

  const times = [];
  let last;
  for (const [index, line] of lines.entries()) {
    const record = /^SCOPEHUB_ACL_PROGRESS (?:([0-9]{1,2}) )?([a-z-]{1,24}) (0|[1-9][0-9]{0,4})\r?$/u.exec(line);
    if (!record || record[0] !== line) return;
    const [, row, stage, milliseconds] = record;
    const [expectedRow, expectedStage, name] = positionAt(index);
    const elapsed = Number(milliseconds);
    if (row !== expectedRow || stage !== expectedStage || elapsed < (times.at(-1) ?? 0)) return;
    times.push(elapsed);
    last = name === undefined ? stage : `case ${name} ${stage}`;
  }
  return { complete: times.length === count, last: `${last} at ${times.at(-1)} ms`, times };
}

function leafProgress(controls, stdout, interrupted) {
  const count = setupStages.length + controls.length * rowStages.length;
  return aclProgress(stdout, interrupted, count, index => {
    const offset = index - setupStages.length;
    if (offset < 0) return [undefined, setupStages[index]];
    const row = Math.floor(offset / rowStages.length);
    return [String(row), rowStages[offset % rowStages.length], controls[row].name];
  });
}

export async function assertWindowsLeafAclMatrix(profile, {
  prepareFixture = prepareAclFixture, launch = runHandoffProbe,
} = {}) {
  const prepare = await readFile(new URL('./workspace_test_handoff_leaf_acl.ps1', import.meta.url), 'utf8')
    .catch(() => { throw aclFixtureFailure('leaf-matrix', 'fixture preparation'); });
  const layouts = [
    { name: 'allow', effective: 58 },
    { name: 'deny', deny: true, effective: 55 },
    { name: 'callback-allow', callback: true, effective: 54 },
    { name: 'callback-deny', callback: true, deny: true, effective: 54 },
    { name: 'object-allow', objectFlags: 3, effective: 54 },
    { name: 'object-deny', objectFlags: 1, deny: true, effective: 54 },
    { name: 'object-callback-allow', objectFlags: 2, callback: true, effective: 54 },
    { name: 'object-callback-deny', objectFlags: 0, callback: true, deny: true, effective: 54 },
  ];
  const modes = ['zero-mask', 'inherit-only', 'effective'];
  const controls = layouts.flatMap(({ name, effective, ...layout }) => modes.map(mode => ({
    ...layout, mode, name: `${name}-${mode}`,
    path: join(profile, `synthetic-acl-${name}-${mode}.txt`),
    expectedStage: mode === 'effective' ? effective : 0,
  })));
  await withAclFixtures(controls, async () => {
    const started = performance.now();
    try {
      const progress = await prepareFixture(prepare, profile, JSON.stringify({ controls }), {
        1: 'fixture preparation', 4: stderr => leafFailure(controls, stderr),
      }, (stdout, interrupted) => leafProgress(controls, stdout, interrupted));
      if (progress) {
        console.info('Synthetic leaf ACL matrix preparation', JSON.stringify({
          subprocessMs: Math.round(performance.now() - started), setupStages, rowStages, elapsedMs: progress.times,
        }));
      }
    } catch (error) {
      throw new Error(`Could not verify the synthetic leaf ACL matrix (${error.message}).`);
    }
    for (const { name, path, expectedStage } of controls) {
      try {
        if (expectedStage === 0) await assertWindowsPrivateHandoff(path, launch);
        else await assertRejectedHandoff(path, expectedStage, launch);
      } catch (error) {
        // A mismatched native result must not obscure the isolated fixture.
        // The expected stage is fixed and the observed output is bounded.
        if (error instanceof assert.AssertionError) {
          const actual = error.actual instanceof Error ? error.actual.message : 'unexpected acceptance';
          throw new Error(`Synthetic leaf ACL case ${name} expected ${stages[expectedStage]}, got ${actual}.`);
        }
        throw new Error(`Synthetic leaf ACL case ${name} failed. ${error.message}`);
      }
    }
  });
}
