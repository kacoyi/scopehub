import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { AiTextChartEvidence, AiTextRouteChoice, VisibleRange } from '../../../../contracts/generated/protocol';
import { exchangeModel, modelErrorCode, type ModelChartSelection, type ModelConnection, type ModelResult, type ModelRoute } from './model-chat-transport';
import './model-chat.css';

export type ModelChatView = 'question' | 'settings';
type Proposal = ModelResult<'propose'>;
type PendingTurn = {
  id: string;
  connection: ModelConnection;
  question: string | undefined;
  selection: ModelChartSelection | undefined;
};
type CompletedTurn = {
  id: string;
  question: string | undefined;
  model: string;
  text: string;
  evidence: AiTextChartEvidence | undefined;
  chartRequested: boolean;
};

const unavailableModel = 'The model could not answer. Check the configured server and try again.';
const unknownTurn = 'The workspace could not confirm this turn. Check its status before sending again.';
const deniedChart = 'Selected chart data is unavailable to this model connection.';
const staleChart = 'The chart selection changed. Send the question again for the current selection.';
const reselectChart = 'The chart selection changed. Close Ask AI and select the bar again. If prompted, reload workspace state, then select the bar before reopening Ask AI.';
const pollIntervalMs = 200;
const busyPollLimit = 10;

function modelFailure(error: unknown) {
  switch (modelErrorCode(error)) {
    case 'INVALID_ARGUMENT': return 'Enter a valid server origin and model name.';
    case 'CONFIRMATION_REQUIRED': return 'Review the connection again before enabling it.';
    case 'PERMISSION_DENIED':
    case 'STALE_CONTEXT':
    case 'PROVIDER_NOT_CONFIGURED': return 'This connection has expired. Open AI settings to enable a route.';
    case 'BUSY': return 'An answer is already in progress for this connection.';
    case 'TIMEOUT': return 'The configured model took too long to answer. Try again when the server is ready.';
    case 'DATA_UNAVAILABLE':
    case 'OUTPUT_INVALID':
    case 'WORKER_EXITED': return unavailableModel;
    case 'RESOURCE_LIMIT': return 'The question or response exceeded the text limit.';
    case 'CANCELLED': return 'Turn cancelled. Connect and enable again to send another question.';
    default: return 'The local model connection could not be updated.';
  }
}

function RouteDisclosure({ route }: { route: ModelRoute }) {
  return <>
    <dl className="model-route">
      <dt>Provider</dt><dd>{route.kind === 'ollama' ? 'Ollama' : 'Offline mock'}</dd>
      <dt>Destination</dt><dd><bdi>{route.destination}</bdi></dd>
      <dt>Model</dt><dd><bdi>{route.model}</bdi></dd>
      <dt>Scope</dt><dd>Typed questions and up to four earlier question and answer pairs in this session.{' '}
        {route.scope === 'conversation_text_and_sample_closes'
          ? 'The model can request close prices and times from the selected interval of the offline sample. CSV data and saved private records stay local.'
          : 'Chart data and saved private records stay local.'}</dd>
      <dt>Charges</dt><dd>{route.charge_basis === 'none' ? 'None' : 'Set by the server operator. Check its fees before enabling.'}</dd>
    </dl>
    <p className="model-muted">This connection lasts for this app session. Enable it again after restarting ScopeHub.</p>
    {route.kind === 'ollama' && <p className="model-muted">The server operator may process or forward requests, even at a loopback address. Disconnect in AI settings to stop future sends.</p>}
  </>;
}

function intervalLabel(range: VisibleRange) {
  const start = new Date(range.from_ms).toISOString();
  return range.to_ms === range.from_ms + 1 ? `Bar starting ${start}`
    : `${start} to ${new Date(range.to_ms).toISOString()} (end excluded)`;
}

function ChartEvidence({ evidence }: { evidence: AiTextChartEvidence }) {
  return <section className="model-evidence" aria-label="Chart evidence used">
    <h3>Chart evidence used</h3>
    <dl className="model-route">
      <dt>Interval (UTC)</dt><dd>{intervalLabel(evidence.range)}</dd>
      <dt>Offline sample</dt><dd><bdi>{evidence.source_reference}</bdi></dd>
      <dt>Revision</dt><dd><bdi>{evidence.revision}</bdi></dd>
    </dl>
    <table aria-label="Selected chart closes">
      <thead><tr><th scope="col">Bar start (UTC)</th><th scope="col">Close</th></tr></thead>
      <tbody>{evidence.rows.map(row => {
        const time = new Date(row.time_ms).toISOString();
        return <tr key={row.time_ms}><td><time dateTime={time}>{time}</time></td><td>{row.close}</td></tr>;
      })}</tbody>
    </table>
  </section>;
}

