import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  ListLocalImportRevisionsInput, ListLocalImportRevisionsOutput,
  LoadLocalImportMappingInput, LoadLocalImportMappingOutput, LocalImportPreviewInput, LocalImportPreviewOutput,
  ListLocalFolderRevisionsInput, ListLocalFolderRevisionsOutput,
  PrepareLocalFolderPublicationInput, PrepareLocalFolderPublicationOutput,
  PreviewLocalFolderRefreshInput, PreviewLocalFolderRefreshOutput,
  PublishLocalFolderRefreshInput, PublishLocalFolderRefreshOutput,
  ReadLocalFolderRevisionInput, ReadLocalFolderRevisionOutput,
  RevokeLocalFolderRevisionInput, RevokeLocalFolderRevisionOutput,
  PublishLocalImportInput, PublishLocalImportOutput, ReadLocalImportRevisionInput, ReadLocalImportRevisionOutput,
  ReadLocalImportSourceRowInput, ReadLocalImportSourceRowOutput,
  RevokeLocalImportRevisionInput, RevokeLocalImportRevisionOutput,
  SaveLocalImportMappingInput, SaveLocalImportMappingOutput,
} from '../../../../contracts/generated/protocol';
import type { LocalFolderCatalog } from './local-import-editor';

export function hasLocalCsvPicker() {
  return isTauri() && !import.meta.env.SCOPEHUB_UBUNTU_PREVIEW;
}

function requireNative() {
  if (!hasLocalCsvPicker()) throw new Error('Local CSV selection is available in the desktop app.');
}

export async function chooseLocalCsvFolder() {
  requireNative();
  return invoke<LocalFolderCatalog | null>('choose_local_csv_folder');
}

export async function previewLocalImport(payload: LocalImportPreviewInput, requestId: string) {
  requireNative();
  return invoke<LocalImportPreviewOutput>('execute_operation', {
    operation: 'market.previewLocalImport', payload, requestId,
  });
}

export async function saveLocalImportMapping(payload: SaveLocalImportMappingInput, requestId: string) {
  requireNative();
  return invoke<SaveLocalImportMappingOutput>('execute_operation', {
    operation: 'market.saveLocalImportMapping', payload, requestId,
  });
}

export async function loadLocalImportMapping(payload: LoadLocalImportMappingInput, requestId: string) {
  requireNative();
  return invoke<LoadLocalImportMappingOutput>('execute_operation', {
    operation: 'market.loadLocalImportMapping', payload, requestId,
  });
}

export async function publishLocalImport(payload: PublishLocalImportInput, requestId: string) {
  requireNative();
  return invoke<PublishLocalImportOutput>('execute_operation', {
    operation: 'market.publishLocalImport', payload, requestId,
  });
}

export async function readLocalImportRevision(payload: ReadLocalImportRevisionInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalImportRevisionOutput>('execute_operation', {
    operation: 'market.readLocalImportRevision', payload, requestId,
  });
}

export async function readLocalImportSourceRow(payload: ReadLocalImportSourceRowInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalImportSourceRowOutput>('execute_operation', {
    operation: 'market.readLocalImportSourceRow', payload, requestId,
  });
}

export async function listLocalImportRevisions(payload: ListLocalImportRevisionsInput, requestId: string) {
  requireNative();
  return invoke<ListLocalImportRevisionsOutput>('execute_operation', {
    operation: 'market.listLocalImportRevisions', payload, requestId,
  });
}

export async function revokeLocalImportRevision(payload: RevokeLocalImportRevisionInput, requestId: string) {
  requireNative();
  return invoke<RevokeLocalImportRevisionOutput>('execute_operation', {
    operation: 'market.revokeLocalImportRevision', payload, requestId,
  });
}

export async function previewLocalFolderRefresh(payload: PreviewLocalFolderRefreshInput, requestId: string) {
  requireNative();
  return invoke<PreviewLocalFolderRefreshOutput>('execute_operation', {
    operation: 'market.previewLocalFolderRefresh', payload, requestId,
  });
}

export async function prepareLocalFolderPublication(payload: PrepareLocalFolderPublicationInput, requestId: string) {
  requireNative();
  return invoke<PrepareLocalFolderPublicationOutput>('execute_operation', {
    operation: 'market.prepareLocalFolderPublication', payload, requestId,
  });
}

export async function publishLocalFolderRefresh(payload: PublishLocalFolderRefreshInput, requestId: string) {
  requireNative();
  return invoke<PublishLocalFolderRefreshOutput>('execute_operation', {
    operation: 'market.publishLocalFolderRefresh', payload, requestId,
  });
}

export async function listLocalFolderRevisions(payload: ListLocalFolderRevisionsInput, requestId: string) {
  requireNative();
  return invoke<ListLocalFolderRevisionsOutput>('execute_operation', {
    operation: 'market.listLocalFolderRevisions', payload, requestId,
  });
}

export async function readLocalFolderRevision(payload: ReadLocalFolderRevisionInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalFolderRevisionOutput>('execute_operation', {
    operation: 'market.readLocalFolderRevision', payload, requestId,
  });
}

export async function revokeLocalFolderRevision(payload: RevokeLocalFolderRevisionInput, requestId: string) {
  requireNative();
  return invoke<RevokeLocalFolderRevisionOutput>('execute_operation', {
    operation: 'market.revokeLocalFolderRevision', payload, requestId,
  });
}

export async function cancelLocalPreview(requestId: string) {
  requireNative();
  await invoke('cancel_local_import_preview', { requestId });
}
