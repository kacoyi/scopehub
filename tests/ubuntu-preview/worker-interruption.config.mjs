import { marketPreviewConfig } from './market-preview-config.mjs';

const config = marketPreviewConfig({
  spec: 'worker-interruption.spec.mjs',
  port: Number(process.env.SCOPEHUB_INTERRUPTION_PORT ?? 1428),
  artifactDirectory: 'market-worker-interruption',
  scope: 'One real chart subscription with a terminated and replaced local synthetic market worker.',
  trace: 'off',
});

// The test owns the preview process and can identify its worker exclusively.
export default { ...config, webServer: undefined };
