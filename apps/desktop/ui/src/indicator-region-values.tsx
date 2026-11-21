import type { Region } from '../../../../contracts/generated/protocol';

function instant(time: number) {
  const date = new Date(time);
  return Number.isNaN(date.valueOf()) ? time + ' ms' : date.toISOString();
}

export function IndicatorRegionValues({ regions }: { regions: readonly Region[] }) {
  if (!regions.length) return null;
  return <ul aria-label="Indicator regions">{regions.map((region, index) => <li key={index}>
    {region.label} · {instant(region.from_ms)} to {instant(region.to_ms)} (exclusive)
  </li>)}</ul>;
}
