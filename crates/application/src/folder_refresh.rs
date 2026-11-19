//! Compare complete, authorized native CSV sources with an immutable revision.

use std::{collections::BTreeMap, sync::Arc};

use scopehub_domain::{
    protocol::{Bar, Error, ErrorCode, PreviewLocalFolderRefreshInput, PublishedLocalImportBar},
    wire::max_message_bytes,
};
use serde_json::{Value, json};

use crate::{
    Actor, LocalDatasetStore, OperationRegistry, READ_LOCAL_IMPORT_REVISION, ReviewedImportMapping,
    StoredLocalDataset, WorkspaceAccess, authorized_operation, failure, local_mapping_fingerprint,
    local_publication::{valid_records, validated_input},
    reviewed_mapping::ordered_header_digest,
};

pub const PREVIEW_LOCAL_FOLDER_REFRESH: &str = "market.previewLocalFolderRefresh";
pub const MAX_STAGED_RECORDS: usize = 20_000;
const MAX_ALTERNATIVES_PER_BAR: usize = 64;

pub struct FolderRefreshBase {
    pub dataset: StoredLocalDataset,
    pub mapping: ReviewedImportMapping,
}

pub struct FolderRefreshFile {
    pub relative_path: String,
    pub sha256: Option<String>,
    pub check: FolderRefreshCheck,
}

#[derive(Clone)]
pub enum FolderRefreshCheck {
    Inspected(Arc<Value>),
    HeaderChanged,
    InvalidRows(String),
    Unavailable(String),
}

pub fn folder_refresh_input(
    actor: Actor,
    payload: Value,
) -> Result<PreviewLocalFolderRefreshInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        PREVIEW_LOCAL_FOLDER_REFRESH,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

