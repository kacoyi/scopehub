CREATE TABLE private_records (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content TEXT NOT NULL CHECK (
        json_valid(content) AND json_type(content) = 'object'
        AND length(CAST(content AS BLOB)) <= 8192
    ),
    PRIMARY KEY (workspace_id, record_id),
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;

CREATE TABLE private_record_deletions (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, record_id),
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;

CREATE TABLE private_record_receipts (
    operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operation_log(id),
    record_id TEXT
) STRICT, WITHOUT ROWID;

CREATE TABLE private_record_cleanup (
    workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id),
    revision INTEGER NOT NULL,
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;
