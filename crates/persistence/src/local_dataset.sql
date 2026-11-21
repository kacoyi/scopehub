CREATE TABLE local_dataset_source_blobs (
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    original_bytes BLOB NOT NULL CHECK (length(original_bytes) > 0),
    PRIMARY KEY (source_sha256)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_dataset_revisions (
    revision_id TEXT NOT NULL CHECK (length(revision_id) = 36),
    source_key TEXT NOT NULL,
    relative_path TEXT NOT NULL CHECK (length(relative_path) BETWEEN 1 AND 32768),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    review_revision INTEGER NOT NULL,
    mapping_sha256 TEXT NOT NULL CHECK (length(mapping_sha256) = 64),
    content_json TEXT NOT NULL CHECK (json_valid(content_json) AND json_type(content_json) = 'object'),
    PRIMARY KEY (revision_id),
    UNIQUE (source_key, source_sha256, review_revision),
    FOREIGN KEY (source_sha256) REFERENCES local_dataset_source_blobs(source_sha256),
    FOREIGN KEY (source_key, review_revision) REFERENCES reviewed_import_mappings(source_key, review_revision)
) STRICT, WITHOUT ROWID;

CREATE TABLE local_dataset_display_revocations (
    revision_id TEXT NOT NULL PRIMARY KEY,
    FOREIGN KEY (revision_id) REFERENCES local_dataset_revisions(revision_id)
) STRICT, WITHOUT ROWID;
