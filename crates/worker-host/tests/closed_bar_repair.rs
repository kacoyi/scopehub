use std::path::PathBuf;

use scopehub_application::{
    Actor, MarketFuture, MarketLifecycle, MarketPort, MarketRegistry, MarketStatus, ShutdownFuture,
    SubscriptionFuture, SubscriptionPort,
};
use scopehub_domain::protocol::{
    ErrorCode, MarketSubscriptionInput, MarketSubscriptionOutput, Message,
};
use scopehub_worker_host::market::{MarketWorker, WorkerConfig, run_subscription};
use serde_json::{Value, json};

fn config() -> WorkerConfig {
    WorkerConfig::development(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

fn query(scenario: &str) -> Value {
    json!({
        "series": {"instrument_id": "SIM.CN.FUTURE.001", "timeframe": "1m", "quantity_unit": "contracts",
          "provider_id": "scopehub.synthetic", "price_adjustment": "raw", "session_scope": "sample-sessions",
          "calendar_id": "SIM.CN.FUTURE.001.sample-calendar.v1"},
        "from_ms": 1704067200000_i64, "to_ms": 1704499200000_i64, "warmup_bars": 0,
        "expected_revision": format!("scopehub-synthetic-v1:20241013:{scenario}"),
        "include_stream_position": true,
    })
}

fn subscription(page: &Value) -> Value {
    json!({"snapshot": page["snapshot"], "series": page["snapshot"]["series"], "after": page["stream_position"]})
}

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/fixtures/market/closed-bar-repair.json"
    ))
    .unwrap()
}

#[tokio::test]
async fn real_worker_repairs_one_closed_bar_and_preserves_the_initial_snapshot() {
    let oracle = oracle();
    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let query = query("repairable");
    let initial = reads
        .execute(Actor::User, "market.readHistoryPage", query.clone())
        .await
        .unwrap();
    assert_eq!(initial["total_bars"], oracle["initial_count"]);
    assert_eq!(
        initial["quality_state"]["gaps"],
        json!([oracle["affected"]])
    );
    let mut frames = Vec::new();
    let result = run_subscription(
        Some(config()),
        Actor::User,
        subscription(&initial),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await
    .unwrap();

    assert_eq!(
        frames[0]["subscription"]["from_position"],
        initial["stream_position"]
    );
    assert_eq!(frames[1]["event"]["sequence"], 1);
    let gap = &frames[2]["event"];
    assert_eq!(gap["event"], "market.stream_gap");
    assert_eq!(gap["sequence"], 3);
    assert_eq!(gap["payload"]["expected_sequence"], 2);
    assert_eq!(gap["payload"]["lost_count"], oracle["lost_count"]);
    assert_eq!(gap["payload"]["affected"], oracle["affected"]);
    assert_eq!(result, json!({"status": "complete"}));
    assert_eq!(frames.len(), 4);
    let repair = &frames[3]["repair"];
    assert_eq!(repair["bars"], json!([oracle["bar"], oracle["final_bar"]]));
    let snapshot = &repair["snapshot"];
    assert_eq!(snapshot["dataset_id"], initial["snapshot"]["dataset_id"]);
    assert_eq!(
        snapshot["dataset_revision"],
        initial["snapshot"]["dataset_revision"]
    );
    assert_eq!(snapshot["series"], initial["snapshot"]["series"]);
    assert_eq!(snapshot["from_ms"], oracle["affected"]["from_ms"]);
    assert_eq!(snapshot["to_ms"], oracle["cutoff_ms"]);
    assert_eq!(snapshot["as_of_ms"], oracle["cutoff_ms"]);
    assert_ne!(
        snapshot["content_hash"],
        initial["snapshot"]["content_hash"]
    );
    assert_eq!(
        repair["position"],
        json!({"sequence": 3, "epoch": gap["epoch"], "stream_id": gap["stream_id"]})
    );
    assert_eq!(repair["quality_state"]["coverage"], "observed");
    assert_eq!(repair["quality_state"]["gaps"], json!([]));

    let mut projection = initial["bars"].as_array().unwrap().clone();
    assert_eq!(
        projection.pop().unwrap()["time_ms"],
        oracle["final_bar"]["time_ms"]
    );
    projection.extend(repair["bars"].as_array().unwrap().iter().cloned());
    assert_eq!(
        projection.len(),
        oracle["repaired_count"].as_u64().unwrap() as usize
    );
    assert!(
        projection
            .windows(2)
            .all(|bars| bars[0]["time_ms"].as_i64() < bars[1]["time_ms"].as_i64())
    );

    let mut pinned = query;
    pinned["snapshot"] = initial["snapshot"].clone();
    let frozen = reads
        .execute(Actor::User, "market.readHistoryPage", pinned)
        .await
        .unwrap();
    assert_eq!(frozen, initial);
    assert_eq!(frozen["bars"][38]["close"], 3500.8);
    assert_eq!(frozen["bars"][38]["volume"], 110.0);
    reads.shutdown().await.unwrap();
}

#[derive(Clone, Copy)]
enum Fault {
    Epoch,
    Revision,
    Cutoff,
    LostCount,
}

#[derive(Default)]
struct Trace {
    reads: Vec<Value>,
    opened: usize,
    completed: bool,
}

struct RepairProbe<'a> {
    worker: MarketWorker,
    trace: &'a mut Trace,
    fault: Option<Fault>,
}

