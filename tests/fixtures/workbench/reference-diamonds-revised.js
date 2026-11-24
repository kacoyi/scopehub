// future free
const reference = 3499.4;
const selectedIndex = -1; // Edit to 0 or 1 to outline a minute.
const baseline = yValue(reference);
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
  if (i === selectedIndex) diamond(x, y, 13);
}
