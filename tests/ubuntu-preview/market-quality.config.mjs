import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-quality.spec.mjs',
  port: Number(process.env.SCOPEHUB_MARKET_QUALITY_PORT ?? 1426),
  artifactDirectory: 'market-quality-states',
  scope: 'Synthetic market quality through the loopback chart with controlled loading and error responses.',
  trace: 'off',
});
