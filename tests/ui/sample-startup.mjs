import { isDeepStrictEqual } from 'node:util';
import { expect } from '@playwright/test';
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

export async function openDefaultSample(page, {
  trigger = () => page.goto('/#demo', { waitUntil: 'domcontentloaded', timeout: 10_000 }),
  verifyChart,
} = {}) {
  const historyCall = sampleHistoryCall();
  const attempts = new Map();
  let order = 0;
  let navigationFailure;
  const issued = request => {
    if (request.method() !== 'POST') return;
    let call;
    try { call = request.postDataJSON(); } catch { /* A malformed body fails the request assertion. */ }
    attempts.set(request, { path: new URL(request.url()).pathname, call, issued: ++order });
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
    const code = request.failure()?.errorText;
    const failure = /^(?:net::)?ERR_[A-Z0-9_]+$/.test(code ?? '') ? code : 'request failed';
    const attempt = attempts.get(request);
    if (attempt) attempt.failure = failure;
    if (request.isNavigationRequest() && request.resourceType() === 'document' && request.method() === 'GET') {
      navigationFailure = failure;
    }
  };
  const read = async name => {
    const label = `${name} POST ${operation}`;
    let terminal;
    await expect.poll(async () => {
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
    }, { ...replayStage, message: `Waiting for ${label} to settle during sample startup` }).toBe(true);
    if (terminal.error) throw new Error(terminal.error);
    return terminal.result;
  };

  page.on('request', issued);
  page.on('response', received);
  page.on('requestfinished', finished);
  page.on('requestfailed', failed);
  try {
    const navigation = await trigger().catch(cause => {
      if (!navigationFailure) throw cause;
      throw new Error(`Sample document GET failed (${navigationFailure})`, { cause });
    });
    if (navigation?.status() >= 400) throw new Error(`Sample document GET returned HTTP ${navigation.status()}`);
    const catalog = await read(sampleCatalogCall.operation);
    expect(catalog.instruments.map(item => item.id)).toContain(future);
    const history = await read(historyCall.operation);
    expect(history.snapshot.series).toEqual(historyCall.payload.series);
    expect(history.snapshot.dataset_revision).toBe(normalRevision);
    expect(history.total_bars).toBe(40);
    expect(history.bars).toHaveLength(40);

    await expect(chart(page)).toBeVisible();
    await expect(chart(page)).toHaveAttribute('data-instrument', future);
    await expect(chart(page)).toHaveAttribute('data-timeframe', '1m');
    await expect(chart(page)).toHaveAttribute('data-bars', '40');
    if (verifyChart) await verifyChart(history.snapshot);

    const expected = [sampleCatalogCall, historyCall];
    let phase = 0;
    let previousFinish = 0;
    for (const attempt of attempts.values()) {
      const label = attempt.call?.operation ?? attempt.path;
      expect(attempt.path, `POST endpoint for ${label}`).toBe(operation);
      expect(attempt.failure, `Transport failure for ${label}`).toBeUndefined();
      expect(attempt.response, `Response received for ${label}`).toBeDefined();
      expect(attempt.finished, `Response finished for ${label}`).toBeDefined();
      expect(attempt.call, `POST parameters for ${label}`).toEqual(expected[phase]);
      expect(attempt.issued, `Previous response finished before ${label} was sent`).toBeGreaterThan(previousFinish);
      previousFinish = attempt.finished;
      if (attempt.response.status() === 429) {
        expect(await attempt.response.json(), `Pre-admission BUSY for ${label}`).toEqual(busy);
      } else {
        expect(attempt.response.status(), label).toBe(200);
        phase++;
      }
    }
    expect(phase, 'catalog and history each completed once').toBe(expected.length);
    return history.snapshot;
  } finally {
    page.off('request', issued);
    page.off('response', received);
    page.off('requestfinished', finished);
    page.off('requestfailed', failed);
  }
}
