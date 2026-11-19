CREATE TABLE workspace_layouts (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL CHECK (length(name) <= 48),
    layout TEXT NOT NULL CHECK (json_valid(layout)),
    PRIMARY KEY (workspace_id, name)
) STRICT, WITHOUT ROWID;

CREATE TABLE workspace_layout_changes (
    operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operation_log(id),
    before_layout TEXT NOT NULL CHECK (json_valid(before_layout)),
    after_layout TEXT NOT NULL CHECK (json_valid(after_layout)),
    saved_name TEXT CHECK (length(saved_name) BETWEEN 1 AND 48),
    saved_before TEXT CHECK (json_valid(saved_before)),
    CHECK (saved_name IS NOT NULL OR saved_before IS NULL)
) STRICT, WITHOUT ROWID;

CREATE TABLE workspace_source_preference (
    workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id),
    kind TEXT NOT NULL CHECK (kind IN ('none', 'sample', 'frozen')),
    revision_id TEXT,
    instrument_id TEXT CHECK (instrument_id IS NULL OR kind = 'sample'),
    CHECK ((kind = 'frozen') = (revision_id IS NOT NULL))
) STRICT, WITHOUT ROWID;
