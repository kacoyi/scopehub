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
