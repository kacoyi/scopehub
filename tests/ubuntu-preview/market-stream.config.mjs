import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-stream.spec.mjs',
  port: Number(process.env.SCOPEHUB_MARKET_STREAM_PORT ?? 1425),
  artifactDirectory: 'market-live-stream',
  scope: 'Explicit synthetic replay via the ordinary local development chart and worker.',
});
