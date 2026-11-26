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

export async function paintedCandleDirections(priceCanvas) {
  return priceCanvas.evaluateAll(([canvas], colors) => {
    if (!canvas?.width || !canvas.height) return [];
    const { width, height } = canvas;
    const cssHeight = canvas.getBoundingClientRect().height;
    if (!cssHeight) return [];
    const context = canvas.getContext('2d');
    if (!context) return [];
    const { data } = context.getImageData(0, 0, width, height);
    const [up, down] = colors.map(([r, g, b]) => (r << 16) | (g << 8) | b);

    // A last-price stroke is one CSS pixel thick, including on a scaled backing canvas.
    const minimumVerticalRun = Math.ceil(2 * height / cssHeight);
    const positions = [];
    let positionColors = 0;
    for (let x = 0; x < width; x++) {
      let columnColors = 0;
      let previousColor = 0;
      let run = 0;
      for (let y = 0; y < height; y++) {
        const i = (y * width + x) * 4;
        const rgb = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        const color = rgb === up ? 1 : rgb === down ? 2 : 0;
        run = color === previousColor ? run + 1 : 1;
        previousColor = color;
        if (run >= minimumVerticalRun) columnColors |= color;
      }
      if (columnColors) positionColors |= columnColors;
      else if (positionColors) {
        positions.push(positionColors);
        positionColors = 0;
      }
    }
    if (positionColors) positions.push(positionColors);
    return positions.map(colors => colors === 1 ? 'up' : colors === 2 ? 'down' : 'mixed');
  }, candleColors);
}
