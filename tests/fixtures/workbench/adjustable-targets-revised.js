// future free
// scalar rimSize 2 18 1 6
const r = params.rimSize;
for (const [i, bar] of rows.entries()) {
  const x = xTime(bar.time_ms);
  const y = yValue(bar.close);
  line(x - 2, y, x + 2, y, 1);
  line(x, y - 2, x, y + 2, 1);
  if (selected === i) rect(x - r, y - r, 2 * r, 2 * r, 0);
  else circle(x, y, r, 0);
  hit(i, x, y, Math.max(8, r + 2));
}
