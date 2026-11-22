import { control } from './workspace-controls.mjs';

const entryModule = '/src/main.tsx';
// The preview has already passed its HTTP readiness probe. This budget covers
// one document and application bootstrap, before the user starts a worker read.
const workspaceTimeoutMs = 10_000;
const snapshotTimeoutMs = 10_000;

function failureCode(request) {
  const code = request.failure()?.errorText;
  return /^(?:net::)?ERR_[A-Z0-9_]+$/.test(code ?? '') ? code : 'request failed';
}

function browserErrorType(error) {
  const types = ['EvalError', 'RangeError', 'ReferenceError', 'SyntaxError', 'TypeError', 'URIError', 'AggregateError'];
  return types.includes(error.name) ? error.name : 'Error';
}

function startupResource(request, origin) {
  const url = new URL(request.url());
  if (url.origin !== origin) return;
  if (request.isNavigationRequest() && request.resourceType() === 'document') return 'document';
  if (request.resourceType() !== 'script' && request.resourceType() !== 'stylesheet') return;
  if (/^\/(?:src\/|node_modules\/\.vite\/deps\/)[\w./-]+$/.test(url.pathname)
    || ['/@vite/client', '/@react-refresh'].includes(url.pathname)) return url.pathname;
  return `same-origin ${request.resourceType()}`;
}

function describe(resource) {
  if (!resource) return 'not requested';
  if (resource.failure) return resource.failure;
  if (resource.status === undefined) return 'no response';
  return `HTTP ${resource.status}${resource.finished ? '' : ' (body pending)'}`;
}

async function openPreview(page, baseURL, mountControls, timeout, workspaceMounted) {
  const origin = new URL(baseURL).origin;
  const resources = new Map();
  const terminal = Promise.withResolvers();
  const onRequest = request => {
    const name = startupResource(request, origin);
    if (name) resources.set(request, { name, finished: false });
  };
  const onResponse = response => {
    const resource = resources.get(response.request());
    if (!resource) return;
    resource.status = response.status();
    if (resource.status >= 400) terminal.resolve(`${resource.name} returned HTTP ${resource.status}`);
  };
  const onFinished = request => {
    const resource = resources.get(request);
    if (resource) resource.finished = true;
  };
  const onFailed = request => {
    const resource = resources.get(request);
    if (!resource) return;
    resource.failure = failureCode(request);
    terminal.resolve(`${resource.name} failed (${resource.failure})`);
  };
  const onPageError = error => terminal.resolve(`Uncaught browser ${browserErrorType(error)} before the workspace mounted`);
  const onCrash = () => terminal.resolve('Browser page crashed before the workspace mounted');
  const onClose = () => terminal.resolve('Browser page closed before the workspace mounted');
  const listeners = {
    request: onRequest, response: onResponse, requestfinished: onFinished, requestfailed: onFailed,
    pageerror: onPageError, crash: onCrash, close: onClose,
  };
  for (const [event, listener] of Object.entries(listeners)) page.on(event, listener);

  let stage = 'document navigation';
  const deadline = setTimeout(() => terminal.resolve(`Startup exceeded ${timeout} ms`), timeout);
  const mount = async () => {
    let response;
    try {
      response = await page.goto(origin, { waitUntil: 'commit', timeout });
    } catch (error) {
      if (error.name === 'TimeoutError') return `Startup exceeded ${timeout} ms`;
      const request = [...resources.entries()].findLast(([, resource]) => resource.name === 'document')?.[0];
      return request ? `document failed (${failureCode(request)})` : 'Document navigation failed';
    }
    if (!response?.ok()) return `Document navigation returned HTTP ${response?.status() ?? 'unknown'}`;
    stage = 'application mount';
    try {
      const visible = Promise.all(mountControls.map(name => control(page, name).waitFor({ state: 'visible', timeout })));
      await (workspaceMounted ? Promise.race([visible, workspaceMounted]) : visible);
    } catch (error) {
      return error.name === 'TimeoutError' ? `Startup exceeded ${timeout} ms` : 'Could not observe application controls';
    }
  };
  try {
    const reason = await Promise.race([mount(), terminal.promise]);
    if (reason) {
      const observed = [...resources.values()];
      const document = describe(observed.findLast(resource => resource.name === 'document'));
      const entry = describe(observed.findLast(resource => resource.name === entryModule));
      const pending = observed.filter(resource => !resource.finished && !resource.failure).length;
      throw new Error(`Preview startup failed during ${stage}. ${reason}. Document ${document}, entry ${entry}, pending resources ${pending}.`);
    }
  } finally {
    clearTimeout(deadline);
    for (const [event, listener] of Object.entries(listeners)) page.off(event, listener);
  }
}

