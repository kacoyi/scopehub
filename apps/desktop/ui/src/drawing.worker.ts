import variant from '@jitl/quickjs-asmjs-mjs-release-sync';
import { newQuickJSWASMModuleFromVariant } from 'quickjs-emscripten-core';
import { markLimit, readProgram, validMark, viewport, type ViewBounds, type Mark } from './drawing-program';

export type RenderRequest = {
  code: string;
  rows: { time_ms: number; open: number; high: number; low: number; close: number; volume: number }[];
  bounds: ViewBounds;
};

async function evaluate({ code, rows, bounds }: RenderRequest) {
  const module = await newQuickJSWASMModuleFromVariant(variant);
  const runtime = module.newRuntime();
  try {
    runtime.setMemoryLimit(2 * 1024 * 1024);
    runtime.setMaxStackSize(128 * 1024);
    const vm = runtime.newContext({ intrinsics: { BaseObjects: true, Eval: true } });
    try {
      const marks: Mark[] = [];
      let invalid = false;
      const deadline = performance.now() + 120;
      runtime.setInterruptHandler(() => invalid || performance.now() >= deadline);
      const scale = viewport(bounds);
      for (const name of ['line', 'circle', 'rect', 'xTime', 'yValue'] as const) {
        const fn = vm.newFunction(name, (...args) => {
          if (invalid || args.length > 5 || !args.every(arg => vm.typeof(arg) === 'number')) {
            invalid = true;
            return;
          }
          const values = args.map(arg => vm.getNumber(arg));
          if (name === 'xTime' || name === 'yValue') {
            if (values.length === 1 && Number.isFinite(values[0])) return vm.newNumber(scale[name](values[0]!));
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
      const result = vm.evalCode('"use strict";\nconst rows = ' + JSON.stringify(rows) + ';\n' + code,
        'drawing.js', { type: 'global' });
      const failed = Boolean(result.error);
      result.dispose();
      if (performance.now() >= deadline) return { status: 'timeout' };
      if (failed || invalid || !marks.length) return { status: 'rejected' };
      return { status: 'ready', marks };
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
