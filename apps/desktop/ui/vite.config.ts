import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { ubuntuPreview } from './ubuntu-preview.mjs';
import { ubuntuWorkspace } from './ubuntu-workspace.mjs';

export default defineConfig(({ command, mode, isPreview }) => {
  const serving = command === 'serve' && !isPreview;
  const workspace = serving && mode === 'ubuntu-workspace';
  const history = serving && (mode === 'ubuntu-preview' || workspace);
  const testPreview = process.env.SCOPEHUB_TEST_PREVIEW === '1';
  return {
    // History and authenticated workspaces can run concurrently. Their defines
    // differ, so their optimized worker modules need independent URLs on disk.
    cacheDir: `node_modules/.vite-${mode}`,
    plugins: [react(), ...(history ? [ubuntuPreview()] : []), ...(workspace ? [ubuntuWorkspace()] : [])],
    // Discover the worker-only interpreter before serving the editor. Late
    // dependency optimization reloads the page and discards the first draft.
    optimizeDeps: { include: ['@jitl/quickjs-asmjs-mjs-release-sync', 'quickjs-emscripten-core'] },
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
