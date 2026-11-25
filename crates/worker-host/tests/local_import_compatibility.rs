//! A caller using the original public Rust frozen CSV API.

use std::{future, path::Path};

use scopehub_domain::protocol::{
    FolderRefreshSource, LocalImportRowLocator, ReadLocalFolderSourceRowInput,
    ReadLocalFolderSourceRowOutput, ReadLocalImportSourceRowInput, ReadLocalImportSourceRowOutput,
};
use scopehub_worker_host::{local_import::read_source_row, market::WorkerConfig};
use serde_json::json;

#[test]
fn original_source_row_api_compiles() {
    let revision_id = "00000000-0000-4000-8000-000000000001".to_owned();
    let digest = "0".repeat(64);
    let locator = LocalImportRowLocator {
        record_number: 1,
        line_start: 2,
        line_end: 2,
    };
    let source = FolderRefreshSource {
        relative_path: "synthetic.csv".into(),
        source_sha256: digest.clone(),
        record_number: 1,
        line_start: 2,
        line_end: 2,
    };
    let file = ReadLocalImportSourceRowInput {
        version: 1,
        revision_id: revision_id.clone(),
        locator: locator.clone(),
    };
    let folder = ReadLocalFolderSourceRowInput {
        version: 1,
        revision_id: revision_id.clone(),
        source: source.clone(),
    };
    let raw_file = ReadLocalImportSourceRowOutput {
        version: 1,
        revision_id: revision_id.clone(),
        source_sha256: digest.clone(),
        locator,
        header: vec!["Close".into()],
        raw_fields: vec!["04.00".into()],
    };
    let raw_folder = ReadLocalFolderSourceRowOutput {
        version: 1,
        revision_id,
        source,
        header: vec!["Close".into()],
        raw_fields: vec!["04.00".into()],
    };
    let config = WorkerConfig::development(Path::new("."));
    let mapping = json!({});
    drop(read_source_row(
        &config,
        b"",
        &digest,
        &mapping,
        1,
        future::pending(),
    ));
    for value in [
        json!(file),
        json!(folder),
        json!(raw_file),
        json!(raw_folder),
    ] {
        assert!(value.get("review_declarations").is_none());
        assert!(value.get("declaration_conflicts").is_none());
    }
}
