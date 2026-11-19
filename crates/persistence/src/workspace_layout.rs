use rusqlite::{Connection, OptionalExtension, params};
use scopehub_application::{
    Actor, FrozenWorkspaceSourceStatus, WorkspaceLayoutChange, WorkspaceLayoutState,
    WorkspaceSourcePreference, WorkspaceSourceView, WorkspaceUndoAccess, WorkspaceUndoTarget,
    failure,
};
use scopehub_domain::protocol::{Error, ErrorCode, NamedWorkspaceLayout11, WorkspaceLayout11};

use crate::{revision, store_error};

pub fn read(connection: &Connection, workspace_id: &str) -> Result<WorkspaceLayoutState, Error> {
    let mut statement = connection
        .prepare("SELECT name, layout FROM workspace_layouts WHERE workspace_id = ?1 ORDER BY name")
        .map_err(store_error)?;
    let mut rows = statement.query([workspace_id]).map_err(store_error)?;
    let mut layouts = WorkspaceLayoutState::default();
    while let Some(row) = rows.next().map_err(store_error)? {
        let name: String = row.get(0).map_err(store_error)?;
        let layout: String = row.get(1).map_err(store_error)?;
        if name.is_empty() {
            layouts.active = checked_json(serde_json::from_str(&layout))?;
        } else {
            layouts
                .saved
                .insert(name, checked_json(serde_json::from_str(&layout))?);
        }
    }
    Ok(layouts)
}

pub fn change(
    connection: &Connection,
    workspace_id: &str,
    operation_id: &str,
    actor: Actor,
) -> Result<Option<WorkspaceLayoutChange>, Error> {
    let row = connection
        .query_row(
            "SELECT edit.before_layout, edit.after_layout, edit.saved_name, edit.saved_before
             FROM workspace_layout_changes AS edit JOIN operation_log AS log ON log.id = edit.operation_id
             WHERE log.workspace_id = ?1 AND log.id = ?2 AND log.actor = ?3",
            params![workspace_id, operation_id, actor.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            },
        )
        .optional()
        .map_err(store_error)?;
    row.map(|(before, after, saved_name, saved_before)| {
        Ok(WorkspaceLayoutChange {
            before: checked_json(serde_json::from_str(&before))?,
            after: checked_json(serde_json::from_str(&after))?,
            saved_name,
            saved_before: checked_json(
                saved_before
                    .as_deref()
                    .map(serde_json::from_str)
                    .transpose(),
            )?,
        })
    })
    .transpose()
}

pub fn eligible_undo(
    connection: &Connection,
    workspace_id: &str,
    access: WorkspaceUndoAccess<'_>,
) -> Result<Option<WorkspaceUndoTarget>, Error> {
    connection
        .query_row(
            "SELECT id, revision, actor, principal_id, paired_client_id, undoable
             FROM operation_log AS log JOIN workspace_layout_changes AS edit ON edit.operation_id = log.id
             WHERE workspace_id = ?1 ORDER BY revision DESC LIMIT 1",
            [workspace_id],
            |row| {
                let actor: String = row.get(2)?;
                let principal_id: String = row.get(3)?;
                let client: String = row.get(4)?;
                let undoable: bool = row.get(5)?;
                Ok(if undoable && actor == access.actor.as_str() && principal_id == access.principal_id
                    && client == access.paired_client_id.unwrap_or("") {
                    Some(WorkspaceUndoTarget {
                        operation_id: row.get(0)?,
                        committed_workspace_revision: revision(row, 1)?,
                    })
                } else {
                    None
                })
            },
        )
        .optional()
        .map(Option::flatten)
        .map_err(store_error)
}

