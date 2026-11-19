import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { ubuntuPreview } from './ubuntu-preview.mjs';

export default defineConfig(({ command, mode, isPreview }) => {
  const enabled = command === 'serve' && !isPreview && mode === 'ubuntu-preview';
  const testPreview = process.env.SCOPEHUB_TEST_PREVIEW === '1';
  return {
    plugins: [react(), ...(enabled ? [ubuntuPreview()] : [])],
    define: { 'import.meta.env.SCOPEHUB_UBUNTU_PREVIEW': enabled },
    server: enabled ? {
      host: '127.0.0.1', port: 1420, strictPort: true, cors: false,
      ...(testPreview ? { watch: null, hmr: false, ws: false } : {}),
    } : {},
    clearScreen: false,
    build: { target: 'es2022' },
  };
});
