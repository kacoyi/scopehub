import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-closed-bar-repair.spec.mjs',
  port: Number(process.env.SCOPEHUB_CLOSED_BAR_REPAIR_PORT ?? 1453),
  artifactDirectory: 'market-recovery',
  scope: 'Real synthetic closed-bar repair and unresolved coverage through the chart, plus held and altered bridge-frame checks.',
});
