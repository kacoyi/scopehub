use scopehub_domain::{
    protocol::{
        DecisionNoteAppendOutcomeCommand11, DecisionNoteAppendOutcomeReply11,
        DecisionNoteDeleteCommand11, DecisionNoteDeleteReply11, DecisionNoteExportBackupCommand11,
        DecisionNoteExportBackupReply11, DecisionNotePreviewRestoreCommand11,
        DecisionNotePreviewRestoreReply11, DecisionNoteReadCommand11, DecisionNoteReadReply11,
        DecisionNoteRestoreBackupCommand11, DecisionNoteRestoreBackupReply11,
        DecisionNoteSaveCommand11, DecisionNoteSaveReply11, Message, Request, ToolCall,
    },
    wire::normalize_integers,
};
use scopehub_worker_host::{SCHEMA, decode_line, max_message_bytes};
use serde_json::Value;
use std::collections::BTreeSet;

// Explicit semantic oracles: new operations must extend these exhaustive matches.
fn request_operation(value: &Request) -> &'static str {
    match value {
        Request::InstrumentSearchRequest(_) => "instrument.search",
        Request::MarketGetBarsRequest(_) => "market.getBars",
        Request::WorkspaceOpenInstrumentRequest(_) => "workspace.openInstrument",
        Request::ChartSetTimeframeRequest(_) => "chart.setTimeframe",
        Request::ChartSetStyleRequest(_) => "chart.setStyle",
        Request::ChartAddAnnotationRequest(_) => "chart.addAnnotation",
        Request::ChartRemoveLayerRequest(_) => "chart.removeLayer",
        Request::IndicatorCreateDraftRequest(_) => "indicator.createDraft",
        Request::IndicatorRunPreviewRequest(_) => "indicator.runPreview",
        Request::IndicatorAcceptVersionRequest(_) => "indicator.acceptVersion",
        Request::IndicatorRemoveRequest(_) => "indicator.remove",
        Request::WorkspaceUndoRequest(_) => "workspace.undo",
        Request::TaskCancelRequest(_) => "task.cancel",
        Request::WorkspaceGetContextRequest(_) => "workspace.getContext",
    }
}

fn tool_operation(value: &ToolCall) -> &'static str {
    match value {
        ToolCall::InstrumentSearchToolCall(_) => "instrument.search",
        ToolCall::MarketGetBarsToolCall(_) => "market.getBars",
        ToolCall::WorkspaceOpenInstrumentToolCall(_) => "workspace.openInstrument",
        ToolCall::ChartSetTimeframeToolCall(_) => "chart.setTimeframe",
        ToolCall::ChartSetStyleToolCall(_) => "chart.setStyle",
        ToolCall::ChartAddAnnotationToolCall(_) => "chart.addAnnotation",
        ToolCall::ChartRemoveLayerToolCall(_) => "chart.removeLayer",
        ToolCall::IndicatorCreateDraftToolCall(_) => "indicator.createDraft",
        ToolCall::IndicatorRunPreviewToolCall(_) => "indicator.runPreview",
        ToolCall::IndicatorAcceptVersionToolCall(_) => "indicator.acceptVersion",
        ToolCall::IndicatorRemoveToolCall(_) => "indicator.remove",
        ToolCall::WorkspaceUndoToolCall(_) => "workspace.undo",
        ToolCall::TaskCancelToolCall(_) => "task.cancel",
        ToolCall::WorkspaceGetContextToolCall(_) => "workspace.getContext",
    }
}

