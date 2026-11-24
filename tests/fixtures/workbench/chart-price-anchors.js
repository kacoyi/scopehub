// future overlay
// Mark each admitted close and emphasize the actual chart crosshair timestamp.
rows.forEach((bar, index) => {
  const x = xTime(bar.time_ms);
  const y = yValue(bar.close);
  circle(x, y, index === selected ? 8 : 5, 2);
  if (index === selected) line(x - 10, y, x + 10, y, 2);
});
