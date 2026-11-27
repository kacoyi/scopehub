import { marketPreviewConfig } from './market-preview-config.mjs';

const config = marketPreviewConfig({
  spec: 'market-closed-bar-repair.spec.mjs',
  port: Number(process.env.SCOPEHUB_CLOSED_BAR_REPAIR_PORT ?? 1453),
  artifactDirectory: 'market-recovery',
  scope: 'Real synthetic closed-bar repair and unresolved coverage through the chart, plus held and altered bridge-frame checks.',
});

export default {
  ...config,
  webServer: {
    ...config.webServer,
    command: `${config.webServer.command} --config ../../../tests/ubuntu-preview/market-recovery.vite.config.mjs`,
    // The document can respond before the preview has finished preparing its startup modules.
    url: undefined,
    wait: { stdout: /^Market recovery preview ready in \d+ ms\s*$/m },
    stdout: 'pipe',
  },
};
