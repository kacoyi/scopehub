// Native invoke can still settle after the caller stops waiting. Keep both
// handlers attached, while making a late result inert for this caller.
export async function waitForReply<T>(signal: AbortSignal, work: () => Promise<T>): Promise<T> {
  signal.throwIfAborted();
  let abort!: () => void;
  try {
    return await new Promise<T>((resolve, reject) => {
      abort = () => reject(signal.reason);
      signal.addEventListener('abort', abort, { once: true });
      void work().then(resolve, reject);
    });
  } finally {
    signal.removeEventListener('abort', abort);
  }
}

export async function withReplyDeadline<T>(duration: number, work: (signal: AbortSignal) => Promise<T>,
  caller?: AbortSignal): Promise<T> {
  const deadline = new AbortController();
  const signal = caller ? AbortSignal.any([caller, deadline.signal]) : deadline.signal;
  const timer = window.setTimeout(() => {
    deadline.abort(new DOMException('The reply did not arrive in time.', 'TimeoutError'));
  }, duration);
  try {
    return await waitForReply(signal, () => work(signal));
  } finally {
    window.clearTimeout(timer);
  }
}
