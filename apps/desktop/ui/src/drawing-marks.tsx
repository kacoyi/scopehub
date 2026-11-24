import type { Mark } from './drawing-program';
import { t } from './i18n';

const inks = ['#60d4bf', '#e8af72', '#a8a4fc', '#e68ca8'];

export function DrawingMarks({ marks, weight }: { marks: Mark[]; weight: number }) {
  return <g aria-label={t('Program geometry')} strokeWidth={weight}>
    {marks.map((mark, i) => {
      const [kind, x, y, a, b] = mark;
      const stroke = inks[mark.at(-1) as number];
      if (kind === 'circle') return <circle key={i} cx={x} cy={y} r={a} stroke={stroke} fill="none" />;
      if (kind === 'rect') return <rect key={i} x={x} y={y} width={a} height={b} stroke={stroke} fill="none" />;
      return <line key={i} x1={x} y1={y} x2={a} y2={b} stroke={stroke} />;
    })}
  </g>;
}
