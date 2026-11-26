import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { readFrozenRevisionStatus, type LocalWorkspace } from './use-local-workspace';
import { readLocalFolderRevision } from './local-import-transport';
import { waitForReply, withReplyDeadline } from './reply-deadline';

export type PublishedRevisionKind = 'csv' | 'folder';
type RevisionKey = `${PublishedRevisionKind}:${string}`;
type WithheldStatus = 'unavailable' | 'revoked';
export type PublishedRevisionStatus = 'available' | WithheldStatus | 'unconfirmed';
type StatusRequest = {
  result: Promise<PublishedRevisionStatus>;
  key: RevisionKey;
  superseded: boolean;
};

function revisionKey(kind: PublishedRevisionKind, id: string): RevisionKey {
  return `${kind}:${id}`;
}

export function displayAccessDenied(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error
    && (error.code === 'SNAPSHOT_UNAVAILABLE' || error.code === 'PERMISSION_DENIED');
}

async function readAccessStatus(kind: PublishedRevisionKind, id: string, signal: AbortSignal): Promise<PublishedRevisionStatus> {
  if (kind === 'csv') {
    const reply = await readFrozenRevisionStatus(id, signal);
    if (reply?.available === true && reply.revoked === undefined) return 'available';
    if (reply?.available === false && reply.revoked === true) return 'revoked';
    if (reply?.available === false && reply.revoked === undefined) return 'unavailable';
    return 'unconfirmed';
  }
  try {
    const reply = await readLocalFolderRevision(
      { version: 1, revision_id: id, include_details: false }, crypto.randomUUID(), signal);
    return reply.revision.revision_id === id ? 'available' : 'unconfirmed';
  } catch (error) {
    if (error && typeof error === 'object' && 'code' in error) {
      if (error.code === 'PERMISSION_DENIED') return 'revoked';
      if (error.code === 'SNAPSHOT_UNAVAILABLE') return 'unavailable';
    }
    throw error;
  }
}

