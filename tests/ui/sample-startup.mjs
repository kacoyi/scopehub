import { expect } from '@playwright/test';
import { replayStage } from './replay-stages.mjs';

const operation = '/__scopehub/operation';
const chart = page => page.locator('.history');
export const busy = { ok: false, error: { code: 'BUSY', retryable: true } };
export const future = 'SIM.CN.FUTURE.001';
const normalRevision = 'scopehub-synthetic-v1:20241013:normal';
const catalogCall = { operation: 'instrument.search', payload: { query: '', limit: 50 } };
const historyCall = {
  operation: 'market.readHistoryPage',
  payload: {
    series: {
      instrument_id: future, provider_id: 'scopehub.synthetic', timeframe: '1m', price_adjustment: 'raw',
      session_scope: 'sample-sessions', calendar_id: `${future}.sample-calendar.v1`, quantity_unit: 'contracts',
    },
    from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6), warmup_bars: 0, expected_revision: normalRevision,
  },
};

export async function openDefaultSample(page, { trigger = () => page.goto('/#demo'), verifyChart } = {}) {
  const attempts = new Map();
  let order = 0;
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
    const attempt = attempts.get(request);
    if (attempt) attempt.failure = request.failure()?.errorText ?? 'Request failed';
  };
  const read = async name => {
    const completed = () => [...attempts.values()].find(attempt => attempt.call?.operation === name
      && attempt.path === operation && attempt.finished !== undefined && attempt.response && attempt.response.status() !== 429);
    await expect.poll(() => Boolean(completed()), { ...replayStage, message: `Waiting for ${name} during sample startup` }).toBe(true);
    const { response } = completed();
    expect(response.status(), name).toBe(200);
    const body = await response.json();
    expect(body.ok, name).toBe(true);
    return body.result;
  };

  page.on('request', issued);
  page.on('response', received);
  page.on('requestfinished', finished);
  page.on('requestfailed', failed);
  try {
    await trigger();
    const catalog = await read(catalogCall.operation);
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

    const expected = [catalogCall, historyCall];
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
