import { expect, test } from '@playwright/test';
import { control, openPanel } from '../ui/workspace-controls.mjs';

const revisionId = 'c5ea2d3b-6a7f-4620-b519-151e49b4e3dc';
const series = {
  instrument_kind: 'equity', venue: 'FIXTURE', timeframe: '1d', calendar_id: 'fixture-calendar',
  session_scope: 'regular', timezone: 'UTC', quantity_unit: 'shares', price_adjustment: 'raw', roll_method: null,
};
const frozenBars = ['2026-02-03', '2026-02-04', '2026-02-05'].map((day, index) => ({
  bar: {
    time_ms: Date.parse(day), trading_day: day, open: 12, high: 13, low: 11,
    close: [11.75, 12.25, 12.34][index], volume: 120 - index * 10, is_closed: true,
  },
  locators: [{ record_number: index + 1, line_start: index + 2, line_end: index + 2 }],
}));
const frozenCsv = {
  version: 1,
  revision: {
    revision_id: revisionId, relative_path: 'Watchlist.csv', symbol: 'FIX-CSV', series,
    source_sha256: '1'.repeat(64), mapping_sha256: '2'.repeat(64), review_revision: 1,
    bar_count: frozenBars.length, source_record_count: frozenBars.length,
  },
  snapshot: {
    series: {
      instrument_id: 'FIXTURE.FIX-CSV', provider_id: 'local.csv', timeframe: series.timeframe,
      price_adjustment: series.price_adjustment, session_scope: series.session_scope,
      calendar_id: series.calendar_id, quantity_unit: series.quantity_unit,
    },
    dataset_id: revisionId, dataset_revision: revisionId, content_hash: '3'.repeat(64), warmup_bars: 0,
    from_ms: Date.parse('2026-02-03'), to_ms: Date.parse('2026-02-06'), as_of_ms: Date.parse('2026-02-06'),
  },
  bars: frozenBars,
};

async function restoreFrozenCsv(page, dispatch) {
  await page.exposeBinding('invokeFrozenCsvFixture', (_, command, args) => {
    expect(command).toBe('execute_operation');
    const { operation, payload } = args;
    if (payload.type === 'snapshot') {
      expect(operation).toBeNull();
      return {
        workspace: {
          id: 'main', revision: 1, timeframe: series.timeframe, style: 'candlestick',
          instrument_id: null, dataset: null, visible_range: null, layers: [],
        },
        selection_revision: 1, undo: null, chart_evidence: null,
        source: { kind: 'frozen', revision_id: revisionId, status: 'available' },
      };
    }
    if (payload.type === 'frozen_chart' || payload.type === 'frozen_chart_status') {
      expect(operation).toBeNull();
      expect(payload.revision_id).toBe(revisionId);
      return payload.type === 'frozen_chart' ? frozenCsv : { available: true };
    }
    if (dispatch) return dispatch(args);
    throw new Error(`Unexpected native request: ${operation ?? payload.type}`);
  });
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__SCOPEHUB_DESKTOP_WORKSPACE__ = true;
    window.__TAURI_INTERNALS__ = { invoke: (command, args) => window.invokeFrozenCsvFixture(command, args) };
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'FIX-CSV', exact: true })).toBeVisible();
}

test('retains a restored CSV close without a price tick in both languages', async ({ page }) => {
  await restoreFrozenCsv(page);
  await openPanel(page, 'Watchlist');
  const englishDetails = page.getByRole('region', { name: 'Selected instrument details' });
  await expect(englishDetails.locator('.quote')).toHaveText('12.34');

  await control(page, 'Language settings').click();
  await page.getByRole('combobox', { name: 'Interface language' }).selectOption('zh-Hans');
  await control(page, '关闭').click();
  const chineseDetails = page.getByRole('region', { name: '所选品种详情' });
  await expect(chineseDetails.locator('.quote')).toHaveText('12.34');
  await expect(page.getByText('Watchlist.csv', { exact: true })).toBeVisible();
});

