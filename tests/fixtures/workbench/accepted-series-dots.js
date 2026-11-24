// series free
// Place a ring at each available observation, using its original time and value.
for (const [index, point] of rows.entries()) {
  if (point.value === null) continue;
  const x = xTime(point.time_ms);
  const y = yValue(point.value);
  circle(x, y, 6, 0);
  hit(index, x, y, 12);
}
