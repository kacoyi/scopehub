import { expect, test } from '@playwright/test';

test('translates owned folder review details and preserves opaque source details', async ({ page }) => {
  await page.goto('/');
  const details = [
    'The completed file describes a different series.',
    'Ordered CSV header differs from the saved mapping.',
    'Review CSV record 7. Check the CSV quoting or field size. Feed/{symbol}: unexpected end of data. Source lines 7–8.',
    'Check the header delimiter, quoting or field size. Feed/{symbol}: sequence gap. Source lines 7–8.',
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
    ...details.slice(4),
  ]);
});