export function usePublishedRevisionAccess(workspace: LocalWorkspace | undefined) {
  const withheld = useRef(new Map<RevisionKey, WithheldStatus>());
  const generations = useRef(new Map<RevisionKey, number>());
  const checking = useRef<StatusRequest | undefined>(undefined);
  const profile = useRef(new AbortController());
  const [, setWithheld] = useState<ReadonlyMap<RevisionKey, WithheldStatus>>(withheld.current);
  const selected = useRef<{ token: number; id: string } | undefined>(undefined);
  const previousUnavailableSource = useRef<string | undefined>(undefined);
  const snapshot = workspace?.snapshot;
  const canCheckRevision = workspace?.canCheckRevision;
  const refreshPublishedAccess = workspace?.refreshPublishedAccess;

  const setStatus = useCallback((key: RevisionKey, status?: WithheldStatus) => {
    const previous = withheld.current.get(key);
    if (previous === status || previous === 'revoked') return;
    const next = new Map(withheld.current);
    if (status === undefined) next.delete(key);
    else {
      next.set(key, status);
      generations.current.set(key, (generations.current.get(key) ?? 0) + 1);
    }
    withheld.current = next;
    setWithheld(next);
  }, []);
  const withheldStatus = useCallback((kind: PublishedRevisionKind, id: string) =>
    withheld.current.get(revisionKey(kind, id)), []);
  // The ref also fences a response delivered before React commits the withheld view.
  const canDisplay = useCallback((kind: PublishedRevisionKind, id: string) =>
    withheldStatus(kind, id) === undefined, [withheldStatus]);
  const beginRead = useCallback((kind: PublishedRevisionKind, id: string) => {
    const key = revisionKey(kind, id);
    const generation = generations.current.get(key);
    return () => canDisplay(kind, id) && generations.current.get(key) === generation;
  }, [canDisplay]);
  const hold = useCallback((kind: PublishedRevisionKind, id: string, status: WithheldStatus) => {
    const key = revisionKey(kind, id);
    const current = checking.current;
    if (current?.key === key) current.superseded = true;
    setStatus(key, status);
  }, [setStatus]);
  const withhold = useCallback((kind: PublishedRevisionKind, id: string) =>
    hold(kind, id, 'unavailable'), [hold]);
  const revoke = useCallback((kind: PublishedRevisionKind, id: string) =>
    hold(kind, id, 'revoked'), [hold]);

  const verify = useCallback(async (kind: PublishedRevisionKind, id: string,
    options: { signal?: AbortSignal; fresh?: boolean } = {}): Promise<PublishedRevisionStatus> => {
    const { signal, fresh } = options;
    const key = revisionKey(kind, id);
    const lifetime = profile.current.signal;
    const prior = fresh ? checking.current : undefined;
    const wait = (result: Promise<PublishedRevisionStatus>) =>
      signal ? waitForReply(signal, () => result).catch(() => 'unconfirmed' as const) : result;

    for (;;) {
      if (lifetime.aborted || signal?.aborted) return 'unconfirmed';
      if (withheld.current.get(key) === 'revoked') return 'revoked';
      if (canCheckRevision?.() === false) return 'unconfirmed';

      const current = checking.current;
      if (!current) break;
      // Share only the same revision and provider. A check issued before focus
      // cannot confirm the new view. Other checks wait on the same native lane.
      if (current.key === key && current !== prior && !current.superseded) return wait(current.result);
      await wait(current.result);
    }

    // Once sent, the observation belongs to the profile. Page cancellation
    // stops its wait without releasing the slot or discarding the observation.
    const result = withReplyDeadline(5000, deadline => readAccessStatus(kind, id, deadline), lifetime)
      .catch(() => 'unconfirmed' as const).then(status => {
        if (lifetime.aborted) return 'unconfirmed';
        if (withheld.current.get(key) === 'revoked') return 'revoked';
        if (request.superseded) return 'unconfirmed';
        if (status === 'revoked' || status === 'unavailable') setStatus(key, status);
        else if (status === 'available' && withheld.current.has(key)) {
          setStatus(key);
          if (kind === 'csv') refreshPublishedAccess?.(id);
        }
        return status;
      }).finally(() => {
        if (checking.current === request) checking.current = undefined;
      });
    const request: StatusRequest = { key, result, superseded: false };
    checking.current = request;
    return wait(result);
  }, [canCheckRevision, refreshPublishedAccess, setStatus]);

  const recheckListed = useCallback(async (kind: PublishedRevisionKind, ids: readonly string[], signal: AbortSignal) => {
    // A list observes membership. Only status can restore a withheld revision.
    for (const id of new Set(ids)) {
      if (signal.aborted) return;
      if (withheldStatus(kind, id) === 'unavailable') await verify(kind, id, { signal });
    }
  }, [verify, withheldStatus]);

  useLayoutEffect(() => {
    if (profile.current.signal.aborted) profile.current = new AbortController();
    const lifetime = profile.current;
    return () => {
      lifetime.abort();
      checking.current = undefined;
    };
  }, []);
  useLayoutEffect(() => {
    if (!snapshot) return;
    const { source, chart_evidence: target, selection_revision: token } = snapshot;
    if (source?.kind === 'frozen' && source.status === 'revoked') revoke('csv', source.revision_id);
    const unavailableSource = source?.kind === 'frozen' && source.status === 'unavailable' ? source.revision_id : undefined;
    if (unavailableSource && unavailableSource !== previousUnavailableSource.current) withhold('csv', unavailableSource);
    previousUnavailableSource.current = unavailableSource;

    const previous = selected.current;
    selected.current = target && 'revision_id' in target ? { token, id: target.revision_id } : undefined;
    if (target && 'unavailable' in target && previous?.token === token) {
      // The same selection is now redacted. Fence its previous reply immediately,
      // then let the registry distinguish temporary absence from withdrawal.
      withhold('csv', previous.id);
      void verify('csv', previous.id);
    }
  }, [snapshot, revoke, verify, withhold]);
  return { withheldStatus, canDisplay, beginRead, revoke, withhold, verify, recheckListed };
}

export type PublishedRevisionAccess = ReturnType<typeof usePublishedRevisionAccess>;

export function useWatchPublishedRevision(
  id: string | undefined, kind: PublishedRevisionKind, verify: PublishedRevisionAccess['verify'], workspace: LocalWorkspace | undefined,
  initialAccess: 'check' | 'checked-by-reader' = 'check',
) {
  const refresh = workspace?.snapshot;
  const workspaceHandlesFocus = workspace !== undefined && !workspace.sourceRecovery;
  const canCheckRevision = workspace?.canCheckRevision;
  const errorId = workspace?.errorId;
  const previous = useRef({ id, refresh, errorId });
  useEffect(() => {
    const observed = previous.current;
    previous.current = { id, refresh, errorId };
    // The foreground read authorizes its own result. An independent profile change
    // still rechecks access, including while that result is in transit.
    const profileChanged = observed.id === id && (observed.refresh !== refresh || observed.errorId !== errorId);
    if (!id || (initialAccess !== 'check' && !profileChanged)) return;
    const lifetime = new AbortController();
    void verify(kind, id, { signal: lifetime.signal });
    return () => lifetime.abort();
  }, [id, kind, verify, refresh, errorId, initialAccess]);

  useEffect(() => {
    if (!id) return;
    const lifetime = new AbortController();
    const check = () => {
      if (document.visibilityState === 'visible') void verify(kind, id, { signal: lifetime.signal });
    };
    const focus = () => {
      if (!workspaceHandlesFocus && canCheckRevision?.() !== false) check();
    };
    const timer = window.setInterval(check, 2000);
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      lifetime.abort();
      window.clearInterval(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
  }, [id, kind, workspaceHandlesFocus, verify, canCheckRevision]);
}
