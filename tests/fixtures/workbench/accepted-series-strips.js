// series free
// Give each time key a row. Leave a short dash where the core returned null.
for (const [index, point] of rows.entries()) {
  const y = 16 + index * 208 / rows.length;
  if (point.value === null) {
    line(64, y, 72, y, 2);
  } else {
    const length = 240 - yValue(point.value);
    rect(64, y, length, 6, 1);
  }
  hit(index, 69, y, 8);
}
