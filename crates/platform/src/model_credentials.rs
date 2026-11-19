//! OpenAI Responses credentials scoped to the trusted local profile.

use std::{collections::HashMap, fmt, sync::Mutex};

use uuid::Uuid;

#[cfg(windows)]
mod windows;

const MAX_TOKEN_BYTES: usize = 2048;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialLifetime {
    Session,
    WindowsCredentialManager,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialError {
    InvalidSecret,
    StoreUnavailable,
}

impl fmt::Display for CredentialError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidSecret => "Enter a valid OpenAI API key without spaces or line breaks.",
            Self::StoreUnavailable => "The OpenAI credential store could not be accessed.",
        })
    }
}

impl std::error::Error for CredentialError {}

/// Only the trusted provider connector should read the bearer token.
pub struct OpenAiCredential(Vec<u8>);

impl OpenAiCredential {
    pub fn as_bearer_token(&self) -> &str {
        std::str::from_utf8(&self.0).expect("credential was validated before use")
    }
}

impl fmt::Debug for OpenAiCredential {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("OpenAiCredential([redacted])")
    }
}

impl Drop for OpenAiCredential {
    fn drop(&mut self) {
        clear_secret(&mut self.0);
    }
}

pub struct OpenAiCredentials(Backend);

enum Backend {
    Session(Mutex<HashMap<String, StoredCredential>>),
    #[cfg(windows)]
    Windows,
}

struct StoredCredential {
    revision: Uuid,
    value: OpenAiCredential,
}

impl Default for OpenAiCredentials {
    fn default() -> Self {
        #[cfg(windows)]
        {
            Self(Backend::Windows)
        }
        #[cfg(not(windows))]
        {
            Self::session_only()
        }
    }
}

impl OpenAiCredentials {
    pub fn session_only() -> Self {
        Self(Backend::Session(Mutex::default()))
    }

    pub fn lifetime(&self) -> CredentialLifetime {
        match self.0 {
            Backend::Session(_) => CredentialLifetime::Session,
            #[cfg(windows)]
            Backend::Windows => CredentialLifetime::WindowsCredentialManager,
        }
    }

    pub fn revision(&self, profile_id: &str) -> Result<Option<String>, CredentialError> {
        let revision = match &self.0 {
            Backend::Session(store) => store
                .lock()
                .map_err(|_| CredentialError::StoreUnavailable)?
                .get(profile_id)
                .map(|entry| entry.revision),
            #[cfg(windows)]
            Backend::Windows => windows::with_credential(profile_id, |revision, _| revision)?,
        };
        Ok(revision.map(|revision| revision.to_string()))
    }

    /// Replace the key and issue a new opaque revision, invalidating older revisions.
    pub fn replace(&self, profile_id: &str, secret: String) -> Result<String, CredentialError> {
        let value = OpenAiCredential(secret.into_bytes());
        if !valid_token(&value.0) {
            return Err(CredentialError::InvalidSecret);
        }

        let revision = Uuid::new_v4();
        match &self.0 {
            Backend::Session(store) => {
                store
                    .lock()
                    .map_err(|_| CredentialError::StoreUnavailable)?
                    .insert(profile_id.to_owned(), StoredCredential { revision, value });
            }
            #[cfg(windows)]
            Backend::Windows => windows::replace(profile_id, revision, &value.0)?,
        }
        Ok(revision.to_string())
    }

    pub fn remove(&self, profile_id: &str) -> Result<(), CredentialError> {
        match &self.0 {
            Backend::Session(store) => {
                store
                    .lock()
                    .map_err(|_| CredentialError::StoreUnavailable)?
                    .remove(profile_id);
                Ok(())
            }
            #[cfg(windows)]
            Backend::Windows => windows::remove(profile_id),
        }
    }

    pub fn resolve(
        &self,
        profile_id: &str,
        expected_revision: &str,
    ) -> Result<Option<OpenAiCredential>, CredentialError> {
        let Ok(expected) = Uuid::parse_str(expected_revision) else {
            return Ok(None);
        };
        match &self.0 {
            Backend::Session(store) => Ok(store
                .lock()
                .map_err(|_| CredentialError::StoreUnavailable)?
                .get(profile_id)
                .filter(|entry| entry.revision == expected)
                .map(|entry| OpenAiCredential(entry.value.0.clone()))),
            #[cfg(windows)]
            Backend::Windows => Ok(windows::with_credential(profile_id, |revision, secret| {
                (revision == expected).then(|| OpenAiCredential(secret.to_vec()))
            })?
            .flatten()),
        }
    }
}

fn valid_token(bytes: &[u8]) -> bool {
    !bytes.is_empty()
        && bytes.len() <= MAX_TOKEN_BYTES
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-._~+/=".contains(byte))
}

fn clear_secret(bytes: &mut [u8]) {
    bytes.fill(0);
    // Best-effort cleanup of the buffer we own.
    let _ = std::hint::black_box(bytes);
}
