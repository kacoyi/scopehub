import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const ui = createRequire(new URL('../../apps/desktop/ui/package.json', import.meta.url));
const vite = createRequire(ui.resolve('vite'));
const tinyglobby = createRequire(vite.resolve('tinyglobby'));
const fdir = createRequire(tinyglobby.resolve('fdir'));

for (const [consumer, load] of Object.entries({ vite, tinyglobby, fdir })) {
  test(`${consumer} loads the patched glob matcher`, () => {
    const match = load('picomatch');
    for (const pattern of ['+(tag).ts', '*(tag).ts']) {
      assert.throws(() => match(pattern), {
        name: 'SyntaxError', message: 'ScopeHub build tools do not support repeated extglobs',
      });
    }

    const sourceFile = match('src/**/*.{ts,tsx}');
    assert.equal(sourceFile('src/chart/view.tsx'), true);
    assert.equal(sourceFile('src/chart/view.css'), false);
    assert.equal(match('chart-?.ts')('chart-a.ts'), true);
    assert.equal(match('@(chart|axis).ts')('axis.ts'), true);
  });
}
