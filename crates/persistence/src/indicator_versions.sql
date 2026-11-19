CREATE TABLE indicator_versions (
    workspace_id TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    challenge_id TEXT NOT NULL,
    acceptance_id TEXT NOT NULL REFERENCES operation_log(id),
    dataset_id TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (workspace_id, layer_id),
    UNIQUE (workspace_id, version_id),
    UNIQUE (workspace_id, preview_id),
    UNIQUE (workspace_id, challenge_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
) STRICT, WITHOUT ROWID;

CREATE TABLE indicator_layer_state (
    workspace_id TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    PRIMARY KEY (workspace_id, layer_id),
    FOREIGN KEY (workspace_id, layer_id) REFERENCES indicator_versions(workspace_id, layer_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE indicator_layer_operations (
    operation_id TEXT NOT NULL PRIMARY KEY REFERENCES operation_log(id),
    workspace_id TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    before_active INTEGER NOT NULL CHECK (before_active IN (0, 1)),
    after_active INTEGER NOT NULL CHECK (after_active IN (0, 1)),
    CHECK (before_active <> after_active),
    FOREIGN KEY (workspace_id, layer_id) REFERENCES indicator_versions(workspace_id, layer_id)
) STRICT, WITHOUT ROWID;

CREATE INDEX indicator_versions_by_source ON indicator_versions (workspace_id, dataset_id);
CREATE INDEX indicator_layer_operation_history ON indicator_layer_operations (workspace_id, layer_id);
