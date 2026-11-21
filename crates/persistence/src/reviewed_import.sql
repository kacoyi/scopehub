CREATE TABLE reviewed_import_mappings (
    source_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision BETWEEN 1 AND 9007199254740991),
    header_sha256 TEXT NOT NULL CHECK (length(header_sha256) = 64),
    mapping_json TEXT NOT NULL CHECK (json_valid(mapping_json) AND json_type(mapping_json) = 'object'),
    PRIMARY KEY (source_key, review_revision)
) STRICT, WITHOUT ROWID;
