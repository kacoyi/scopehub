import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { ubuntuPreview } from './ubuntu-preview.mjs';
import { ubuntuWorkspace } from './ubuntu-workspace.mjs';

export default defineConfig(({ command, mode, isPreview }) => {
  const serving = command === 'serve' && !isPreview;
  const workspace = serving && mode === 'ubuntu-workspace';
  const history = serving && (mode === 'ubuntu-preview' || workspace);
  return {
    plugins: [react(), ...(history ? [ubuntuPreview()] : []), ...(workspace ? [ubuntuWorkspace()] : [])],
    define: {
      'import.meta.env.SCOPEHUB_UBUNTU_PREVIEW': history,
      'import.meta.env.SCOPEHUB_UBUNTU_WORKSPACE': workspace,
    },
    server: history ? { host: '127.0.0.1', port: 1420, strictPort: true, cors: false } : {},
    clearScreen: false,
    build: { target: 'es2022' },
  };
});
