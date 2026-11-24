// Prepare a fresh synthetic profile through the same confirmation UI as the regression.
import { once } from 'node:events';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from '@playwright/test';
import { prepareSeries } from './accepted-drawing-fixture.mjs';
import { spawnWorkspaceService } from './workspace-service.mjs';

const root = await mkdtemp(join(tmpdir(), 'scopehub-accepted-drawing-'));
const browser = await chromium.launch();
let data;
try { data = await prepareSeries(browser, root); } finally { await browser.close(); }
const service = spawnWorkspaceService(data.profile, { port: 1445 });
service.stdout.pipe(process.stdout);
service.stderr.pipe(process.stderr);
service.once('message', port => console.log(JSON.stringify({
  handoff: join(data.profile.directory, 'workspace-access-' + port + '.html'),
  revision: data.revision, layer: data.layer_id, version: data.version_id, result_hash: data.result_hash,
})));
const [code] = await once(service, 'exit');
process.exitCode = code ?? 1;
