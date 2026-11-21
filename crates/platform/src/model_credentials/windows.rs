//! Narrow FFI boundary for generic credentials in the current Windows user's vault.

#![allow(unsafe_code)]

use std::{ptr, ptr::NonNull, slice};

use sha2::{Digest, Sha256};
use uuid::Uuid;
use windows_sys::Win32::{
    Foundation::{ERROR_NOT_FOUND, GetLastError},
    Security::Credentials::{
        CRED_MAX_CREDENTIAL_BLOB_SIZE, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC, CREDENTIALW,
        CredDeleteW, CredFree, CredReadW, CredWriteW,
    },
};

use super::{CredentialError, MAX_TOKEN_BYTES, clear_secret, valid_token};

const REVISION_BYTES: usize = 16;
const MAX_BLOB_BYTES: usize = REVISION_BYTES + MAX_TOKEN_BYTES;

const _: () = assert!(MAX_BLOB_BYTES <= CRED_MAX_CREDENTIAL_BLOB_SIZE as usize);

pub(super) fn with_credential<T>(
    profile_id: &str,
    inspect: impl FnOnce(Uuid, &[u8]) -> T,
) -> Result<Option<T>, CredentialError> {
    let target = target_name(profile_id);
    let mut raw = ptr::null_mut();
    // SAFETY: target is terminated and raw is writable for one pointer. Windows
    // allocates a single credential block on success.
    if unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) } == 0 {
        return missing_or_unavailable().map(|()| None);
    }
    let owned = CredentialBlock(NonNull::new(raw).ok_or(CredentialError::StoreUnavailable)?);
    let blob = owned.blob().ok_or(CredentialError::StoreUnavailable)?;
    let (revision, secret) = blob
        .split_at_checked(REVISION_BYTES)
        .filter(|(_, secret)| valid_token(secret))
        .ok_or(CredentialError::StoreUnavailable)?;
    let revision = Uuid::from_slice(revision).map_err(|_| CredentialError::StoreUnavailable)?;
    Ok(Some(inspect(revision, secret)))
}

pub(super) fn replace(
    profile_id: &str,
    revision: Uuid,
    secret: &[u8],
) -> Result<(), CredentialError> {
    let mut target = target_name(profile_id);
    let size = REVISION_BYTES + secret.len();
    let mut blob = [0; MAX_BLOB_BYTES];
    blob[..REVISION_BYTES].copy_from_slice(revision.as_bytes());
    blob[REVISION_BYTES..size].copy_from_slice(secret);

    let credential = CREDENTIALW {
        Type: CRED_TYPE_GENERIC,
        TargetName: target.as_mut_ptr(),
        CredentialBlobSize: size as u32,
        CredentialBlob: blob.as_mut_ptr(),
        Persist: CRED_PERSIST_LOCAL_MACHINE,
        ..Default::default()
    };
    // SAFETY: target and blob remain live and bounded for the synchronous call.
    let wrote = unsafe { CredWriteW(&credential, 0) } != 0;
    clear_secret(&mut blob[..size]);
    wrote.then_some(()).ok_or(CredentialError::StoreUnavailable)
}

pub(super) fn remove(profile_id: &str) -> Result<(), CredentialError> {
    let target = target_name(profile_id);
    // SAFETY: target is terminated and remains live for the synchronous call.
    if unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) } == 0 {
        missing_or_unavailable()
    } else {
        Ok(())
    }
}

fn target_name(profile_id: &str) -> Vec<u16> {
    let scope = Sha256::digest(profile_id.as_bytes());
    format!("ScopeHub/OpenAI.Responses/v1/{scope:x}")
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect()
}

fn missing_or_unavailable() -> Result<(), CredentialError> {
    // SAFETY: GetLastError reads the current thread's last error.
    if unsafe { GetLastError() } == ERROR_NOT_FOUND {
        Ok(())
    } else {
        Err(CredentialError::StoreUnavailable)
    }
}

struct CredentialBlock(NonNull<CREDENTIALW>);

impl CredentialBlock {
    fn blob(&self) -> Option<&[u8]> {
        // SAFETY: this block remains owned until Drop calls CredFree.
        let credential = unsafe { self.0.as_ref() };
        let size = credential.CredentialBlobSize as usize;
        if credential.CredentialBlob.is_null() || size > MAX_BLOB_BYTES {
            return None;
        }
        // SAFETY: the blob pointer and length come from the live OS-owned block.
        Some(unsafe { slice::from_raw_parts(credential.CredentialBlob, size) })
    }
}

impl Drop for CredentialBlock {
    fn drop(&mut self) {
        // SAFETY: CredReadW allocated this single block and we own its only
        // handle. No borrows of its contents survive into Drop.
        unsafe {
            let credential = self.0.as_ref();
            let size = credential.CredentialBlobSize as usize;
            if !credential.CredentialBlob.is_null()
                && size <= CRED_MAX_CREDENTIAL_BLOB_SIZE as usize
            {
                clear_secret(slice::from_raw_parts_mut(credential.CredentialBlob, size));
            }
            CredFree(self.0.as_ptr().cast());
        }
    }
}