impl MarketPort for RepairProbe<'_> {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a> {
        Box::pin(async move {
            let mut result = self.worker.request(operation, payload).await?;
            if operation == "market.readHistoryPage" {
                self.trace.reads.push(payload.clone());
                if self.trace.reads.len() == 2 {
                    match self.fault {
                        Some(Fault::Epoch) => result["stream_position"]["epoch"] = json!("other"),
                        Some(Fault::Revision) => {
                            result["snapshot"]["dataset_revision"] = json!("other")
                        }
                        Some(Fault::Cutoff) => {
                            result["snapshot"]["as_of_ms"] = json!(1704330585000_i64)
                        }
                        _ => {}
                    }
                }
            }
            Ok(result)
        })
    }
}

impl SubscriptionPort for RepairProbe<'_> {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput> {
        self.trace.opened += 1;
        self.worker.open_subscription(input)
    }

    fn next_event(&mut self) -> SubscriptionFuture<'_, Message> {
        Box::pin(async {
            let mut event = self.worker.next_event().await?;
            if let Message::MarketStreamGapEvent(gap) = &mut event
                && matches!(self.fault, Some(Fault::LostCount))
            {
                gap.payload.lost_count += 1;
            }
            Ok(event)
        })
    }

    fn restart_subscription(&mut self, _: u32) -> SubscriptionFuture<'_, ()> {
        panic!("closed-bar repair must never retry or open a replacement worker")
    }

    fn complete_subscription(&mut self) {
        self.trace.completed = true;
        self.worker.complete_subscription();
    }
}

impl MarketLifecycle for RepairProbe<'_> {
    fn status(&self) -> MarketStatus {
        self.worker.status()
    }

    fn shutdown(&mut self) -> ShutdownFuture<'_> {
        Box::pin(self.worker.shutdown())
    }
}

#[tokio::test]
async fn one_bounded_read_requires_available_bars_and_the_exact_revision_cutoff_and_position() {
    let oracle = oracle();
    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let repairable = reads
        .execute(Actor::User, "market.readHistoryPage", query("repairable"))
        .await
        .unwrap();
    let unrepaired = reads
        .execute(Actor::User, "market.readHistoryPage", query("unrepaired"))
        .await
        .unwrap();
    assert_eq!(unrepaired["total_bars"], oracle["initial_count"]);
    assert_eq!(unrepaired["bars"], repairable["bars"]);
    assert_eq!(unrepaired["quality_state"], repairable["quality_state"]);
    reads.shutdown().await.unwrap();

    for (page, fault, completes) in [
        (&repairable, None, true),
        (&unrepaired, None, false),
        (&repairable, Some(Fault::Epoch), false),
        (&repairable, Some(Fault::Revision), false),
        (&repairable, Some(Fault::Cutoff), false),
        (&repairable, Some(Fault::LostCount), false),
    ] {
        let mut trace = Trace::default();
        let mut core = MarketRegistry::new(RepairProbe {
            worker: MarketWorker::new(Some(config())),
            trace: &mut trace,
            fault,
        });
        let mut frames = Vec::new();
        let outcome = core
            .subscribe(Actor::User, subscription(page), |frame| {
                frames.push(frame);
                Ok(())
            })
            .await;
        core.shutdown().await.unwrap();
        assert!(core.status().process_id.is_none());
        drop(core);

        assert_eq!(trace.opened, 1);
        assert_eq!(trace.completed, completes);
        assert_eq!(trace.reads[0]["snapshot"], page["snapshot"]);
        if matches!(fault, Some(Fault::LostCount)) {
            assert_eq!(trace.reads.len(), 1);
            assert_eq!(outcome.unwrap_err().code, ErrorCode::OUTPUTINVALID);
            assert_eq!(frames.len(), 2);
            continue;
        }
        assert_eq!(trace.reads.len(), 2);
        assert_eq!(
            trace.reads[1],
            json!({
                "series": page["snapshot"]["series"], "expected_revision": page["snapshot"]["dataset_revision"],
                "from_ms": oracle["affected"]["from_ms"], "to_ms": oracle["cutoff_ms"],
                "warmup_bars": 0, "as_of_ms": oracle["cutoff_ms"], "page_size": 2,
                "include_stream_position": true,
            })
        );
        assert_eq!(
            frames[2]["event"]["payload"]["lost_count"],
            oracle["lost_count"]
        );
        assert_eq!(
            frames[2]["event"]["payload"]["affected"],
            oracle["affected"]
        );
        if completes {
            assert_eq!(frames.len(), 4);
            assert_eq!(outcome.unwrap(), json!({"status":"complete"}));
        } else {
            assert_eq!(frames.len(), 3);
            assert_eq!(
                outcome.unwrap(),
                json!({"status":"gapped", "reason":"sequence_gap"})
            );
        }
    }
}
