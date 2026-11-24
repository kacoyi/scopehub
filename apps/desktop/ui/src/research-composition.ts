import { modelQuestionLimit } from './model-question-limit';
import type { ResearchDisclosure11, ResearchNote11 } from '../../../../contracts/generated/protocol';

export type Field = 'id' | 'issuer' | 'title' | 'published_at' | 'reviewed_at';
type OrderField = Exclude<Field, 'reviewed_at'>;
type Predicate = [OrderField, 'eq' | 'ne' | 'lt' | 'le' | 'gt' | 'ge', string | number];
export type Node = ['row' | 'stack', ...Node[]] | ['text' | 'heading' | 'open', string]
  | ['field', Field] | ['note', Node]
  | ['list', Predicate[], [OrderField, 'asc' | 'desc'], Node] | ['group', OrderField, Node];
export type RecordRow = ResearchDisclosure11 | ResearchNote11;
export const sampleProgram: Node = ['row',
  ['note', ['stack', ['field', 'title'], ['field', 'reviewed_at'], ['open', 'Read note']]],
  ['list', [['issuer', 'eq', '$issuer'], ['published_at', 'gt', '$reviewed_at']], ['published_at', 'asc'],
    ['stack', ['field', 'title'], ['field', 'published_at'], ['open', 'Read disclosure']]]];
const fields = ['id', 'issuer', 'title', 'published_at'];
const reviewed = ['id', 'issuer', 'title', 'reviewed_at'];
const ops = ['eq', 'ne', 'lt', 'le', 'gt', 'ge'];
const label = (v: unknown) => typeof v === 'string' && v.trim().length > 0 && v.length <= 100;

function predicate(p: unknown): boolean {
  if (!Array.isArray(p) || p.length !== 3) return false;
  const [field, op, value] = p;
  if (!fields.includes(field) || !ops.includes(op)) return false;
  if (field === 'published_at') return value === '$reviewed_at' || Number.isSafeInteger(value);
  return label(value) && value !== '$reviewed_at';
}

function valid(n: unknown, binding: 'root' | 'note' | 'record' = 'root', depth = 0): n is Node {
  if (!Array.isArray(n) || depth > 8) return false;
  switch (n[0]) {
    case 'row': case 'stack':
      return n.length > 1 && n.length <= 9 && n.slice(1).every(v => valid(v, binding, depth + 1));
    case 'text': case 'heading': case 'open':
      return n.length === 2 && label(n[1]) && (n[0] !== 'open' || binding !== 'root');
    case 'field':
      return n.length === 2 && binding !== 'root' && (binding === 'note' ? reviewed : fields).includes(n[1]);
    case 'note':
      return binding === 'root' && n.length === 2 && valid(n[1], 'note', depth + 1);
    case 'list': {
      if (binding !== 'root' || n.length !== 4 || !Array.isArray(n[1]) || n[1].length > 8 || !n[1].every(predicate)
          || !Array.isArray(n[2]) || n[2].length !== 2 || !fields.includes(n[2][0]) || !['asc','desc'].includes(n[2][1])) return false;
      const item = n[3];
      return Array.isArray(item) && item[0] === 'group'
        ? item.length === 3 && fields.includes(item[1]) && valid(item[2], 'record', depth + 1)
        : valid(item, 'record', depth + 1);
    }
    default: return false;
  }
}

export function readProgram(text: string): Node | undefined {
  if (text.length > 4096) return undefined;
  try {
    const tree: unknown = JSON.parse(text);
    if (valid(tree) && new TextEncoder().encode(text).length <= 4096) return tree;
  } catch { /* The editor keeps the last accepted definition on error. */ }
  return undefined;
}

export function fieldValue(row: RecordRow, f: Field): number | string {
  if (f === 'published_at') return 'published_at_ms' in row ? row.published_at_ms : '';
  if (f === 'reviewed_at') return 'reviewed_at_ms' in row ? row.reviewed_at_ms : '';
  return row[f];
}

