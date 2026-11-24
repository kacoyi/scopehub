CREATE TABLE local_api_connection (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    rule_version INTEGER NOT NULL CHECK (rule_version = 1)
) STRICT;
