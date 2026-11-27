import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { indicatorPreviewStatusSchema } from './indicator-preview-status-schema.mjs';
import { ubuntuPreview } from './ubuntu-preview.mjs';
import { ubuntuWorkspace } from './ubuntu-workspace.mjs';

export default defineConfig(({ command, mode, isPreview }) => {
  const serving = command === 'serve' && !isPreview;
  const workspace = serving && mode === 'ubuntu-workspace';
  const history = serving && (mode === 'ubuntu-preview' || workspace);
  const testPreview = process.env.SCOPEHUB_TEST_PREVIEW === '1';
  return {
    plugins: [react(), indicatorPreviewStatusSchema(), ...(history ? [ubuntuPreview()] : []), ...(workspace ? [ubuntuWorkspace()] : [])],
    define: {
      'import.meta.env.SCOPEHUB_UBUNTU_PREVIEW': history,
      'import.meta.env.SCOPEHUB_UBUNTU_WORKSPACE': workspace,
    },
    server: history ? {
      host: '127.0.0.1', port: 1420, strictPort: true, cors: false,
      ...(testPreview ? { watch: null, hmr: false, ws: false } : {}),
    } : {},
    clearScreen: false,
    build: { target: 'es2022' },
  };
});
