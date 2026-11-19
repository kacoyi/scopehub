//! Compare complete, authorized native CSV sources with an immutable revision.

use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};

use scopehub_domain::{
    protocol::{
        Bar, Error, ErrorCode, FolderRefreshAlternative, FolderRefreshSource,
        PrepareLocalFolderPublicationInput, PreviewLocalFolderRefreshInput,
        PublishedLocalFolderRevision, PublishedLocalImportBar, PublishedLocalImportRevision,
    },
    wire::max_message_bytes,
};
use serde_json::{Value, json};

use crate::{
    Actor, FolderManifestBar, FolderManifestLocator, FolderManifestPublication,
    FolderManifestSource, FolderManifestStore, LocalDatasetStore, OperationRegistry,
    READ_LOCAL_FOLDER_REVISION, READ_LOCAL_IMPORT_REVISION, ReviewedImportMapping,
    StoredFolderManifest, WorkspaceAccess, authorized_operation, failure,
    folder_publication::folder_bars,
    local_mapping_fingerprint,
    local_publication::{valid_records, validated_input},
    reviewed_mapping::ordered_header_digest,
};

pub const PREVIEW_LOCAL_FOLDER_REFRESH: &str = "market.previewLocalFolderRefresh";
pub const MAX_STAGED_RECORDS: usize = 20_000;
const MAX_ALTERNATIVES_PER_BAR: usize = 64;

pub struct FolderRefreshBase {
    pub dataset: PublishedLocalImportRevision,
    pub source_key: String,
    pub mapping: ReviewedImportMapping,
    prior: PriorRevision,
}

impl FolderRefreshBase {
    /// The accepted file paths whose ancestry must be checked during a refresh.
    pub fn previous_paths(&self) -> impl Iterator<Item = &str> {
        let (single, sources) = match &self.prior {
            PriorRevision::SingleFile(_) => (Some(self.dataset.relative_path.as_str()), &[][..]),
            PriorRevision::Folder(folder) => (None, folder.manifest.sources.as_slice()),
        };
        single
            .into_iter()
            .chain(sources.iter().map(|source| source.relative_path.as_str()))
    }
}

enum PriorRevision {
    SingleFile(Vec<PublishedLocalImportBar>),
    Folder(Box<PriorFolder>),
}

