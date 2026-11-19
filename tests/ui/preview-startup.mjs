import { control } from './workspace-controls.mjs';

const entryModule = '/src/main.tsx';
// The preview has already passed its HTTP readiness probe. This budget covers
// one document and application bootstrap, before the user starts a worker read.
const workspaceTimeoutMs = 10_000;

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

export async function openUnconfiguredPreview(page, baseURL, { timeout = workspaceTimeoutMs } = {}) {
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
    const code = request.failure()?.errorText;
    resource.failure = /^(?:net::)?ERR_[A-Z0-9_]+$/.test(code ?? '') ? code : 'request failed';
    terminal.resolve(`${resource.name} failed (${resource.failure})`);
  };
  const onPageError = error => {
    const type = /^[A-Za-z]+Error$/.test(error.name) ? error.name : 'Error';
    terminal.resolve(`Uncaught browser ${type} before the workspace mounted`);
  };
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
    const response = await page.goto(origin, { waitUntil: 'commit', timeout });
    if (!response?.ok()) throw new Error(`Document navigation returned HTTP ${response?.status() ?? 'unknown'}`);
    stage = 'application mount';
    await Promise.all([
      control(page, 'Open offline sample').waitFor({ state: 'visible', timeout }),
      control(page, 'Data sources').waitFor({ state: 'visible', timeout }),
    ]);
  };
  try {
    const reason = await Promise.race([mount(), terminal.promise]);
    if (reason) throw new Error(reason);
  } catch (cause) {
    const observed = [...resources.values()];
    const document = describe(observed.findLast(resource => resource.name === 'document'));
    const entry = describe(observed.findLast(resource => resource.name === entryModule));
    const pending = observed.filter(resource => !resource.finished && !resource.failure).length;
    throw new Error(`Preview startup failed during ${stage}. ${cause.message}. Document ${document}, entry ${entry}, pending resources ${pending}.`, { cause });
  } finally {
    clearTimeout(deadline);
    for (const [event, listener] of Object.entries(listeners)) page.off(event, listener);
  }
}