export function openUnconfiguredPreview(page, baseURL, { timeout = workspaceTimeoutMs } = {}) {
  return openPreview(page, baseURL, ['Open offline sample', 'Data sources'], timeout);
}

export async function openWorkspacePreview(page, baseURL, endpoint, { snapshotTimeout = snapshotTimeoutMs } = {}) {
  const address = new URL(endpoint, baseURL).href;
  const first = Promise.withResolvers();
  const failure = Promise.withResolvers();
  let request;
  const listeners = {
    request(value) {
      if (request || value.method() !== 'POST' || value.url() !== address) return;
      try {
        if (value.postDataJSON()?.type !== 'snapshot') return;
      } catch {
        return;
      }
      request = value;
      first.resolve(value);
    },
    requestfailed(value) {
      if (value === request) failure.resolve(`Snapshot request failed (${failureCode(value)})`);
    },
    pageerror: error => failure.resolve(`Uncaught browser ${browserErrorType(error)} before the workspace was ready`),
    crash: () => failure.resolve('Browser page crashed before the snapshot arrived'),
    close: () => failure.resolve('Browser page closed before the snapshot arrived'),
  };
  for (const [event, listener] of Object.entries(listeners)) page.on(event, listener);

  async function read() {
    try {
      const observed = await first.promise;
      if (!observed) return 'Snapshot was not requested';
      const response = await observed.response();
      if (!response) return `Snapshot request failed (${failureCode(observed)})`;
      if (!response.ok()) return `Snapshot returned HTTP ${response.status()}`;
      const frame = await response.json();
      if (frame?.ok !== true || !frame.result?.workspace) return 'Snapshot reply could not be verified';
      return frame;
    } catch {
      return 'Snapshot response body could not be read as JSON';
    }
  }

  async function verify() {
    const result = await read();
    if (typeof result === 'string') return result;
    const ready = control(page, 'Private records').and(page.locator(':enabled'));
    const rejected = control(page, 'Reload workspace state');
    try {
      await ready.or(rejected).first().waitFor({ state: 'visible', timeout: snapshotTimeout });
      return await ready.isVisible() ? result : 'Application rejected the initial snapshot';
    } catch {
      return 'Could not observe workspace readiness';
    }
  }

  let timer;
  try {
    // The snapshot request comes from the mounted React application's effect.
    // A fast request can reach the runner before Playwright observes the controls.
    await openPreview(page, baseURL, ['Data sources'], workspaceTimeoutMs, first.promise);
    // Register before navigation to catch a fast reply and start this independent
    // budget at mount so a slow module graph cannot use the snapshot's time.
    timer = setTimeout(() => failure.resolve(`Snapshot exceeded ${snapshotTimeout} ms`), snapshotTimeout);
    const result = await Promise.race([verify(), failure.promise]);
    if (typeof result === 'string') {
      const response = request?.existingResponse();
      const observed = response ? `HTTP ${response.status()}` : request ? 'no response' : 'not requested';
      throw new Error(`Preview startup failed during workspace snapshot. ${result}. Snapshot ${observed}.`);
    }
    return result;
  } finally {
    clearTimeout(timer);
    first.resolve(null);
    for (const [event, listener] of Object.entries(listeners)) page.off(event, listener);
  }
}
