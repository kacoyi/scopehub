// future free
// scalar reference 3498.6 3500.4 0.2 3499.4
// difference close reference
const baseline = yValue(params.reference);
line(40, baseline, 632, baseline, 1);

function diamond(x, y, r) {
  line(x, y - r, x + r, y, 0);
  line(x + r, y, x, y + r, 0);
  line(x, y + r, x - r, y, 0);
  line(x - r, y, x, y - r, 0);
}

for (const [i, bar] of rows.entries()) {
  const x = xTime(bar.time_ms);
  const y = yValue(bar.close);
  if (baseline !== y) line(x, baseline, x, y, 1);
  diamond(x, y, 8);
  hit(i, x, y, 12);
  if (selected === i) diamond(x, y, 13);
}
