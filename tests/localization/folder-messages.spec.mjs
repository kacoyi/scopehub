import { expect, test } from '@playwright/test';

test('translates owned folder review details and preserves opaque source details', async ({ page }) => {
  await page.goto('/');
  const details = [
    'The completed file describes a different series.',
    'Ordered CSV header differs from the saved mapping.',
    'Review CSV record 7. Check the CSV quoting or field size. Feed/{symbol}: unexpected end of data. Source lines 7–8.',
    'Check the header delimiter, quoting or field size. Feed/{symbol}: sequence gap. Source lines 7–8.',
    "The source timeframe is '1m'. The reviewed value is '5m'. Review the mapping.",
    "The source timezone is 'UTC'. The reviewed value is 'Asia/Shanghai'. Review the mapping.",
    "The source volume basis is 'cumulative'. The reviewed value is 'per_bar'. Review the mapping.",
    'Feed/{symbol}: sequence gap. Source lines 7–8.',
    'Review CSV record 7. Feed/{symbol}: sequence gap. Source lines 7–8.',
  ];

  const result = await page.evaluate(async details => {
    const { setLocalePreference } = await import('/src/i18n/index.ts');
    const { folderRefreshFileDetail } = await import('/src/local-import-transport.ts');
    setLocalePreference('en');
    const english = details.map(folderRefreshFileDetail);
    setLocalePreference('zh-Hans');
    return { english, chinese: details.map(folderRefreshFileDetail) };
  }, details);

  expect(result.english).toEqual(details);
  expect(result.chinese).toEqual([
    '完整文件中的数据序列与已接受的序列不一致。',
    'CSV 表头的列名或顺序与已保存的映射不同。',
    '请核对第 7 条 CSV 记录。请检查 CSV 的引号或字段大小。Feed/{symbol}: unexpected end of data. 源文件第 7–8 行。',
    '请检查表头的分隔符、引号或字段大小。Feed/{symbol}: sequence gap. Source lines 7–8.',
    "源数据的 K 线周期为 '1m'，已核对的值为 '5m'。请检查映射。",
    "源数据的时区为 'UTC'，已核对的值为 'Asia/Shanghai'。请检查映射。",
    "源数据的成交量口径为 'cumulative'，已核对的值为 'per_bar'。请检查映射。",
    ...details.slice(7),
  ]);
});

test('translates native source declaration errors with their code and their unmodified source values', async ({ page }) => {
  await page.goto('/');
  const cases = [
    ['calendar_id', 'calendar id', '日历 ID'], ['daily_start_time', 'daily start time', '每日开始时间'],
    ['instrument_kind', 'instrument kind', '品种类型'], ['record_kind', 'record kind', '记录类型'],
    ['roll_method', 'roll method', '换月方法'], ['session_scope', 'session scope', '交易时段范围'],
    ['timestamp_convention', 'timestamp convention', '时间戳约定'], ['timestamp_format', 'timestamp format', '时间戳格式'],
    ['trading_day_format', 'trading day format', '交易日格式'],
  ];
  const observed = await page.evaluate(async cases => {
    const { setLocalePreference } = await import('/src/i18n/index.ts');
    const { folderRefreshFileDetail, localCsvAuxiliaryRole, localImportErrorMessage, renderLocalImportMessage } =
      await import('/src/local-import-transport.ts');
    const collect = locale => {
      setLocalePreference(locale);
      return cases.map(([code, field]) => {
        const message = "Review CSV record 2. The source " + field + " is 'SOURCE'. The reviewed value is 'Reviewed'. Review the mapping.";
        const error = { code: 'DATA_UNAVAILABLE', message, details: { reason: 'csv_review_required', issue_code: code + '_mismatch' } };
        return [renderLocalImportMessage(localImportErrorMessage(error)), folderRefreshFileDetail(message), localCsvAuxiliaryRole('declared ' + field)];
      });
    };
    return { english: collect('en'), chinese: collect('zh-Hans') };
  }, cases);
  for (const [index, [, field, label]] of cases.entries()) {
    const english = "Review CSV record 2. The source " + field + " is 'SOURCE'. The reviewed value is 'Reviewed'. Review the mapping.";
    const chineseLabel = label.endsWith(' ID') ? label + ' ' : label;
    const chinese = "请核对第 2 条 CSV 记录。源数据的" + chineseLabel + "为 'SOURCE'，已核对的值为 'Reviewed'。请检查映射。";
    expect(observed.english[index]).toEqual([english, english, 'declared ' + field]);
    expect(observed.chinese[index]).toEqual([chinese, chinese, '声明的' + label]);
  }
});
