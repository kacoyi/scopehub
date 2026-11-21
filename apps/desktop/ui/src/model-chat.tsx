import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { AiTextRouteChoice } from '../../../../contracts/generated/protocol';
import { exchangeModel, isModelWorkspaceBusy, modelErrorCode, type ModelConnection, type ModelResult, type ModelRoute } from './model-chat-transport';
import './model-chat.css';

export type ModelChatView = 'question' | 'settings';
type Proposal = ModelResult<'propose'>;
type PendingTurn = {
  id: string;
  connection: ModelConnection;
  question: string | undefined;
};
type TrackedTurn = PendingTurn & {
  observation: 'polling' | 'checking-send' | 'checking-poll';
};
type CompletedTurn = {
  id: string;
  question: string | undefined;
  model: string;
  text: string;
};

const unavailableModel = 'The model could not answer. Check the configured server and try again.';
const unknownTurn = 'The workspace could not confirm this turn. Check its status to continue.';
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

function RouteDisclosure({ route }: { route: ModelRoute }) {
  return <>
    <dl className="model-route">
      <dt>Provider</dt><dd>{route.kind === 'ollama' ? 'Ollama' : 'Offline mock'}</dd>
      <dt>Destination</dt><dd><bdi>{route.destination}</bdi></dd>
      <dt>Model</dt><dd><bdi>{route.model}</bdi></dd>
      <dt>Scope</dt><dd>Typed questions and up to four earlier question and answer pairs in this session. Chart data and saved private records stay local.</dd>
      <dt>Charges</dt><dd>{route.charge_basis === 'none' ? 'None' : 'Set by the server operator. Check its fees before enabling.'}</dd>
    </dl>
    <p className="model-muted">This connection lasts for this app session. Enable it again after restarting ScopeHub.</p>
    {route.kind === 'ollama' && <p className="model-muted">The server operator may process or forward the conversation. Disconnect in AI settings to stop future sends.</p>}
  </>;
}

export function ModelChat({ workspaceId, theme, view, show }: {
  workspaceId: string | undefined;
  theme: 'light' | 'dark' | undefined;
  view: ModelChatView | undefined;
  show: (view: ModelChatView | undefined) => void;
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
  const [origin, setOrigin] = useState('');
  const [model, setModel] = useState('');
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<CompletedTurn[]>([]);
  const [trackedTurn, setTrackedTurn] = useState<TrackedTurn>();
  const pending = !connectionUnconfirmed && trackedTurn?.observation === 'polling' ? trackedTurn : undefined;
  const unconfirmed = trackedTurn !== undefined && pending === undefined;
  const cancellable = trackedTurn?.observation === 'checking-send' ? undefined : trackedTurn;
  const visible = view !== undefined;

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
    } finally {
      if (action.current === controller) action.current = undefined;
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  function clearSession() {
    setConnection(undefined);
    setConnectionUnconfirmed(false);
    setTurns([]);
    setTrackedTurn(undefined);
    if (trackedTurn?.question !== undefined) setQuestion('');
    lastTurn.current = undefined;
  }

  function applyStatus(status: ModelResult<'status'>, attempted = trackedTurn) {
    const active = status.connection ?? undefined;
    const recovered = active && attempted && status.turn_id === attempted.id
      && active.connection_id === attempted.connection.connection_id;
    if (connection?.connection_id !== active?.connection_id) clearSession();
    setConnection(active);
    setConnectionUnconfirmed(false);
    if (active && status.turn_id && status.turn_id !== lastTurn.current) {
      setTrackedTurn({ id: status.turn_id, connection: active, question: recovered ? attempted.question : undefined,
        observation: 'polling' });
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
    const { id, connection: active, question: submitted } = pending;
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
        lastTurn.current = id;
        setTrackedTurn(undefined);
        if (turn.state === 'failed') {
          setError(turn.error.code === 'PROVIDER_NOT_CONFIGURED' ? unavailableModel : modelFailure(turn.error));
          return;
        }
        setError(undefined);
        setTurns(previous => [...previous, { id, question: submitted, model: turn.model, text: turn.text }]);
        if (submitted !== undefined) setQuestion('');
      } catch (failure) {
        if (superseded || action.current) return;
        if (modelErrorCode(failure) === 'BUSY' && busyPolls < busyPollLimit) {
          busyPolls += 1;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        // Re-read the same local turn. Only send may dispatch to the model.
        if (modelErrorCode(failure) !== 'BUSY' && failedPolls < failedPollLimit) {
          failedPolls += 1;
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        setTrackedTurn({ id, connection: active, question: submitted, observation: 'checking-poll' });
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
        if (modelErrorCode(failure) === 'INVALID_ARGUMENT' || isModelWorkspaceBusy(failure)) throw failure;
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
    review({ kind: 'ollama', origin: candidate, model: model.trim() });
  }

  function activate(candidate: Proposal) {
    void perform(async (id, signal) => {
      const reply = await exchangeModel(id, { action: 'activate', proposal_id: candidate.proposal_id }, signal);
      setConnection(reply.connection);
      setProposal(undefined);
      show('question');
    });
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
    if (!submitted || trackedTurn || working || connectionUnconfirmed) return;
    if (!connection) {
      setError(undefined);
      setNotice(undefined);
      show('settings');
      return;
    }
    const active = connection;
    void perform(async (id, signal) => {
      const turn = { id: crypto.randomUUID(), connection: active, question: submitted, observation: 'checking-send' } satisfies TrackedTurn;
      try {
        await exchangeModel(id, { action: 'send', connection_id: active.connection_id, question: submitted }, signal, turn.id);
        setTrackedTurn({ ...turn, observation: 'polling' });
      } catch (failure) {
        const code = String(modelErrorCode(failure));
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

  function leaveSettings(next: ModelChatView | undefined) {
    setProposal(undefined);
    setChanging(false);
    setError(undefined);
    setNotice(undefined);
    show(next);
  }

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
      {error && <p className="model-error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {connectionUnconfirmed ? <>
        <p role="status">The workspace could not confirm which model connection is active.</p>
        <div className="model-actions">
          <button type="button" className="secondary-button" disabled={working} onClick={refresh}>Check connection status</button>
        </div>
      </> : view === 'question' ? <>
        <p className="model-muted" role="status">{connection?.route.kind === 'offline_mock' ? 'Offline mock enabled'
          : connection ? <>Ollama connected · <bdi>{connection.route.model}</bdi></> : 'No model connected'}</p>
        <div ref={transcript} className="model-transcript" role="log" aria-label="Conversation">
          {turns.map(turn => <article key={turn.id} className="model-turn">
            {turn.question && <div className="model-question"><h3>You</h3><p dir="auto">{turn.question}</p></div>}
            <div className="model-answer"><h3>Model <bdi>{turn.model}</bdi></h3><p dir="auto">{turn.text}</p></div>
          </article>)}
        </div>
        <form className="model-form" onSubmit={submit}>
          <label>Question<textarea ref={questionField} rows={3} maxLength={2000} required value={question}
            disabled={Boolean(trackedTurn) || working} onChange={event => setQuestion(event.currentTarget.value)} /></label>
          <div className="model-actions">
            <button type="submit" className="primary" disabled={Boolean(trackedTurn) || working || !question.trim()}>{connection ? 'Send' : 'Ask'}</button>
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
