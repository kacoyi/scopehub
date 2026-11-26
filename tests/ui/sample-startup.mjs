import { isDeepStrictEqual } from 'node:util';
import { expect } from '@playwright/test';
import { isMarketOutput } from '../../apps/desktop/ui/src/market-output.ts';
import { sampleHistoryMatchesRequest, sampleHistoryPageFitsSnapshot } from '../../apps/desktop/ui/src/sample-history-validation.ts';
import { replayStage } from './replay-stages.mjs';

const operation = '/__scopehub/operation';
const chart = page => page.locator('.history');
export const busy = { ok: false, error: { code: 'BUSY', retryable: true } };
export const future = 'SIM.CN.FUTURE.001';
const normalRevision = 'scopehub-synthetic-v1:20241013:normal';
export const sampleCatalogCall = { operation: 'instrument.search', payload: { query: '', limit: 50 } };
export const sampleHistoryCall = (timeframe = '1m') => ({
  operation: 'market.readHistoryPage',
  payload: {
    series: {
      instrument_id: future, provider_id: 'scopehub.synthetic', timeframe, price_adjustment: 'raw',
      session_scope: 'sample-sessions', calendar_id: `${future}.sample-calendar.v1`, quantity_unit: 'contracts',
    },
    from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6), warmup_bars: 0, expected_revision: normalRevision,
  },
});

function requestFailure(request) {
  const code = request.failure()?.errorText;
  return /^(?:net::)?ERR_[A-Z0-9_]+$/.test(code ?? '') ? code : 'request failed';
}

export function observeSampleRequests(page, { marketOnly = false, poll = expect.poll } = {}) {
  const attempts = new Map();
  let order = 0;
  let navigationFailure;
  const issued = request => {
    if (request.method() !== 'POST') return;
    const path = new URL(request.url()).pathname;
    if (marketOnly && path !== operation) return;
    let call;
    try { call = request.postDataJSON(); } catch { /* A malformed body fails the request assertion. */ }
    attempts.set(request, { path, call, issued: ++order });
  };
  const received = response => {
    const attempt = attempts.get(response.request());
    if (attempt) attempt.response = response;
  };
  const finished = request => {
    const attempt = attempts.get(request);
    if (attempt) attempt.finished = ++order;
  };
  const failed = request => {
    const failure = requestFailure(request);
    const attempt = attempts.get(request);
    if (attempt) attempt.failure = failure;
    if (request.isNavigationRequest() && request.resourceType() === 'document' && request.method() === 'GET') {
      navigationFailure = failure;
    }
  };
  const read = async (name, { allowFailure = false, fixtureHoldMs = 0 } = {}) => {
    const label = `${name} POST ${operation}`;
    let terminal;
    await poll(async () => {
      for (const attempt of attempts.values()) {
        if (attempt.call?.operation !== name || attempt.path !== operation || attempt.busy) continue;
        if (attempt.failure) {
          terminal = { error: `${label} failed (${attempt.failure})` };
          return true;
        }
        const { response } = attempt;
        if (!attempt.finished || !response) continue;
        const status = response.status();
        const body = await response.json().catch(() => undefined);
        if (status === 429 && isDeepStrictEqual(body, busy)) {
          attempt.busy = true;
          continue;
        }
        const detail = body?.error?.code ? ` (${body.error.code})` : '';
        terminal = status === 200 && body?.ok === true
          ? { result: body.result }
          : { error: `${label} returned HTTP ${status}${detail}${body === undefined ? ' with no readable JSON body' : ''}` };
        return true;
      }
      return false;
    }, {
      ...replayStage, timeout: replayStage.timeout + fixtureHoldMs,
      message: `Waiting for ${label} to settle during sample startup`,
    }).toBe(true);
    if (terminal.error && !allowFailure) throw new Error(terminal.error);
    return terminal.result;
  };

  page.on('request', issued);
  page.on('response', received);
  page.on('requestfinished', finished);
  page.on('requestfailed', failed);
  const stop = () => {
    page.off('request', issued);
    page.off('response', received);
    page.off('requestfinished', finished);
    page.off('requestfailed', failed);
  };
  return { attempts, read, stop, get navigationFailure() { return navigationFailure; } };
}

