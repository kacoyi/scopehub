// Each preview HTTP operation has a 15 s watchdog. Admission can also wait
// up to 2.5 s for the previous worker to be reaped. Allow 0.5 s for delivery.
export const replayStage = { timeout: 18_000 };

export async function waitForReplayRequest(page, trigger) {
  let preparation;
  const observePreparation = response => {
    const request = response.request();
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/__scopehub/operation') return;
    let call;
    try { call = request.postDataJSON(); } catch { return; }
    if (call?.operation === 'market.readHistoryPage' && call.payload?.include_stream_position === true) preparation = response;
  };
  page.on('response', observePreparation);
  try {
    const [request] = await Promise.all([
      page.waitForRequest(request => request.method() === 'POST'
        && new URL(request.url()).pathname === '/__scopehub/market-stream', replayStage),
      trigger(),
    ]);
    return request;
  } catch (error) {
    if (error.name === 'TimeoutError' && preparation) {
      const body = await preparation.json().catch(() => undefined);
      if (body?.ok === false) {
        throw new Error(`Replay preparation returned HTTP ${preparation.status()} (${body.error?.code ?? 'unknown error'})`, { cause: error });
      }
    }
    throw error;
  } finally {
    page.off('response', observePreparation);
  }
}
