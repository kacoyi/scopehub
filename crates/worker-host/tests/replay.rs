use std::{collections::VecDeque, future::ready, path::PathBuf, time::Duration};

use scopehub_application::{
    Actor, MarketFuture, MarketPort, MarketRegistry, SubscriptionFuture, SubscriptionPort,
};
use scopehub_domain::protocol::{
    Error, ErrorCode, MarketSubscriptionInput, MarketSubscriptionOutput, Message,
};
use scopehub_worker_host::{
    decode_line,
    market::{MarketWorker, WorkerConfig, run_subscription},
};
use serde_json::{Value, json};

fn config() -> WorkerConfig {
    WorkerConfig::development(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

fn query(instrument: &str, timeframe: &str, unit: &str) -> Value {
    json!({
        "series": {"instrument_id": instrument, "timeframe": timeframe, "quantity_unit": unit,
          "provider_id": "scopehub.synthetic", "price_adjustment": "raw", "session_scope": "sample-sessions",
          "calendar_id": format!("{instrument}.sample-calendar.v1")},
        "from_ms": 1704067200000_i64, "to_ms": 1704499200000_i64, "warmup_bars": 0,
        "include_stream_position": true,
    })
}

fn subscription(page: &Value) -> Value {
    json!({"snapshot": page["snapshot"], "series": page["snapshot"]["series"], "after": page["stream_position"]})
}

#[tokio::test]
async fn resumes_at_the_exact_frozen_cutoff_in_a_fresh_worker() {
    let mut core = MarketRegistry::new(MarketWorker::new(Some(config())));
    for (timeframe, instrument, unit, close, volume) in [
        ("1m", "SIM.CN.FUTURE.001", "contracts", 3501.0, 130),
        ("5m", "SIM.CN.STOCK.001", "shares", 12.16, 530), // 10 + 120 + 160 + 110 + 130
        ("1d", "SIM.US.STOCK.001", "shares", 174.73, 2620),
    ] {
        let mut args = query(instrument, timeframe, unit);
        let initial = core
            .execute(Actor::User, "market.readHistoryPage", args.clone())
            .await
            .unwrap();
        assert_eq!(initial["stream_position"]["sequence"], 0);
        let cutoff = initial["snapshot"]["as_of_ms"].as_i64().unwrap();
        args["as_of_ms"] = json!(cutoff + 15_000);
        args["page_size"] = json!(1);
        let interim = core
            .execute(Actor::User, "market.readHistoryPage", args.clone())
            .await
            .unwrap();
        assert_eq!(interim["stream_position"]["sequence"], 1);
        assert_ne!(
            interim["snapshot"]["content_hash"],
            initial["snapshot"]["content_hash"]
        );
        assert_eq!(interim["next_cursor"]["next_index"], 1);
        let mut frames = Vec::new();
        let result = run_subscription(
            Some(config()),
            Actor::User,
            subscription(&interim),
            std::future::pending(),
            |frame| {
                frames.push(frame);
                Ok(())
            },
        )
        .await
        .unwrap();
        assert_eq!(result["status"], "complete");
        assert_eq!(frames.len(), 2);
        assert_eq!(
            frames[0]["subscription"]["from_position"],
            interim["stream_position"]
        );
        assert_eq!(frames[1]["event"]["sequence"], 2);
        let bar = &frames[1]["event"]["payload"]["bar"];
        assert_eq!(bar["close"], close);
        assert_eq!(bar["volume"].as_f64(), Some(f64::from(volume)));
        assert_eq!(bar["is_closed"], true);
        assert_eq!(
            frames[1]["event"]["payload"]["observed_at_ms"],
            cutoff + 30_000
        );

        args.as_object_mut().unwrap().remove("as_of_ms");
        args.as_object_mut().unwrap().remove("page_size");
        args.as_object_mut()
            .unwrap()
            .remove("include_stream_position");
        args["snapshot"] = initial["snapshot"].clone();
        let frozen = core
            .execute(Actor::User, "market.readHistoryPage", args)
            .await
            .unwrap();
        assert_eq!(frozen["bars"], initial["bars"]);
        assert_eq!(frozen["snapshot"], initial["snapshot"]);
        assert!(frozen["stream_position"].is_null());
    }
    core.shutdown().await.unwrap();
}

struct FaultPort {
    page: Value,
    instrument: Value,
    events: VecDeque<Value>,
    completed: bool,
}

impl MarketPort for FaultPort {
    fn request<'a>(&'a mut self, op: &'a str, _: &'a Value) -> MarketFuture<'a> {
        Box::pin(ready(Ok(if op == "instrument.search" {
            json!({"instruments": [self.instrument]})
        } else {
            self.page.clone()
        })))
    }
}

