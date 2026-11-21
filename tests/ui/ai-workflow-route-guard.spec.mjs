import { expect, test } from '@playwright/test';
import { installBrowserNetworkGuard, withPeer } from './ai-workflow-fixture.mjs';

test('the browser guard leaves an in-flight local route alone and reports a blocked origin', async ({ browser }) => {
  await withPeer(local => withPeer(blocked => exerciseGuard(browser, local, blocked)));
});

async function exerciseGuard(browser, local, blocked) {
  const context = await browser.newContext({ serviceWorkers: 'block' });
  const entered = Promise.withResolvers();
  const release = Promise.withResolvers();
  try {
    const assertNoBrowserSends = await installBrowserNetworkGuard(context, local.origin);
    const page = await context.newPage();
    await page.goto(local.origin);

    const path = '/local-route-probe';
    const localURL = local.origin + path;
    await context.route(localURL, async route => {
      entered.resolve();
      await release.promise;
      await route.fallback();
    });
    const pageFallback = route => route.fallback();
    await page.route(localURL, pageFallback);
    const reply = page.evaluate(async url => (await fetch(url, { signal: AbortSignal.timeout(8_000) })).status, localURL);

    await Promise.race([entered.promise, reply.then(() => {
      throw new Error('The local request completed before the context observer held it');
    })]);
    expect(local.traffic.filter(request => request.path === path)).toHaveLength(0);
    // Removing the last page handler lets the driver release this request while the observer still holds it.
    await page.unroute(localURL, pageFallback);
    expect(await reply).toBe(200);
    release.resolve();
    expect(local.traffic.filter(request => request.path === path)).toHaveLength(1);
    assertNoBrowserSends();

    const blockedURL = blocked.origin + '/blocked-route-probe';
    await Promise.all([
      page.waitForEvent('requestfailed', { predicate: request => request.url() === blockedURL }),
      expect(page.evaluate(url => fetch(url, { mode: 'no-cors' }), blockedURL)).rejects.toThrow('Failed to fetch'),
    ]);
    expect(blocked.traffic).toEqual([]);
    expect(assertNoBrowserSends).toThrow(blocked.origin);
  } finally {
    release.resolve();
    await context.close();
  }
}
