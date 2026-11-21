import type { IPaneApi, IPanePrimitive, IPrimitivePaneRenderer, ITimeScaleApi, Logical, Time } from 'lightweight-charts';
import type { Bar, Region, VisibleRange } from '../../../../../contracts/generated/protocol';

type Band = { first: number; last: number; color: string; label: string; preview: boolean };

export function clipIndicatorRegions(
  bars: readonly Bar[], regions: readonly Region[], range: VisibleRange, preview: boolean,
): Band[] {
  function lowerBound(time: number) {
    let start = 0;
    let end = bars.length;
    while (start < end) {
      const middle = Math.floor((start + end) / 2);
      if (bars[middle]!.time_ms < time) start = middle + 1;
      else end = middle;
    }
    return start;
  }

  const bands: Band[] = [];
  for (const region of regions) {
    if (!/^#[0-9a-f]{6}$/i.test(region.color)) continue;
    const first = lowerBound(Math.max(range.from_ms, region.from_ms));
    const last = lowerBound(Math.min(range.to_ms, region.to_ms)) - 1;
    if (first <= last) {
      const label = region.label.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/gu, ' ').trim();
      bands.push({ first, last, color: region.color, label, preview });
    }
  }
  return bands;
}

export function mountIndicatorRegions(pane: IPaneApi<Time>, time: ITimeScaleApi<Time>) {
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
              const state = band.preview ? 'Preview' : 'Accepted';
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
  return (next: readonly Band[]) => {
    bands = next;
    if (bands.length) {
      if (redraw) redraw();
      else pane.attachPrimitive(primitive);
    } else if (redraw) {
      pane.detachPrimitive(primitive);
    }
  };
}
