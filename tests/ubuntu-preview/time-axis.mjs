// Observe the public canvas draw calls. Glyph bounds use the browser's actual fonts and transforms.
export function observeTimeLabels() {
  const frames = new WeakMap();
  const fillText = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function (...args) {
    fillText.apply(this, args);
    const [text, x, y] = args;
    if (!/^(?:\d{2} Jan, \d{2}:\d{2}|\d{4}-\d{2}-\d{2})$/.test(text)) return;

    const time = document.timeline.currentTime;
    if (frames.get(this.canvas)?.time !== time) frames.set(this.canvas, { time, labels: new Map() });
    const metrics = this.measureText(text);
    const transform = this.getTransform();
    const left = new DOMPoint(x - metrics.actualBoundingBoxLeft, y).matrixTransform(transform).x;
    const right = new DOMPoint(x + metrics.actualBoundingBoxRight, y).matrixTransform(transform).x;
    frames.get(this.canvas).labels.set(text, {
      text, left: Math.round(left), right: Math.round(right), width: this.canvas.width,
      centered: this.textAlign === 'center',
    });
  };

  window.readTimeLabels = () => {
    const labels = [...document.querySelectorAll('.chart canvas')].flatMap(canvas =>
      [...(frames.get(canvas)?.labels.values() ?? [])],
    );
    return {
      ticks: labels.filter(label => label.centered).sort((a, b) => a.left - b.left),
      crosshair: labels.filter(label => !label.centered).map(label => label.text),
    };
  };
}
