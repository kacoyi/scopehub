import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type {
  Bar, CancelTaskReply11, DeclarativeDraft11, FrozenSnapshot, IndicatorCreateDraftCommand11,
  IndicatorCreateDraftReply11, IndicatorRunPreviewCommand11, IndicatorRunPreviewReply11,
  IndicatorResult, MutationScope11, PreviewReady11, TaskTerminalEvent11,
} from '../../../../contracts/generated/protocol';
import { IndicatorAcceptance } from './indicator-acceptance';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';
import { exchangeWorkspace, type LocalWorkspace } from './use-local-workspace';

type Ready = {
  status: 'ready'; task_id: string; event: PreviewReady11; source_snapshot: FrozenSnapshot;
  resolved_parameters: Record<string, unknown>; warmup_from_ms: number | null; calendar_hash: string | null;
};
type Waiting = { status: 'running'; task_id: string };
type Terminal = { status: 'terminal'; task_id: string; event: TaskTerminalEvent11 };

type Field = 'close' | 'volume';
const draftFor = (field: Field): DeclarativeDraft11 => ({
  language: 'declarative',
  source: {
    parameters: [{ type: 'integer', name: 'period', default: 2 }],
    outputs: [
      { kind: 'line', id: 'mean', pane: field === 'close' ? 'price' : 'volume-average', style: { color: '#f0b866', width: 2 },
        value: { op: 'rolling_mean', value: { op: 'field', field }, window: { parameter: 'period' } } },
      { kind: 'line', id: 'standard-deviation', pane: 'volatility', style: { color: '#95adff', width: 2 },
        value: { op: 'rolling_std', value: { op: 'field', field }, window: { parameter: 'period' } } },
    ],
  },
});

const validHash = (value: string) => /^[0-9a-f]{64}$/.test(value);
const command = () => ({
  protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
  request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
} as const);
const code = (error: unknown) => error && typeof error === 'object' && 'code' in error ? error.code : undefined;
const pause = () => new Promise<void>(resolve => setTimeout(resolve, 100));

type PreviewAttempt = { poll: AbortController; taskId?: string };

async function submit<T>(request: Parameters<typeof exchangeWorkspace>[0], signal: AbortSignal): Promise<T> {
  for (;;) {
    try {
      return await exchangeWorkspace<T>(request, signal);
    } catch (error) {
      if (code(error) !== 'BUSY') throw error;
      await pause();
      signal.throwIfAborted();
    }
  }
}

async function cancelTask(taskId: string) {
  const request = { ...command(), operation: 'task.cancel' as const, task_id: taskId };
  const reply = await submit<CancelTaskReply11>(request, AbortSignal.timeout(15_000));
  if (reply.request_id !== request.request_id) throw new Error();
  if ('error' in reply) {
    if (reply.error.code === 'STALE_CONTEXT') return;
    throw reply.error;
  }
  if (reply.task_id !== taskId) throw new Error();
}

function describeError(error: unknown) {
  switch (code(error)) {
    case 'CANCELLED': return 'Indicator preview cancelled.';
    case 'BUSY': return 'The workspace is busy. Try the preview again.';
    case 'INVALID_ARGUMENT': return 'Choose a valid period and visible interval.';
    case 'RESOURCE_LIMIT': return 'The indicator exceeded its limits. Try a smaller interval.';
    case 'STALE_CONTEXT': return 'The workspace changed. Open the frozen chart and run the preview again.';
    case 'PERMISSION_DENIED': return 'This source is unavailable for indicator calculation.';
    case 'TIMEOUT': return 'Indicator preview timed out.';
    default: return 'The indicator preview could not be completed.';
  }
}

type Props = {
  snapshot: FrozenSnapshot; bars: readonly Bar[]; workspace: LocalWorkspace; access?: PublishedRevisionAccess | undefined;
  showResult: (result: IndicatorResult | undefined) => void;
  showAccepted: (results: readonly IndicatorResult[]) => void;
};

