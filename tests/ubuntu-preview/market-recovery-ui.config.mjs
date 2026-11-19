import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-recovery-ui.spec.mjs',
  port: Number(process.env.SCOPEHUB_MARKET_RECOVERY_UI_PORT ?? 1448),
  artifactDirectory: 'market-recovery-ui',
  scope: 'Injected replay progress through the ordinary chart and browser NDJSON reader with real frozen history.',
});
