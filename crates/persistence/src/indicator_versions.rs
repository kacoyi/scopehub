//! Storage for version records and their reversible chart attachments.

use rusqlite::{Connection, OptionalExtension, Row, params};
use scopehub_application::{
    IndicatorLayerRef, IndicatorLayerUndo, IndicatorVersionRecord, WorkspaceReceipt, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use serde_json::Value;

use crate::store_error;

fn layer(row: &Row<'_>) -> rusqlite::Result<IndicatorLayerRef> {
    Ok(IndicatorLayerRef {
        layer_id: row.get(0)?,
        version_id: row.get(1)?,
        dataset_id: row.get(2)?,
        active: row.get(3)?,
        sample_instrument: row.get(4)?,
    })
}

pub(super) fn read_layer(
    conn: &Connection,
    workspace: &str,
    layer_id: &str,
) -> Result<Option<IndicatorLayerRef>, Error> {
    conn.query_row(
        "SELECT version.layer_id, version.version_id, version.dataset_id, state.active,
            CASE WHEN json_extract(version.document, '$.source_snapshot.series.provider_id') = 'scopehub.synthetic'
             THEN json_extract(version.document, '$.source_snapshot.series.instrument_id') END
         FROM indicator_versions AS version JOIN indicator_layer_state AS state USING (workspace_id, layer_id)
         WHERE version.workspace_id = ?1 AND version.layer_id = ?2", params![workspace, layer_id], layer,
    ).optional().map_err(store_error)
}

pub(super) fn list_layers(
    conn: &Connection,
    workspace: &str,
    dataset_id: &str,
) -> Result<Vec<IndicatorLayerRef>, Error> {
    conn.prepare(
        "SELECT version.layer_id, version.version_id, version.dataset_id, state.active,
            CASE WHEN json_extract(version.document, '$.source_snapshot.series.provider_id') = 'scopehub.synthetic'
             THEN json_extract(version.document, '$.source_snapshot.series.instrument_id') END
         FROM indicator_versions AS version JOIN indicator_layer_state AS state USING (workspace_id, layer_id)
         WHERE version.workspace_id = ?1 AND version.dataset_id = ?2 AND state.active = 1
         ORDER BY version.layer_id",
    ).map_err(store_error)?.query_map(params![workspace, dataset_id], layer).map_err(store_error)?
        .collect::<rusqlite::Result<_>>().map_err(store_error)
}

pub(super) fn operation_layer(
    conn: &Connection,
    workspace: &str,
    operation: &str,
) -> Result<Option<IndicatorLayerRef>, Error> {
    conn.query_row(
        "SELECT version.layer_id, version.version_id, version.dataset_id, tracked.after_active,
            CASE WHEN json_extract(version.document, '$.source_snapshot.series.provider_id') = 'scopehub.synthetic'
             THEN json_extract(version.document, '$.source_snapshot.series.instrument_id') END
         FROM indicator_layer_operations AS tracked JOIN indicator_versions AS version USING (workspace_id, layer_id)
         WHERE tracked.workspace_id = ?1 AND tracked.operation_id = ?2", params![workspace, operation], layer,
    ).optional().map_err(store_error)
}

pub(super) fn read_document(
    conn: &Connection,
    workspace: &str,
    layer_id: &str,
) -> Result<Option<Value>, Error> {
    conn.query_row(
        "SELECT document FROM indicator_versions WHERE workspace_id = ?1 AND layer_id = ?2",
        params![workspace, layer_id],
        |row| row.get::<_, String>(0),
    )
    .optional()
    .map_err(store_error)?
    .map(|saved| serde_json::from_str(&saved).map_err(|_| failure(ErrorCode::OUTPUTINVALID)))
    .transpose()
}

pub(super) fn active_count(conn: &Connection, workspace: &str) -> Result<u64, Error> {
    conn.query_row(
        "SELECT count(*) FROM indicator_layer_state WHERE workspace_id = ?1 AND active = 1",
        [workspace],
        |row| crate::revision(row, 0),
    )
    .map_err(store_error)
}

pub(super) fn accepted_preview(
    conn: &Connection,
    workspace: &str,
    preview_id: &str,
) -> Result<bool, Error> {
    conn.query_row(
        "SELECT EXISTS (SELECT 1 FROM indicator_versions WHERE workspace_id = ?1 AND preview_id = ?2)",
        params![workspace, preview_id], |row| row.get(0),
    ).map_err(store_error)
}

pub(super) fn record_version(
    conn: &Connection,
    workspace: &str,
    operation: &str,
    version: &IndicatorVersionRecord,
) -> Result<(), Error> {
    let layer = &version.layer;
    let body =
        serde_json::to_string(&version.document).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    conn.execute(
        "INSERT INTO indicator_versions (workspace_id, layer_id, version_id, preview_id, challenge_id, acceptance_id, dataset_id, document)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![workspace, layer.layer_id, layer.version_id, version.preview_id, version.challenge_id, operation, layer.dataset_id, body],
    ).map_err(store_error)?;
    conn.execute(
        "INSERT INTO indicator_layer_state (workspace_id, layer_id, active) VALUES (?1, ?2, 1)",
        params![workspace, layer.layer_id],
    )
    .map_err(store_error)?;
    record_operation(conn, workspace, operation, &layer.layer_id, false, true)
}

pub(super) fn record_layer(
    conn: &Connection,
    workspace: &str,
    operation: &str,
    layer: &IndicatorLayerRef,
    active: bool,
) -> Result<(), Error> {
    let updated = conn.execute(
        "UPDATE indicator_layer_state SET active = ?1 WHERE workspace_id = ?2 AND layer_id = ?3 AND active = ?4",
        params![active, workspace, layer.layer_id, layer.active],
    ).map_err(store_error)?;
    if updated != 1 {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    record_operation(
        conn,
        workspace,
        operation,
        &layer.layer_id,
        layer.active,
        active,
    )
}

fn record_operation(
    conn: &Connection,
    workspace: &str,
    operation: &str,
    layer: &str,
    before: bool,
    after: bool,
) -> Result<(), Error> {
    conn.execute(
        "INSERT INTO indicator_layer_operations (operation_id, workspace_id, layer_id, before_active, after_active)
         VALUES (?1, ?2, ?3, ?4, ?5)", params![operation, workspace, layer, before, after],
    ).map_err(store_error)?;
    Ok(())
}

pub(super) fn undo_target(
    conn: &Connection,
    workspace: &str,
    operation: &str,
    principal: &str,
    layer_id: &str,
) -> Result<Option<IndicatorLayerUndo>, Error> {
    conn.query_row(
        "SELECT change.before_active, change.after_active
         FROM indicator_layer_operations AS change JOIN operation_log AS log ON log.id = change.operation_id
         WHERE change.workspace_id = ?1 AND change.operation_id = ?2 AND log.principal_id = ?3
           AND change.layer_id = ?4 AND log.actor = 'user' AND log.paired_client_id = '' AND log.undoable = 1
           AND NOT EXISTS (
             SELECT 1 FROM indicator_layer_operations AS latest JOIN operation_log AS next ON next.id = latest.operation_id
             WHERE latest.workspace_id = ?1 AND latest.layer_id = ?4 AND next.revision > log.revision
           )",
        params![workspace, operation, principal, layer_id],
        |row| Ok(IndicatorLayerUndo { before: row.get(0)?, after: row.get(1)? }),
    ).optional().map_err(store_error)
}

pub(super) fn confirmation_receipt(
    conn: &Connection,
    workspace: &str,
    challenge_id: &str,
    principal_id: &str,
) -> Result<Option<(IndicatorLayerRef, WorkspaceReceipt)>, Error> {
    conn.query_row(
        "SELECT log.id, log.revision, log.undoable, log.before_timeframe, log.after_timeframe,
                version.layer_id, version.version_id, version.dataset_id,
                CASE WHEN json_extract(version.document, '$.source_snapshot.series.provider_id') = 'scopehub.synthetic'
             THEN json_extract(version.document, '$.source_snapshot.series.instrument_id') END
         FROM indicator_versions AS version JOIN operation_log AS log ON log.id = version.acceptance_id
         WHERE version.workspace_id = ?1 AND version.challenge_id = ?2 AND log.principal_id = ?3
           AND log.actor = 'user' AND log.paired_client_id = ''",
        params![workspace, challenge_id, principal_id],
        |row| Ok((IndicatorLayerRef { layer_id: row.get(5)?, version_id: row.get(6)?,
            dataset_id: row.get(7)?, sample_instrument: row.get(8)?, active: true }, crate::receipt(row)?)),
    ).optional().map_err(store_error)
}
