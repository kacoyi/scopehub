use std::io::Write;

use scopehub_application::AI_TEXT_CONNECTION;
use scopehub_domain::wire::max_message_bytes;
use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    LiveBridge, PROFILE_ID, Profile, WORKSPACE_ID, assert_committed, assert_snapshot, set,
    with_new_correlation,
};

#[test]
fn private_text_results_cross_the_public_frame_limit() {
    let profile = Profile::new();
    let mut live = LiveBridge::open(&profile, PROFILE_ID);
    assert_snapshot(&live.reply(), 0, "1m");
    let connection = activate_text_route(&mut live);
    let escaped = "界🧭\"\n\\\u{1f}";
    let mut maximum = escaped.repeat(65_536 / escaped.len());
    maximum.push_str(&"x".repeat(65_536 - maximum.len()));
    assert_eq!(maximum.len(), 65_536);

    for (text, expected_frame_bytes) in [("a".repeat(20_480), 20_943), (maximum, 113_655)] {
        let poll = start_text_poll(&mut live, &connection);
        let carrier = json!({
            "type": "ai_text_result", "command": poll,
            "worker": {"model": "demo", "text": text},
        });
        assert_eq!(carrier.to_string().len() + 1, expected_frame_bytes);
        let reply = live.send(&carrier);
        assert_eq!(reply["request_id"], poll["request_id"]);
        let turn = &reply["outcome"]["result"]["turn"];
        assert_eq!(
            *turn,
            json!({"state":"completed", "model":"demo", "text":text}),
        );
        let replay = live.send(&with_new_correlation(poll));
        assert_eq!(replay["outcome"]["result"]["turn"], *turn);
    }
    assert_committed(&live.send(&set(0, "5m", Uuid::new_v4())), 1, "5m");
    live.finish();
}

#[test]
fn oversized_frames_leave_the_same_writer_and_pending_turn_available() {
    let profile = Profile::new();
    let mut live = LiveBridge::open(&profile, PROFILE_ID);
    assert_snapshot(&live.reply(), 0, "1m");

    let mutation = set(0, "15m", Uuid::new_v4());
    writeln!(
        live.input.as_mut().unwrap(),
        "{mutation}{}",
        " ".repeat(16 * 1024)
    )
    .unwrap();
    assert_bridge_limit(&live);

    let connection = activate_text_route(&mut live);
    let poll = start_text_poll(&mut live, &connection);
    let worker = json!({"model":"responding:synthetic", "text":"original answer"});
    let mut oversized_poll = poll.clone();
    oversized_poll["padding"] = json!("x".repeat(16 * 1024));
    live.dispatch(&json!({"type":"ai_text_result", "command":oversized_poll, "worker":worker}));
    assert_bridge_limit(&live);
    live.dispatch(&json!({"type":"ai_text_result", "command":poll,
        "worker":{"model":"responding:synthetic", "text":"x".repeat(512 * 1024)}}));
    assert_bridge_limit(&live);

    let beyond_wire_limit = " ".repeat(max_message_bytes() + 20);
    writeln!(
        live.input.as_mut().unwrap(),
        "{beyond_wire_limit}{mutation}"
    )
    .unwrap();
    assert_bridge_limit(&live);

    let reply = live.send(&json!({"type":"ai_text_result", "command":poll, "worker":worker}));
    assert_eq!(
        reply["outcome"]["result"]["turn"]["text"],
        "original answer"
    );
    assert_committed(&live.send(&set(0, "5m", Uuid::new_v4())), 1, "5m");
    live.finish();
}

fn activate_text_route(live: &mut LiveBridge) -> Value {
    let proposal = live.send(&text_command(json!({
        "action":"propose", "route":{"kind":"ollama", "origin":"http://127.0.0.1:12345", "model":"synthetic:local"},
    })));
    let active = live.send(&text_command(json!({
        "action":"activate", "proposal_id":proposal["outcome"]["result"]["proposal_id"],
    })));
    active["outcome"]["result"]["connection"]["connection_id"].clone()
}

fn start_text_poll(live: &mut LiveBridge, connection: &Value) -> Value {
    let send = text_command(json!({
        "action":"send", "connection_id":connection, "question":"Synthetic question",
    }));
    live.dispatch(&send);
    let pending = live.frame();
    assert_eq!(
        pending["result"]["outcome"]["result"]["turn_id"],
        send["request_id"]
    );
    assert!(pending["text_model_work"].is_string());
    text_command(json!({
        "action":"poll", "connection_id":connection, "turn_id":send["request_id"],
    }))
}

fn text_command(arguments: Value) -> Value {
    with_new_correlation(json!({
        "protocol_version":"1.1", "type":"request", "timestamp_ms":1_763_600_000_000_u64,
        "scope":{"workspace_id":WORKSPACE_ID},
        "intent":{"operation":AI_TEXT_CONNECTION, "arguments":arguments},
    }))
}

fn assert_bridge_limit(live: &LiveBridge) {
    let frame = live.frame();
    assert_eq!(frame["ok"], false, "{frame}");
    assert_eq!(frame["error"]["code"], "RESOURCE_LIMIT", "{frame}");
}
