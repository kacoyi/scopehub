import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { readFrozenRevisionStatus, type LocalWorkspace } from './use-local-workspace';
import { readLocalFolderRevision } from './local-import-transport';

export function displayAccessDenied(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error
    && (error.code === 'SNAPSHOT_UNAVAILABLE' || error.code === 'PERMISSION_DENIED');
}

export function usePublishedRevisionAccess(workspace: LocalWorkspace | undefined) {
  const observed = useRef(new Set<string>());
  const missing = useRef(new Set<string>());
  const checking = useRef(new Map<string, Promise<void>>());
  const [revoked, setRevoked] = useState<ReadonlySet<string>>(observed.current);
  const [unavailable, setUnavailable] = useState<ReadonlySet<string>>(missing.current);
  const selected = useRef<{ token: number; id: string } | undefined>(undefined);
  const snapshot = workspace?.snapshot;
  const canCheckRevision = workspace?.canCheckRevision;
  const refreshPublishedAccess = workspace?.refreshPublishedAccess;

  const revoke = useCallback((id: string) => {
    if (observed.current.has(id)) return;
    observed.current = new Set(observed.current).add(id);
    setRevoked(observed.current);
  }, []);
  // The ref also fences a response delivered before React commits the revoked view.
  const canDisplay = useCallback((id: string) => !observed.current.has(id) && !missing.current.has(id), []);
  const setMissing = useCallback((id: string, absent: boolean) => {
    if (missing.current.has(id) === absent) return;
    const next = new Set(missing.current);
    if (absent) next.add(id);
    else next.delete(id);
    missing.current = next;
    setUnavailable(next);
  }, []);
  useLayoutEffect(() => {
    const target = snapshot?.chart_evidence;
    if (target && snapshot) {
      if ('revision_id' in target) {
        selected.current = { token: snapshot.selection_revision, id: target.revision_id };
        return;
      }
      const { source } = snapshot;
      if (selected.current?.token === snapshot.selection_revision && source?.kind === 'frozen'
        && source.revision_id === selected.current.id && source.status === 'revoked') revoke(source.revision_id);
    }
    selected.current = undefined;
  }, [snapshot, revoke]);
  const verifyAccess = useCallback((id: string, read: () => Promise<'available' | 'missing' | 'revoked' | undefined>) => {
    if (observed.current.has(id) || canCheckRevision?.() === false) return;
    const current = checking.current.get(id);
    if (current) return current;
    const request = read().then(status => {
      if (status === 'revoked') revoke(id);
      else if (status === 'missing') setMissing(id, true);
      else if (status === 'available' && missing.current.has(id)) {
        setMissing(id, false);
        refreshPublishedAccess?.(id);
      }
    }, error => {
      if (displayAccessDenied(error)) setMissing(id, true);
    }).finally(() => checking.current.delete(id));
    checking.current.set(id, request);
    return request;
  }, [canCheckRevision, refreshPublishedAccess, revoke, setMissing]);
  const verify = useCallback((id: string) => verifyAccess(id, async () => {
    const reply = await readFrozenRevisionStatus(id, AbortSignal.timeout(5000));
    if (reply?.available === true && reply.revoked === undefined) return 'available';
    if (reply?.available === false) return reply.revoked === true ? 'revoked' : 'missing';
  }), [verifyAccess]);
  const verifyFolder = useCallback((id: string) => verifyAccess(id, async () => {
    try {
      const reply = await readLocalFolderRevision({ version: 1, revision_id: id, include_details: false }, crypto.randomUUID());
      if (reply.revision.revision_id === id) return 'available';
    } catch (error) {
      if (error && typeof error === 'object' && 'code' in error && error.code === 'PERMISSION_DENIED') return 'revoked';
      throw error;
    }
  }), [verifyAccess]);
  return { revoked, unavailable, canDisplay, revoke, verify, verifyFolder };
}

export type PublishedRevisionAccess = ReturnType<typeof usePublishedRevisionAccess>;

export function useWatchPublishedRevision(
  id: string | undefined, verify: PublishedRevisionAccess['verify'], workspace: LocalWorkspace | undefined,
  initialAccess: 'check' | 'checked-by-reader' = 'check',
) {
  const refresh = workspace?.snapshot;
  const reload = workspace?.sourceRecovery ? undefined : workspace?.reload;
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
      if (document.visibilityState !== 'visible' || canCheckRevision?.() === false) return;
      if (reload) reload();
      else check();
    };
    const timer = window.setInterval(check, 2000);
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
  }, [id, reload, verify, canCheckRevision]);
}