#[test]
fn shared_vectors_and_generated_types_agree() {
    let mut requests = BTreeSet::new();
    let mut tools = BTreeSet::new();
    let examples: Vec<Value> =
        serde_json::from_str(include_str!("../../../tests/fixtures/protocol-cases.json")).unwrap();
    for example in examples {
        let result = decode_line(example["wire"].as_str().unwrap().as_bytes());
        assert_eq!(
            result.is_ok(),
            example["valid"].as_bool().unwrap(),
            "{}: {:?}",
            example["name"],
            result
        );
        if let Ok(message) = result {
            let original: Value = serde_json::from_str(example["wire"].as_str().unwrap()).unwrap();
            match &message {
                Message::Request(request) => {
                    let operation = request_operation(request);
                    assert_eq!(operation, original["operation"]);
                    requests.insert(operation);
                }
                Message::AiToolRequestEvent(event) => {
                    let operation = tool_operation(&event.payload);
                    assert_eq!(operation, original["payload"]["operation"]);
                    tools.insert(operation);
                }
                Message::MarketHistoryPageRequest(_) => {
                    requests.insert("market.readHistoryPage");
                }
                Message::MarketSubscriptionRequest(_) => {
                    requests.insert("market.subscribe");
                }
                Message::LocalImportPreviewRequest(_) => {
                    requests.insert("market.previewLocalImport");
                }
                Message::SaveLocalImportMappingRequest(_) => {
                    requests.insert("market.saveLocalImportMapping");
                }
                Message::LoadLocalImportMappingRequest(_) => {
                    requests.insert("market.loadLocalImportMapping");
                }
                Message::PublishLocalImportRequest(_) => {
                    requests.insert("market.publishLocalImport");
                }
                Message::ListLocalImportRevisionsRequest(_) => {
                    requests.insert("market.listLocalImportRevisions");
                }
                Message::ReadLocalImportRevisionRequest(_) => {
                    requests.insert("market.readLocalImportRevision");
                }
                Message::ReadLocalImportSourceRowRequest(_) => {
                    requests.insert("market.readLocalImportSourceRow");
                }
                Message::PreviewLocalFolderRefreshRequest(_) => {
                    requests.insert("market.previewLocalFolderRefresh");
                }
                Message::PrepareLocalFolderPublicationRequest(_) => {
                    requests.insert("market.prepareLocalFolderPublication");
                }
                Message::PublishLocalFolderRefreshRequest(_) => {
                    requests.insert("market.publishLocalFolderRefresh");
                }
                Message::ListLocalFolderRevisionsRequest(_) => {
                    requests.insert("market.listLocalFolderRevisions");
                }
                Message::ReadLocalFolderRevisionRequest(_) => {
                    requests.insert("market.readLocalFolderRevision");
                }
                Message::ReadLocalFolderSourceRowRequest(_) => {
                    requests.insert("market.readLocalFolderSourceRow");
                }
                Message::RevokeLocalFolderRevisionRequest(_) => {
                    requests.insert("market.revokeLocalFolderRevision");
                }
                Message::RevokeLocalImportRevisionRequest(_) => {
                    requests.insert("market.revokeLocalImportRevision");
                }
                Message::ReadProjectedChart11(_) => {
                    requests.insert("workspace.readChartProjection");
                }
                Message::SharedSelectionRequest11(_) => {
                    requests.insert("workspace.selectEvidence");
                }
                Message::AiToolRequest11(call) => {
                    use scopehub_domain::protocol::AiRequestedAction11;
                    match &call.payload {
                        AiRequestedAction11::MarketHistoryPageToolCall11(_) => {
                            tools.insert("market.readHistoryPage");
                        }
                        AiRequestedAction11::ProjectedReadIntent11(_) => {
                            tools.insert("workspace.readChartProjection");
                        }
                        AiRequestedAction11::SelectEvidenceIntent11(_) => {
                            tools.insert("workspace.selectEvidence");
                        }
                        _ => {}
                    }
                }
                Message::Heartbeat(heartbeat) if example.get("expected_timestamp").is_some() => {
                    assert_eq!(
                        heartbeat.timestamp_ms,
                        example["expected_timestamp"].as_i64().unwrap()
                    );
                }
                _ => {}
            }
            let encoded = serde_json::to_vec(&message).unwrap();
            assert_eq!(
                decode_line(&encoded).unwrap(),
                message,
                "roundtrip {}",
                example["name"]
            );
            let serialized: Value = serde_json::from_slice(&encoded).unwrap();
            if original["operation"] == "workspace.readChartProjection"
                && original["type"] == "response"
            {
                // Missing fields are withheld. A granted field with explicit null is
                // an unknown observation and must survive deserialization distinctly.
                assert_eq!(serialized["result"], original["result"]);
            }
            if original["operation"] == "market.previewLocalImport" && original["type"] == "request"
            {
                // A null volume selector explicitly declares that the source has
                // no volume column. An omitted selector is an unfinished mapping.
                assert_eq!(serialized["payload"], original["payload"]);
            }
            if matches!(
                original["operation"].as_str(),
                Some(
                    "market.saveLocalImportMapping"
                        | "market.loadLocalImportMapping"
                        | "market.publishLocalImport"
                        | "market.listLocalImportRevisions"
                        | "market.readLocalImportRevision"
                        | "market.readLocalImportSourceRow"
                        | "market.previewLocalFolderRefresh"
                        | "market.prepareLocalFolderPublication"
                        | "market.publishLocalFolderRefresh"
                        | "market.listLocalFolderRevisions"
                        | "market.readLocalFolderRevision"
                        | "market.readLocalFolderSourceRow"
                        | "market.revokeLocalFolderRevision"
                        | "market.revokeLocalImportRevision"
                )
            ) {
                let mut actual = serialized.clone();
                let mut expected = original.clone();
                // Native OHLC prices are floats even when the wire value is a whole number.
                normalize_integers(&mut actual);
                normalize_integers(&mut expected);
                assert_eq!(actual, expected, "{}", example["name"]);
            }
            if original.get("operation").is_some() {
                assert_eq!(serialized["operation"], original["operation"]);
            }
            if original["event"] == "ai.tool_request" {
                assert_eq!(
                    serialized["payload"]["operation"],
                    original["payload"]["operation"]
                );
            }
        }
    }
    let manifest: Value =
        serde_json::from_str(include_str!("../../../contracts/contract.json")).unwrap();
    let operations_for = |actor: &str| -> BTreeSet<&str> {
        manifest["operations"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|op| op["transport"] != "private_workspace")
            .filter(|op| {
                op["actors"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|allowed| allowed == actor)
            })
            .map(|operation| operation["name"].as_str().unwrap())
            .collect()
    };
    assert_eq!(
        requests,
        operations_for("user"),
        "Every public human request needs a semantic vector"
    );
    assert_eq!(
        tools,
        operations_for("ai"),
        "Every AI tool call needs a semantic vector"
    );
}