export function IndicatorPreview({ snapshot, bars, workspace, access, showResult, showAccepted }: Props) {
  const heading = useId();
  const [period, setPeriod] = useState('2');
  const [field, setField] = useState<Field>('close');
  const [first, setFirst] = useState(bars[0]?.time_ms ?? snapshot.from_ms);
  const [last, setLast] = useState(bars.at(-1)?.time_ms ?? snapshot.from_ms);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Ready>();
  const [message, setMessage] = useState<{ text: string; error?: boolean }>();
  const active = useRef<PreviewAttempt | undefined>(undefined);
  const displayed = useRef<string | undefined>(undefined);
  const workspaceRevision = workspace.snapshot?.workspace.revision;
  const selectionRevision = workspace.snapshot?.selection_revision;

  const invalidateJobs = useCallback(() => {
    const attempt = active.current;
    attempt?.poll.abort();
    const obsolete = new Set([attempt?.taskId, displayed.current]);
    displayed.current = undefined;
    for (const id of obsolete) if (id) void cancelTask(id).catch(() => {});
  }, []);

  const clearPreview = useCallback(() => {
    invalidateJobs();
    showResult(undefined);
    setResult(undefined);
  }, [invalidateJobs, showResult]);

  useEffect(() => invalidateJobs, [invalidateJobs]);
  useLayoutEffect(() => {
    clearPreview();
  }, [snapshot.content_hash, workspaceRevision, selectionRevision, clearPreview]);

  async function preview() {
    const state = workspace.snapshot;
    const requestedPeriod = Number(period);
    if (!state || running || !Number.isSafeInteger(requestedPeriod) || requestedPeriod < 1 || first > last) return;
    clearPreview();
    const attempt: PreviewAttempt = { poll: new AbortController() };
    active.current = attempt;
    // Keep the submission alive long enough to identify and cancel an obsolete
    // task even if the editor changes before its task ID is received.
    const submission = AbortSignal.timeout(15_000);
    const scope: MutationScope11 = {
      workspace_id: state.workspace.id, target: { kind: 'chart', object_id: 'main-chart' },
      expected_workspace_revision: state.workspace.revision,
      expected_selection_revision: state.selection_revision,
      idempotency_key: crypto.randomUUID(),
    };
    const range = { from_ms: first, to_ms: last + 1 };
    setMessage(undefined);
    setRunning(true);
    try {
      const create: IndicatorCreateDraftCommand11 = {
        ...command(), task_id: null, scope, intent: { operation: 'indicator.createDraft', arguments: draftFor(field) },
      };
      const created = await submit<IndicatorCreateDraftReply11>(create, submission);
      if (attempt.poll.signal.aborted) return;
      if (created.request_id !== create.request_id) throw new Error();
      if (created.outcome.state !== 'committed') throw ('error' in created.outcome ? created.outcome.error : new Error());
      const { draft_id, code_hash } = created.outcome.result;
      const run: IndicatorRunPreviewCommand11 = {
        ...command(), task_id: null,
        scope: { ...scope, target: { kind: 'chart_interval', snapshot, range }, idempotency_key: crypto.randomUUID() },
        intent: { operation: 'indicator.runPreview', arguments: {
          draft_id, snapshot, parameters: [{ name: 'period', value: requestedPeriod }],
        } },
      };
      const started = await submit<IndicatorRunPreviewReply11>(run, submission);
      if (started.request_id !== run.request_id) throw new Error();
      if (started.outcome.state !== 'committed') throw ('error' in started.outcome ? started.outcome.error : new Error());
      const id = started.outcome.result.task_id;
      attempt.taskId = id;
      if (attempt.poll.signal.aborted) {
        void cancelTask(id).catch(() => {});
        return;
      }
      const signal = AbortSignal.any([attempt.poll.signal, AbortSignal.timeout(15_000)]);
      while (!signal.aborted) {
        await pause();
        let status: Ready | Waiting | Terminal;
        try {
          status = await exchangeWorkspace({ type: 'indicator_preview_status', request_id: crypto.randomUUID(), task_id: id }, signal);
        } catch (error) {
          if (code(error) === 'BUSY') continue;
          throw error;
        }
        signal.throwIfAborted();
        if (status.task_id !== id) throw new Error();
        if (status.status === 'running') continue;
        if (status.status === 'terminal') {
          const outcome = status.event.outcome;
          if (outcome.status === 'failed') throw outcome.error;
          throw { code: 'CANCELLED' };
        }
        const event = status.event;
        if (event.task_id !== id || event.draft_id !== draft_id || event.code_hash !== code_hash
          || !validHash(event.params_hash) || !validHash(event.snapshot.content_hash)
          || event.artifact.storage !== 'inline' || !validHash(event.artifact.result_hash)
          || (snapshot.series.provider_id === 'scopehub.synthetic' && (typeof status.calendar_hash !== 'string'
            || !validHash(status.calendar_hash)))
          || status.source_snapshot.content_hash !== snapshot.content_hash
          || event.snapshot.dataset_id !== snapshot.dataset_id || event.snapshot.dataset_revision !== snapshot.dataset_revision
          || event.snapshot.as_of_ms !== snapshot.as_of_ms || event.snapshot.from_ms !== range.from_ms
          || event.snapshot.to_ms !== range.to_ms || status.resolved_parameters.period !== requestedPeriod) throw new Error();
        if (access && !access.canDisplay(snapshot.dataset_id)) return;
        displayed.current = id;
        showResult(event.artifact.result);
        setResult(status);
        return;
      }
      signal.throwIfAborted();
    } catch (error) {
      if (attempt.poll.signal.aborted) return;
      if (displayAccessDenied(error)) void access?.verify(snapshot.dataset_id);
      setMessage({ text: describeError(error), error: code(error) !== 'CANCELLED' });
    } finally {
      if (active.current === attempt) {
        setRunning(false);
        active.current = undefined;
      }
    }
  }

  function revise(apply: () => void) {
    clearPreview();
    setMessage(undefined);
    apply();
  }

  function cancel() {
    clearPreview();
    setMessage({ text: 'Indicator preview cancelled.' });
  }

  function clearAfterAcceptance() {
    displayed.current = undefined;
    showResult(undefined);
    setResult(undefined);
  }

  const output = result?.event.artifact.storage === 'inline' ? result.event.artifact.result : undefined;
  const points = output?.series;
  const ready = result?.event.snapshot;
  const date = (time: number) => new Date(time).toISOString();
  return <section className="local-import-card" aria-labelledby={heading}>
    <h2 id={heading}>Indicator preview</h2>
    <div className="local-publication-actions">
      <label>Indicator input <select value={field} onChange={event => {
        const value = event.currentTarget.value;
        if (value === 'close' || value === 'volume') revise(() => setField(value));
      }}>
        <option value="close">Close</option>
        <option value="volume">Volume</option>
      </select></label>
      <label>Rolling period <input type="number" min="1" max="100000" value={period}
        onChange={event => { const value = event.currentTarget.value; revise(() => setPeriod(value)); }} /></label>
      <label>First visible bar <select value={first} onChange={event => { const value = Number(event.currentTarget.value); revise(() => setFirst(value)); }}>
        {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{date(bar.time_ms)}</option>)}
      </select></label>
      <label>Last visible bar <select value={last} onChange={event => { const value = Number(event.currentTarget.value); revise(() => setLast(value)); }}>
        {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{date(bar.time_ms)}</option>)}
      </select></label>
      <button type="button" className="secondary-button" disabled={running || workspace.pending !== undefined
        || !workspace.snapshot || !!workspace.error || Number(period) < 1 || Number(period) > 100000
        || !Number.isSafeInteger(Number(period)) || first > last}
        onClick={() => void preview()}>Run preview</button>
      {running && <button type="button" className="secondary-button" onClick={cancel}>Cancel preview</button>}
      {result && <button type="button" className="secondary-button" onClick={clearPreview}>Clear preview</button>}
    </div>
    {running && <p role="status">Calculating rolling mean and population standard deviation…</p>}
    {message && <p role={message.error ? 'alert' : 'status'}>{message.text}</p>}
    {ready && points && <div>
      <p role="status">Preview layers · rolling mean and population standard deviation</p>
      <p>Period {String(result.resolved_parameters.period)} · {ready.warmup_bars} warm-up bars</p>
      {output?.diagnostics.map(diagnostic => <p key={diagnostic.code}>{diagnostic.message}</p>)}
      <details>
        <summary>Preview provenance and values</summary>
        <dl>
          <dt>Input hash</dt><dd><code>{ready.content_hash}</code></dd>
          {result.calendar_hash && <><dt>Schedule hash</dt><dd><code>{result.calendar_hash}</code></dd></>}
          <dt>Visible interval</dt><dd>{date(ready.from_ms)} to {date(ready.to_ms)} (exclusive)</dd>
          <dt>Warm-up start</dt><dd>{result.warmup_from_ms === null ? 'None' : date(result.warmup_from_ms)}</dd>
          <dt>Dataset revision</dt><dd><code>{ready.dataset_revision}</code></dd>
          <dt>Engine</dt><dd><code>{result.event.engine_version}</code></dd>
          <dt>Code hash</dt><dd><code>{result.event.code_hash}</code></dd>
          <dt>Parameter hash</dt><dd><code>{result.event.params_hash}</code></dd>
        </dl>
        <table aria-label="Indicator preview values">
          <thead><tr><th>Visible bar</th><th>Mean</th><th>Standard deviation</th></tr></thead>
          <tbody>{points[0]?.points.slice(0, 20).map((point, index) => <tr key={point.time_ms}>
            <th scope="row">{date(point.time_ms)}</th><td>{point.value ?? 'Unknown'}</td>
            <td>{points[1]?.points[index]?.value ?? 'Unknown'}</td>
          </tr>)}</tbody>
        </table>
      </details>
    </div>}
    {access && <IndicatorAcceptance preview={result?.event} snapshot={snapshot} workspace={workspace} access={access}
      clearPreview={clearAfterAcceptance} showAccepted={showAccepted} />}
  </section>;
}
