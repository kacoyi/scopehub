import { useCallback, useEffect, useRef, useState } from 'react';
import type { ResearchSampleCommand11, ResearchSampleInput, ResearchSampleOutput,
  ResearchSampleReply11 } from '../../../../contracts/generated/protocol';
import { exchangeWorkspace } from './use-local-workspace';

async function request(workspace: string, args: ResearchSampleInput, signal: AbortSignal) {
  const command: ResearchSampleCommand11 = { protocol_version: '1.1', type: 'request', timestamp_ms: Date.now(),
    message_id: crypto.randomUUID(), request_id: crypto.randomUUID(), scope: { workspace_id: workspace },
    intent: { operation: 'research.sample', arguments: args } };
  const response = await exchangeWorkspace<ResearchSampleReply11>(command, signal);
  if (response.request_id !== command.request_id || response.protocol_version !== '1.1'
      || response.type !== 'response') throw new Error('Invalid research reply');
  if (response.outcome.state === 'failed') throw response.outcome.error;
  if (response.outcome.operation !== command.intent.operation) throw new Error('Invalid research operation');
  return response.outcome.result;
}

export function useResearchSource(workspace: string | undefined, noteId: string) {
  const [data, setData] = useState<ResearchSampleOutput>();
  const [original, setOriginal] = useState<ResearchSampleOutput['original'] | ResearchSampleOutput['note']>();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const active = useRef<AbortController | undefined>(undefined);
  const current = useRef<ResearchSampleOutput | undefined>(undefined);
  const reset = useCallback(() => { setOriginal(undefined); }, []);
  const run = useCallback(async (action: ResearchSampleInput['action'], record?: string) => {
    if (!workspace) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const before = current.current;
    const args: ResearchSampleInput = { action };
    if (action === 'original' || action === 'read') args.note_id = noteId;
    if (before && action !== 'status' && action !== 'read') args.expected_revision = before.revision;
    if (record && action === 'original') args.record_id = record;
    if (action !== 'status') { reset(); setPending(true); }
    if (action === 'disconnect' || action === 'revoke') { current.current = undefined; setData(undefined); }
    // Native IPC can finish after cancellation. Expire the displayed grant
    // locally and allow a fresh status read before the old invocation settles.
    const deadline = window.setTimeout(() => {
      if (active.current !== controller) return;
      controller.abort();
      active.current = undefined; current.current = undefined;
      setData(undefined); reset(); setFailed(true); setPending(false);
    }, 3000);
    try {
      let result = await request(workspace, args, controller.signal);
      if (controller.signal.aborted) return;
      if (action === 'status' && result.access === 'connected' && result.revision === before?.revision
          && before.note?.id === noteId) { setFailed(false); return; }
      if (action === 'original' && result.access === 'connected' && result.revision === before?.revision) {
        setOriginal(result.original);
        return;
      }
      if (result.access === 'connected' && action !== 'read' && action !== 'original')
        result = await request(workspace, { action: 'read', note_id: noteId }, controller.signal);
      if (controller.signal.aborted) return;
      if (action === 'read' && record === noteId && result.access === 'connected') setOriginal(result.note);
      else reset();
      current.current = result;
      setData(result);
      setFailed(false);
    } catch {
      if (controller.signal.aborted) return;
      current.current = undefined;
      setData(undefined);
      reset();
      setFailed(true);
    } finally {
      window.clearTimeout(deadline);
      if (!controller.signal.aborted) { active.current = undefined; setPending(false); }
    }
  }, [workspace, noteId, reset]);

  useEffect(() => {
    current.current = undefined; setData(undefined); setFailed(false); reset();
    void run('status');
    function verify() {
      if (!document.hidden && !active.current) void run('status');
    }
    window.addEventListener('focus', verify);
    document.addEventListener('visibilitychange', verify);
    const timer = window.setInterval(verify, 1500);
    return () => {
      active.current?.abort(); active.current = undefined;
      window.removeEventListener('focus', verify);
      document.removeEventListener('visibilitychange', verify);
      window.clearInterval(timer);
    };
  }, [run, reset]);
  return { data, pending, failed, run, reset, original };
}
