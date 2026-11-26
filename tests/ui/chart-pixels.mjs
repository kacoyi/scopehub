const candleColors = [[67, 212, 172], [241, 125, 133]];

export async function painted(page, style) {
  // Verify marks from the price series after the chart has painted.
  const colors = style === 'line' ? [[115, 184, 246]] : candleColors;
  return page.locator('.chart canvas').evaluateAll((canvases, colors) => {
    let found = 0;
    for (const canvas of canvases) {
      const context = canvas.getContext('2d');
      if (!context) continue;
      const values = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < values.length; i += 4) {
        if (colors.some(rgb => rgb.every((v, k) => Math.abs(values[i + k] - v) < 5))) found++;
      }
    }
    return found;
  }, colors);
}

export async function paintedFittedCandles(priceCanvas, bars) {
  return priceCanvas.evaluateAll(([canvas], { bars, colors }) => {
    if (!canvas?.width || !canvas.height) return false;
    const { width, height } = canvas;
    const bounds = canvas.getBoundingClientRect();
    const context = canvas.getContext('2d');
    if (!bounds.width || !bounds.height || !context) return false;
    const scaleX = width / bounds.width;
    const scaleY = height / bounds.height;
    const { data } = context.getImageData(0, 0, width, height);
    const [up, down] = colors.map(([r, g, b]) => (r << 16) | (g << 8) | b);
    const colorAt = (x, y) => {
      const i = (y * width + x) * 4;
      return (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
    };

    // The initial history adapter fits all time slots. Its normal autoscale maps the
    // history extrema to the default 20% top and 10% bottom price-scale margins.
    // These independent landmarks describe the fitted pane, without using candle ink as a scale.
    const spacing = width / bars.length;
    const highest = Math.max(...bars.map(bar => bar.high));
    const lowest = Math.min(...bars.map(bar => bar.low));
    const topMargin = 0.2 * height;
    const bottomMargin = 0.1 * height;
    const priceHeight = height - topMargin - bottomMargin - scaleY;
    const yFor = price => Math.round(topMargin + priceHeight * (highest - price) / (highest - lowest));
    const last = bars.at(-1);
    const priceLineY = yFor(last.close);
    const priceLineColor = last.close >= last.open ? up : down;
    const priceLineRows = Math.max(1, Math.floor(scaleX));
    const strokeTop = priceLineY - Math.floor(priceLineRows / 2);
    const onPriceLine = y => y >= strokeTop && y < strokeTop + priceLineRows;
    const tolerance = Math.ceil(scaleY);
    const wing = Math.floor(scaleX / 2) + 1;
    const wingLimit = Math.max(wing, Math.floor(spacing / 4));

    function matchesSpan(x, top, bottom, color) {
      if (x < 0 || x >= width) return false;
      let actualTop = height;
      let actualBottom = -1;
      let matches = 0;
      for (let y = 0; y < height; y++) {
        if (onPriceLine(y) || colorAt(x, y) !== color) continue;
        actualTop = Math.min(actualTop, y);
        actualBottom = y;
        matches++;
      }
      while (top <= bottom && onPriceLine(top)) top++;
      while (bottom >= top && onPriceLine(bottom)) bottom--;
      if (top > bottom) {
        // A body entirely on the dashed line needs visible ink of its own in a gap.
        return !matches && color !== priceLineColor && colorAt(x, priceLineY) === color;
      }
      const span = actualBottom - actualTop + 1;
      const coveredByLine = Math.max(0, Math.min(actualBottom + 1, strokeTop + priceLineRows) - Math.max(actualTop, strokeTop));
      return matches > 0 && matches + coveredByLine === span
        && Math.abs(actualTop - top) <= tolerance && Math.abs(actualBottom - bottom) <= tolerance;
    }

    return bars.every((bar, index) => {
      const center = Math.round((index + 0.5) * spacing - scaleX);
      const color = bar.close >= bar.open ? up : down;
      const high = yFor(bar.high);
      const low = yFor(bar.low);
      const top = yFor(Math.max(bar.open, bar.close));
      const bottom = yFor(Math.min(bar.open, bar.close));
      // Allow one backing pixel of horizontal rounding. A wick spans H/L at the
      // center. Search nearby for an exposed side of its O/C body because the
      // last-price dashes may cover the pixels immediately beside the wick.
      const spine = [center, center - 1, center + 1].find(x => matchesSpan(x, high, low, color));
      if (spine === undefined) return false;
      for (let offset = wing; offset <= wingLimit; offset++) {
        if (matchesSpan(spine - offset, top, bottom, color) || matchesSpan(spine + offset, top, bottom, color)) return true;
      }
      return false;
    });
  }, { bars, colors: candleColors });
}
