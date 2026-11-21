import { expect } from '@playwright/test';

export const operationPath = '/__scopehub/operation';
export const traceHeader = 'x-scopehub-test-request-id';

export async function observeSelectedHistory(page, { includeCatalog = false } = {}) {
  await page.addInitScript(({ operationPath, traceHeader, includeCatalog }) => {
    const fetch = window.fetch;
    const attempts = {};
    window.readHistoryAttempts = () => attempts;
    window.fetch = async (input, options) => {
      if (input !== operationPath || !options?.body) return fetch(input, options);
      const { operation, payload } = JSON.parse(options.body);
      const selected = operation === 'market.readHistoryPage' && !payload.snapshot;
      if (!selected && !(includeCatalog && operation === 'instrument.search')) return fetch(input, options);

      const id = crypto.randomUUID();
      const headers = new Headers(options.headers);
      headers.set(traceHeader, id);
      const attempt = attempts[id] = { stage: 'fetch' };
      const recordError = (stage, error) => Object.assign(attempt, {
        stage, error: error.name, selectionAborted: options.signal?.aborted === true,
      });
      let response;
      try {
        response = await fetch(input, { ...options, headers });
      } catch (error) {
        recordError('fetch-rejected', error);
        throw error;
      }
      Object.assign(attempt, { stage: 'headers', status: response.status });
      const json = response.json.bind(response);
      response.json = async () => {
        try {
          const reply = await json();
          Object.assign(attempt, {
            stage: 'body', ok: reply?.ok, code: reply?.error?.code, retryable: reply?.error?.retryable,
          });
          return reply;
        } catch (error) {
          recordError('body-rejected', error);
          throw error;
        }
      };
      return response;
    };
  }, { operationPath, traceHeader, includeCatalog });
}

export async function readMarketAttemptStages(page, requests) {
  const client = await page.evaluate(() => window.readHistoryAttempts()).catch(() => ({}));
  return requests.map(request => {
    const trace = request.headers()[traceHeader];
    return { trace, status: request.existingResponse()?.status(), failure: request.failure()?.errorText, client: client[trace] };
  });
}

export async function waitForSelectedHistoryTerminal(page, canvas, { id, timeframe }) {
  const label = `${id} ${timeframe}`;
  let terminal;
  await expect.poll(async () => {
    terminal = await page.locator('.plot-workspace').evaluate((plot, { id, timeframe }) => {
      const alert = plot.querySelector('.plot-empty [role="alert"]');
      if (alert) return { state: 'error', message: alert.textContent };
      const history = plot.querySelector('.history');
      if (history?.dataset.instrument === id && history.dataset.timeframe === timeframe && history.dataset.bars === '0') {
        return { state: 'empty' };
      }
      return null;
    }, { id, timeframe });
    if (!terminal && await canvas.isVisible()) terminal = { state: 'chart' };
    return terminal;
  }, { message: `Waiting for the ${label} history result` }).not.toBeNull();
  return terminal;
}

export async function expectSelectedHistory(page, canvas, calls, start, { id, timeframe }, testInfo) {
  const label = `${id} ${timeframe}`;
  let terminal;
  let attempts;
  try {
    terminal = await waitForSelectedHistoryTerminal(page, canvas, { id, timeframe });
  } finally {
    const observed = calls.slice(start).filter(({ request }) => request.headers()[traceHeader]);
    const stages = await readMarketAttemptStages(page, observed.map(({ request }) => request));
    attempts = observed.map(({ payload }, index) => {
      const series = payload.series;
      return {
        ...stages[index], instrument: series.instrument_id, timeframe: series.timeframe,
        selected: series.instrument_id === id && series.timeframe === timeframe,
      };
    });
    const observation = { selection: { id, timeframe }, terminal, attempts };
    console.info('Selected history', JSON.stringify(observation));
    await testInfo.attach(`${label} history`, {
      contentType: 'application/json', body: JSON.stringify(observation, null, 2),
    });
  }
  expect(terminal, `${label} history reached an unexpected terminal`).toEqual({ state: 'chart' });
  const current = attempts.filter(attempt => attempt.selected);
  expect(current.at(-1), `${label} completed current history`).toMatchObject({ status: 200, client: { ok: true } });
  for (const attempt of current.slice(0, -1)) {
    expect(attempt, `${label} prior current history attempt`).toMatchObject({
      status: 429, client: { ok: false, code: 'BUSY', retryable: true },
    });
  }
}
