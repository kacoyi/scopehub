import { marketPreviewConfig } from './market-preview-config.mjs';

export default marketPreviewConfig({
  spec: 'market-workflow.spec.mjs',
  port: Number(process.env.SCOPEHUB_MARKET_WORKFLOW_PORT ?? 1427),
  artifactDirectory: 'market-workflow',
  scope: 'Three synthetic instruments through one chart session and the shared market worker.',
  trace: 'off',
});
