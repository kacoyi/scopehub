import sourceData from './workbench-frozen-sources.json';

// The two entries pin the first two finished minute bars of the committed
// normal market fixtures. The futures pin matches the original walkthrough.
export const sources = { future: sourceData[0]!, stock: sourceData[1]! } as const;
type Source = keyof typeof sources;
export type NumericField = 'open' | 'high' | 'low' | 'close' | 'volume';
type Field = NumericField | 'time';

export type Element =
  | ['row' | 'stack', ...Element[]]
  | ['title' | 'text', string]
  | ['each', Source, Element]
  | ['value', Field]
  | ['bar', NumericField]
  | ['inspect', string];

export type Composition = {
  tree: Element;
  sourceIds: Source[];
};

const draftLimit = 900;
const numericFields = ['open', 'high', 'low', 'close', 'volume'];

function validElement(node: unknown, bound: Set<Source>, inRow = false, depth = 0): node is Element {
  if (depth > 7 || !Array.isArray(node)) return false;
  const [kind, argument, child] = node;
  switch (kind) {
    case 'row':
    case 'stack':
      return node.length >= 2 && node.length <= 7
        && node.slice(1).every(item => validElement(item, bound, inRow, depth + 1));
    case 'each':
      if (inRow || node.length !== 3 || (argument !== 'future' && argument !== 'stock')
        || !validElement(child, bound, true, depth + 1)) return false;
      bound.add(argument);
      return true;
    case 'title':
    case 'text':
    case 'inspect':
      return node.length === 2 && (kind !== 'inspect' || inRow)
        && typeof argument === 'string' && Boolean(argument.trim()) && argument.length <= 100;
    case 'value':
    case 'bar':
      return node.length === 2 && inRow
        && (numericFields.includes(argument) || (kind === 'value' && argument === 'time'));
    default:
      return false;
  }
}

export function readComposition(text: string): Composition | undefined {
  if (text.length > 4096) return undefined;
  const trimmed = text.trim();
  const json = /^\x60\x60\x60(?:json)?\s*([\s\S]*?)\s*\x60\x60\x60$/i.exec(trimmed)?.[1] ?? trimmed;
  try {
    const tree: unknown = JSON.parse(json);
    const bound = new Set<Source>();
    if (JSON.stringify(tree).length <= draftLimit && validElement(tree, bound) && bound.size > 0) {
      return { tree, sourceIds: [...bound] };
    }
  } catch {
    // Invalid model output leaves the accepted tree on screen.
  }
  return undefined;
}

export function authoringPrompt(accepted: Composition | undefined) {
  return [
    'Write a chart-free workbench for the user intent below. Reply with one JSON tree, no prose, max 900 characters.',
    'Nodes (JSON arrays): ["row",...nodes] side by side; ["stack",...nodes] vertical; ["title",label]; ["text",label];',
    '["each",source,node] repeats over source bars. Sources: future=SIM.CN.FUTURE.001; stock=SIM.CN.STOCK.001. Both frozen synthetic 1m.',
    'Inside each only: ["value",field] uses time/open/high/low/close/volume; ["bar",numericField] uses length from zero;',
    '["inspect",label] opens that bar’s evidence locally. No nested each. Bind at least one source. Do not invent values.',
    'Use the accepted tree below as context when revising. Author a whole replacement tree. Write labels in the user’s language.',
    'Accepted tree: ' + (accepted ? JSON.stringify(accepted.tree) : 'null'),
    'User intent:',
  ].join('\n') + '\n';
}

export type WorkbenchAuthoringTurn = {
  scope: symbol;
  prompt: string;
  receive: (text: string) => boolean;
};