struct PriorFolder {
    manifest: StoredFolderManifest,
    summary: PublishedLocalFolderRevision,
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

impl<S: LocalDatasetStore + FolderManifestStore> OperationRegistry<S> {
    pub fn load_folder_refresh_base(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<FolderRefreshBase, Error> {
        self.authorize_local_import(access, PREVIEW_LOCAL_FOLDER_REFRESH)?;
        let folder = if access.operations.contains(&READ_LOCAL_FOLDER_REVISION) {
            self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
            self.port.read_folder_manifest(revision_id)?
        } else {
            None
        };
        let (dataset, source_key, prior) = if let Some(manifest) = folder {
            let anchor = self
                .port
                .read_folder_manifest_anchor(revision_id)?
                .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
            let summary = self
                .port
                .folder_manifest_summary(revision_id)?
                .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
            (
                anchor.revision,
                anchor.source_key,
                PriorRevision::Folder(Box::new(PriorFolder { manifest, summary })),
            )
        } else {
            self.authorize_local_import(access, READ_LOCAL_IMPORT_REVISION)?;
            let dataset = self
                .port
                .read_local_dataset(revision_id)?
                .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
            let summary = serde_json::from_value(dataset.summary())
                .map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
            let prior = PriorRevision::SingleFile(bars(dataset.content["bars"].clone())?);
            (summary, dataset.source_key, prior)
        };
        let mapping = self
            .port
            .load_mapping_version(&source_key, dataset.review_revision)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        if local_mapping_fingerprint(&mapping.mapping)? != dataset.mapping_sha256 {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(FolderRefreshBase {
            dataset,
            source_key,
            mapping,
            prior,
        })
    }
}

enum AcceptedBar {
    SingleFile(PublishedLocalImportBar),
    Folder(FolderRefreshAlternative),
}

impl AcceptedBar {
    fn bar(&self) -> &Bar {
        match self {
            Self::SingleFile(entry) => &entry.bar,
            Self::Folder(entry) => &entry.bar,
        }
    }

    fn into_value(self) -> Value {
        match self {
            Self::SingleFile(entry) => json!(entry),
            Self::Folder(entry) => json!(entry),
        }
    }
}

#[derive(Default)]
struct BarReview {
    accepted: Option<AcceptedBar>,
    alternatives: Vec<FolderRefreshAlternative>,
}

impl BarReview {
    fn result(self, time_ms: i64, coverage_unverified: bool) -> Value {
        let status = match (&self.accepted, self.alternatives.as_slice()) {
            (None, [_]) => "addition",
            (Some(_), []) if coverage_unverified => "unverified",
            (Some(_), []) => "withdrawal",
            (Some(old), [current]) if *old.bar() == current.bar => "unchanged",
            _ => "conflict",
        };
        json!({
            "time_ms": time_ms,
            "status": status,
            "accepted": self.accepted.map(AcceptedBar::into_value),
            "alternatives": self.alternatives,
        })
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
    let mut prior_sources = match &base.prior {
        PriorRevision::SingleFile(bars) => {
            for bar in bars {
                events.entry(bar.bar.time_ms).or_default().accepted =
                    Some(AcceptedBar::SingleFile(bar.clone()));
            }
            BTreeMap::from([(
                accepted.relative_path.as_str(),
                accepted.source_sha256.as_str(),
            )])
        }
        PriorRevision::Folder(folder) => {
            let sources: BTreeMap<_, _> = folder
                .manifest
                .sources
                .iter()
                .map(|source| (source.relative_path.as_str(), source.sha256.as_str()))
                .collect();
            for entry in folder_bars(&folder.manifest)? {
                check()?;
                let time_ms = entry.bar.time_ms;
                events.entry(time_ms).or_default().accepted = Some(AcceptedBar::Folder(entry));
            }
            sources
        }
    };

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
    let mut current_canonical = BTreeMap::new();
    // Keep an accepted representative only while its current bytes still match.
    // Groups without one take the first current path in sorted order below.
    for file in &input_files {
        let path = file.relative_path.as_str();
        if let Some(digest) = file.sha256.as_deref()
            && prior_sources.get(path) == Some(&digest)
        {
            current_canonical.entry(digest).or_insert(path);
        }
    }
    let series = json!(accepted.series);
    let mut sources = Vec::new();
    let mut coverage_unverified = false;
    let mut publication_allowed = true;
    for file in &input_files {
        check()?;
        let previous_digest = prior_sources.remove(file.relative_path.as_str());
        let digest = file.sha256.as_deref();
        let alias_of = digest
            .map(|digest| {
                *current_canonical
                    .entry(digest)
                    .or_insert(file.relative_path.as_str())
            })
            .filter(|path| *path != file.relative_path);
        let change = match (previous_digest, digest) {
            (None, _) => "added",
            (Some(_), None) => "unverified",
            (previous, current) if previous == current => "unchanged",
            _ => "replaced",
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
                if content["symbol"] != accepted.symbol || content["series"] != series {
                    coverage_unverified |=
                        previous_digest.is_some() || content["symbol"] == accepted.symbol;
                    publication_allowed = false;
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
        let unverified_file = matches!(
            &file.check,
            FolderRefreshCheck::HeaderChanged
                | FolderRefreshCheck::InvalidRows(_)
                | FolderRefreshCheck::Unavailable(_)
        );
        coverage_unverified |= unverified_file;
        publication_allowed &= !unverified_file;
        sources.push(json!({
            "relative_path": file.relative_path,
            "source_sha256": digest,
            "prior_sha256": previous_digest,
            "change": change,
            "alias_of": alias_of,
            "review": review,
            "detail": detail,
        }));
    }

    for (path, digest) in prior_sources {
        let obstructed = input_files.iter().any(|file| {
            matches!(&file.check, FolderRefreshCheck::Unavailable(_))
                && path
                    .strip_prefix(file.relative_path.as_str())
                    .is_some_and(|suffix| suffix.starts_with('/'))
        });
        let (change, review, detail) = if obstructed {
            (
                "unverified",
                "unavailable",
                "An opaque ancestor prevents checking this accepted source path.",
            )
        } else {
            (
                "removed",
                "removed",
                "The accepted source path is absent from the current scan.",
            )
        };
        sources.push(json!({
            "relative_path": path,
            "source_sha256": null,
            "prior_sha256": digest,
            "change": change,
            "alias_of": null,
            "review": review,
            "detail": detail,
        }));
    }
    sources.sort_unstable_by(|a, b| {
        a["relative_path"]
            .as_str()
            .cmp(&b["relative_path"].as_str())
    });

    if events.len() > MAX_STAGED_RECORDS {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let mut comparisons = Vec::with_capacity(events.len());
    for (time, review) in events {
        check()?;
        comparisons.push(review.result(time, coverage_unverified));
    }
    let mut output = json!({
        "version": 1,
        "files": sources,
        "bars": comparisons,
        "publication_allowed": publication_allowed,
    });
    match &base.prior {
        PriorRevision::SingleFile(_) => output["base"] = json!(accepted),
        PriorRevision::Folder(folder) => output["folder_base"] = json!(folder.summary),
    }
    if output.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !operation.output.is_valid(&output) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(output)
}

/// Resolve only the choices shown in the authoritative comparison. The retained
/// originals came from the same native scan and are still checked at commit.
pub fn prepare_folder_manifest<'a>(
    parent_id: &'a str,
    staged: &Value,
    retained_sources: Vec<FolderManifestSource<'a>>,
    decision: &PrepareLocalFolderPublicationInput,
) -> Result<FolderManifestPublication<'a>, Error> {
    let staged_parent = staged["folder_base"]["revision_id"]
        .as_str()
        .or_else(|| staged["base"]["revision_id"].as_str());
    if staged_parent != Some(parent_id) || staged["review_id"] != decision.review_id {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    if staged["publication_allowed"] != true {
        let mut error = failure(ErrorCode::DATAUNAVAILABLE);
        error.message = "Resolve unavailable or incompatible sources before publishing.".into();
        return Err(error);
    }

    let mut removed_paths: BTreeSet<_> = decision
        .withdraw_sources
        .iter()
        .map(String::as_str)
        .collect();
    let mut withdrawn_bars: BTreeSet<_> = decision.withdraw_bars.iter().copied().collect();
    let mut conflicts: BTreeMap<_, _> = decision
        .conflicts
        .iter()
        .map(|choice| (choice.time_ms, choice.alternative_index))
        .collect();
    if removed_paths.len() != decision.withdraw_sources.len()
        || withdrawn_bars.len() != decision.withdraw_bars.len()
        || conflicts.len() != decision.conflicts.len()
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }

    let files = staged["files"]
        .as_array()
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
    let source_hashes: BTreeMap<_, _> = retained_sources
        .iter()
        .map(|source| (source.relative_path, source.sha256))
        .collect();
    let mut checked_paths = BTreeSet::new();
    for file in files {
        let path = file["relative_path"]
            .as_str()
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
        if file["change"] == "removed" {
            if !removed_paths.remove(path) {
                return Err(failure(ErrorCode::CONFIRMATIONREQUIRED));
            }
        } else if !checked_paths.insert(path)
            || source_hashes
                .get(path)
                .is_none_or(|digest| file["source_sha256"] != *digest)
        {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
    }
    if checked_paths.len() != retained_sources.len() {
        return Err(failure(ErrorCode::STALECONTEXT));
    }

    let comparisons = staged["bars"]
        .as_array()
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
    let mut bars = Vec::with_capacity(comparisons.len());
    for comparison in comparisons {
        let time = comparison["time_ms"]
            .as_i64()
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
        let selected = match comparison["status"].as_str() {
            Some("addition" | "unchanged") => 0,
            Some("conflict") => conflicts
                .remove(&time)
                .ok_or_else(|| failure(ErrorCode::CONFIRMATIONREQUIRED))?,
            Some("withdrawal") if withdrawn_bars.remove(&time) => continue,
            Some("withdrawal") => return Err(failure(ErrorCode::CONFIRMATIONREQUIRED)),
            _ => return Err(failure(ErrorCode::OUTPUTINVALID)),
        };
        let alternative = comparison["alternatives"]
            .get(usize::try_from(selected).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?)
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        let choice: FolderRefreshAlternative = serde_json::from_value(alternative.clone())
            .map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
        if choice.bar.time_ms != time
            || choice.sources.iter().any(|locator| {
                source_hashes.get(locator.relative_path.as_str())
                    != Some(&locator.source_sha256.as_str())
            })
        {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        bars.push(FolderManifestBar {
            bar: choice.bar,
            locators: choice
                .sources
                .into_iter()
                .map(|locator| FolderManifestLocator {
                    relative_path: locator.relative_path,
                    record_number: locator.record_number,
                    line_start: locator.line_start,
                    line_end: locator.line_end,
                })
                .collect(),
        });
    }
    if !removed_paths.is_empty() || !withdrawn_bars.is_empty() || !conflicts.is_empty() {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    FolderManifestPublication::new(parent_id, retained_sources, bars)
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
        let sources = incoming.locators.iter().map(|locator| FolderRefreshSource {
            relative_path: file.relative_path.clone(),
            source_sha256: digest.to_owned(),
            record_number: locator.record_number,
            line_start: locator.line_start,
            line_end: locator.line_end,
        });
        if let Some(cached) = alternatives
            .iter_mut()
            .find(|entry| entry.bar == incoming.bar)
        {
            cached.sources.extend(sources);
        } else {
            if alternatives.len() >= MAX_ALTERNATIVES_PER_BAR {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            alternatives.push(FolderRefreshAlternative {
                bar: incoming.bar,
                sources: sources.collect(),
            });
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::FolderManifestSourceRef;
    use scopehub_domain::protocol::{
        FolderPublicationConflictChoice, LocalImportRevisionBarSummary,
    };
    use sha2::{Digest, Sha256};

    #[test]
    fn folder_parent_tracks_withdrawn_alias_without_withdrawing_its_bar() {
        const ANCHOR: &str = "00000000-0000-4000-8000-000000000001";
        const PARENT: &str = "00000000-0000-4000-8000-000000000002";
        const ALIAS_HASH: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const ANCHOR_HASH: &str =
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        let mut base = FolderRefreshBase {
            dataset: serde_json::from_value(json!({
                "revision_id": ANCHOR,
                "relative_path": "origin.csv",
                "source_sha256": ANCHOR_HASH,
                "mapping_sha256": ANCHOR_HASH,
                "review_revision": 1,
                "symbol": "000418",
                "series": {
                    "instrument_kind": "equity",
                    "venue": "TEST",
                    "timezone": "UTC",
                    "calendar_id": "fixture-calendar-1",
                    "session_scope": "regular",
                    "timeframe": "1d",
                    "quantity_unit": "shares",
                    "price_adjustment": "raw",
                    "roll_method": null
                },
                "bar_count": 1,
                "source_record_count": 1
            }))
            .unwrap(),
            source_key: "internal-origin-identity".into(),
            mapping: serde_json::from_value(json!({
                "review_revision": 1,
                "header_sha256": "",
                "mapping": {
                    "version": 1, "revision": "equity-check", "encoding": "utf-8",
                    "delimiter": ",", "record_kind": "native_ohlcv"
                }
            }))
            .unwrap(),
            prior: PriorRevision::SingleFile(Vec::new()),
        };
        let header = vec![json!("Date"), json!("Close")];
        base.mapping.header_sha256 = ordered_header_digest(&header, &base.mapping.mapping).unwrap();
        let bar = Bar {
            time_ms: 1_767_346_200_000,
            trading_day: "2026-01-02".into(),
            open: 5.0,
            high: 8.0,
            low: 4.0,
            close: 6.0,
            volume: None,
            is_closed: true,
        };
        let paths = ["archive/alpha.csv", "beta.csv"];
        base.prior = PriorRevision::Folder(Box::new(PriorFolder {
            manifest: StoredFolderManifest {
                revision_id: PARENT.into(),
                base_revision_id: ANCHOR.into(),
                parent_revision_id: None,
                sources: paths
                    .iter()
                    .map(|path| FolderManifestSourceRef {
                        relative_path: (*path).into(),
                        sha256: ALIAS_HASH.into(),
                        record_count: 1,
                    })
                    .collect(),
                bars: vec![FolderManifestBar {
                    bar: bar.clone(),
                    locators: paths
                        .iter()
                        .map(|path| FolderManifestLocator {
                            relative_path: (*path).into(),
                            record_number: 1,
                            line_start: 2,
                            line_end: 2,
                        })
                        .collect(),
                }],
            },
            summary: PublishedLocalFolderRevision {
                revision_id: PARENT.into(),
                base_revision_id: ANCHOR.into(),
                parent_revision_id: None,
                origin_path: base.dataset.relative_path.clone(),
                symbol: base.dataset.symbol.clone(),
                series: base.dataset.series.clone(),
                source_count: 2,
                bar_count: 1,
                bar_summary: Some(LocalImportRevisionBarSummary {
                    venue: "TEST".into(),
                    first_trading_day: "2026-01-02".into(),
                    final_trading_day: "2026-01-02".into(),
                    final_close: 6.0,
                }),
            },
        }));
        let current = FolderRefreshFile {
            relative_path: "beta.csv".into(),
            sha256: Some(ALIAS_HASH.into()),
            check: FolderRefreshCheck::Inspected(Arc::new(json!({
                "header": header,
                "file_sha256": ALIAS_HASH,
                "mapping_version": 1,
                "mapping_revision": "equity-check",
                "symbol": base.dataset.symbol,
                "series": base.dataset.series,
                "source_record_count": 1,
                "bars": [{
                    "bar": bar,
                    "locators": [{"record_number": 1, "line_start": 2, "line_end": 2}]
                }]
            }))),
        };
        assert_eq!(base.previous_paths().collect::<Vec<_>>(), paths);
        let blocked = folder_refresh_output(
            Actor::LocalUser,
            &base,
            vec![FolderRefreshFile {
                relative_path: "archive".into(),
                sha256: None,
                check: FolderRefreshCheck::Unavailable("Opaque directory entry.".into()),
            }],
            || false,
        )
        .unwrap();
        assert_eq!(blocked["files"][1]["relative_path"], "archive/alpha.csv");
        assert_eq!(blocked["files"][1]["change"], "unverified");
        assert_eq!(blocked["bars"][0]["status"], "unverified");
        assert_eq!(blocked["publication_allowed"], false);

        let compare = |paths: [&str; 2]| {
            let files = paths
                .into_iter()
                .map(|path| FolderRefreshFile {
                    relative_path: path.into(),
                    sha256: current.sha256.clone(),
                    check: current.check.clone(),
                })
                .collect();
            folder_refresh_output(Actor::LocalUser, &base, files, || false).unwrap()
        };
        let forward = compare(paths);
        let reverse = compare([paths[1], paths[0]]);
        assert_eq!(
            serde_json::to_vec(&forward).unwrap(),
            serde_json::to_vec(&reverse).unwrap()
        );
        assert_eq!(
            forward["bars"][0]["alternatives"].as_array().unwrap().len(),
            1
        );
        assert_eq!(
            forward["bars"][0]["alternatives"][0]["sources"]
                .as_array()
                .unwrap()
                .len(),
            2
        );

        let output =
            folder_refresh_output(Actor::LocalUser, &base, vec![current], || false).unwrap();
        assert!(output.get("base").is_none());
        assert!(!output.to_string().contains(ANCHOR_HASH));
        assert_eq!(output["folder_base"]["revision_id"], PARENT);
        assert_eq!(output["folder_base"]["bar_summary"]["final_close"], 6.0);
        assert_eq!(output["files"][0]["relative_path"], "archive/alpha.csv");
        assert_eq!(output["files"][0]["change"], "removed");
        assert_eq!(output["files"][0]["prior_sha256"], ALIAS_HASH);
        assert_eq!(output["files"][1]["relative_path"], "beta.csv");
        assert_eq!(output["files"][1]["change"], "unchanged");
        let comparison = &output["bars"][0];
        assert_eq!(comparison["status"], "unchanged");
        assert_eq!(
            comparison["accepted"]["sources"][0]["relative_path"],
            "archive/alpha.csv"
        );
        assert_eq!(
            comparison["accepted"]["sources"][1]["relative_path"],
            "beta.csv"
        );
        assert_eq!(
            comparison["alternatives"][0]["sources"],
            json!([comparison["accepted"]["sources"][1]])
        );
        assert_eq!(output["publication_allowed"], true);
    }

    #[test]
    fn publication_requires_every_reviewed_disposition_and_keeps_the_selected_row() {
        const PARENT: &str = "00000000-0000-4000-8000-000000000001";
        const REVIEW: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const ORIGINAL: &[u8] = b"Date,Close\n2026-01-02,6\n2026-01-02,7\n";
        const CONFLICT: i64 = 1_767_346_200_000;
        const WITHDRAWN: i64 = 1_767_432_600_000;
        let digest = format!("{:x}", Sha256::digest(ORIGINAL));
        let sources = || {
            vec![FolderManifestSource {
                relative_path: "new.csv",
                sha256: &digest,
                record_count: 2,
                original: ORIGINAL,
            }]
        };
        let alternative = |close, record_number, line| {
            json!({
                "bar": {
                    "time_ms": CONFLICT,
                    "trading_day": "2026-01-02",
                    "open": 5.0,
                    "high": 8.0,
                    "low": 4.0,
                    "close": close,
                    "volume": null,
                    "is_closed": true
                },
                "sources": [{
                    "relative_path": "new.csv",
                    "source_sha256": digest,
                    "record_number": record_number,
                    "line_start": line,
                    "line_end": line
                }]
            })
        };
        let mut staged = json!({
            "folder_base": {"revision_id": PARENT},
            "review_id": REVIEW,
            "publication_allowed": true,
            "files": [
                {"relative_path": "old.csv", "change": "removed"},
                {"relative_path": "new.csv", "change": "added", "source_sha256": digest}
            ],
            "bars": [
                {
                    "time_ms": CONFLICT,
                    "status": "conflict",
                    "alternatives": [alternative(6.0, 1, 2), alternative(7.0, 2, 3)]
                },
                {"time_ms": WITHDRAWN, "status": "withdrawal", "alternatives": []}
            ]
        });
        let mut decision = PrepareLocalFolderPublicationInput {
            version: 1,
            folder_id: "00000000-0000-4000-8000-000000000002".into(),
            review_id: REVIEW.into(),
            conflicts: vec![FolderPublicationConflictChoice {
                time_ms: CONFLICT,
                alternative_index: 1,
            }],
            withdraw_bars: vec![WITHDRAWN],
            withdraw_sources: vec!["old.csv".into()],
        };
        let candidate = prepare_folder_manifest(PARENT, &staged, sources(), &decision).unwrap();
        assert_eq!(candidate.parent_id(), PARENT);
        assert_eq!(candidate.sources()[0].relative_path, "new.csv");
        assert_eq!(candidate.bars().len(), 1);
        assert_eq!(candidate.bars()[0].bar.close, 7.0);
        assert_eq!(
            candidate.bars()[0].locators,
            [FolderManifestLocator {
                relative_path: "new.csv".into(),
                record_number: 2,
                line_start: 3,
                line_end: 3,
            }]
        );

        let attempted = |decision: &PrepareLocalFolderPublicationInput, staged: &Value| {
            prepare_folder_manifest(PARENT, staged, sources(), decision)
                .unwrap_err()
                .code
        };
        let choice = decision.conflicts.pop().unwrap();
        assert_eq!(
            attempted(&decision, &staged),
            ErrorCode::CONFIRMATIONREQUIRED
        );
        decision.conflicts.push(choice);
        decision.withdraw_bars.clear();
        assert_eq!(
            attempted(&decision, &staged),
            ErrorCode::CONFIRMATIONREQUIRED
        );
        decision.withdraw_bars.push(WITHDRAWN);
        decision.withdraw_sources.clear();
        assert_eq!(
            attempted(&decision, &staged),
            ErrorCode::CONFIRMATIONREQUIRED
        );
        decision.withdraw_sources.push("old.csv".into());
        decision.withdraw_sources.push("unreviewed.csv".into());
        assert_eq!(attempted(&decision, &staged), ErrorCode::INVALIDARGUMENT);
        decision.withdraw_sources.pop();
        decision.conflicts[0].alternative_index = 2;
        assert_eq!(attempted(&decision, &staged), ErrorCode::INVALIDARGUMENT);
        decision.conflicts[0].alternative_index = 1;
        staged["publication_allowed"] = json!(false);
        assert_eq!(attempted(&decision, &staged), ErrorCode::DATAUNAVAILABLE);
        decision.review_id =
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".into();
        assert_eq!(attempted(&decision, &staged), ErrorCode::STALECONTEXT);
    }
}