pub fn write(
    connection: &Connection,
    workspace_id: &str,
    operation_id: &str,
    edit: &WorkspaceLayoutChange,
    saved_after: Option<&WorkspaceLayout11>,
) -> Result<(), Error> {
    let encode = |layout: &NamedWorkspaceLayout11| checked_json(serde_json::to_string(layout));
    let after = encode(&edit.after)?;
    store_row(connection, workspace_id, "", &after)?;
    if let Some(name) = &edit.saved_name {
        if let Some(layout) = saved_after {
            store_row(connection, workspace_id, name, &encode_definition(layout)?)?;
        } else {
            connection
                .execute(
                    "DELETE FROM workspace_layouts WHERE workspace_id = ?1 AND name = ?2",
                    params![workspace_id, name],
                )
                .map_err(store_error)?;
        }
    }
    connection
        .execute(
            "INSERT INTO workspace_layout_changes (operation_id, before_layout, after_layout, saved_name, saved_before)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                operation_id,
                encode(&edit.before)?,
                after,
                edit.saved_name,
                edit.saved_before.as_ref().map(encode_definition).transpose()?,
            ],
        )
        .map_err(store_error)?;
    Ok(())
}

fn store_row(
    connection: &Connection,
    workspace_id: &str,
    name: &str,
    layout: &str,
) -> Result<(), Error> {
    connection
        .execute(
            "INSERT INTO workspace_layouts (workspace_id, name, layout) VALUES (?1, ?2, ?3)
             ON CONFLICT(workspace_id, name) DO UPDATE SET layout = excluded.layout",
            params![workspace_id, name, layout],
        )
        .map_err(store_error)?;
    Ok(())
}

fn checked_json<T>(value: serde_json::Result<T>) -> Result<T, Error> {
    value.map_err(|_| failure(ErrorCode::INTERNALERROR))
}

fn encode_definition(layout: &WorkspaceLayout11) -> Result<String, Error> {
    checked_json(serde_json::to_string(layout))
}

pub fn source_view(
    connection: &Connection,
    workspace_id: &str,
) -> Result<Option<WorkspaceSourceView>, Error> {
    let row = connection.query_row(
        "SELECT preference.kind, preference.revision_id, preference.instrument_id,
                revision.revision_id IS NOT NULL, revoked.revision_id IS NOT NULL
         FROM workspace_source_preference AS preference
         LEFT JOIN local_dataset_revisions AS revision ON revision.revision_id = preference.revision_id
         LEFT JOIN local_dataset_display_revocations AS revoked ON revoked.revision_id = preference.revision_id
         WHERE preference.workspace_id = ?1",
        [workspace_id],
        |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?,
                  row.get::<_, Option<String>>(2)?, row.get::<_, bool>(3)?, row.get::<_, bool>(4)?)),
    ).optional().map_err(store_error)?;
    row.map(|(kind, revision_id, symbol, available, revoked)| {
        let (preference, frozen_status) = match (kind.as_str(), revision_id) {
            ("none", None) => (WorkspaceSourcePreference::Empty, None),
            ("sample", None) => (WorkspaceSourcePreference::Sample(symbol), None),
            ("frozen", Some(id)) => (
                WorkspaceSourcePreference::Frozen(id),
                Some(if revoked {
                    FrozenWorkspaceSourceStatus::Revoked
                } else if available {
                    FrozenWorkspaceSourceStatus::Available
                } else {
                    FrozenWorkspaceSourceStatus::Unavailable
                }),
            ),
            _ => return Err(failure(ErrorCode::INTERNALERROR)),
        };
        Ok(WorkspaceSourceView {
            preference,
            frozen_status,
        })
    })
    .transpose()
}

pub fn write_source(
    connection: &Connection,
    workspace_id: &str,
    source: &WorkspaceSourcePreference,
) -> Result<(), Error> {
    let (kind, revision_id, instrument_id) = match source {
        WorkspaceSourcePreference::Empty => ("none", None, None),
        WorkspaceSourcePreference::Sample(id) => ("sample", None, id.as_deref()),
        WorkspaceSourcePreference::Frozen(id) => ("frozen", Some(id.as_str()), None),
    };
    connection.execute(
        "INSERT INTO workspace_source_preference (workspace_id, kind, revision_id, instrument_id)
         VALUES (?1, ?2, ?3, ?4) ON CONFLICT(workspace_id) DO UPDATE
         SET kind = excluded.kind, revision_id = excluded.revision_id, instrument_id = excluded.instrument_id",
        params![workspace_id, kind, revision_id, instrument_id],
    ).map_err(store_error)?;
    Ok(())
}
