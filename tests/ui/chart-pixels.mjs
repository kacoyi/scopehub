export async function painted(page, style) {
  // Verify marks from the price series after the chart has painted.
  return page.locator('.chart canvas').evaluateAll((canvases, style) => {
    let found = 0;
    const colors = style === 'line' ? [[115, 184, 246]] : [[67, 212, 172], [241, 125, 133]];
    for (const canvas of canvases) {
      const context = canvas.getContext('2d');
      if (!context) continue;
      const values = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < values.length; i += 4) {
        if (colors.some(rgb => rgb.every((v, k) => Math.abs(values[i + k] - v) < 5))) found++;
      }
    }
    return found;
  }, style);
}
