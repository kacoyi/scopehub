import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { AiTextChartEvidence, AiTextRouteChoice, VisibleRange } from '../../../../contracts/generated/protocol';
import { CloudCredentialSettings, providerName, RouteDisclosure } from './model-chat-settings';
import {
  exchangeModel, isModelWorkspaceBusy, modelErrorCode, openAiCredential, openAiDestination,
  type ModelChartSelection, type ModelConnection, type ModelResult, type OpenAiCredential,
} from './model-chat-transport';
import './model-chat.css';

export type ModelChatView = 'question' | 'settings';
type Proposal = ModelResult<'propose'>;
type PendingTurn = {
  id: string;
  connection: ModelConnection;
  question: string | undefined;
  selection: ModelChartSelection | undefined;
};
type TrackedTurn = PendingTurn & {
  observation: 'polling' | 'checking-send' | 'checking-poll';
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
const missingOpenAiKey = 'Enter an OpenAI API key in AI settings, then review the connection again.';
const unknownTurn = 'The workspace could not confirm this turn. Check its status to continue.';
const deniedChart = 'Selected chart data is unavailable to this model connection.';
const staleChart = 'The chart selection changed. Send the question again for the current selection.';
const reselectChart = 'The chart selection changed. Close Ask AI and select the bar again. If prompted, reload workspace state, then select the bar before reopening Ask AI.';
const pollIntervalMs = 200;
const busyPollLimit = 10;
const failedPollLimit = 2;

function modelFailure(error: unknown) {
  if (isModelWorkspaceBusy(error)) return 'The local workspace is busy. Try again when it is ready.';
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

function routeSetupFailure(error: unknown, kind: AiTextRouteChoice['kind'] | undefined) {
  if (kind === 'openai_responses') {
    switch (modelErrorCode(error)) {
      case 'INVALID_ARGUMENT': return 'Enter a valid OpenAI model ID.';
      case 'PROVIDER_NOT_CONFIGURED': return missingOpenAiKey;
    }
  }
  return modelFailure(error);
}

function textTurnFailure(connection: ModelConnection, error: unknown) {
  const code = modelErrorCode(error);
  if (connection.route.kind === 'openai_responses') {
    if (code === 'PROVIDER_NOT_CONFIGURED') return missingOpenAiKey;
    if (code === 'PERMISSION_DENIED') {
      return 'OpenAI rejected the API key or account permissions. Update the key in AI settings and review the connection again.';
    }
    if (code === 'INVALID_ARGUMENT') {
      return 'OpenAI could not use the selected model for this request. Check the model ID in AI settings and try again.';
    }
    if (['DATA_UNAVAILABLE', 'WORKER_EXITED', 'OUTPUT_INVALID'].includes(String(code))) {
      return 'OpenAI could not answer. Check that the selected model is available to your API account and try again.';
    }
  }
  return code === 'PROVIDER_NOT_CONFIGURED' ? unavailableModel : modelFailure(error);
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
  const [connectionUnconfirmed, setConnectionUnconfirmed] = useState(false);
  const [proposal, setProposal] = useState<Proposal>();
  const [changing, setChanging] = useState(false);
  const [provider, setProvider] = useState<'ollama' | 'openai_responses'>('ollama');
  const [origin, setOrigin] = useState('');
  const [ollamaModel, setOllamaModel] = useState('');
  const [openAiModel, setOpenAiModel] = useState('');
  const [credential, setCredential] = useState<OpenAiCredential>();
  const [chartTools, setChartTools] = useState(false);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<CompletedTurn[]>([]);
  const [trackedTurn, setTrackedTurn] = useState<TrackedTurn>();
  const pending = !connectionUnconfirmed && trackedTurn?.observation === 'polling' ? trackedTurn : undefined;
  const unconfirmed = trackedTurn !== undefined && pending === undefined;
  const cancellable = trackedTurn?.observation === 'checking-send' ? undefined : trackedTurn;
  const [staleSelectionRevision, setStaleSelectionRevision] = useState<number>();
  const visible = view !== undefined;
  const chartScope = connection?.route.scope === 'conversation_text_and_sample_closes';
  const selection = chartScope ? chartSelection : undefined;
  const usingOpenAi = provider === 'openai_responses';
  const model = usingOpenAi ? openAiModel : ollamaModel;
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

  async function perform(work: (id: string, signal: AbortSignal) => Promise<void>, clearFeedback = true,
    routeKind?: AiTextRouteChoice['kind'],
  ) {
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
      const code = modelErrorCode(failure);
      setError(routeSetupFailure(failure, routeKind));
      if (code === 'CONFIRMATION_REQUIRED') setProposal(undefined);
    } finally {
      if (action.current === controller) action.current = undefined;
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  function clearSession(preserveOutput = false) {
    setConnection(undefined);
    setConnectionUnconfirmed(false);
    if (!preserveOutput) setTurns([]);
    setTrackedTurn(undefined);
    if (trackedTurn?.question !== undefined) setQuestion('');
    lastTurn.current = undefined;
  }

  function applyStatus(status: ModelResult<'status'>, attempted = trackedTurn) {
    const active = status.connection ?? undefined;
    const recovered = active && attempted && status.turn_id === attempted.id
      && active.connection_id === attempted.connection.connection_id;
    if (connection?.connection_id !== active?.connection_id) clearSession(!active && !!attempted);
    setConnection(active);
    setConnectionUnconfirmed(false);
    if (active && status.turn_id && (!attempted || recovered) && status.turn_id !== lastTurn.current) {
      setTrackedTurn({ id: status.turn_id, connection: active, question: recovered ? attempted.question : undefined,
        selection: recovered ? attempted.selection : undefined, observation: 'polling' });
    } else {
      setTrackedTurn(undefined);
    }
    if (attempted && !recovered) {
      if (attempted.observation === 'checking-send') {
        setError('The workspace did not confirm accepting this question.');
      } else {
        if (attempted.question !== undefined) setQuestion('');
        setError('The workspace no longer has the result for this turn.');
      }
    } else if (recovered && attempted.observation !== 'polling') {
      setError(undefined);
    }
  }

  async function loadCredential(signal: AbortSignal) {
    try {
      setCredential(await openAiCredential({ action: 'status' }, signal));
    } catch (failure) {
      if (signal.aborted) return;
      setCredential(undefined);
      setError(modelErrorCode(failure) === 'BUSY' ? 'The workspace is busy. Check OpenAI key status again.'
        : 'ScopeHub could not check the OpenAI API key. Check the local workspace and try again.');
    }
  }

  async function checkConnectionAfterChange(id: string, signal: AbortSignal) {
    if (signal.aborted) return undefined;
    setConnectionUnconfirmed(true);
    try {
      const status = await exchangeModel(id, { action: 'status' }, signal);
      if (status.connection) applyStatus(status);
      else clearSession();
      return status;
    } catch {
      // Wait for a confirmed grant before showing a recipient or enabling sends.
      return undefined;
    }
  }

  function refresh() {
    void perform(async (id, signal) => {
      if (connectionUnconfirmed) {
        await checkConnectionAfterChange(id, signal);
        return;
      }
      try {
        const status = await exchangeModel(id, { action: 'status' }, signal);
        applyStatus(status);
        if (view === 'settings' && (usingOpenAi || status.connection?.route.kind === 'openai_responses')) {
          await loadCredential(signal);
        }
      } catch (failure) {
        if (!signal.aborted) {
          setError(isModelWorkspaceBusy(failure) ? modelFailure(failure)
            : trackedTurn ? unknownTurn : 'The workspace could not check the model connection.');
        }
      }
    }, false);
  }

  useEffect(() => {
    if (!visible) return;
    dialog.current?.showModal();
    refresh();
  }, [visible]);

  useEffect(() => {
    if (view === 'question' && !working && !connectionUnconfirmed && !trackedTurn) questionField.current?.focus();
  }, [view, working, connectionUnconfirmed, trackedTurn]);

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
    let failedPolls = 0;
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
          failedPolls = 0;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        const evidence = turn.state === 'completed' ? turn.chart_evidence : undefined;
        if (evidence && (!selected || evidence.source_reference !== selected.sourceReference
          || evidence.revision !== selected.datasetRevision || evidence.range.from_ms !== selected.range.from_ms
          || evidence.range.to_ms !== selected.range.to_ms)) throw { code: 'OUTPUT_INVALID' };
        lastTurn.current = id;
        setTrackedTurn(undefined);
        if (turn.state === 'failed') {
          if (selected && turn.error.code === 'STALE_CONTEXT') setStaleSelectionRevision(selected.selectionRevision);
          setError(selected && turn.error.code === 'STALE_CONTEXT' ? staleChart
            : selected && turn.error.code === 'PERMISSION_DENIED' ? deniedChart
            : textTurnFailure(active, turn.error));
          return;
        }
        setError(undefined);
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
        // Poll the same turn. The registry hands off its chart continuation once. Recovery never resubmits the question.
        if (modelErrorCode(failure) !== 'BUSY' && failedPolls < failedPollLimit) {
          failedPolls += 1;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        setTrackedTurn({ id, connection: active, question: submitted, selection: selected, observation: 'checking-poll' });
        setError(unknownTurn);
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
      let candidate: Proposal;
      try {
        candidate = await exchangeModel(id, { action: 'propose', route }, signal);
      } catch (failure) {
        const code = modelErrorCode(failure);
        if (code === 'INVALID_ARGUMENT' || code === 'PROVIDER_NOT_CONFIGURED' || isModelWorkspaceBusy(failure)) throw failure;
        const status = await checkConnectionAfterChange(id, signal);
        if (!status) return;
        if (status.connection) throw failure;
        setError('The workspace could not confirm the review. Review the connection again before enabling it.');
        return;
      }
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
    }, true, route.kind);
  }

  function reviewSettings(event: FormEvent) {
    event.preventDefault();
    if (usingOpenAi) {
      if (!credential?.configured) {
        setError(missingOpenAiKey);
        return;
      }
      review({ kind: 'openai_responses', model: model.trim() });
      return;
    }
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
    }, true, candidate.route.kind);
  }

  function disconnect(active: ModelConnection) {
    void perform(async (id, signal) => {
      try {
        await exchangeModel(id, { action: 'disconnect', connection_id: active.connection_id }, signal);
        clearSession();
      } catch (failure) {
        if (isModelWorkspaceBusy(failure)) throw failure;
        const status = await checkConnectionAfterChange(id, signal);
        if (!status) return;
        if (status.connection) throw failure;
      }
      setNotice('Disconnected.');
    });
  }

  function checkCredential() {
    void perform((_id, signal) => loadCredential(signal));
  }

  function openSettings() {
    show('settings');
    if (usingOpenAi || connection?.route.kind === 'openai_responses') checkCredential();
  }

  function updateCredential(args: { action: 'replace'; secret: string } | { action: 'remove' }) {
    void perform(async (id, signal) => {
      try {
        setCredential(await openAiCredential(args, signal));
        if (connection?.route.kind === 'openai_responses') {
          clearSession();
          setProvider('openai_responses');
        }
        if (proposal?.route.kind === 'openai_responses') setProposal(undefined);
        setNotice(args.action === 'remove' ? 'OpenAI API key removed.' : 'OpenAI API key saved.');
      } catch (failure) {
        if (signal.aborted) return;
        if (modelErrorCode(failure) === 'INVALID_ARGUMENT') {
          setError('Enter a valid OpenAI API key.');
          return;
        }
        setCredential(undefined);
        if (connection?.route.kind === 'openai_responses') {
          setProvider('openai_responses');
          await checkConnectionAfterChange(id, signal);
        }
        setError('ScopeHub could not confirm the OpenAI API key update. Check its status.');
      }
    });
  }

  function cancel(turn: TrackedTurn) {
    void perform(async (id, signal) => {
      try {
        await exchangeModel(id, { action: 'cancel', connection_id: turn.connection.connection_id, turn_id: turn.id }, signal);
      } catch (failure) {
        if (isModelWorkspaceBusy(failure)) throw failure;
        signal.throwIfAborted();
        const status = await exchangeModel(id, { action: 'status' }, signal).catch(() => undefined);
        signal.throwIfAborted();
        if (!status) {
          setTrackedTurn({ ...turn, observation: 'checking-poll' });
          setError(unknownTurn);
          return;
        }
        if (status.connection) {
          applyStatus(status, turn);
          throw failure;
        }
        clearSession(true);
        setNotice('The model connection ended. Connect and enable again to send another question.');
        return;
      }
      clearSession(true);
      setNotice('Turn cancelled. Connect and enable again to send another question.');
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const submitted = question.trim();
    if (!submitted || trackedTurn || working || mustReselect || connectionUnconfirmed) return;
    if (!connection) {
      setError(undefined);
      setNotice(undefined);
      openSettings();
      return;
    }
    const active = connection;
    void perform(async (id, signal) => {
      const turn = { id: crypto.randomUUID(), connection: active, question: submitted, selection, observation: 'checking-send' } satisfies TrackedTurn;
      try {
        await exchangeModel(id, {
          action: 'send', connection_id: active.connection_id, question: submitted,
          ...(selection && { selection_revision: selection.selectionRevision }),
        }, signal, turn.id);
        setTrackedTurn({ ...turn, observation: 'polling' });
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
        if (signal.aborted || isModelWorkspaceBusy(failure) || ['INVALID_ARGUMENT', 'RESOURCE_LIMIT'].includes(code)) throw failure;
        try {
          const status = await exchangeModel(id, { action: 'status' }, signal);
          applyStatus(status, turn);
        } catch (statusFailure) {
          if (signal.aborted) throw statusFailure;
          setTrackedTurn(turn);
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
  const cloudCredentialSettings = workspaceId && view === 'settings' && !proposal
    && (connection && !changing ? connection.route.kind === 'openai_responses' : usingOpenAi)
    ? <CloudCredentialSettings credential={credential} working={working} reload={checkCredential}
      replace={secret => updateCredential({ action: 'replace', secret })} remove={() => updateCredential({ action: 'remove' })} />
    : undefined;
  return <dialog ref={dialog} className="symbol-dialog decision-dialog model-dialog" data-layout-theme={theme}
    aria-labelledby={heading} onCancel={event => { event.preventDefault(); if (!working) leaveSettings(undefined); }}>
    <div className="dialog-heading">
      <h2 id={heading}>{view === 'question' ? 'Ask AI' : 'AI settings'}</h2>
      <div className="model-actions">
        {view === 'question' && <button type="button" className="secondary-button" disabled={working}
          onClick={openSettings}>AI settings</button>}
        <button type="button" className="secondary-button" disabled={working} onClick={() => leaveSettings(undefined)}>Close</button>
      </div>
    </div>
    <div className="model-body">
      {feedback && <p className="model-error" role="alert">{feedback}</p>}
      {notice && <p role="status">{notice}</p>}
      {connectionUnconfirmed ? <>
        <p role="status">The workspace could not confirm which model connection is active.</p>
        <div className="model-actions">
          <button type="button" className="secondary-button" disabled={working} onClick={refresh}>Check connection status</button>
        </div>
      </> : view === 'question' ? <>
        <p className="model-muted" role="status">{connection?.route.kind === 'offline_mock' ? 'Offline mock enabled'
          : connection ? <>{providerName(connection.route.kind)} connected · <bdi>{connection.route.model}</bdi></> : 'No model connected'}</p>
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
            disabled={Boolean(trackedTurn) || working} onChange={event => setQuestion(event.currentTarget.value)} /></label>
          <div className="model-actions">
            <button type="submit" className="primary" disabled={Boolean(trackedTurn) || working || mustReselect || !question.trim()}>{connection ? 'Send' : 'Ask'}</button>
            {mustReselect && <button type="button" className="secondary-button" disabled={Boolean(trackedTurn) || working}
              onClick={startTextQuestion}>Start a text-only question</button>}
            {unconfirmed && <button type="button" className="secondary-button" disabled={working} onClick={refresh}>Check turn status</button>}
            {cancellable && <button type="button" className="secondary-button" disabled={working} onClick={() => cancel(cancellable)}>Cancel turn</button>}
            {pending && <span role="status">Waiting for the model…</span>}
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
        </section> : connection && !changing ? <>
          <section className="model-card" aria-label="Active model connection">
            <RouteDisclosure route={connection.route} />
            <div className="model-actions">
              <button type="button" className="secondary-button" disabled={working} onClick={() => disconnect(connection)}>Disconnect</button>
              <button type="button" className="secondary-button" disabled={working} onClick={() => {
                if (connection.route.kind !== 'offline_mock') setProvider(connection.route.kind);
                setChanging(true);
              }}>Change connection</button>
            </div>
          </section>
          {cloudCredentialSettings}
        </> : <>
          <div className="model-form">
            <label>Provider<select value={provider} disabled={!workspaceId || working} onChange={event => {
              const cloud = event.currentTarget.value === 'openai_responses';
              setProvider(cloud ? 'openai_responses' : 'ollama');
              setError(undefined);
              setNotice(undefined);
              if (cloud) checkCredential();
            }}>
              <option value="openai_responses">OpenAI Responses</option>
              <option value="ollama">Ollama</option>
            </select></label>
          </div>
          {cloudCredentialSettings}
          <form className="model-form model-card" onSubmit={reviewSettings}>
            <h3>{usingOpenAi ? 'OpenAI Responses' : 'Ollama text server'}</h3>
            {usingOpenAi ? <p className="model-muted">Use a model available to your OpenAI API account. Your first question sends a request to <bdi>{openAiDestination}</bdi>.</p>
              : <p className="model-muted">Use a server you control that accepts requests without sign-in. Use HTTPS for a remote server.</p>}
            {!usingOpenAi && <label>Server origin<input type="url" required autoComplete="off" spellCheck={false} maxLength={512}
              placeholder="http://127.0.0.1:11434" value={origin} disabled={!workspaceId || working}
              onChange={event => setOrigin(event.currentTarget.value)} /></label>}
            <label>Model<input required autoComplete="off" spellCheck={false} maxLength={128}
              placeholder={usingOpenAi ? 'Model ID on your API account' : 'Model name on the server'}
              value={model} disabled={!workspaceId || working}
              onChange={event => (usingOpenAi ? setOpenAiModel : setOllamaModel)(event.currentTarget.value)} /></label>
            {!usingOpenAi && <label className="model-checkbox"><input type="checkbox" checked={chartTools} disabled={!workspaceId || working}
              onChange={event => setChartTools(event.currentTarget.checked)} />Allow the selected offline sample’s close prices and times</label>}
            <div className="model-actions">
              <button type="submit" className="primary" disabled={!workspaceId || working || !model.trim()
                || (usingOpenAi ? !credential?.configured : !origin.trim())}>Review connection</button>
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
