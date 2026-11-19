CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY
) STRICT;

CREATE TABLE profile_binding (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    profile_id TEXT NOT NULL
) STRICT;

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision BETWEEN 0 AND 9007199254740991),
    selection_revision INTEGER NOT NULL CHECK (selection_revision BETWEEN 0 AND 9007199254740991),
    timeframe TEXT NOT NULL CHECK (timeframe IN ('1m', '5m', '1d'))
) STRICT;

CREATE TABLE operation_log (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    operation TEXT NOT NULL,
    actor TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    paired_client_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    committed_at_ms INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991),
    undoable INTEGER NOT NULL CHECK (undoable IN (0, 1)),
    before_timeframe TEXT NOT NULL CHECK (before_timeframe IN ('1m', '5m', '1d')),
    after_timeframe TEXT NOT NULL CHECK (after_timeframe IN ('1m', '5m', '1d')),
    undoes TEXT REFERENCES operation_log(id) UNIQUE,
    UNIQUE (workspace_id, revision)
) STRICT;

CREATE TABLE idempotency_records (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    principal_id TEXT NOT NULL,
    paired_client_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    digest BLOB NOT NULL CHECK (length(digest) = 32),
    operation_id TEXT NOT NULL UNIQUE REFERENCES operation_log(id),
    PRIMARY KEY (workspace_id, principal_id, paired_client_id, operation, idempotency_key)
) STRICT, WITHOUT ROWID;
