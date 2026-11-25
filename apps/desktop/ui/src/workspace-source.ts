import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react';
import type { RestoredChartSource } from './chart-workspace';
import { openAuthenticatedFrozenChart, type FrozenChartResult } from './frozen-evidence';
import type { ChartHistory, Instrument } from './market';
import { displayAccessDenied, useWatchPublishedRevision, type PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';

function projectFrozenChart(result: FrozenChartResult): {
  instrument: Instrument; history: ChartHistory;
} | undefined {
  const { revision, snapshot, bars } = result;
  if (!snapshot) return;
  const { series, symbol } = revision;
  const id = snapshot.series.instrument_id;
  return {
    instrument: {
      id, name: symbol, display_symbol: symbol, asset_class: series.instrument_kind === 'equity' ? 'stock' : 'future',
      venue: series.venue, currency: '', calendar_id: series.calendar_id, timezone: series.timezone,
      price_tick: null, quantity_unit: series.quantity_unit, tradability: 'non_tradable',
      metadata: {}, provider_mappings: [],
    },
    history: {
      bars: bars.map(entry => entry.bar), snapshot,
      dataset: {
        id: revision.revision_id, instrument_id: id, provider_id: snapshot.series.provider_id,
        mode: 'historical', timeframe: series.timeframe, price_adjustment: series.price_adjustment,
        timezone: series.timezone, calendar_id: series.calendar_id, quantity_unit: series.quantity_unit,
        revision: snapshot.dataset_revision, quality: 'degraded',
        provenance: { source_sha256: revision.source_sha256, mapping_sha256: revision.mapping_sha256 },
      },
      quality_state: {
        as_of_ms: snapshot.as_of_ms, last_observed_ms: null, session_state: 'unknown',
        freshness: 'unknown', connection: 'offline', coverage: 'unknown', gaps: [], source_kind: 'native_bars',
      },
    },
  };
}

export function useRestoredWorkspaceSource(workspace: LocalWorkspace | undefined, access: PublishedRevisionAccess) {
  const source = workspace?.snapshot?.source;
  const id = source?.kind === 'frozen' ? source.revision_id : undefined;
  const status = source?.kind === 'frozen' ? source.status : undefined;
  const revoked = id !== undefined && (status === 'revoked' || access.revoked.has(id));
  const missing = id !== undefined && access.unavailable.has(id);
  const available = id && status === 'available' && !revoked && !missing ? id : undefined;
  const openable = available && !workspace?.unconfirmedRevisions.has(available) ? available : undefined;
  const [loaded, setLoaded] = useState<RestoredChartSource>();
  const [attempt, setAttempt] = useState(0);
  const opening = useRef<AbortController>(null);
  const { beginRead, withhold } = access;
  const refreshPublishedAccess = workspace?.sourceRecovery ? undefined : workspace?.refreshPublishedAccess;
  const fenceFirstReadOnFocus = useCallback((revision: string) => {
    if (!opening.current || !refreshPublishedAccess) return false;
    // Fence the old bars before awaiting the snapshot. The existing access
    // queue also handles a workspace operation that already owns admission.
    opening.current.abort();
    refreshPublishedAccess(revision);
    setAttempt(value => value + 1);
    return true;
  }, [refreshPublishedAccess]);
  const refreshAccess = useEffectEvent((revision: string) => {
    workspace?.refreshPublishedAccess(revision);
  });

  const ready = loaded?.status === 'ready' && loaded.revision_id === id;
  const watched = id && !revoked && (available || missing || ready) ? id : undefined;
  const watchMode = missing ? 'checked-by-reader' : ready ? 'check' : 'focus-only';
  useWatchPublishedRevision(watched, access.verify, workspace, watchMode, fenceFirstReadOnFocus);
  useEffect(() => {
    if (!openable) {
      setLoaded(undefined);
      return;
    }
    const controller = new AbortController();
    opening.current = controller;
    const reading = beginRead(openable);
    setLoaded({ kind: 'frozen', revision_id: openable, status: 'loading' });
    void openAuthenticatedFrozenChart(openable, crypto.randomUUID(), controller.signal).then(result => {
      const chart = projectFrozenChart(result);
      if (!controller.signal.aborted && reading()) setLoaded(chart ? {
        kind: 'frozen', revision_id: openable, status: 'ready', chart, label: result.revision.relative_path,
      } : { kind: 'frozen', revision_id: openable, status: 'unavailable' });
    }).catch(error => {
      if (controller.signal.aborted) return;
      setLoaded({ kind: 'frozen', revision_id: openable, status: 'unavailable' });
      // The first read can lose access before the open chart's watcher starts.
      // The current snapshot distinguishes a withdrawal from missing data.
      if (reading() && displayAccessDenied(error)) {
        withhold(openable);
        refreshAccess(openable);
      }
    }).finally(() => {
      if (opening.current === controller) opening.current = null;
    });
    return () => {
      controller.abort();
      if (opening.current === controller) opening.current = null;
    };
  }, [openable, beginRead, withhold, attempt]);

  const retry = () => {
    if (id && missing) void access.verify(id);
    else if (openable) setAttempt(value => value + 1);
    else workspace?.reload();
  };
  if (!id) return { retry, restored: undefined };
  if (revoked || missing || status === 'unavailable') return {
    retry, restored: { kind: 'frozen', revision_id: id, status: revoked ? 'revoked' : 'unavailable' } satisfies RestoredChartSource,
  };
  return {
    retry,
    restored: loaded?.revision_id === id ? loaded
      : { kind: 'frozen', revision_id: id, status: 'loading' } satisfies RestoredChartSource,
  };
}
