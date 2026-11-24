// future overlay
for (const bar of rows) {
  const x = xTime(bar.time_ms);
  const y = yValue(bar.close);
  const tip = x - 12;
  line(x, y, x, y + bar.volume / 5, 1);
  line(x, y, tip, y + 4, 0);
  line(tip, y + 4, x, y + 8, 0);
  line(x, y + 8, x, y, 0);
  if (bar.time_ms === rows[rows.length - 1].time_ms) circle(tip, y + 4, 3, 0);
}
