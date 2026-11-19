//! Validated native storage inputs for a reviewed folder revision.

use std::{
    collections::BTreeMap,
    ffi::OsStr,
    path::{Component, Path},
    sync::LazyLock,
};

use scopehub_domain::protocol::{
    Bar, Error, ErrorCode, PublishedLocalFolderRevision, PublishedLocalImportRevision,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{MAX_STAGED_RECORDS, MappingCommitGuard, failure, local_import::valid_bar, validator};

const MAX_SOURCES: usize = 64;
const MAX_SOURCE_BYTES: usize = 4 * 1024 * 1024;
const MAX_ORIGINAL_BYTES: usize = 16 * 1024 * 1024;
const MAX_SOURCE_RECORDS: u64 = 10_000;
const MAX_RELATIVE_PATH_CHARS: usize = 32_768;

static BAR_SCHEMA: LazyLock<jsonschema::Validator> = LazyLock::new(|| validator("Bar"));

#[derive(Debug)]
pub struct FolderManifestSource<'a> {
    pub relative_path: &'a str,
    pub sha256: &'a str,
    pub record_count: u64,
    pub original: &'a [u8],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FolderManifestSourceRef {
    pub relative_path: String,
    pub sha256: String,
    pub record_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FolderManifestLocator {
    pub relative_path: String,
    pub record_number: u64,
    pub line_start: u64,
    pub line_end: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FolderManifestBar {
    pub bar: Bar,
    pub locators: Vec<FolderManifestLocator>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StoredFolderManifest {
    pub revision_id: String,
    pub base_revision_id: String,
    pub parent_revision_id: Option<String>,
    pub sources: Vec<FolderManifestSourceRef>,
    pub bars: Vec<FolderManifestBar>,
}

/// The immutable mapping and source identity behind an entitled folder revision.
pub struct FolderManifestAnchor {
    pub source_key: String,
    pub revision: PublishedLocalImportRevision,
}

/// The authenticated local principal and the stable request identity in its workspace.
pub struct FolderManifestRequest<'a> {
    pub principal_id: &'a str,
    pub idempotency_key: &'a str,
}

/// A validated snapshot supplied by the trusted local review path.
#[derive(Debug)]
pub struct FolderManifestPublication<'a> {
    parent_id: &'a str,
    sources: Vec<FolderManifestSource<'a>>,
    bars: Vec<FolderManifestBar>,
    input_sha256: String,
}

impl<'a> FolderManifestPublication<'a> {
    pub fn new(
        parent_id: &'a str,
        mut sources: Vec<FolderManifestSource<'a>>,
        mut bars: Vec<FolderManifestBar>,
    ) -> Result<Self, Error> {
        if !Uuid::parse_str(parent_id).is_ok_and(|id| id.to_string() == parent_id) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        if sources.len() > MAX_SOURCES || bars.len() > MAX_STAGED_RECORDS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }

        sources.sort_unstable_by_key(|source| source.relative_path);
        let source_indices = validate_sources(&sources)?;
        bars.sort_unstable_by_key(|entry| entry.bar.time_ms);
        if bars
            .windows(2)
            .any(|pair| pair[0].bar.time_ms == pair[1].bar.time_ms)
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let input_sha256 = validate_bars_and_hash(parent_id, &sources, &source_indices, &mut bars)?;
        Ok(Self {
            parent_id,
            sources,
            bars,
            input_sha256,
        })
    }

    pub fn parent_id(&self) -> &str {
        self.parent_id
    }

    pub fn sources(&self) -> &[FolderManifestSource<'_>] {
        &self.sources
    }

    pub fn bars(&self) -> &[FolderManifestBar] {
        &self.bars
    }

    pub fn input_sha256(&self) -> &str {
        &self.input_sha256
    }
}

