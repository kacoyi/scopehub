import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  LoadLocalImportMappingInput, LoadLocalImportMappingOutput, LocalImportPreviewInput, LocalImportPreviewOutput,
  PublishLocalImportInput, PublishLocalImportOutput, ReadLocalImportRevisionInput, ReadLocalImportRevisionOutput,
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

export async function revokeLocalImportRevision(payload: RevokeLocalImportRevisionInput, requestId: string) {
  requireNative();
  return invoke<RevokeLocalImportRevisionOutput>('execute_operation', {
    operation: 'market.revokeLocalImportRevision', payload, requestId,
  });
}

export async function cancelLocalPreview(requestId: string) {
  requireNative();
  await invoke('cancel_local_import_preview', { requestId });
}
