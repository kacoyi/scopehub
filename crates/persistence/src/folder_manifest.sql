CREATE TABLE local_folder_manifest_revisions (
    revision_id TEXT NOT NULL CHECK (length(revision_id) = 36),
    base_revision_id TEXT NOT NULL REFERENCES local_dataset_revisions(revision_id),
    parent_revision_id TEXT CHECK (parent_revision_id <> revision_id),
    input_sha256 TEXT NOT NULL UNIQUE CHECK (length(input_sha256) = 64),
    PRIMARY KEY (revision_id),
    UNIQUE (revision_id, base_revision_id),
    FOREIGN KEY (parent_revision_id, base_revision_id)
        REFERENCES local_folder_manifest_revisions(revision_id, base_revision_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_folder_manifest_sources (
    revision_id TEXT NOT NULL REFERENCES local_folder_manifest_revisions(revision_id),
    relative_path TEXT NOT NULL CHECK (length(relative_path) BETWEEN 1 AND 32768),
    source_sha256 TEXT NOT NULL REFERENCES local_dataset_source_blobs(source_sha256),
    record_count INTEGER NOT NULL CHECK (record_count BETWEEN 1 AND 10000),
    PRIMARY KEY (revision_id, relative_path)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_folder_manifest_bars (
    revision_id TEXT NOT NULL REFERENCES local_folder_manifest_revisions(revision_id),
    time_ms INTEGER NOT NULL CHECK (time_ms BETWEEN -9007199254740991 AND 9007199254740991),
    bar_json TEXT NOT NULL CHECK (
        json_valid(bar_json) AND json_type(bar_json) = 'object'
        AND json_extract(bar_json, '$.time_ms') IS time_ms
    ),
    PRIMARY KEY (revision_id, time_ms)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_folder_manifest_locators (
    revision_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    record_number INTEGER NOT NULL CHECK (record_number BETWEEN 1 AND 10000),
    time_ms INTEGER NOT NULL,
    line_start INTEGER NOT NULL CHECK (line_start BETWEEN 1 AND 4194305),
    line_end INTEGER NOT NULL CHECK (line_end BETWEEN line_start AND 4194305),
    PRIMARY KEY (revision_id, relative_path, record_number),
    FOREIGN KEY (revision_id, relative_path)
        REFERENCES local_folder_manifest_sources(revision_id, relative_path),
    FOREIGN KEY (revision_id, time_ms) REFERENCES local_folder_manifest_bars(revision_id, time_ms)
) STRICT, WITHOUT ROWID;

CREATE INDEX local_folder_manifest_locators_bar ON local_folder_manifest_locators
    (revision_id, time_ms, relative_path, record_number);

CREATE TABLE local_folder_manifest_receipts (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    principal_id TEXT NOT NULL CHECK (length(principal_id) BETWEEN 1 AND 256),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    revision_id TEXT NOT NULL REFERENCES local_folder_manifest_revisions(revision_id),
    PRIMARY KEY (workspace_id, principal_id, idempotency_key)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_folder_manifest_revocations (
    revision_id TEXT NOT NULL PRIMARY KEY REFERENCES local_folder_manifest_revisions(revision_id)
) STRICT, WITHOUT ROWID;
