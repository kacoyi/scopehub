use scopehub_domain::protocol::{Message, Request, ToolCall};
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
    let expected: BTreeSet<&str> = manifest["operations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|operation| operation["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        requests, expected,
        "Every human request needs a semantic vector"
    );
    assert_eq!(
        tools, expected,
        "Every AI tool call needs a semantic vector"
    );
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
