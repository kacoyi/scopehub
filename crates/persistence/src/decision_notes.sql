ALTER TABLE operation_log ADD COLUMN decision_note_id TEXT;
CREATE INDEX operations_by_decision_note ON operation_log (workspace_id, decision_note_id, revision)
    WHERE decision_note_id IS NOT NULL;

CREATE TABLE decision_notes (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    note_id TEXT NOT NULL,
    decided_on TEXT NOT NULL,
    thesis TEXT NOT NULL,
    evidence_refs TEXT NOT NULL CHECK (json_valid(evidence_refs) AND json_type(evidence_refs) = 'array'),
    choice TEXT NOT NULL,
    PRIMARY KEY (workspace_id, note_id)
) STRICT, WITHOUT ROWID;

CREATE INDEX decision_notes_by_date ON decision_notes (workspace_id, decided_on DESC, note_id);

CREATE TABLE decision_note_outcomes (
    workspace_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    outcome TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, note_id, revision),
    UNIQUE (workspace_id, outcome_id),
    FOREIGN KEY (workspace_id, note_id) REFERENCES decision_notes(workspace_id, note_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;

CREATE TABLE decision_note_deletions (
    profile_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    note_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    deleted_at_ms INTEGER NOT NULL,
    cleanup_pending INTEGER NOT NULL CHECK (cleanup_pending IN (0, 1)),
    PRIMARY KEY (profile_id, workspace_id, note_id),
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;