pub trait FolderManifestStore {
    fn publish_folder_manifest(
        &mut self,
        request: &FolderManifestRequest<'_>,
        candidate: &FolderManifestPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<String, Error>;

    fn replay_folder_manifest(
        &self,
        request: &FolderManifestRequest<'_>,
        input_sha256: &str,
    ) -> Result<Option<String>, Error>;

    fn read_folder_manifest(
        &self,
        revision_id: &str,
    ) -> Result<Option<StoredFolderManifest>, Error>;

    fn read_folder_manifest_anchor(
        &self,
        revision_id: &str,
    ) -> Result<Option<FolderManifestAnchor>, Error>;

    fn folder_manifest_summary(
        &self,
        revision_id: &str,
    ) -> Result<Option<PublishedLocalFolderRevision>, Error>;

    fn list_folder_manifest_summaries(
        &self,
        after_revision_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<PublishedLocalFolderRevision>, Error>;

    fn read_folder_manifest_source(
        &self,
        revision_id: &str,
        relative_path: &str,
    ) -> Result<Option<Vec<u8>>, Error>;

    fn revoke_folder_manifest(&mut self, revision_id: &str) -> Result<bool, Error>;
}

fn valid_relative_path(path: &str) -> bool {
    if path.contains('\0') || path.chars().count() > MAX_RELATIVE_PATH_CHARS {
        return false;
    }
    let mut native = Path::new(path).components();
    path.split('/').all(|part| {
        !matches!(part, "" | "." | "..")
            && native.next() == Some(Component::Normal(OsStr::new(part)))
    }) && native.next().is_none()
}

#[derive(Default)]
struct SourceIndices<'a> {
    by_path: BTreeMap<&'a str, usize>,
    aliases_by_digest: BTreeMap<&'a str, (u64, usize)>,
}

fn validate_sources<'a>(sources: &[FolderManifestSource<'a>]) -> Result<SourceIndices<'a>, Error> {
    let mut indices = SourceIndices::default();
    let mut bytes = 0;
    let mut records = 0;
    for (index, source) in sources.iter().enumerate() {
        if source.original.len() > MAX_SOURCE_BYTES || source.record_count > MAX_SOURCE_RECORDS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        bytes += source.original.len();
        records += source.record_count;
        if bytes > MAX_ORIGINAL_BYTES || records > MAX_STAGED_RECORDS as u64 {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        if !valid_relative_path(source.relative_path)
            || source.original.is_empty()
            || source.record_count == 0
            || indices
                .by_path
                .insert(source.relative_path, index)
                .is_some()
            || format!("{:x}", Sha256::digest(source.original)) != source.sha256
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let (record_count, aliases) = indices
            .aliases_by_digest
            .entry(source.sha256)
            .or_insert((source.record_count, 0));
        if *record_count != source.record_count {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        *aliases += 1;
    }
    Ok(indices)
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct RowLocation {
    time_ms: i64,
    line_start: u64,
    line_end: u64,
}

fn validate_bars_and_hash(
    parent_id: &str,
    sources: &[FolderManifestSource<'_>],
    source_indices: &SourceIndices<'_>,
    bars: &mut [FolderManifestBar],
) -> Result<String, Error> {
    let mut source_records = BTreeMap::new();
    let mut resolved = Vec::with_capacity(bars.len());
    for entry in bars {
        if entry.locators.len() > MAX_STAGED_RECORDS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let bar =
            serde_json::to_value(&entry.bar).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        if entry.locators.is_empty()
            || entry.bar.volume.is_some_and(|volume| !volume.is_finite())
            || !BAR_SCHEMA.is_valid(&bar)
            || !valid_bar(&bar)
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        entry.locators.sort_unstable_by(|left, right| {
            (&left.relative_path, left.record_number)
                .cmp(&(&right.relative_path, right.record_number))
        });
        let mut locators = Vec::with_capacity(entry.locators.len());
        for locator in &entry.locators {
            let Some(&source_index) = source_indices.by_path.get(locator.relative_path.as_str())
            else {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            };
            let source = &sources[source_index];
            let location = RowLocation {
                time_ms: entry.bar.time_ms,
                line_start: locator.line_start,
                line_end: locator.line_end,
            };
            if !(1..=source.record_count).contains(&locator.record_number)
                || location.line_start < 2
                || location.line_end < location.line_start
                || location.line_end > source.original.len() as u64 + 1
                || source_records
                    .insert((source_index, locator.record_number), location)
                    .is_some()
            {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            locators.push((
                source_index,
                locator.record_number,
                location.line_start,
                location.line_end,
            ));
        }
        resolved.push((&entry.bar, locators));
    }

    validate_source_records(sources, source_indices, source_records)?;

    let references: Vec<_> = sources
        .iter()
        .map(|source| (source.relative_path, source.sha256, source.record_count))
        .collect();
    let mut digest = Sha256::new();
    serde_json::to_writer(
        &mut digest,
        &(
            "scopehub-folder-manifest-v1",
            parent_id,
            references,
            resolved,
        ),
    )
    .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    Ok(format!("{:x}", digest.finalize()))
}

fn validate_source_records(
    sources: &[FolderManifestSource<'_>],
    source_indices: &SourceIndices<'_>,
    source_records: BTreeMap<(usize, u64), RowLocation>,
) -> Result<(), Error> {
    let mut previous = None;
    let mut alias_records = BTreeMap::new();
    for ((source, record), location) in source_records {
        if previous.is_some_and(|(prior_source, prior_end)| {
            prior_source == source && location.line_start <= prior_end
        }) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        previous = Some((source, location.line_end));
        let (accepted_location, seen) = alias_records
            .entry((sources[source].sha256, record))
            .or_insert((location, 0));
        if *accepted_location != location {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        *seen += 1;
    }
    // Alias files can omit any losing row together after explicit review. A
    // retained row must still carry the same evidence for every retained alias.
    if alias_records
        .iter()
        .any(|((digest, _), (_, seen))| *seen != source_indices.aliases_by_digest[digest].1)
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retained_aliases_agree_on_counts_and_carry_every_accepted_locator() {
        const PARENT: &str = "00000000-0000-4000-8000-000000000001";
        const ORIGINAL: &[u8] = b"Date,Close\n2026-01-02,5\n2026-01-03,6\n";
        let digest = format!("{:x}", Sha256::digest(ORIGINAL));
        let source = |relative_path, record_count| FolderManifestSource {
            relative_path,
            sha256: &digest,
            record_count,
            original: ORIGINAL,
        };
        let aliases = || vec![source("quotes.csv", 2), source("aliases/quotes.csv", 2)];
        let locator = |relative_path: &str, line_end| FolderManifestLocator {
            relative_path: relative_path.into(),
            record_number: 1,
            line_start: 2,
            line_end,
        };
        let bar = Bar {
            time_ms: 1_767_346_200_000,
            trading_day: "2026-01-02".into(),
            open: 4.0,
            high: 6.0,
            low: 3.0,
            close: 5.0,
            volume: None,
            is_closed: true,
        };
        let accepted = |locators| FolderManifestBar {
            bar: bar.clone(),
            locators,
        };
        let reject = |sources, bars| {
            assert_eq!(
                FolderManifestPublication::new(PARENT, sources, bars)
                    .unwrap_err()
                    .code,
                ErrorCode::INVALIDARGUMENT
            );
        };

        // Both aliases omit the second record after review.
        assert!(
            FolderManifestPublication::new(
                PARENT,
                aliases(),
                vec![accepted(vec![
                    locator("quotes.csv", 2),
                    locator("aliases/quotes.csv", 2),
                ])],
            )
            .is_ok()
        );
        reject(
            vec![source("quotes.csv", 2), source("aliases/quotes.csv", 1)],
            vec![],
        );
        reject(aliases(), vec![accepted(vec![locator("quotes.csv", 2)])]);
        reject(
            aliases(),
            vec![accepted(vec![
                locator("quotes.csv", 2),
                locator("aliases/quotes.csv", 3),
            ])],
        );
        let mut later = accepted(vec![locator("aliases/quotes.csv", 2)]);
        later.bar.time_ms += 86_400_000;
        later.bar.trading_day = "2026-01-03".into();
        reject(
            aliases(),
            vec![accepted(vec![locator("quotes.csv", 2)]), later],
        );
        reject(
            aliases(),
            vec![accepted(vec![
                locator("quotes.csv", ORIGINAL.len() as u64 + 2),
                locator("aliases/quotes.csv", ORIGINAL.len() as u64 + 2),
            ])],
        );
    }
}
