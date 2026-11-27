import desktopConfig from '../../apps/desktop/ui/vite.config.ts';

const prepareSample = {
  name: 'scopehub-prepare-market-recovery-preview',
  configureServer(server) {
    server.httpServer.once('listening', async () => {
      const started = performance.now();
      try {
        // Prepare source transforms without opening the sample or starting a market worker.
        const client = server.environments.client;
        const entries = ['/src/workspace-start.ts', '/src/main.tsx', '/src/style.css?direct'];
        for (const entry of entries) {
          if (!await client.transformRequest(entry)) throw new Error(`Missing preview entry ${entry}`);
        }
        await client.waitForRequestsIdle();
        console.log(`Market recovery preview ready in ${Math.round(performance.now() - started)} ms`);
      } catch (error) {
        console.error(error);
        process.exitCode = 1;
        await server.close();
      }
    });
  },
};

export default environment => {
  const config = desktopConfig(environment);
  return { ...config, plugins: [...config.plugins, prepareSample] };
};