test('translates indicator diagnostics by engine in preview and accepted details', async ({ page }) => {
  const english = 'This source has no verified session schedule. Indicator windows restart after timestamp gaps.';
  const chinese = '此数据源没有经过验证的交易时段表。时间戳存在缺口时，指标计算窗口会重新开始。';
  const codeHash = '4'.repeat(64);
  const paramsHash = '5'.repeat(64);
  const resultHash = '6'.repeat(64);
  const draftId = 'diagnostic-draft';
  const taskId = '9ef10244-ac40-4190-b61c-517a01a34e84';
  const engines = {
    'built-in': 'scopehub-declarative-2',
    'trusted-python': 'scopehub-trusted-python-1',
  };
  const layers = Object.keys(engines).map(id => ({ layer_id: id, version_id: `saved-${id}`, available: true }));
  const artifact = engine_version => ({
    storage: 'inline', result_hash: resultHash,
    result: {
      series: [], markers: [], regions: [],
      diagnostics: [{ code: 'calendar_unverified', message: english, severity: 'info' }],
      provenance: { code_hash: codeHash, params_hash: paramsHash, dataset_id: revisionId, dataset_revision: revisionId, engine_version },
    },
  });
  let range;
  await restoreFrozenCsv(page, ({ operation, payload }) => {
    const reply = result => ({ request_id: payload.request_id, outcome: { state: 'committed', result } });
    if (operation === 'indicator.createDraft') return reply({ draft_id: draftId, code_hash: codeHash });
    if (operation === 'indicator.runPreview') {
      range = payload.scope.target.range;
      return reply({ task_id: taskId });
    }
    if (payload.type === 'indicator_preview_status') {
      expect(payload.task_id).toBe(taskId);
      const engine = 'scopehub-declarative-3';
      return {
        status: 'ready', task_id: taskId, source_snapshot: frozenCsv.snapshot,
        resolved_parameters: { period: 2 }, warmup_from_ms: null, calendar_hash: null,
        event: {
          task_id: taskId, draft_id: draftId, code_hash: codeHash, params_hash: paramsHash, engine_version: engine,
          snapshot: { ...frozenCsv.snapshot, ...range }, artifact: artifact(engine),
        },
      };
    }
    if (payload.type === 'indicator_accepted_layers') return { dataset_id: revisionId, layers };
    if (payload.type === 'indicator_accepted_output') {
      const layer = layers.find(item => item.layer_id === payload.layer_id);
      return { ...layer, document: {
        snapshot: frozenCsv.snapshot, resolved_parameters: { period: 2 }, warmup_from_ms: null,
        artifact: artifact(engines[layer.layer_id]),
      } };
    }
    throw new Error(`Unexpected indicator request: ${operation ?? payload.type}`);
  });

  await page.getByText('Indicators', { exact: true }).click();
  const preview = page.getByRole('region', { name: 'Indicator preview' });
  await preview.getByRole('button', { name: 'Run preview' }).click();
  await expect(preview.getByText(english, { exact: true }).filter({ visible: true })).toHaveCount(1);

  await control(page, 'Language settings').click();
  await page.getByRole('combobox', { name: 'Interface language' }).selectOption('zh-Hans');
  await control(page, '关闭').click();
  const localizedPreview = page.getByRole('region', { name: '指标预览' });
  await expect(localizedPreview.getByText(chinese, { exact: true }).filter({ visible: true })).toHaveCount(1);

  const accepted = localizedPreview.getByRole('region', { name: '已接受的指标' });
  const builtIn = accepted.getByRole('listitem').filter({ hasText: 'saved-built-in' });
  const python = accepted.getByRole('listitem').filter({ hasText: 'saved-trusted-python' });
  await builtIn.getByText('已接受指标的来源与数值').click();
  await python.getByText('已接受指标的来源与数值').click();
  await expect(builtIn.getByText(chinese, { exact: true })).toBeVisible();
  await expect(python.getByText(english, { exact: true })).toBeVisible();
});
