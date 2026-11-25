import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { readFrozenRevisionStatus, type LocalWorkspace } from './use-local-workspace';
import { readLocalFolderRevision } from './local-import-transport';
import { withReplyDeadline } from './reply-deadline';

export function displayAccessDenied(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error
    && (error.code === 'SNAPSHOT_UNAVAILABLE' || error.code === 'PERMISSION_DENIED');
}

export function usePublishedRevisionAccess(workspace: LocalWorkspace | undefined) {
  const observed = useRef(new Set<string>());
  const missing = useRef(new Set<string>());
  const generations = useRef(new Map<string, number>());
  const checking = useRef(new Map<string, { controller: AbortController; request: Promise<void> }>());
  const [revoked, setRevoked] = useState<ReadonlySet<string>>(observed.current);
  const [unavailable, setUnavailable] = useState<ReadonlySet<string>>(missing.current);
  const selected = useRef<{ token: number; id: string } | undefined>(undefined);
  const previousUnavailableSource = useRef<string | undefined>(undefined);
  const snapshot = workspace?.snapshot;
  const canCheckRevision = workspace?.canCheckRevision;
  const refreshPublishedAccess = workspace?.refreshPublishedAccess;

  const stopChecking = useCallback((id: string) => {
    checking.current.get(id)?.controller.abort();
    checking.current.delete(id);
  }, []);
  const revoke = useCallback((id: string) => {
    if (observed.current.has(id)) return;
    stopChecking(id);
    observed.current = new Set(observed.current).add(id);
    setRevoked(observed.current);
  }, [stopChecking]);
  // The ref also fences a response delivered before React commits the revoked view.
  const canDisplay = useCallback((id: string) => !observed.current.has(id) && !missing.current.has(id), []);
  const beginRead = useCallback((id: string) => {
    const generation = generations.current.get(id);
    return () => canDisplay(id) && generations.current.get(id) === generation;
  }, [canDisplay]);
  const setMissing = useCallback((id: string, absent: boolean) => {
    if (missing.current.has(id) === absent) return;
    const next = new Set(missing.current);
    if (absent) {
      next.add(id);
      generations.current.set(id, (generations.current.get(id) ?? 0) + 1);
    } else next.delete(id);
    missing.current = next;
    setUnavailable(next);
  }, []);
  const withhold = useCallback((id: string) => {
    if (observed.current.has(id)) return;
    stopChecking(id);
    setMissing(id, true);
  }, [setMissing, stopChecking]);

  const verifyAccess = useCallback((id: string,
    read: (signal: AbortSignal) => Promise<'available' | 'missing' | 'revoked' | undefined>) => {
    if (observed.current.has(id) || canCheckRevision?.() === false) return;
    const current = checking.current.get(id);
    if (current) return current.request;
    const controller = new AbortController();
    const request = withReplyDeadline(5000, read, controller.signal).then(status => {
      if (controller.signal.aborted) return;
      if (status === 'revoked') revoke(id);
      else if (status === 'missing') setMissing(id, true);
      else if (status === 'available' && missing.current.has(id)) {
        setMissing(id, false);
        refreshPublishedAccess?.(id);
      }
    }, error => {
      if (!controller.signal.aborted && displayAccessDenied(error)) setMissing(id, true);
    }).finally(() => {
      if (checking.current.get(id)?.controller === controller) checking.current.delete(id);
    });
    checking.current.set(id, { controller, request });
    return request;
  }, [canCheckRevision, refreshPublishedAccess, revoke, setMissing]);
  const verify = useCallback((id: string) => verifyAccess(id, async signal => {
    const reply = await readFrozenRevisionStatus(id, signal);
    if (reply?.available === true && reply.revoked === undefined) return 'available';
    if (reply?.available === false) return reply.revoked === true ? 'revoked' : 'missing';
  }), [verifyAccess]);
  const verifyFolder = useCallback((id: string) => verifyAccess(id, async signal => {
    try {
      const reply = await readLocalFolderRevision(
        { version: 1, revision_id: id, include_details: false }, crypto.randomUUID(), signal);
      if (reply.revision.revision_id === id) return 'available';
    } catch (error) {
      if (error && typeof error === 'object' && 'code' in error && error.code === 'PERMISSION_DENIED') return 'revoked';
      throw error;
    }
  }), [verifyAccess]);

  useLayoutEffect(() => {
    const requests = checking.current;
    return () => {
      for (const { controller } of requests.values()) controller.abort();
      requests.clear();
    };
  }, []);
  useLayoutEffect(() => {
    if (!snapshot) return;
    const { source, chart_evidence: target, selection_revision: token } = snapshot;
    if (source?.kind === 'frozen' && source.status === 'revoked') revoke(source.revision_id);
    const unavailableSource = source?.kind === 'frozen' && source.status === 'unavailable' ? source.revision_id : undefined;
    if (unavailableSource && unavailableSource !== previousUnavailableSource.current) withhold(unavailableSource);
    previousUnavailableSource.current = unavailableSource;

    const previous = selected.current;
    selected.current = target && 'revision_id' in target ? { token, id: target.revision_id } : undefined;
    if (target && 'unavailable' in target && previous?.token === token) {
      // The same selection is now redacted. Fence its previous reply immediately,
      // then let the registry distinguish temporary absence from withdrawal.
      withhold(previous.id);
      void verify(previous.id);
    }
  }, [snapshot, revoke, verify, withhold]);
  return { revoked, unavailable, canDisplay, beginRead, revoke, withhold, verify, verifyFolder };
}

export type PublishedRevisionAccess = ReturnType<typeof usePublishedRevisionAccess>;

export function useWatchPublishedRevision(
  id: string | undefined, verify: PublishedRevisionAccess['verify'], workspace: LocalWorkspace | undefined,
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
    if (id && (initialAccess === 'check' || profileChanged)) void verify(id);
  }, [id, verify, refresh, errorId, initialAccess]);

  useEffect(() => {
    if (!id) return;
    const check = () => {
      if (document.visibilityState === 'visible') void verify(id);
    };
    const focus = () => {
      if (!workspaceHandlesFocus && canCheckRevision?.() !== false) check();
    };
    const timer = window.setInterval(check, 2000);
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
  }, [id, workspaceHandlesFocus, verify, canCheckRevision]);
}