function compare(a: string | number, b: string | number) {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function queryRows(rows: ResearchDisclosure11[], note: ResearchNote11, where: Predicate[],
  [order, direction]: [OrderField, 'asc' | 'desc']) {
  return rows.filter(row => where.every(([key, op, operand]) => {
    const right = operand === '$issuer' ? note.issuer : operand === '$reviewed_at' ? note.reviewed_at_ms : operand;
    const cmp = compare(fieldValue(row, key), right);
    switch (op) {
      case 'eq': return cmp === 0;
      case 'ne': return cmp !== 0;
      case 'lt': return cmp < 0;
      case 'le': return cmp <= 0;
      case 'gt': return cmp > 0;
      case 'ge': return cmp >= 0;
    }
  })).sort((a, b) => (direction === 'asc' ? 1 : -1) * compare(fieldValue(a, order), fieldValue(b, order))
    || compare(a.id, b.id));
}

// Preserve authored literals locally. Only node names, public field identifiers,
// operators and opaque literal placeholders are needed for a structural edit.
function mapLiterals(n: Node, replace: (v: string | number) => string | number): Node {
  switch (n[0]) {
    case 'text': case 'heading': case 'open': return [n[0], String(replace(n[1]))];
    case 'field': return n;
    case 'note': return ['note', mapLiterals(n[1], replace)];
    case 'group': return ['group', n[1], mapLiterals(n[2], replace)];
    case 'row': case 'stack': return [n[0], ...(n.slice(1) as Node[]).map(v => mapLiterals(v, replace))];
    case 'list': return ['list', n[1].map(([f, op, value]) => [f, op,
      value === '$issuer' || value === '$reviewed_at' ? value : replace(value)]), n[2], mapLiterals(n[3], replace)];
  }
}

export function editRegions(tree: Node): { path: number[]; kind: string }[] {
  const regions: { path: number[]; kind: string }[] = [];
  function walk(node: Node, path: number[]) {
    regions.push({ path, kind: node[0] });
    if (node[0] === 'row' || node[0] === 'stack')
      (node.slice(1) as Node[]).forEach((n, i) => walk(n, [...path, i + 1]));
    if (node[0] === 'list') walk(node[3], [...path, 3]);
    if (node[0] === 'note') walk(node[1], [...path, 1]);
    if (node[0] === 'group') walk(node[2], [...path, 2]);
  }
  walk(tree, []);
  return regions;
}

export function researchTurn(accepted: Node | undefined, path: number[] = []) {
  const kept = new Map<string, string | number>();
  let selected = accepted;
  for (const position of path) selected = selected?.[position] as Node | undefined;
  const masked = selected && mapLiterals(selected, value => {
    const key = '~' + kept.size;
    kept.set(key, value);
    return key;
  });
  const tree = JSON.stringify(masked ?? null);
  const prompt = [
    'Author a research workbench. Reply only with a JSON tree. Node arrays: ["row"|"stack",...nodes], ["text"|"heading",label], ["note",node], ["list",where,[field,"asc"|"desc"],node].',
    'Each list loops over authorized disclosures. Item can be ["group",field,node]. Inside note/item: ["field",field], ["open",label]. Fields: id,issuer,title; UTC milliseconds reviewed_at (note),published_at (item).',
    'where is AND of [field,op,operand]; ops eq ne gt ge lt le. Operand is a literal, "$issuer" or "$reviewed_at" from selected note. Keep placeholders like "~0" for existing user labels/literals. Reply with a replacement for the selected node only.',
    'Selected path: ' + JSON.stringify(path) + '. Current node: ' + tree,
    'User intent:',
  ].join('\n') + '\n';
  // Leave room for at least one character of user intent. The normal turn
  // editor reports the exact remaining budget for longer requests.
  if (prompt.length >= modelQuestionLimit) return undefined;
  return { prompt, decode(text: string) {
    if (text.length > 4600) return undefined;
    const json = /^\x60\x60\x60(?:json)?\s*([\s\S]*?)\s*\x60\x60\x60$/i.exec(text.trim())?.[1] ?? text;
    try {
      const parsed: unknown = JSON.parse(json, (_key, value: unknown) =>
        typeof value === 'string' && kept.has(value) ? kept.get(value) : value);
      if (!path.length) return readProgram(JSON.stringify(parsed));
      const copy = structuredClone(accepted);
      if (!copy) return undefined;
      let parent: unknown[] = copy;
      for (const position of path.slice(0, -1)) parent = parent[position] as unknown[];
      parent[path[path.length - 1]!] = parsed;
      return readProgram(JSON.stringify(copy));
    } catch { return undefined; }
  } };
}