export function ModelChat({ workspaceId, theme, view, show, chartSelection, invalidChartSelectionRevision, discardChartSelection }: {
  workspaceId: string | undefined;
  theme: 'light' | 'dark' | undefined;
  view: ModelChatView | undefined;
  show: (view: ModelChatView | undefined) => void;
  chartSelection: ModelChartSelection | undefined;
  invalidChartSelectionRevision: number | undefined;
  discardChartSelection: () => void;
}) {
  const heading = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const questionField = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const action = useRef<AbortController | undefined>(undefined);
  const pollController = useRef<AbortController | undefined>(undefined);
  const pollInFlight = useRef<Promise<void> | undefined>(undefined);
  const lastTurn = useRef<string | undefined>(undefined);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [connection, setConnection] = useState<ModelConnection>();
  const [proposal, setProposal] = useState<Proposal>();
  const [changing, setChanging] = useState(false);
  const [origin, setOrigin] = useState('');
  const [model, setModel] = useState('');
  const [chartTools, setChartTools] = useState(false);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<CompletedTurn[]>([]);
  const [pending, setPending] = useState<PendingTurn>();
  const [unconfirmed, setUnconfirmed] = useState<PendingTurn>();
  const [staleSelectionRevision, setStaleSelectionRevision] = useState<number>();
  const visible = view !== undefined;
  const chartScope = connection?.route.scope === 'conversation_text_and_sample_closes';
  const selection = chartScope ? chartSelection : undefined;
  const invalidSelectionRevision = chartScope ? invalidChartSelectionRevision : undefined;
  const staleChartDraft = staleSelectionRevision !== undefined
    && (selection === undefined || selection.selectionRevision === staleSelectionRevision);
  const mustReselect = Boolean(connection) && (invalidSelectionRevision !== undefined || staleChartDraft);

  useEffect(() => {
    if (invalidSelectionRevision !== undefined && question.trim()) setStaleSelectionRevision(invalidSelectionRevision);
  }, [invalidSelectionRevision, question]);

  useEffect(() => () => {
    action.current?.abort();
    pollController.current?.abort();
  }, []);

  async function perform(work: (id: string, signal: AbortSignal) => Promise<void>, clearFeedback = true) {
    if (!workspaceId || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setWorking(true);
    if (clearFeedback) {
      setError(undefined);
      setNotice(undefined);
    }
    try {
      // Drain a poll before issuing a command on the single-request workspace transport.
      await pollInFlight.current;
      controller.signal.throwIfAborted();
      await work(workspaceId, controller.signal);
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(modelFailure(failure));
      const code = modelErrorCode(failure);
      if (code === 'CONFIRMATION_REQUIRED') setProposal(undefined);
      if (['PERMISSION_DENIED', 'STALE_CONTEXT', 'PROVIDER_NOT_CONFIGURED', 'CANCELLED'].includes(String(code))) {
        clearSession();
        setProposal(undefined);
      }
    } finally {
      if (action.current === controller) action.current = undefined;
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  function clearSession() {
    setConnection(undefined);
    setTurns([]);
    setPending(undefined);
    setUnconfirmed(undefined);
    lastTurn.current = undefined;
  }

  function applyStatus(status: ModelResult<'status'>, attempted: PendingTurn | undefined) {
    const active = status.connection ?? undefined;
    if (connection?.connection_id !== active?.connection_id) clearSession();
    setConnection(active);
    setUnconfirmed(undefined);
    const recovered = Boolean(attempted && status.turn_id === attempted.id
      && active?.connection_id === attempted.connection.connection_id);
    if (active && status.turn_id && status.turn_id !== pending?.id && status.turn_id !== lastTurn.current) {
      setPending({
        id: status.turn_id, connection: active, question: recovered ? attempted?.question : undefined,
        selection: recovered ? attempted?.selection : undefined,
      });
    }
    return recovered;
  }

  function refresh() {
    void perform(async (id, signal) => {
      try {
        const status = await exchangeModel(id, { action: 'status' }, signal);
        applyStatus(status, unconfirmed);
        if (unconfirmed) setError(undefined);
      } catch (failure) {
        if (!unconfirmed || signal.aborted) throw failure;
        setError(unknownTurn);
      }
    }, false);
  }

  useEffect(() => {
    if (!visible) return;
    dialog.current?.showModal();
    refresh();
  }, [visible]);

  useEffect(() => {
    if (view === 'question' && !working && !pending) questionField.current?.focus();
  }, [view, working, pending]);

  useEffect(() => {
    const log = transcript.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns, view]);

  useEffect(() => {
    if (!workspaceId || !pending || working) return;
    const controller = new AbortController();
    pollController.current = controller;
    const { id, connection: active, question: submitted, selection: selected } = pending;
    let superseded = false;
    let timer: number | undefined;
    let busyPolls = 0;
    async function poll() {
      if (!workspaceId || superseded || action.current) return;
      const reply = exchangeModel(workspaceId, {
        action: 'poll', connection_id: active.connection_id, turn_id: id,
      }, controller.signal);
      const drained = reply.then(() => {}, () => {});
      pollInFlight.current = drained;
      try {
        const { turn } = await reply;
        if (superseded || action.current) return;
        if (turn.state === 'running') {
          busyPolls = 0;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        lastTurn.current = id;
        setPending(undefined);
        if (turn.state === 'failed') {
          if (selected && turn.error.code === 'STALE_CONTEXT') setStaleSelectionRevision(selected.selectionRevision);
          setError(selected && turn.error.code === 'STALE_CONTEXT' ? staleChart
            : selected && turn.error.code === 'PERMISSION_DENIED' ? deniedChart
            : turn.error.code === 'PROVIDER_NOT_CONFIGURED' ? unavailableModel : modelFailure(turn.error));
          return;
        }
        const evidence = turn.chart_evidence;
        if (evidence && selected && (evidence.source_reference !== selected.sourceReference
          || evidence.revision !== selected.datasetRevision || evidence.range.from_ms !== selected.range.from_ms
          || evidence.range.to_ms !== selected.range.to_ms)) throw { code: 'OUTPUT_INVALID' };
        setTurns(previous => [...previous, {
          id, question: submitted, model: turn.model, text: turn.text, evidence, chartRequested: Boolean(selected),
        }]);
        if (submitted !== undefined) {
          setQuestion('');
          setStaleSelectionRevision(undefined);
        }
      } catch (failure) {
        if (superseded || action.current) return;
        if (modelErrorCode(failure) === 'BUSY' && busyPolls < busyPollLimit) {
          busyPolls += 1;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        clearSession();
        setError(modelFailure(failure));
      } finally {
        if (pollInFlight.current === drained) pollInFlight.current = undefined;
      }
    }
    void poll();
    return () => {
      superseded = true;
      window.clearTimeout(timer);
    };
  }, [workspaceId, pending, working]);

  function review(route: AiTextRouteChoice) {
    void perform(async (id, signal) => {
      const candidate = await exchangeModel(id, { action: 'propose', route }, signal);
      clearSession();
      setChanging(false);
      if (candidate.route.kind === 'offline_mock') {
        const reply = await exchangeModel(id, { action: 'activate', proposal_id: candidate.proposal_id }, signal);
        setConnection(reply.connection);
        setStaleSelectionRevision(undefined);
        show('question');
      } else {
        setProposal(candidate);
      }
    });
  }

  function validateOrigin(event: FormEvent) {
    event.preventDefault();
    const candidate = origin.trim();
    let url: URL | undefined;
    try { url = new URL(candidate); } catch { /* Report the invalid field below. */ }
    if (!url || url.username || url.password || url.pathname !== '/' || /[?#]/.test(candidate)
      || !['http:', 'https:'].includes(url.protocol)) {
      setError('Enter a server origin, such as http://127.0.0.1:11434, without a path or sign-in details.');
      return;
    }
    review({ kind: 'ollama', origin: candidate, model: model.trim(), ...(chartTools && { chart_tools: true }) });
  }

  function activate(candidate: Proposal) {
    void perform(async (id, signal) => {
      const reply = await exchangeModel(id, { action: 'activate', proposal_id: candidate.proposal_id }, signal);
      setConnection(reply.connection);
      setStaleSelectionRevision(undefined);
      setProposal(undefined);
      show('question');
    });
  }

  function disconnect(active: ModelConnection) {
    void perform(async (id, signal) => {
      await exchangeModel(id, { action: 'disconnect', connection_id: active.connection_id }, signal);
      clearSession();
      setNotice('Disconnected.');
    });
  }

  function cancel(turn: PendingTurn) {
    void perform(async (id, signal) => {
      await exchangeModel(id, { action: 'cancel', connection_id: turn.connection.connection_id, turn_id: turn.id }, signal);
      clearSession();
      setNotice('Turn cancelled. Connect and enable again to send another question.');
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const submitted = question.trim();
    if (!submitted || pending || working || unconfirmed || mustReselect) return;
    if (!connection) {
      setError(undefined);
      setNotice(undefined);
      show('settings');
      return;
    }
    const active = connection;
    void perform(async (id, signal) => {
      const turn = { id: crypto.randomUUID(), connection: active, question: submitted, selection };
      try {
        await exchangeModel(id, {
          action: 'send', connection_id: active.connection_id, question: submitted,
          ...(selection && { selection_revision: selection.selectionRevision }),
        }, signal, turn.id);
        setPending(turn);
      } catch (failure) {
        const code = String(modelErrorCode(failure));
        if (selection && code === 'PERMISSION_DENIED') {
          setError(deniedChart);
          return;
        }
        if (selection && code === 'STALE_CONTEXT') {
          const status = await exchangeModel(id, { action: 'status' }, signal).catch(() => {
            signal.throwIfAborted();
            return undefined;
          });
          setStaleSelectionRevision(selection.selectionRevision);
          if (status && status.connection?.connection_id !== active.connection_id) {
            applyStatus(status, undefined);
            setError(status.connection ? 'The model connection changed. Review the active route in AI settings.'
              : modelFailure(failure));
            return;
          }
          setError(staleChart);
          return;
        }
        if (signal.aborted || ['INVALID_ARGUMENT', 'PERMISSION_DENIED', 'STALE_CONTEXT', 'RESOURCE_LIMIT'].includes(code)) throw failure;
        try {
          const status = await exchangeModel(id, { action: 'status' }, signal);
          if (!applyStatus(status, turn)) setError(modelFailure(failure));
        } catch (statusFailure) {
          if (signal.aborted) throw statusFailure;
          setUnconfirmed(turn);
          setError(unknownTurn);
        }
      }
    });
  }

  function startTextQuestion() {
    discardChartSelection();
    setStaleSelectionRevision(undefined);
    setQuestion('');
    setError(undefined);
    questionField.current?.focus();
  }

  function leaveSettings(next: ModelChatView | undefined) {
    setProposal(undefined);
    setChanging(false);
    setError(undefined);
    setNotice(undefined);
    show(next);
  }

  const feedback = view === 'question' && mustReselect ? reselectChart : error;
  if (!view) return null;
  return <dialog ref={dialog} className="symbol-dialog decision-dialog model-dialog" data-layout-theme={theme}
    aria-labelledby={heading} onCancel={event => { event.preventDefault(); if (!working) leaveSettings(undefined); }}>
    <div className="dialog-heading">
      <h2 id={heading}>{view === 'question' ? 'Ask AI' : 'AI settings'}</h2>
      <div className="model-actions">
        {view === 'question' && <button type="button" className="secondary-button" disabled={working}
          onClick={() => show('settings')}>AI settings</button>}
        <button type="button" className="secondary-button" disabled={working} onClick={() => leaveSettings(undefined)}>Close</button>
      </div>
    </div>
    <div className="model-body">
      {feedback && <p className="model-error" role="alert">{feedback}</p>}
      {notice && <p role="status">{notice}</p>}
      {view === 'question' ? <>
        <p className="model-muted" role="status">{connection?.route.kind === 'offline_mock' ? 'Offline mock enabled'
          : connection ? <>Ollama connected · <bdi>{connection.route.model}</bdi></> : 'No model connected'}</p>
        <div ref={transcript} className="model-transcript" role="log" aria-label="Conversation">
          {turns.map(turn => <article key={turn.id} className="model-turn">
            {turn.question && <div className="model-question"><h3>You</h3><p dir="auto">{turn.question}</p></div>}
            <div className="model-answer"><h3>Model <bdi>{turn.model}</bdi></h3><p dir="auto">{turn.text}</p></div>
            {turn.evidence ? <ChartEvidence evidence={turn.evidence} />
              : turn.chartRequested && <p className="model-muted">No chart evidence used.</p>}
          </article>)}
        </div>
        <form className="model-form" onSubmit={submit}>
          {chartScope && !mustReselect && <p className="model-muted">
            {selection ? `Selected chart · ${intervalLabel(selection.range)}` : 'No chart selection for this turn.'}
          </p>}
          <label>Question<textarea ref={questionField} rows={3} maxLength={2000} required value={question}
            disabled={Boolean(pending || unconfirmed) || working} onChange={event => setQuestion(event.currentTarget.value)} /></label>
          <div className="model-actions">
            <button type="submit" className="primary" disabled={Boolean(pending || unconfirmed) || working || mustReselect || !question.trim()}>{connection ? 'Send' : 'Ask'}</button>
            {mustReselect && <button type="button" className="secondary-button" disabled={Boolean(pending || unconfirmed) || working}
              onClick={startTextQuestion}>Start a text-only question</button>}
            {unconfirmed && <button type="button" className="secondary-button" disabled={working} onClick={refresh}>Check turn status</button>}
            {pending && <><button type="button" className="secondary-button" disabled={working} onClick={() => cancel(pending)}>Cancel turn</button>
              <span role="status">Waiting for the model…</span></>}
          </div>
        </form>
      </> : <>
        {!workspaceId && <p className="model-muted">Open the local workspace to connect a model or use the offline mock.</p>}
        {proposal ? <section className="model-card" aria-label="Review model connection">
          <RouteDisclosure route={proposal.route} />
          <div className="model-actions">
            <button type="button" className="primary" disabled={working} onClick={() => activate(proposal)}>Connect and enable</button>
            <button type="button" className="secondary-button" disabled={working} onClick={() => setProposal(undefined)}>Edit connection</button>
          </div>
        </section> : connection && !changing ? <section className="model-card" aria-label="Active model connection">
          <RouteDisclosure route={connection.route} />
          <div className="model-actions">
            <button type="button" className="secondary-button" disabled={working} onClick={() => disconnect(connection)}>Disconnect</button>
            <button type="button" className="secondary-button" disabled={working} onClick={() => setChanging(true)}>Change connection</button>
          </div>
        </section> : <>
          <form className="model-form model-card" onSubmit={validateOrigin}>
            <h3>Ollama text server</h3>
            <p className="model-muted">Use a server you control that accepts requests without sign-in. Use HTTPS for a remote server.</p>
            <label>Server origin<input type="url" required autoComplete="off" spellCheck={false} maxLength={512}
              placeholder="http://127.0.0.1:11434" value={origin} disabled={!workspaceId || working}
              onChange={event => setOrigin(event.currentTarget.value)} /></label>
            <label>Model<input required autoComplete="off" spellCheck={false} maxLength={128}
              placeholder="Model name on the server" value={model} disabled={!workspaceId || working}
              onChange={event => setModel(event.currentTarget.value)} /></label>
            <label className="model-checkbox"><input type="checkbox" checked={chartTools} disabled={!workspaceId || working}
              onChange={event => setChartTools(event.currentTarget.checked)} />Allow the selected offline sample’s close prices and times</label>
            <div className="model-actions">
              <button type="submit" className="primary" disabled={!workspaceId || working || !origin.trim() || !model.trim()}>Review connection</button>
            </div>
          </form>
          <section className="model-card" aria-label="Offline demonstration">
            <p className="model-muted">Try canned replies on this device, without a model server or service charges.</p>
            <div className="model-actions">
              <button type="button" className="secondary-button" disabled={!workspaceId || working}
                onClick={() => review({ kind: 'offline_mock' })}>Use offline mock</button>
            </div>
          </section>
        </>}
        <div className="model-actions">
          <button type="button" className="secondary-button" disabled={working} onClick={() => leaveSettings('question')}>Back to question</button>
        </div>
      </>}
    </div>
  </dialog>;
}
