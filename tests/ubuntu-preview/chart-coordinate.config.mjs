import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'chart-coordinate.spec.mjs',
  port: Number(process.env.SCOPEHUB_CHART_COORDINATE_PORT ?? 1461),
  artifactDirectory: 'chart-coordinate-prototype',
  scope: 'An isolated author program projected onto the actual synthetic financial chart in Chromium.',
  trace: 'off',
});
