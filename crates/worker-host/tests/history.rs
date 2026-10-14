use std::{path::PathBuf, time::Duration};

use scopehub_application::{Actor, MarketFuture, MarketPort, MarketRegistry};
use scopehub_domain::protocol::ErrorCode;
use scopehub_worker_host::market::{MarketWorker, WorkerConfig};
use serde_json::{Value, json};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}
fn input() -> Value {
    json!({"instrument_id":"SIM.CN.FUTURE.001", "provider_id":"scopehub.synthetic", "timeframe":"1m", "from_ms":0, "to_ms":9007199254740991_u64})
}
fn fixture() -> Value {
    let bundle: Value =
        serde_json::from_str(include_str!("../../../tests/fixtures/market/series.json")).unwrap();
    bundle["fixtures"][0]["history"].clone()
}
struct Fixed(Value, usize);
impl MarketPort for Fixed {
    fn request<'a>(&'a mut self, _: &'a str, _: &'a Value) -> MarketFuture<'a> {
        self.1 += 1;
        Box::pin(std::future::ready(Ok(self.0.clone())))
    }
}

#[tokio::test]
async fn trusted_actor_schema_and_implemented_allowlist_precede_worker_access() {
    let mut core = MarketRegistry::new(Fixed(fixture(), 0));
    for (actor, operation, payload, expected) in [
        (
            Actor::System,
            "market.getBars",
            input(),
            ErrorCode::PERMISSIONDENIED,
        ),
        (
            Actor::User,
            "shell.execute",
            json!({}),
            ErrorCode::UNSUPPORTEDCAPABILITY,
        ),
        (
            Actor::Ai,
            "workspace.openInstrument",
            json!({}),
            ErrorCode::UNSUPPORTEDCAPABILITY,
        ),
        (
            Actor::User,
            "market.getBars",
            json!({}),
            ErrorCode::INVALIDARGUMENT,
        ),
        (
            Actor::User,
            "instrument.search",
            json!({"query":"", "limit":51}),
            ErrorCode::INVALIDARGUMENT,
        ),
    ] {
        assert_eq!(
            core.execute(actor, operation, payload)
                .await
                .unwrap_err()
                .code,
            expected
        );
    }
    let mut args = input();
    args["actor"] = json!("system");
    assert_eq!(
        core.execute(Actor::User, "market.getBars", args)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let mut args = input();
    args["provider_id"] = json!("paid.provider");
    assert_eq!(
        core.execute(Actor::User, "market.getBars", args)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PROVIDERNOTCONFIGURED
    );
    let mut args = input();
    args["to_ms"] = json!(0);
    assert_eq!(
        core.execute(Actor::User, "market.getBars", args)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    assert_eq!(core.port_mut().1, 0);
    for actor in [Actor::User, Actor::Ai] {
        assert_eq!(
            core.execute(actor, "market.getBars", input())
                .await
                .unwrap(),
            fixture()
        );
    }
}

#[tokio::test]
async fn rejects_wrong_operation_identity_duplicate_order_ohlc_and_partial_output() {
    let base = fixture();
    let mut cases = vec![json!({"instruments":[]})];
    for (key, value) in [
        ("instrument_id", "OTHER"),
        ("provider_id", "OTHER"),
        ("timeframe", "5m"),
        ("mode", "historical"),
        ("price_adjustment", "adjusted"),
    ] {
        let mut changed = base.clone();
        changed["dataset"][key] = json!(value);
        cases.push(changed);
    }
    let mut changed = base.clone();
    changed["dataset"]["provenance"]["synthetic"] = json!(false);
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"][1]["time_ms"] = changed["bars"][0]["time_ms"].clone();
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"].as_array_mut().unwrap().swap(0, 1);
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"][0]["high"] = json!(0);
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"][0]["volume"] = json!(-1);
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"][0]["is_closed"] = json!(false);
    cases.push(changed);
    let mut changed = base.clone();
    changed["bars"][0]["trading_day"] = json!("2024-02-30");
    cases.push(changed);
    for output in cases {
        let mut core = MarketRegistry::new(Fixed(output, 0));
        assert_eq!(
            core.execute(Actor::User, "market.getBars", input())
                .await
                .unwrap_err()
                .code,
            ErrorCode::OUTPUTINVALID
        );
    }
    let mut core = MarketRegistry::new(Fixed(base.clone(), 0));
    let mut args = input();
    args["from_ms"] = base["bars"][1]["time_ms"].clone();
    assert_eq!(
        core.execute(Actor::User, "market.getBars", args)
            .await
            .unwrap_err()
            .code,
        ErrorCode::OUTPUTINVALID
    );
    let mut args = input();
    args["to_ms"] = base["bars"][39]["time_ms"].clone();
    assert_eq!(
        core.execute(Actor::User, "market.getBars", args)
            .await
            .unwrap_err()
            .code,
        ErrorCode::OUTPUTINVALID
    );
}

#[tokio::test]
async fn catalog_limits_identity_and_integer_wire_equivalence_are_enforced() {
    let bundle: Value =
        serde_json::from_str(include_str!("../../../tests/fixtures/market/series.json")).unwrap();
    let instrument = bundle["fixtures"][0]["instrument"].clone();
    let mut core = MarketRegistry::new(Fixed(json!({"instruments":[instrument.clone()]}), 0));
    assert!(
        core.execute(
            Actor::User,
            "instrument.search",
            json!({"query":"", "limit":1.0})
        )
        .await
        .is_ok()
    );
    for output in [
        json!({"instruments":[instrument.clone(), instrument.clone()]}),
        {
            let mut instrument = instrument.clone();
            instrument["metadata"]["synthetic"] = json!(false);
            json!({"instruments":[instrument]})
        },
    ] {
        let mut core = MarketRegistry::new(Fixed(output, 0));
        assert_eq!(
            core.execute(
                Actor::User,
                "instrument.search",
                json!({"query":"", "limit":50})
            )
            .await
            .unwrap_err()
            .code,
            ErrorCode::OUTPUTINVALID
        );
    }
    let mut args = input();
    args["from_ms"] = json!(0.0);
    assert!(
        MarketRegistry::new(Fixed(fixture(), 0))
            .execute(Actor::User, "market.getBars", args)
            .await
            .is_ok()
    );
}

#[tokio::test]
async fn real_worker_is_lazy_reused_matches_all_golden_histories_and_closes() {
    let mut core = MarketRegistry::new(MarketWorker::new(Some(WorkerConfig::development(&root()))));
    assert_eq!(core.port_mut().process_id(), None);
    let catalog = core
        .execute(
            Actor::User,
            "instrument.search",
            json!({"query":"", "limit":50}),
        )
        .await
        .unwrap();
    assert_eq!(catalog["instruments"].as_array().unwrap().len(), 3);
    let pid = core.port_mut().process_id().unwrap();
    let bundle: Value =
        serde_json::from_str(include_str!("../../../tests/fixtures/market/series.json")).unwrap();
    for item in &bundle["fixtures"].as_array().unwrap()[..9] {
        let mut args = input();
        args["instrument_id"] = item["history"]["dataset"]["instrument_id"].clone();
        args["timeframe"] = item["history"]["dataset"]["timeframe"].clone();
        assert_eq!(
            core.execute(Actor::User, "market.getBars", args)
                .await
                .unwrap(),
            item["history"]
        );
        assert_eq!(core.port_mut().process_id(), Some(pid));
    }
    core.port_mut().shutdown().await;
    assert_eq!(core.port_mut().process_id(), None);
    #[cfg(target_os = "linux")]
    assert!(!PathBuf::from(format!("/proc/{pid}")).exists());
    let mut core = MarketRegistry::new(MarketWorker::new(None));
    assert_eq!(
        core.execute(Actor::User, "market.getBars", input())
            .await
            .unwrap_err()
            .code,
        ErrorCode::PROVIDERNOTCONFIGURED
    );
}

struct FakeWorker(PathBuf);
impl FakeWorker {
    fn new(source: &str) -> Self {
        let root = std::env::temp_dir().join(format!("scopehub-worker-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("worker_runtime.py"), source).unwrap();
        Self(root)
    }
    fn worker(&self, deadline: Duration) -> MarketWorker {
        let python = WorkerConfig::development(&root()).python;
        MarketWorker::new(Some(WorkerConfig {
            python,
            root: self.0.clone(),
            deadline,
        }))
    }
}
impl Drop for FakeWorker {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[tokio::test]
async fn malformed_correlation_direction_frames_capabilities_and_deadlines_close_worker() {
    let hello = json!({"protocol_version":"1.0", "message_id":"00000000-0000-4000-8000-000000000001", "timestamp_ms":0,
        "type":"hello", "role":"market", "worker_version":"0.1.0", "supported_protocols":["1.0"], "capabilities":["instrument.search","market.getBars"]});
    let prefix = format!(
        "import json, sys, time, os\nprint({}, flush=True)\n",
        serde_json::to_string(&hello.to_string()).unwrap()
    );
    let bad_response = json!({"protocol_version":"1.0", "message_id":"00000000-0000-4000-8000-000000000001", "timestamp_ms":0,
        "type":"response", "request_id":"00000000-0000-4000-8000-000000000002", "ok":true, "result":{"instruments":[]}, "error":null});
    for (tail, expected) in [
        (
            format!(
                "sys.stdin.readline()\nprint({}, flush=True)\n",
                serde_json::to_string(&bad_response.to_string()).unwrap()
            ),
            ErrorCode::OUTPUTINVALID,
        ),
        (
            format!(
                "sys.stdin.readline()\nprint({}, flush=True)\n",
                serde_json::to_string(&hello.to_string()).unwrap()
            ),
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "sys.stdin.readline()\nprint('x' * 4194305, flush=True)\n".into(),
            ErrorCode::RESOURCELIMIT,
        ),
        (
            "sys.stdin.readline()\nprint('{}', end='', flush=True)\n".into(),
            ErrorCode::WORKEREXITED,
        ),
        (
            "sys.stdin.readline()\ntime.sleep(60)\n".into(),
            ErrorCode::TIMEOUT,
        ),
    ] {
        let fake = FakeWorker::new(&(prefix.clone() + &tail));
        let mut worker = fake.worker(Duration::from_secs(3));
        assert_eq!(
            worker
                .request("instrument.search", &json!({"query":"", "limit":10}))
                .await
                .unwrap_err()
                .code,
            expected
        );
        assert_eq!(worker.process_id(), None);
    }
    for (key, value) in [
        ("role", json!("ai")),
        (
            "capabilities",
            json!(["instrument.search", "market.getBars", "workspace.undo"]),
        ),
    ] {
        let mut wrong = hello.clone();
        wrong[key] = value;
        let fake = FakeWorker::new(&format!(
            "import time\nprint({}, flush=True)\ntime.sleep(60)\n",
            serde_json::to_string(&wrong.to_string()).unwrap()
        ));
        let mut worker = fake.worker(Duration::from_secs(3));
        assert_eq!(
            worker
                .request("instrument.search", &json!({"query":"", "limit":10}))
                .await
                .unwrap_err()
                .code,
            ErrorCode::PROTOCOLMISMATCH
        );
        assert_eq!(worker.process_id(), None);
    }
}
