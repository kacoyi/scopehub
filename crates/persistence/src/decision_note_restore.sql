CREATE TABLE decision_note_outcomes_v3 (
    workspace_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    outcome TEXT NOT NULL,
    revision INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (workspace_id, note_id, ordinal),
    UNIQUE (workspace_id, outcome_id),
    FOREIGN KEY (workspace_id, note_id) REFERENCES decision_notes(workspace_id, note_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, revision) REFERENCES operation_log(workspace_id, revision)
) STRICT, WITHOUT ROWID;

INSERT INTO decision_note_outcomes_v3 (
    workspace_id, note_id, outcome_id, observed_on, outcome, revision, ordinal
)
SELECT workspace_id, note_id, outcome_id, observed_on, outcome, revision,
       row_number() OVER (PARTITION BY workspace_id, note_id ORDER BY revision)
FROM decision_note_outcomes;

DROP TABLE decision_note_outcomes;
ALTER TABLE decision_note_outcomes_v3 RENAME TO decision_note_outcomes;
