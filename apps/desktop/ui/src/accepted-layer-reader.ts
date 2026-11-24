import type { FrozenSnapshot, InlineIndicatorArtifact11 } from '../../../../contracts/generated/protocol';
import { exchangeWorkspace } from './use-local-workspace';

export type Layer = { layer_id: string; version_id: string; available: boolean };
export type Accepted = Layer & { document?: {
  source_snapshot: FrozenSnapshot; snapshot: FrozenSnapshot; warmup_from_ms: number | null; resolved_parameters: Record<string, unknown>;
  artifact: InlineIndicatorArtifact11;
} };
export type RemovalHint = {
  operation_id: string; layer_id: string; committed_workspace_revision: number;
};
type AcceptedLayers = { layers: Accepted[]; undo: RemovalHint | undefined };
const uuid = (id: unknown) => typeof id === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id);

async function readAccepted<T>(request: Parameters<typeof exchangeWorkspace>[0], signal: AbortSignal): Promise<T> {
  for (;;) {
    try {
      return await exchangeWorkspace<T>(request, signal);
    } catch (error) {
      if (!error || typeof error !== 'object' || !('code' in error) || error.code !== 'BUSY') throw error;
      await new Promise<void>(resolve => setTimeout(resolve, 100));
      signal.throwIfAborted();
    }
  }
}

export async function listAccepted(revision: string, signal: AbortSignal) {
  const listing = await readAccepted<{ dataset_id: string; layers: Layer[]; undo?: RemovalHint | null }>({
    type: 'indicator_accepted_layers', request_id: crypto.randomUUID(), revision_id: revision,
  }, signal);
  if (listing.dataset_id !== revision) throw new Error();
  return listing;
}

export async function readAcceptedLayer(revision: string, layer: Layer, signal: AbortSignal) {
  const output = await readAccepted<{ layer_id: string; version_id: string; document: NonNullable<Accepted['document']> }>({
    type: 'indicator_accepted_output', request_id: crypto.randomUUID(), layer_id: layer.layer_id,
  }, signal);
  if (output.layer_id !== layer.layer_id || output.version_id !== layer.version_id
    || output.document.snapshot.dataset_id !== revision
    || output.document.artifact.storage !== 'inline') throw new Error();
  return output.document;
}

export async function readLayers(snapshot: FrozenSnapshot, signal: AbortSignal): Promise<AcceptedLayers> {
  const listing = await listAccepted(snapshot.dataset_id, signal);
  const accepted: Accepted[] = [];
  for (const layer of listing.layers) {
    if (!layer.available) {
      accepted.push(layer);
      continue;
    }
    const document = await readAcceptedLayer(snapshot.dataset_id, layer, signal);
    accepted.push({ ...layer, document });
  }
  const undo = listing.undo;
  return { layers: accepted, undo: undo && Object.keys(undo).length === 3
    && uuid(undo.operation_id) && uuid(undo.layer_id)
    && Number.isSafeInteger(undo.committed_workspace_revision) && undo.committed_workspace_revision > 0
    && !accepted.some(layer => layer.layer_id === undo.layer_id) ? undo : undefined };
}
