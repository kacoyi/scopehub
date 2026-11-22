import { readFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { expect, test } from '@playwright/test';

const ui = new URL('../../apps/desktop/ui/', import.meta.url);
const src = fileURLToPath(new URL('src/', ui));
const { parseSync } = await import(pathToFileURL(createRequire(new URL('package.json', ui)).resolve('vite')).href);
const readJson = async file => JSON.parse(await readFile(file, 'utf8'));
const placeholders = text => [...new Set(Array.from(text.matchAll(/\{(\w+)\}/g), match => match[1]))].sort();

function visit(node, check) {
  if (!node || typeof node !== 'object') return;
  check(node);
  for (const child of Object.values(node)) visit(child, check);
}

// Product names, required attribution, protocol values and physical key names retain their original text.
const invariantText = new Set([
  'ScopeHub', 'Charts by TradingView Lightweight Charts™', 'UTF-8', 'GB18030', '· SHA-256',
  ') UTC', 'OpenAI Responses', 'Ollama', 'http://127.0.0.1:11434', 'Esc',
]);

async function sourceMessages() {
  const messages = new Set();
  const unlocalized = new Set();
  for (const file of await readdir(src, { recursive: true })) {
    if (!/\.tsx?$/.test(file)) continue;
    const ast = parseSync(file, await readFile(join(src, file), 'utf8'));
    expect(ast.errors, file).toEqual([]);
    visit(ast.program, node => {
      if (node.type === 'CallExpression' && ['t', 'message', 'uiMessage', 'plural', 'label'].includes(node.callee.name)) {
        const [id, , context] = node.arguments;
        if (typeof id?.value === 'string') {
          messages.add(typeof context?.value === 'string' ? context.value + '::' + id.value : id.value);
        }
      }
      const copy = node.type === 'JSXText' ? node.value
        : node.type === 'JSXAttribute' && ['aria-label', 'title', 'placeholder', 'alt'].includes(node.name.name)
          ? node.value?.value : undefined;
      if (copy && /[a-z]{2}/i.test(copy) && !invariantText.has(copy.trim())) unlocalized.add(file + ' — ' + copy.trim());
    });
  }
  expect([...unlocalized], 'Translate application-owned JSX and accessible labels').toEqual([]);
  return messages;
}

test('each registered language has valid metadata, unique messages and matching named arguments', async () => {
  const root = join(src, 'i18n');
  const languages = await readJson(join(root, 'languages.json'));
  const tags = languages.map(language => language.tag);
  expect(tags).toEqual(Intl.getCanonicalLocales(tags));
  expect(tags).toEqual(expect.arrayContaining(['en', 'zh-Hans']));
  const directories = await readdir(join(root, 'locales'));
  expect(directories.sort()).toEqual(tags.filter(tag => tag !== 'en').sort());
  const required = await sourceMessages();
  for (const { tag, name, direction, folderPickerTitle } of languages) {
    expect(name.trim()).not.toBe('');
    expect(['rtl', 'ltr']).toContain(direction);
    if (tag === 'en') {
      expect(folderPickerTitle).toBe('Choose local CSV folder');
      continue;
    }
    const catalog = new Map();
    const directory = join(root, 'locales', tag);
    for (const file of await readdir(directory)) {
      for (const [id, translation] of Object.entries(await readJson(join(directory, file)))) {
        expect(catalog.has(id), tag + ' duplicate ' + id).toBe(false);
        catalog.set(id, translation);
        const forms = typeof translation === 'string' ? [translation] : Object.values(translation);
        if (typeof translation !== 'string') expect(typeof translation.other, id).toBe('string');
        for (const form of forms) {
          expect(form.trim(), id).not.toBe('');
          expect(placeholders(form), id).toEqual(placeholders(id));
        }
      }
    }
    expect([...required].filter(id => !catalog.has(id)), 'Missing translations in ' + tag).toEqual([]);
    expect(folderPickerTitle).toBe(catalog.get('Choose local CSV folder'));
  }
});
