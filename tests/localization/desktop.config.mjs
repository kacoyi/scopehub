import { marketPreviewConfig } from '../ubuntu-preview/market-preview-config.mjs';

export default marketPreviewConfig({
  spec: '*.spec.mjs',
  port: Number(process.env.SCOPEHUB_LOCALIZATION_PORT ?? 1472),
  artifactDirectory: 'desktop-localization',
  scope: 'Desktop language selection, synthetic market data, draft preservation and catalog integrity.',
});