#[test]
fn private_workspace_vectors_use_local_projections_and_generated_types() {
    let manifest: Value =
        serde_json::from_str(include_str!("../../../contracts/contract.json")).unwrap();
    let cases: Vec<Value> = serde_json::from_str(include_str!(
        "../../../tests/fixtures/decision-note-protocol-cases.json"
    ))
    .unwrap();
    let operations: Vec<_> = manifest["operations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|op| op["transport"] == "private_workspace")
        .collect();
    let mut covered = BTreeSet::new();
    for example in &cases {
        let operation = example["operation"].as_str().unwrap();
        let direction = example["direction"].as_str().unwrap();
        let name = example["name"].as_str().unwrap();
        let message = &example["message"];
        let definition = operations
            .iter()
            .find(|op| op["name"] == operation)
            .unwrap()["wire_bindings"]["1.1"][direction]
            .as_str()
            .unwrap();
        let mut schema = SCHEMA.clone();
        schema["$ref"] = Value::String(format!("#/$defs/{definition}"));
        let validator = jsonschema::options()
            .should_validate_formats(true)
            .build(&schema)
            .unwrap();
        let valid = validator.is_valid(message);
        assert_eq!(valid, example["valid"].as_bool().unwrap(), "{name}");
        assert_eq!(
            decode_line(&serde_json::to_vec(message).unwrap()).is_ok(),
            example["shared_fault"] == true,
            "{name}: only the generic redacted fault may cross schema boundaries"
        );
        if !valid {
            continue;
        }
        covered.insert((operation, direction));
        let decoded = match (operation, direction) {
            ("decisionNote.save", "input") => roundtrip::<DecisionNoteSaveCommand11>(message),
            ("decisionNote.save", "output") => roundtrip::<DecisionNoteSaveReply11>(message),
            ("decisionNote.read", "input") => roundtrip::<DecisionNoteReadCommand11>(message),
            ("decisionNote.read", "output") => roundtrip::<DecisionNoteReadReply11>(message),
            ("decisionNote.appendOutcome", "input") => {
                roundtrip::<DecisionNoteAppendOutcomeCommand11>(message)
            }
            ("decisionNote.appendOutcome", "output") => {
                roundtrip::<DecisionNoteAppendOutcomeReply11>(message)
            }
            ("decisionNote.delete", "input") => roundtrip::<DecisionNoteDeleteCommand11>(message),
            ("decisionNote.delete", "output") => roundtrip::<DecisionNoteDeleteReply11>(message),
            ("decisionNote.exportBackup", "input") => {
                roundtrip::<DecisionNoteExportBackupCommand11>(message)
            }
            ("decisionNote.exportBackup", "output") => {
                roundtrip::<DecisionNoteExportBackupReply11>(message)
            }
            ("decisionNote.previewRestore", "input") => {
                roundtrip::<DecisionNotePreviewRestoreCommand11>(message)
            }
            ("decisionNote.previewRestore", "output") => {
                roundtrip::<DecisionNotePreviewRestoreReply11>(message)
            }
            ("decisionNote.restoreBackup", "input") => {
                roundtrip::<DecisionNoteRestoreBackupCommand11>(message)
            }
            ("decisionNote.restoreBackup", "output") => {
                roundtrip::<DecisionNoteRestoreBackupReply11>(message)
            }
            _ => panic!("Missing generated type for {operation} {direction}"),
        };
        assert_eq!(decoded, *message, "{name}");
    }
    for op in operations {
        let operation = op["name"].as_str().unwrap();
        for direction in ["input", "output"] {
            assert!(covered.contains(&(operation, direction)));
        }
    }
}

fn roundtrip<T: serde::de::DeserializeOwned + serde::Serialize>(message: &Value) -> Value {
    let decoded: T = serde_json::from_value(message.clone()).unwrap();
    serde_json::to_value(decoded).unwrap()
}

#[test]
fn rejects_invalid_utf8_and_oversize_frames() {
    assert!(decode_line(&[0xff, b'\n']).is_err());
    assert!(decode_line(&vec![b'x'; max_message_bytes() + 1]).is_err());
}

#[test]
fn every_operation_definition_compiles() {
    let manifest: Value =
        serde_json::from_str(include_str!("../../../contracts/contract.json")).unwrap();
    for operation in manifest["operations"].as_array().unwrap() {
        for direction in ["input", "output"] {
            let mut schema = SCHEMA.clone();
            schema["$ref"] = Value::String(format!(
                "#/$defs/{}",
                operation[direction].as_str().unwrap()
            ));
            jsonschema::options()
                .should_validate_formats(true)
                .build(&schema)
                .unwrap();
        }
    }
}
