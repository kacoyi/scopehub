import { expect, test } from '@playwright/test';

// Only this probe needs automation enabled to read Chromium's effective command line.
test.use({ launchOptions: { args: ['--enable-automation'] } });

// Revisit on Playwright upgrades. Retire after upstream removes the affected path
// or fixes it in the pinned Chromium/Windows combination, with both native suites passing.
test('pinned Chromium disables Windows TCP source-port randomization', async ({ browser }) => {
  const session = await browser.newBrowserCDPSession();
  try {
    const { arguments: args } = await session.send('Browser.getBrowserCommandLine');
    const flag = '--disable-features';
    const disabled = args.filter(arg => arg === flag || arg.startsWith(`${flag}=`));
    // The pinned headless shell uses the last value of a duplicate switch.
    const features = disabled.at(-1)?.slice(flag.length + 1).split(',') ?? [];
    const observation = {
      platform: process.platform,
      chromium: browser.version(),
      disabledFeatureSwitches: disabled.length,
      tcpPortRandomizationDisabled: features.includes('TcpPortRandomizationWin'),
    };
    // The harness archives the localization log on hosted Windows.
    console.info('Chromium TCP source-port randomization', JSON.stringify(observation));
    expect(observation.tcpPortRandomizationDisabled).toBe(true);
  } finally {
    await session.detach();
  }
});