impl SubscriptionPort for FaultPort {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput> {
        let result =
            serde_json::from_value(json!({"status": "accepted", "from_position": input.after}))
                .unwrap();
        Box::pin(ready(Ok(result)))
    }

    fn next_event(&mut self) -> SubscriptionFuture<'_, Message> {
        let frame = self
            .events
            .pop_front()
            .expect("unexpected read past the terminal event");
        Box::pin(ready(
            Ok(decode_line(frame.to_string().as_bytes()).unwrap()),
        ))
    }

    fn restart_subscription(&mut self, _: u32) -> SubscriptionFuture<'_, ()> {
        panic!("event validation should not restart the worker")
    }

    fn complete_subscription(&mut self) {
        assert!(!self.completed);
        self.completed = true;
    }
}

async fn deliver(
    page: &Value,
    instrument: &Value,
    events: Vec<Value>,
) -> (Result<Value, Error>, Vec<Value>) {
    let mut core = MarketRegistry::new(FaultPort {
        page: page.clone(),
        instrument: instrument.clone(),
        events: events.into(),
        completed: false,
    });
    let mut frames = Vec::new();
    let result = core
        .subscribe(Actor::User, subscription(page), |frame| {
            frames.push(frame);
            Ok(())
        })
        .await;
    (result, frames)
}