function observeSampleDocument(page) {
  const began = performance.now();
  const elapsed = () => Math.round(performance.now() - began);
  const marks = {};
  const pending = new Map();
  const failures = [];
  let documentRequest;
  let document = {};
  let origin;
  let omitted = 0;

  const isMainFrame = request => {
    // Some browser-owned requests have no associated page frame.
    try { return request.frame() === page.mainFrame(); }
    catch { return false; }
  };
  const listeners = {
    request(request) {
      const type = request.resourceType();
      if (request.method() !== 'GET' || !['document', 'script', 'stylesheet'].includes(type) || !isMainFrame(request)) return;
      const url = new URL(request.url());
      if (type === 'document' && request.isNavigationRequest()) {
        documentRequest = request;
        document = { requested_ms: elapsed() };
        origin = url.origin;
        pending.clear();
        return;
      }
      if (type === 'document' || url.origin !== origin) return;
      if (pending.size === 96) {
        omitted++;
        return;
      }
      // Record only recognized code routes, without host, query, fragment or a filesystem path.
      const pathname = url.pathname;
      const asset = url.hostname === '127.0.0.1' && /^\/(?:src\/|assets\/|node_modules\/\.vite\/|@vite\/|@react-refresh$|workspace-reload\.js$)[\w./@-]{0,120}$/.test(pathname)
        ? pathname : undefined;
      pending.set(request, { type, asset, requested_ms: elapsed() });
    },
    response(response) {
      const request = response.request();
      const entry = request === documentRequest ? document : pending.get(request);
      if (entry) Object.assign(entry, { http: response.status(), response_ms: elapsed() });
    },
    requestfinished(request) {
      if (request === documentRequest) document.finished_ms = elapsed();
      const entry = pending.get(request);
      if (entry?.http >= 400 && failures.length < 8) failures.push(entry);
      pending.delete(request);
    },
    requestfailed(request) {
      if (request === documentRequest) document.failure = requestFailure(request);
      const entry = pending.get(request);
      if (entry && failures.length < 8) failures.push({ ...entry, failure: requestFailure(request) });
      pending.delete(request);
    },
    framenavigated(frame) {
      if (frame !== page.mainFrame() || !documentRequest) return;
      const url = new URL(frame.url());
      url.hash = '';
      if (url.href === documentRequest.url()) document.committed_ms = elapsed();
    },
    domcontentloaded() {
      if (document.committed_ms !== undefined) document.dom_content_loaded_ms = elapsed();
    },
  };
  for (const [event, observe] of Object.entries(listeners)) page.on(event, observe);
  return {
    mark(stage) { marks[stage] = elapsed(); },
    finish() {
      for (const [event, observe] of Object.entries(listeners)) page.off(event, observe);
      let stage = 'document-not-committed';
      if (document.failure || document.http >= 400) stage = 'document-terminal';
      else if (document.dom_content_loaded_ms !== undefined) stage = 'dom-content-loaded';
      else if (document.committed_ms !== undefined) stage = 'document-committed';
      if (marks.catalog_verified_ms !== undefined) stage = 'catalog-verified';
      if (marks.history_verified_ms !== undefined) stage = 'history-verified';
      if (marks.sample_verified_ms !== undefined) stage = 'sample-verified';
      return {
        stage, elapsed_ms: elapsed(), document, ...marks,
        pending_count: pending.size, pending: [...pending.values()].slice(0, 8), failures, omitted,
      };
    },
  };
}

export async function openDefaultSample(page, { trigger, verifyChart, reportStartup } = {}) {
  const historyCall = sampleHistoryCall();
  const observer = observeSampleRequests(page);
  const startup = reportStartup && !trigger ? observeSampleDocument(page) : undefined;
  const attempts = observer.attempts.values();
  let previousFinish = 0;
  const verifyRequest = async expected => {
    for (let next = attempts.next(); !next.done; next = attempts.next()) {
      const attempt = next.value;
      const label = attempt.call?.operation ?? attempt.path;
      expect(attempt.path, `POST endpoint for ${label}`).toBe(operation);
      expect(attempt.failure, `Transport failure for ${label}`).toBeUndefined();
      expect(attempt.response, `Response received for ${label}`).toBeDefined();
      expect(attempt.finished, `Response finished for ${label}`).toBeDefined();
      expect(attempt.call, `POST parameters for ${label}`).toEqual(expected);
      expect(attempt.issued, `Previous response finished before ${label} was sent`).toBeGreaterThan(previousFinish);
      previousFinish = attempt.finished;
      if (attempt.response.status() === 429) {
        expect(await attempt.response.json(), `Pre-admission BUSY for ${label}`).toEqual(busy);
      } else {
        expect(attempt.response.status(), label).toBe(200);
        return;
      }
    }
    throw new Error(`Missing completed POST for ${expected.operation}`);
  };
  try {
    const navigation = await (trigger ? trigger() : page.goto('/#demo', {
      waitUntil: 'domcontentloaded', timeout: 10_000,
    })).catch(cause => {
      if (!observer.navigationFailure) throw cause;
      throw new Error(`Sample document GET failed (${observer.navigationFailure})`, { cause });
    });
    if (navigation?.status() >= 400) throw new Error(`Sample document GET returned HTTP ${navigation.status()}`);
    const catalog = await observer.read(sampleCatalogCall.operation);
    await verifyRequest(sampleCatalogCall);
    expect(isMarketOutput(sampleCatalogCall.operation, catalog), 'Catalog reply satisfies the operation output schema').toBe(true);
    const instrument = catalog.instruments.find(item => item.id === future);
    expect(instrument).toBeDefined();
    startup?.mark('catalog_verified_ms');
    const history = await observer.read(historyCall.operation);
    await verifyRequest(historyCall);
    expect(isMarketOutput(historyCall.operation, history), 'History reply satisfies the operation output schema').toBe(true);
    expect(sampleHistoryMatchesRequest(history, historyCall.payload, instrument), 'History reply matches the sample request').toBe(true);
    expect(sampleHistoryPageFitsSnapshot(history, history.snapshot, history.total_bars, []), 'History reply is a valid first page').toBe(true);
    expect(history.snapshot.series).toEqual(historyCall.payload.series);
    expect(history.next_cursor).toBeNull();
    expect(history.total_bars).toBe(40);
    expect(history.bars).toHaveLength(40);
    startup?.mark('history_verified_ms');

    await expect(chart(page)).toBeVisible();
    await expect(chart(page)).toHaveAttribute('data-instrument', future);
    await expect(chart(page)).toHaveAttribute('data-timeframe', '1m');
    await expect(chart(page)).toHaveAttribute('data-bars', '40');
    if (verifyChart) await verifyChart(history.snapshot);

    expect(attempts.next().done, 'catalog and history each completed once').toBe(true);
    startup?.mark('sample_verified_ms');
    return history.snapshot;
  } finally {
    observer.stop();
    if (startup) {
      const observation = startup.finish();
      try { await reportStartup(observation); }
      catch (error) {
        if (observation.stage === 'sample-verified') throw error;
        console.warn('Could not attach sample startup observation.');
      }
    }
  }
}
