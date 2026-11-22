import { t } from './i18n';
import type { Region } from '../../../../contracts/generated/protocol';

function instant(time: number) {
  const date = new Date(time);
  return Number.isNaN(date.valueOf()) ? time + ' ms' : date.toISOString();
}

export function IndicatorRegionValues({ regions }: { regions: readonly Region[] }) {
  if (!regions.length) return null;
  return <ul aria-label={t('Indicator regions')}>{regions.map((region, index) => <li key={index}>
    {region.label} · {t('{from} to {to} (exclusive)', { from: instant(region.from_ms), to: instant(region.to_ms) })}
  </li>)}</ul>;
}