#[tokio::test]
async fn stream_filters_old_selection_and_duplicates_and_stops_on_unverified_coverage() {
    let sample: Value =
        serde_json::from_str(include_str!("../../../tests/fixtures/market/series.json")).unwrap();
    let fixture = &sample["fixtures"][0];
    let mut core = MarketRegistry::new(MarketWorker::new(Some(config())));
    let page = core
        .execute(
            Actor::User,
            "market.readHistoryPage",
            query("SIM.CN.FUTURE.001", "1m", "contracts"),
        )
        .await
        .unwrap();
    core.shutdown().await.unwrap();
    let event = |sequence: usize| {
        let update = &fixture["updates"][sequence - 1];
        json!({"protocol_version": "1.1", "type": "event", "event": "market.bar_update",
            "message_id": "00000000-0000-4000-8000-000000000001", "timestamp_ms": 0,
            "stream_id": page["stream_position"]["stream_id"], "epoch": page["stream_position"]["epoch"], "sequence": sequence,
            "payload": {"series": page["snapshot"]["series"], "dataset_revision": page["snapshot"]["dataset_revision"],
                        "observed_at_ms": update["observed_at_ms"], "bar": update["bar"]}})
    };
    let changed = |mut value: Value, path: &str, new: Value| {
        *value.pointer_mut(path).unwrap() = new;
        value
    };
    let mut boundary = changed(event(1), "/sequence", json!(0));
    boundary["payload"]["bar"] = page["bars"].as_array().unwrap().last().unwrap().clone();
    boundary["payload"]["observed_at_ms"] = page["snapshot"]["as_of_ms"].clone();
    let delayed = changed(
        event(2),
        "/payload/series/instrument_id",
        json!("SIM.US.STOCK.001"),
    );
    let duplicate = changed(
        event(1),
        "/message_id",
        json!("00000000-0000-4000-8000-000000000002"),
    );
    let (result, frames) = deliver(
        &page,
        &fixture["instrument"],
        vec![
            delayed.clone(),
            boundary.clone(),
            event(1),
            boundary.clone(),
            duplicate,
            event(2),
        ],
    )
    .await;
    assert_eq!(result.unwrap()["status"], "complete");
    assert_eq!(frames[1]["event"]["sequence"], 1);
    assert_eq!(frames[2]["event"]["sequence"], 2);
    assert_eq!(frames.len(), 3);

    for (events, reason, forwarded) in [
        (
            vec![
                changed(boundary.clone(), "/payload/bar/volume", json!(111.0)),
                event(1),
            ],
            "divergent_duplicate",
            1,
        ),
        (
            vec![
                event(1),
                changed(
                    boundary,
                    "/payload/observed_at_ms",
                    json!(1704330585000_i64),
                ),
                event(2),
            ],
            "divergent_duplicate",
            2,
        ),
        (
            vec![changed(event(1), "/epoch", json!("previous")), event(2)],
            "epoch_reset",
            1,
        ),
        (vec![event(2)], "sequence_gap", 1),
        (
            vec![changed(
                event(1),
                "/payload/observed_at_ms",
                page["snapshot"]["as_of_ms"].clone(),
            )],
            "stale_observation",
            1,
        ),
        (
            vec![
                event(1),
                changed(event(1), "/payload/bar/close", json!(3501.0)),
            ],
            "divergent_duplicate",
            2,
        ),
        (vec![delayed; 128], "overflow", 1),
    ] {
        let (result, frames) = deliver(&page, &fixture["instrument"], events).await;
        assert_eq!(
            result.unwrap(),
            json!({"status": "gapped", "reason": reason})
        );
        assert_eq!(frames.len(), forwarded);
    }

    let mut explicit_gap = event(2);
    explicit_gap["event"] = json!("market.stream_gap");
    explicit_gap["payload"] = json!({"series": page["snapshot"]["series"], "reason": "sequence_gap",
        "expected_sequence": 1, "lost_count": 1, "affected": {"from_ms": 1704330540000_i64, "to_ms": 1704330600000_i64}});
    let (result, frames) = deliver(
        &page,
        &fixture["instrument"],
        vec![explicit_gap.clone(), event(2)],
    )
    .await;
    assert_eq!(result.unwrap()["status"], "gapped");
    assert_eq!(frames.last().unwrap()["event"], explicit_gap);

    let premature_close = changed(event(1), "/payload/bar/is_closed", json!(true));
    let (result, frames) = deliver(&page, &fixture["instrument"], vec![premature_close]).await;
    assert_eq!(result.unwrap_err().code, ErrorCode::OUTPUTINVALID);
    assert_eq!(frames.len(), 1);
}

#[tokio::test]
async fn cancellation_after_acceptance_does_not_forward_or_wait_for_scheduled_updates() {
    let mut core = MarketRegistry::new(MarketWorker::new(Some(config())));
    let page = core
        .execute(
            Actor::User,
            "market.readHistoryPage",
            query("SIM.CN.FUTURE.001", "1m", "contracts"),
        )
        .await
        .unwrap();
    core.shutdown().await.unwrap();
    let (send, receive) = tokio::sync::oneshot::channel();
    let mut send = Some(send);
    let mut frames = Vec::new();
    let result = tokio::time::timeout(
        Duration::from_secs(5),
        run_subscription(
            Some(config()),
            Actor::User,
            subscription(&page),
            async { receive.await.unwrap() },
            |frame| {
                frames.push(frame);
                send.take().unwrap().send(()).unwrap();
                Ok(())
            },
        ),
    )
    .await
    .unwrap();
    assert_eq!(result.unwrap_err().code, ErrorCode::CANCELLED);
    assert_eq!(frames.len(), 1);
    assert_eq!(frames[0]["subscription"]["status"], "accepted");
}
