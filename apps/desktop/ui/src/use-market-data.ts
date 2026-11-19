import { useEffect, useMemo, useState } from 'react';
import { canRetry, errorText, historyReader, readSampleHistory, type Instrument, type ChartHistory } from './market';

export type Load<T> = { state: 'loading' } | { state: 'error'; message: string; retryable: boolean } | { state: 'ready'; value: T };

export function useSampleCatalog(enabled: boolean) {
  const [catalog, setCatalog] = useState<Load<Instrument[]>>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setCatalog({ state: 'loading' });
    historyReader.read('instrument.search', { query: '', limit: 50 }, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) setCatalog({ state: 'ready', value: value.instruments });
      })
      .catch(error => {
        if (!controller.signal.aborted) setCatalog({ state: 'error', message: errorText(error), retryable: canRetry(error) });
      });
    return () => controller.abort();
  }, [enabled, attempt]);
  return { catalog, retryCatalog: () => setAttempt(value => value + 1) };
}

export function useHistory(instrument: Instrument | undefined, timeframe: string) {
  const [attempt, setAttempt] = useState(0);
  const request = useMemo(() => ({}), [instrument, timeframe, attempt]);
  const [reply, setReply] = useState<{ request: object; data: Load<ChartHistory> }>();
  useEffect(() => {
    if (!instrument) return;
    const controller = new AbortController();
    readSampleHistory(instrument, timeframe, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setReply({ request, data: { state: 'ready', value } });
      })
      .catch(error => {
        if (!controller.signal.aborted) setReply({ request, data: { state: 'error', message: errorText(error), retryable: canRetry(error) } });
      });
    return () => controller.abort();
  }, [instrument, timeframe, request]);
  // A selection changes before its effect runs. Never paint the preceding series under its new symbol.
  const result: Load<ChartHistory> = reply?.request === request ? reply.data : { state: 'loading' };
  return { result, retryHistory: () => setAttempt(value => value + 1) };
}
