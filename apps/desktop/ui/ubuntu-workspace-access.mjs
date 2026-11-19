import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { closeSync, openSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export function issueWorkspaceAccess(profileRoot, port) {
  // Rust attests the private root before this call. Windows files inherit its
  // verified NTFS object ACL. POSIX also checks at the point of handoff.
  if (process.platform !== 'win32') {
    const directory = statSync(profileRoot);
    if (!process.getuid || directory.uid !== process.getuid() || (directory.mode & 0o077) !== 0) {
      throw new Error('The workspace handoff requires an owner-private profile directory.');
    }
  }

  const token = randomBytes(32).toString('hex');
  const expected = Buffer.from('Bearer ' + token);
  const name = `workspace-access-${port}.html`;
  const file = join(profileRoot, name);
  const temporary = join(profileRoot, '.' + name + '-' + randomUUID());
  const link = `http://127.0.0.1:${port}/#workspace=${token}`;
  const descriptor = openSync(temporary, 'wx', 0o600);
  try {
    try {
      writeFileSync(descriptor, `<!doctype html><meta charset="utf-8">
<link rel="icon" href="data:,">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'">
<title>ScopeHub workspace access</title>
<a href="${link}" rel="noreferrer">Open ScopeHub workspace</a>
`);
    } finally {
      closeSync(descriptor);
    }
    renameSync(temporary, file);
  } finally {
    rmSync(temporary, { force: true });
  }

  return {
    name,
    permits(authorization) {
      if (typeof authorization !== 'string' || !/^Bearer [a-f0-9]{64}$/.test(authorization)) return false;
      return timingSafeEqual(expected, Buffer.from(authorization));
    },
    close() {
      rmSync(file, { force: true });
    },
  };
}
