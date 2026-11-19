// Each preview HTTP operation has a 15 s watchdog. Admission can also wait
// up to 2.5 s for the previous worker to be reaped. Allow 0.5 s for delivery.
export const replayStage = { timeout: 18_000 };

export async function waitForReplayRequest(page, trigger) {
  const [request] = await Promise.all([
    page.waitForRequest(request => request.method() === 'POST'
      && new URL(request.url()).pathname === '/__scopehub/market-stream', replayStage),
    trigger(),
  ]);
  return request;
}
