//! Bind native mutation retries to the exact request during the host session.

use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use scopehub_application::failure;
use scopehub_domain::protocol::{
    Error, ErrorCode, PublishLocalFolderRefreshInput, PublishLocalImportInput,
};

use super::PreviewBinding;

const MAX_TRACKED_REQUESTS: usize = 4_096;

#[derive(PartialEq)]
pub(super) enum MutationInput {
    Save(PreviewBinding),
    Publish(PublishLocalImportInput),
    PublishFolder(PublishLocalFolderRefreshInput),
}

#[derive(Clone)]
pub(crate) enum ImportReceipt {
    Mapping(u64),
    Publication(String),
    FolderPublication,
}

pub(super) enum ImportAttempt {
    New(Option<PendingImport>),
    Committed(ImportReceipt),
}

struct Entry {
    input: MutationInput,
    receipt: Option<ImportReceipt>,
}

#[derive(Default)]
pub(super) struct ImportRequests(Arc<Mutex<HashMap<String, Entry>>>);

impl ImportRequests {
    pub(super) fn start(
        &self,
        id: Option<&str>,
        input: MutationInput,
    ) -> Result<ImportAttempt, Error> {
        let Some(id) = id else {
            return Ok(ImportAttempt::New(None));
        };
        let mut entries = self.0.lock().expect("local import requests");
        if let Some(entry) = entries.get(id) {
            if entry.input != input {
                return Err(reused_id());
            }
            return entry
                .receipt
                .clone()
                .map(ImportAttempt::Committed)
                .ok_or_else(|| failure(ErrorCode::BUSY));
        }
        // Never forget a committed request and accidentally accept it as new.
        if entries.len() >= MAX_TRACKED_REQUESTS {
            let mut error = failure(ErrorCode::RESOURCELIMIT);
            error.message = "Restart ScopeHub to start more CSV writes. Committed requests can still be retried during this session.".into();
            return Err(error);
        }
        entries.insert(
            id.into(),
            Entry {
                input,
                receipt: None,
            },
        );
        Ok(ImportAttempt::New(Some(PendingImport {
            id: id.into(),
            entries: Arc::clone(&self.0),
        })))
    }

    pub(super) fn reject_reused(&self, id: Option<&str>) -> Result<(), Error> {
        if id.is_some_and(|id| {
            self.0
                .lock()
                .expect("local import requests")
                .contains_key(id)
        }) {
            return Err(reused_id());
        }
        Ok(())
    }

    pub(super) fn committed(&self, id: &str) -> bool {
        self.0
            .lock()
            .expect("local import requests")
            .get(id)
            .is_some_and(|entry| entry.receipt.is_some())
    }
}

fn reused_id() -> Error {
    let mut error = failure(ErrorCode::INVALIDARGUMENT);
    error.message = "This request ID already identifies a different local CSV action.".into();
    error
}

/// Keep the request pending through the blocking writer, even if its caller
/// goes away. Store only the ID of the immutable row after successful commit.
pub(crate) struct PendingImport {
    id: String,
    entries: Arc<Mutex<HashMap<String, Entry>>>,
}

impl PendingImport {
    pub(crate) fn committed(self, receipt: ImportReceipt) {
        self.entries
            .lock()
            .expect("local import requests")
            .get_mut(&self.id)
            .expect("pending import request")
            .receipt = Some(receipt);
    }
}

impl Drop for PendingImport {
    fn drop(&mut self) {
        let mut entries = self.entries.lock().expect("local import requests");
        if entries
            .get(&self.id)
            .is_some_and(|entry| entry.receipt.is_none())
        {
            entries.remove(&self.id);
        }
    }
}
