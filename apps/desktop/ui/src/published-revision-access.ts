import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { exchangeWorkspace, type LocalWorkspace } from './use-local-workspace';

export function displayAccessDenied(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error
    && (error.code === 'SNAPSHOT_UNAVAILABLE' || error.code === 'PERMISSION_DENIED');
}

export function usePublishedRevisionAccess(workspace: LocalWorkspace | undefined) {
  const observed = useRef(new Set<string>());
  const checking = useRef(new Map<string, Promise<void>>());
  const [revoked, setRevoked] = useState<ReadonlySet<string>>(observed.current);
  const selected = useRef<{ token: number; id: string } | undefined>(undefined);
  const snapshot = workspace?.snapshot;

  const revoke = useCallback((id: string) => {
    if (observed.current.has(id)) return;
    observed.current = new Set(observed.current).add(id);
    setRevoked(observed.current);
  }, []);
  // The ref also fences a response delivered before React commits the revoked view.
  const canDisplay = useCallback((id: string) => !observed.current.has(id), []);
  useLayoutEffect(() => {
    const target = snapshot?.chart_evidence;
    if (target && snapshot) {
      if ('revision_id' in target) {
        selected.current = { token: snapshot.selection_revision, id: target.revision_id };
        return;
      }
      // Revocation redacts the reference without advancing its selection token.
      if (selected.current?.token === snapshot.selection_revision) revoke(selected.current.id);
    }
    selected.current = undefined;
  }, [snapshot, revoke]);
  const verify = useCallback((id: string) => {
    if (!canDisplay(id)) return;
    const current = checking.current.get(id);
    if (current) return current;
    const request = exchangeWorkspace<{ available: boolean }>({
      type: 'frozen_chart_status', request_id: crypto.randomUUID(), revision_id: id,
    }, AbortSignal.timeout(5000)).then(reply => {
      if (reply?.available === false) revoke(id);
    }, error => {
      if (displayAccessDenied(error)) revoke(id);
    }).finally(() => checking.current.delete(id));
    checking.current.set(id, request);
    return request;
  }, [canDisplay, revoke]);
  return { revoked, canDisplay, revoke, verify };
}

export type PublishedRevisionAccess = ReturnType<typeof usePublishedRevisionAccess>;

export function useWatchPublishedRevision(id: string | undefined, verify: PublishedRevisionAccess['verify'], workspace: LocalWorkspace | undefined) {
  const refresh = workspace?.snapshot;
  const reload = workspace?.reload;
  const error = workspace?.error;
  useEffect(() => {
    if (id) void verify(id);
  }, [id, verify, refresh, error]);

  useEffect(() => {
    if (!id) return;
    const check = () => {
      if (document.visibilityState === 'visible') void verify(id);
    };
    const focus = () => {
      if (document.visibilityState !== 'visible') return;
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
  }, [id, reload, verify]);
}
