import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-recovery-ui.spec.mjs',
  port: Number(process.env.SCOPEHUB_MARKET_RECOVERY_UI_PORT ?? 1448),
  artifactDirectory: 'market-recovery-ui',
  scope: 'One real loopback futures replay baseline, injected recovery controls with verified synthetic reads, real stock replacement, and startup fault diagnostics.',
});
