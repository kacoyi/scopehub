import variant from '@jitl/quickjs-asmjs-mjs-release-sync';
import { newQuickJSWASMModuleFromVariant } from 'quickjs-emscripten-core';
import { markLimit, readProgram, validMark, validTarget, viewport, type ViewBounds, type Mark, type Target, type Controls } from './drawing-program';

export type RenderRequest = {
  code: string;
  rows: { time_ms: number; open: number; high: number; low: number; close: number; volume: number }[];
  bounds: ViewBounds; controls: Controls;
};

async function evaluate({ code, rows, bounds, controls }: RenderRequest) {
  const module = await newQuickJSWASMModuleFromVariant(variant);
  const runtime = module.newRuntime();
  try {
    runtime.setMemoryLimit(2 * 1024 * 1024);
    runtime.setMaxStackSize(128 * 1024);
    const vm = runtime.newContext({ intrinsics: { BaseObjects: true, Eval: true } });
    try {
      const marks: Mark[] = [];
      const targets: Target[] = [];
      let invalid = false;
      const deadline = performance.now() + 120;
      runtime.setInterruptHandler(() => invalid || performance.now() >= deadline);
      const scale = viewport(bounds);
      for (const name of ['line', 'circle', 'rect', 'hit', 'xTime', 'yValue'] as const) {
        const fn = vm.newFunction(name, (...args) => {
          if (invalid || args.length > 5 || !args.every(arg => vm.typeof(arg) === 'number')) {
            invalid = true;
            return;
          }
          const values = args.map(arg => vm.getNumber(arg));
          if (name === 'xTime' || name === 'yValue') {
            if (values.length === 1 && Number.isFinite(values[0])) return vm.newNumber(scale[name](values[0]!));
          } else if (name === 'hit') {
            if (validTarget(values, rows.length) && !targets.some(t => t[0] === values[0])) {
              targets.push(values);
              return;
            }
          } else {
            const mark = [name, ...values];
            if (marks.length < markLimit && validMark(mark)) {
              marks.push(mark);
              return;
            }
          }
          invalid = true;
        });
        vm.setProp(vm.global, name, fn);
        fn.dispose();
      }
      // Only repository-owned, frozen numeric input crosses into this VM. There
      // is no module loader, pending-job pump, DOM, worker API or native bridge.
      const globals = vm.evalCode('globalThis.rows = ' + JSON.stringify(rows) + ';globalThis.params = '
        + JSON.stringify(controls.params) + ';globalThis.selected = ' + JSON.stringify(controls.selected),
        'frozen-input.js', { type: 'global' });
      const inputFailed = Boolean(globals.error);
      globals.dispose();
      if (inputFailed) return { status: 'rejected' };
      const result = vm.evalCode('"use strict";\n' + code, 'drawing.js', { type: 'global' });
      const failed = Boolean(result.error);
      result.dispose();
      if (performance.now() >= deadline) return { status: 'timeout' };
      if (failed || invalid || !marks.length) return { status: 'rejected' };
      return { status: 'ready', marks, targets };
    } finally { vm.dispose(); }
  } finally { runtime.dispose(); }
}

self.onmessage = async (event: MessageEvent<RenderRequest>) => {
  try {
    const program = readProgram(event.data.code);
    self.postMessage(program ? await evaluate(event.data) : { status: 'rejected' });
  } catch {
    self.postMessage({ status: 'rejected' });
  }
};
