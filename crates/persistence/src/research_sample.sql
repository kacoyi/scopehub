CREATE TABLE research_sample (
    workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id),
    access TEXT NOT NULL CHECK (access IN ('connected', 'disconnected', 'revoked')),
    revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991),
    refreshed INTEGER NOT NULL CHECK (refreshed IN (0, 1))
) STRICT, WITHOUT ROWID;