impl<S: LocalDatasetStore> OperationRegistry<S> {
    pub fn load_folder_refresh_base(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<FolderRefreshBase, Error> {
        self.authorize_local_import(access, PREVIEW_LOCAL_FOLDER_REFRESH)?;
        self.authorize_local_import(access, READ_LOCAL_IMPORT_REVISION)?;
        let dataset = self
            .port
            .read_local_dataset(revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let mapping = self
            .port
            .load_mapping_version(&dataset.source_key, dataset.review_revision)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        if local_mapping_fingerprint(&mapping.mapping)? != dataset.mapping_sha256 {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(FolderRefreshBase { dataset, mapping })
    }
}

#[derive(Default)]
struct BarReview {
    accepted: Option<PublishedLocalImportBar>,
    alternatives: Vec<(Bar, Vec<Value>)>,
}

impl BarReview {
    fn result(self, time_ms: i64, coverage_unverified: bool) -> Value {
        let status = match (&self.accepted, self.alternatives.as_slice()) {
            (None, [(_, _)]) => "addition",
            (Some(_), []) if coverage_unverified => "unverified",
            (Some(_), []) => "withdrawal",
            (Some(old), [(bar, _)]) if old.bar == *bar => "unchanged",
            _ => "conflict",
        };
        let alternatives: Vec<_> = self
            .alternatives
            .into_iter()
            .map(|(bar, sources)| json!({"bar": bar, "sources": sources}))
            .collect();
        json!({"time_ms": time_ms, "status": status, "accepted": self.accepted, "alternatives": alternatives})
    }
}

pub fn folder_refresh_output(
    actor: Actor,
    base: &FolderRefreshBase,
    mut input_files: Vec<FolderRefreshFile>,
    cancelled: impl Fn() -> bool,
) -> Result<Value, Error> {
    let operation = authorized_operation(actor, PREVIEW_LOCAL_FOLDER_REFRESH)?;
    let check = || {
        if cancelled() {
            Err(failure(ErrorCode::CANCELLED))
        } else {
            Ok(())
        }
    };
    check()?;
    let accepted = &base.dataset;
    let mut events = BTreeMap::<i64, BarReview>::new();
    for bar in bars(accepted.content["bars"].clone())? {
        let time_ms = bar.bar.time_ms;
        events.entry(time_ms).or_default().accepted = Some(bar);
    }

    // Sort in the comparator as well as the native scan. The canonical alias
    // and competing alternatives then have the same order across traversals.
    input_files.sort_unstable_by(|a, b| a.relative_path.cmp(&b.relative_path));
    if input_files
        .windows(2)
        .any(|files| files[0].relative_path == files[1].relative_path)
    {
        let mut error = failure(ErrorCode::DATAUNAVAILABLE);
        error.message =
            "The selected folder has CSV names that cannot be distinguished in the review.".into();
        return Err(error);
    }
    let mut records = 0;
    let mut digests = BTreeMap::from([(
        accepted.source_sha256.as_str(),
        accepted.relative_path.as_str(),
    )]);
    let mut sources = Vec::new();
    let mut has_accepted_path = false;
    let mut coverage_unverified = false;
    for file in &input_files {
        check()?;
        let same_path = file.relative_path == accepted.relative_path;
        has_accepted_path |= same_path;
        let digest = file.sha256.as_deref();
        let alias_of = digest
            .map(|digest| *digests.entry(digest).or_insert(file.relative_path.as_str()))
            .filter(|path| *path != file.relative_path);
        let change = if !same_path {
            "added"
        } else if digest.is_none() {
            "unverified"
        } else if digest == Some(accepted.source_sha256.as_str()) {
            "unchanged"
        } else {
            "replaced"
        };

        let (review, detail) = match &file.check {
            FolderRefreshCheck::Inspected(content) => {
                let digest = digest.ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
                let header = content["header"]
                    .as_array()
                    .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
                if ordered_header_digest(header, &base.mapping.mapping).as_deref()
                    != Some(base.mapping.header_sha256.as_str())
                    || content["file_sha256"] != digest
                    || content["mapping_version"] != base.mapping.mapping.version
                    || content["mapping_revision"] != base.mapping.mapping.revision
                {
                    return Err(failure(ErrorCode::OUTPUTINVALID));
                }
                if content["symbol"] != accepted.content["symbol"]
                    || content["series"] != accepted.content["series"]
                {
                    (
                        "series_mismatch",
                        Some("The completed file describes a different series."),
                    )
                } else {
                    let count = content["source_record_count"].as_u64().unwrap_or_default();
                    if !valid_records(&content["bars"], count, true) {
                        return Err(failure(ErrorCode::OUTPUTINVALID));
                    }
                    records += count as usize;
                    if records > MAX_STAGED_RECORDS {
                        return Err(failure(ErrorCode::RESOURCELIMIT));
                    }
                    let incoming = bars(content["bars"].clone())?;
                    let conflicts = incoming.windows(2).any(|pair| {
                        pair[0].bar.time_ms == pair[1].bar.time_ms && pair[0].bar != pair[1].bar
                    });
                    merge_bars(&mut events, file, digest, incoming)?;
                    if conflicts {
                        (
                            "invalid_rows",
                            Some(
                                "This file has competing values at the same bar start. Review the alternatives below.",
                            ),
                        )
                    } else {
                        ("compatible", None)
                    }
                }
            }
            FolderRefreshCheck::HeaderChanged => (
                "header_changed",
                Some("Ordered CSV header differs from the saved mapping."),
            ),
            FolderRefreshCheck::InvalidRows(detail) => ("invalid_rows", Some(detail.as_str())),
            FolderRefreshCheck::Unavailable(detail) => ("unavailable", Some(detail.as_str())),
        };
        coverage_unverified |= matches!(
            &file.check,
            FolderRefreshCheck::HeaderChanged
                | FolderRefreshCheck::InvalidRows(_)
                | FolderRefreshCheck::Unavailable(_)
        );
        sources.push(json!({
            "relative_path": file.relative_path,
            "source_sha256": digest,
            "prior_sha256": same_path.then_some(&accepted.source_sha256),
            "change": change,
            "alias_of": alias_of,
            "review": review,
            "detail": detail,
        }));
    }

    if !has_accepted_path {
        sources.push(json!({
            "relative_path": accepted.relative_path,
            "source_sha256": null,
            "prior_sha256": accepted.source_sha256,
            "change": "removed",
            "alias_of": null,
            "review": "removed",
            "detail": "The accepted source path is absent from the current scan.",
        }));
        sources.sort_unstable_by(|a, b| {
            a["relative_path"]
                .as_str()
                .cmp(&b["relative_path"].as_str())
        });
    }

    if events.len() > MAX_STAGED_RECORDS {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let mut comparisons = Vec::with_capacity(events.len());
    for (time, review) in events {
        check()?;
        comparisons.push(review.result(time, coverage_unverified));
    }
    let output =
        json!({"version": 1, "base": accepted.summary(), "files": sources, "bars": comparisons});
    if output.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !operation.output.is_valid(&output) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(output)
}

fn bars(value: Value) -> Result<Vec<PublishedLocalImportBar>, Error> {
    serde_json::from_value(value).map_err(|_| failure(ErrorCode::OUTPUTINVALID))
}

fn merge_bars(
    events: &mut BTreeMap<i64, BarReview>,
    file: &FolderRefreshFile,
    digest: &str,
    incoming: Vec<PublishedLocalImportBar>,
) -> Result<(), Error> {
    for incoming in incoming {
        let alternatives = &mut events.entry(incoming.bar.time_ms).or_default().alternatives;
        let sources = incoming.locators.iter().map(|locator| {
            json!({
                "relative_path": file.relative_path,
                "source_sha256": digest,
                "record_number": locator.record_number,
                "line_start": locator.line_start,
                "line_end": locator.line_end,
            })
        });
        if let Some((_, cached)) = alternatives
            .iter_mut()
            .find(|(bar, _)| *bar == incoming.bar)
        {
            cached.extend(sources);
        } else {
            if alternatives.len() >= MAX_ALTERNATIVES_PER_BAR {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            alternatives.push((incoming.bar, sources.collect()));
        }
    }
    Ok(())
}
