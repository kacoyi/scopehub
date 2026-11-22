import type { IPaneApi, IPanePrimitive, IPrimitivePaneRenderer, ITimeScaleApi, Logical, Time } from 'lightweight-charts';
import type { Bar, Region, VisibleRange } from '../../../../../contracts/generated/protocol';

type Band = { first: number; last: number; color: string; label: string; preview: boolean };
type Session = { start_ms: number; end_ms: number; trading_day: string };
type BarInterval = { from_ms: number; to_ms: number; bar: number };

function declaredSessions(value: unknown): Session[] {
  if (!Array.isArray(value)) return [];
  const sessions: Session[] = [];
  for (const entry of value) {
    const previous = sessions.at(-1);
    if (!Number.isSafeInteger(entry?.start_ms) || !Number.isSafeInteger(entry.end_ms)
      || entry.start_ms >= entry.end_ms || typeof entry.trading_day !== 'string' || !entry.trading_day.length
      || previous && (entry.start_ms < previous.end_ms || entry.trading_day < previous.trading_day)) return [];
    sessions.push({ start_ms: entry.start_ms, end_ms: entry.end_ms, trading_day: entry.trading_day });
  }
  return sessions;
}

export function indicatorBarIntervals(
  bars: readonly Bar[], timeframe: string, asOf: number | undefined, sample: boolean, sessionMetadata: unknown,
): BarInterval[] {
  const sessions = sample ? declaredSessions(sessionMetadata) : [];
  const byDay = new Map<string, Session[]>();
  if (timeframe === '1d') {
    for (const session of sessions) {
      const day = byDay.get(session.trading_day);
      if (day) day.push(session);
      else byDay.set(session.trading_day, [session]);
    }
  }
  const period = timeframe === '1m' ? 60_000 : timeframe === '5m' ? 300_000 : undefined;
  const intervals: BarInterval[] = [];
  let sessionIndex = 0;
  for (const [index, bar] of bars.entries()) {
    const start = bar.time_ms;
    if (asOf !== undefined && start > asOf) break;
    // A frozen observation may include the first instant of a still-open bar.
    const observedEnd = asOf === undefined ? Infinity : Math.max(start + 1, asOf);
    const endLimit = Math.min(bars[index + 1]?.time_ms ?? Infinity, observedEnd);
    const day = byDay.get(bar.trading_day);
    if (day?.[0]?.start_ms === start) {
      // A daily bar represents its own sessions, including a prior UTC date
      // when applicable. Closed intervals between those sessions stay uncovered.
      for (const session of day) {
        const to = Math.min(session.end_ms, endLimit);
        if (session.start_ms < to) intervals.push({ from_ms: session.start_ms, to_ms: to, bar: index });
      }
      continue;
    }

    // With no verified daily calendar, the only known instant is the bar start.
    let end = start + 1;
    if (period !== undefined) {
      if (!sample) end = start + period;
      else {
        while (sessions[sessionIndex] && sessions[sessionIndex]!.end_ms <= start) sessionIndex++;
        const session = sessions[sessionIndex];
        if (session && session.trading_day === bar.trading_day && session.start_ms <= start
          && (start - session.start_ms) % period === 0) end = Math.min(start + period, session.end_ms);
      }
    }
    intervals.push({ from_ms: start, to_ms: Math.min(end, endLimit), bar: index });
  }
  return intervals;
}

export function clipIndicatorRegions(
  intervals: readonly BarInterval[], regions: readonly Region[], range: VisibleRange, preview: boolean,
): Band[] {
  function lowerBound(before: (interval: BarInterval) => boolean) {
    let start = 0;
    let end = intervals.length;
    while (start < end) {
      const middle = Math.floor((start + end) / 2);
      if (before(intervals[middle]!)) start = middle + 1;
      else end = middle;
    }
    return start;
  }

  const bands: Band[] = [];
  for (const region of regions) {
    if (!/^#[0-9a-f]{6}$/i.test(region.color)) continue;
    const from = Math.max(range.from_ms, region.from_ms);
    const to = Math.min(range.to_ms, region.to_ms);
    if (from >= to) continue;
    const first = lowerBound(interval => interval.to_ms <= from);
    const end = lowerBound(interval => interval.from_ms < to);
    if (first < end) {
      const label = region.label.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/gu, ' ').trim();
      bands.push({ first: intervals[first]!.bar, last: intervals[end - 1]!.bar, color: region.color, label, preview });
    }
  }
  return bands;
}

type RegionLabels = { preview: string; accepted: string };

export function mountIndicatorRegions(pane: IPaneApi<Time>, time: ITimeScaleApi<Time>, labels: RegionLabels) {
  let bands: readonly Band[] = [];
  let redraw: (() => void) | undefined;
  const xAt = (index: number) => time.logicalToCoordinate(index as Logical);
  const renderer: IPrimitivePaneRenderer = {
    draw(target) {
      const zero = xAt(0);
      const one = xAt(1);
      if (zero === null || one === null || one <= zero) return;
      const halfBar = (one - zero) / 2;
      target.useMediaCoordinateSpace(({ context, mediaSize: { width, height } }) => {
        context.save();
        try {
          context.font = '12px sans-serif';
          context.textBaseline = 'top';
          for (const band of bands) {
            const first = xAt(band.first);
            const last = xAt(band.last);
            if (first === null || last === null) continue;
            const left = Math.max(0, first - halfBar);
            const right = Math.min(width, last + halfBar);
            if (right <= left) continue;
            context.fillStyle = band.color;
            context.globalAlpha = band.preview ? 0.15 : 0.26;
            context.fillRect(left, 0, right - left, height);
            context.globalAlpha = 0.78;
            context.strokeStyle = band.color;
            context.setLineDash(band.preview ? [4, 3] : []);
            context.strokeRect(left, 0.5, right - left, height - 1);
            const labelWidth = Math.min(240, width - left - 8);
            if (labelWidth > 0) {
              context.globalAlpha = 1;
              const state = band.preview ? labels.preview : labels.accepted;
              context.fillText(band.label ? state + ' · ' + band.label : state, left + 4, 6, labelWidth);
            }
          }
        } finally {
          context.restore();
        }
      });
    },
  };
  const views = [{ zOrder: () => 'bottom' as const, renderer: () => renderer }];
  const primitive: IPanePrimitive<Time> = {
    paneViews: () => views,
    attached: ({ requestUpdate }) => { redraw = requestUpdate; },
    detached: () => { redraw = undefined; },
  };
  return {
    setBands(next: readonly Band[]) {
      bands = next;
      if (bands.length) {
        if (redraw) redraw();
        else pane.attachPrimitive(primitive);
      } else if (redraw) {
        pane.detachPrimitive(primitive);
      }
    },
    setLabels(next: RegionLabels) {
      labels = next;
      redraw?.();
    },
  };
}
