CREATE TABLE workspace_chart_evidence (
    workspace_id TEXT NOT NULL PRIMARY KEY REFERENCES workspaces(id),
    revision_id TEXT NOT NULL REFERENCES local_dataset_revisions(revision_id),
    from_ms INTEGER NOT NULL CHECK (from_ms BETWEEN -9007199254740991 AND 9007199254740991),
    to_ms INTEGER NOT NULL CHECK (to_ms BETWEEN -9007199254740991 AND 9007199254740991),
    CHECK (from_ms < to_ms)
) STRICT, WITHOUT ROWID;
