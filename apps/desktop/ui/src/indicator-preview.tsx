import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type {
  Bar, CancelTaskReply11, ConfirmationChallenge11, DeclarativeDraft11, FrozenSnapshot, IndicatorCreateDraftCommand11,
  IndicatorCreateDraftReply11, IndicatorRunPreviewCommand11, IndicatorRunPreviewReply11,
  IndicatorResult, Metadata, MutationScope11, PreviewReady11, TaskTerminalEvent11, TrustedConfirmationDecision11,
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
type PythonFile = { name: string; source: string };
type PythonReview = {
  file: PythonFile; parameters: Metadata; input: readonly Bar[]; challenge: ConfirmationChallenge11;
  respond: (decision: TrustedConfirmationDecision11['decision']) => void;
};
const maxPythonCodeBytes = 2048;

async function textHash(source: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function parseParameters(text: string): Metadata {
  const parameters: unknown = JSON.parse(text, (_key: string, value: unknown) => {
    if (typeof value === 'number' && (!Number.isFinite(value)
        || (Number.isInteger(value) && !Number.isSafeInteger(value)))) throw new Error();
    return value;
  });
  if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) throw new Error();
  return parameters as Metadata;
}

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

function describeError(error: unknown, mode: 'declarative' | 'python') {
  switch (code(error)) {
    case 'CANCELLED': return 'Indicator preview cancelled.';
    case 'BUSY': return 'The workspace is busy. Try the preview again.';
    case 'INVALID_ARGUMENT': return mode === 'python' ? 'Check the Python parameters and visible interval.'
      : 'Choose a valid period and visible interval.';
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
  const [mode, setMode] = useState<'declarative' | 'python'>('declarative');
  const [pythonFile, setPythonFile] = useState<PythonFile>();
  const [pythonParameters, setPythonParameters] = useState('{}');
  const [review, setReview] = useState<PythonReview>();
  const fileSelection = useRef(0);
  const sample = snapshot.series.provider_id === 'scopehub.synthetic';
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
    setReview(undefined);
  }, [invalidateJobs, showResult]);

  useEffect(() => invalidateJobs, [invalidateJobs]);
  useLayoutEffect(() => {
    clearPreview();
  }, [snapshot.content_hash, workspaceRevision, selectionRevision, clearPreview]);

  async function preview() {
    const state = workspace.snapshot;
    const requestedPeriod = Number(period);
    if (!state || running || first > last) return;
    const selectedFile = mode === 'python' ? pythonFile : undefined;
    let parameters: Metadata = {};
    if (mode === 'python') {
      if (!sample || !selectedFile) return;
      try { parameters = parseParameters(pythonParameters); }
      catch { setMessage({ text: 'Enter a JSON object with valid Python parameters.', error: true }); return; }
      setPythonFile(undefined);
    } else if (!Number.isSafeInteger(requestedPeriod) || requestedPeriod < 1 || requestedPeriod > 100000) return;
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
        ...command(), task_id: null, scope, intent: {
          operation: 'indicator.createDraft', arguments: selectedFile
            ? { language: 'python', source: selectedFile.source, params: {} }
            : draftFor(field),
        },
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
          draft_id, snapshot, ...(selectedFile ? { python_params: parameters }
            : { parameters: [{ name: 'period', value: requestedPeriod }] }),
        } },
      };
      const started = await submit<IndicatorRunPreviewReply11>(run, submission);
      if (started.request_id !== run.request_id) throw new Error();
      const outcome = started.outcome;
      if (outcome.state === 'failed') throw outcome.error;
      const id = outcome.state === 'awaiting_confirmation' ? outcome.challenge.task_id : outcome.result.task_id;
      if (!id) throw new Error();
      attempt.taskId = id;
      attempt.poll.signal.throwIfAborted();
      let approved: ConfirmationChallenge11['python'] = null;
      if (outcome.state === 'awaiting_confirmation' && selectedFile) {
        const challenge = outcome.challenge;
        const target = challenge.target;
        const python = challenge.python;
        if (!python || challenge.request_id !== run.request_id || challenge.operation !== 'indicator.runPreview'
            || challenge.workspace_id !== scope.workspace_id
            || challenge.workspace_revision !== scope.expected_workspace_revision
            || challenge.selection_revision !== scope.expected_selection_revision
            || target.kind !== 'chart_interval' || target.range.from_ms !== first || target.range.to_ms !== last + 1
            || target.snapshot.from_ms !== first || target.snapshot.to_ms !== last + 1 || target.snapshot.warmup_bars !== 0
            || target.snapshot.dataset_id !== snapshot.dataset_id
            || target.snapshot.dataset_revision !== snapshot.dataset_revision
            || target.snapshot.as_of_ms !== snapshot.as_of_ms
            || python.trust_mode !== 'trusted_user_code_no_sandbox' || python.input_use !== 'trusted_code_permitted'
            || python.code_hash !== code_hash || python.parameters_hash !== challenge.parameters_hash
            || python.snapshot_hash !== target.snapshot.content_hash || await textHash(selectedFile.source) !== code_hash) throw new Error();
        attempt.poll.signal.throwIfAborted();
        const decision = await new Promise<TrustedConfirmationDecision11['decision']>((resolve, reject) => {
          attempt.poll.signal.addEventListener('abort', () => reject(attempt.poll.signal.reason), { once: true });
          setReview({ file: selectedFile, parameters, challenge, respond: resolve,
            input: bars.filter(bar => bar.time_ms >= first && bar.time_ms <= last) });
        });
        attempt.poll.signal.throwIfAborted();
        setReview(undefined);
        const response: TrustedConfirmationDecision11 = {
          ...command(), operation: 'confirmation.respond', challenge_id: challenge.challenge_id, decision,
        };
        const resolved = await submit<IndicatorRunPreviewReply11>(response, AbortSignal.timeout(15_000));
        attempt.poll.signal.throwIfAborted();
        if (resolved.request_id !== response.request_id) throw new Error();
        if (resolved.outcome.state === 'failed') throw resolved.outcome.error;
        if (decision !== 'approve' || resolved.outcome.state !== 'committed'
            || resolved.outcome.result.task_id !== id) throw new Error();
        approved = python;
      } else if (selectedFile || outcome.state !== 'committed') throw new Error();
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
          || event.snapshot.to_ms !== range.to_ms
          || (approved ? event.params_hash !== approved.parameters_hash || event.snapshot.content_hash !== approved.snapshot_hash
            || event.engine_version !== 'scopehub-trusted-python-1' || event.snapshot.warmup_bars !== 0
            : status.resolved_parameters.period !== requestedPeriod)) throw new Error();
        if (access && !access.canDisplay(snapshot.dataset_id)) return;
        displayed.current = id;
        showResult(event.artifact.result);
        setResult(status);
        return;
      }
      signal.throwIfAborted();
    } catch (error) {
      if (attempt.taskId) void cancelTask(attempt.taskId).catch(() => {});
      if (attempt.poll.signal.aborted) return;
      if (displayAccessDenied(error)) void access?.verify(snapshot.dataset_id);
      setMessage({ text: describeError(error, mode), error: code(error) !== 'CANCELLED' });
    } finally {
      if (active.current === attempt) {
        setRunning(false);
        setReview(undefined);
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

  async function selectPython(file: File | undefined) {
    const selection = ++fileSelection.current;
    revise(() => setPythonFile(undefined));
    if (!file) return;
    try {
      if (file.size > maxPythonCodeBytes || !file.name.toLowerCase().endsWith('.py')) throw new Error();
      const source = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
      if (!source.trim()) throw new Error();
      if (fileSelection.current === selection) setPythonFile({ name: file.name, source });
    } catch {
      if (fileSelection.current === selection) setMessage({ text: 'Choose a UTF-8 Python file up to 2 KB.', error: true });
    }
  }

  const output = result?.event.artifact.storage === 'inline' ? result.event.artifact.result : undefined;
  const points = output?.series;
  const ready = result?.event.snapshot;
  const date = (time: number) => new Date(time).toISOString();
  return <section className="local-import-card" aria-labelledby={heading}>
    <h2 id={heading}>Indicator preview</h2>
    {sample && <label>Indicator type <select value={mode} onChange={event => {
      const value = event.currentTarget.value;
      if (value === 'declarative' || value === 'python') revise(() => {
        fileSelection.current += 1;
        setPythonFile(undefined);
        setMode(value);
      });
    }}>
      <option value="declarative">Rolling statistics</option>
      <option value="python">Trusted Python</option>
    </select></label>}
    <div className="local-publication-actions">
      {mode === 'declarative' && <><label>Indicator input <select value={field} onChange={event => {
        const value = event.currentTarget.value;
        if (value === 'close' || value === 'volume') revise(() => setField(value));
      }}>
        <option value="close">Close</option>
        <option value="volume">Volume</option>
      </select></label>
      <label>Rolling period <input type="number" min="1" max="100000" value={period}
        onChange={event => { const value = event.currentTarget.value; revise(() => setPeriod(value)); }} /></label></>}
      {mode === 'python' && <>
        <label>Select Python file <input type="file" accept=".py,text/x-python" onChange={event => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          void selectPython(file);
        }} /></label>
        {pythonFile && <span>{pythonFile.name}</span>}
        <label>Python parameters <textarea value={pythonParameters} onChange={event => {
          const value = event.currentTarget.value; revise(() => setPythonParameters(value));
        }} /></label>
      </>}
      <label>First visible bar <select value={first} onChange={event => { const value = Number(event.currentTarget.value); revise(() => setFirst(value)); }}>
        {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{date(bar.time_ms)}</option>)}
      </select></label>
      <label>Last visible bar <select value={last} onChange={event => { const value = Number(event.currentTarget.value); revise(() => setLast(value)); }}>
        {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{date(bar.time_ms)}</option>)}
      </select></label>
      <button type="button" className="secondary-button" disabled={running || workspace.pending !== undefined
        || !workspace.snapshot || !!workspace.error || first > last
        || (mode === 'python' ? !pythonFile : Number(period) < 1 || Number(period) > 100000
          || !Number.isSafeInteger(Number(period)))}
        onClick={() => void preview()}>{mode === 'python' ? 'Review Python run' : 'Run preview'}</button>
      {running && <button type="button" className="secondary-button" onClick={cancel}>Cancel preview</button>}
      {result && <button type="button" className="secondary-button" onClick={clearPreview}>Clear preview</button>}
    </div>
    {review && <div role="group" aria-label="Confirm trusted Python run">
      <p>Run {review.file.name} once to calculate an indicator on this synthetic chart.</p>
      <p>Trusted Python can read or change files and access the network with your user permissions.</p>
      <pre aria-label="Exact Python code">{review.file.source}</pre>
      <p>Resolved parameters <code>{JSON.stringify(review.parameters)}</code></p>
      <p>Visible interval {date(first)} to {date(last + 1)} (exclusive) · {review.input.length} bars · 0 warm-up bars</p>
      <p>Code hash <code>{review.challenge.python?.code_hash}</code></p>
      <p>Parameter hash <code>{review.challenge.parameters_hash}</code></p>
      <p>Frozen input hash <code>{review.challenge.python?.snapshot_hash}</code></p>
      <p>Dataset revision <code>{snapshot.dataset_revision}</code> · As of {date(snapshot.as_of_ms)}</p>
      <details><summary>Exact frozen input bars</summary><pre>{JSON.stringify(review.input, null, 2)}</pre></details>
      <button type="button" className="secondary-button" onClick={() => review.respond('approve')}>Run selected Python</button>
      <button type="button" className="secondary-button" onClick={() => review.respond('deny')}>Decline Python run</button>
    </div>}
    {running && !review && <p role="status">{mode === 'python' ? 'Preparing or calculating trusted Python preview…'
      : 'Calculating rolling mean and population standard deviation…'}</p>}
    {message && <p role={message.error ? 'alert' : 'status'}>{message.text}</p>}
    {ready && points && <div>
      <p role="status">Preview layers · {mode === 'python' ? 'trusted Python' : 'rolling mean and population standard deviation'}</p>
      <p>{mode === 'python' ? <>Parameters <code>{JSON.stringify(result.resolved_parameters)}</code></>
        : <>Period {String(result.resolved_parameters.period)}</>} · {ready.warmup_bars} warm-up bars</p>
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
        {mode === 'python' ? <ul aria-label="Indicator preview values">{points.map(series => <li key={series.id}>
          {series.id} · {series.points.slice(0, 20).map(point => point.value ?? 'Unknown').join(', ')}
        </li>)}</ul> : <table aria-label="Indicator preview values">
          <thead><tr><th>Visible bar</th><th>Mean</th><th>Standard deviation</th></tr></thead>
          <tbody>{points[0]?.points.slice(0, 20).map((point, index) => <tr key={point.time_ms}>
            <th scope="row">{date(point.time_ms)}</th><td>{point.value ?? 'Unknown'}</td>
            <td>{points[1]?.points[index]?.value ?? 'Unknown'}</td>
          </tr>)}</tbody>
        </table>}
      </details>
    </div>}
    {(access || sample) && <IndicatorAcceptance preview={access || mode === 'python' ? result?.event : undefined} snapshot={snapshot} workspace={workspace} access={access}
      clearPreview={clearAfterAcceptance} showAccepted={showAccepted} />}
  </section>;
}
