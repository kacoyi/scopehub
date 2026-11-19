use super::*;

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

#[derive(Default)]
struct Trace {
    processes: Vec<u32>,
    opened: Vec<(u32, MarketSubscriptionInput)>,
    exits: Vec<u32>,
    restarts: Vec<u32>,
    cleaned: bool,
}

#[derive(Clone, Copy)]
enum BoundaryFault {
    Epoch,
    Sequence,
    ConfirmedBar,
    ClosedHistory,
}

struct InterruptedWorker<'a> {
    worker: MarketWorker,
    trace: &'a mut Trace,
    exit_before_read: bool,
    exit_each_resume: bool,
    exit_first_replacement_before_verification: bool,
    exit_during_repair: bool,
    boundary_fault: Option<BoundaryFault>,
    authentic_resume_hash: Option<String>,
}

impl<'a> InterruptedWorker<'a> {
    fn new(trace: &'a mut Trace) -> Self {
        Self {
            worker: MarketWorker::new(Some(config())),
            trace,
            exit_before_read: false,
            exit_each_resume: false,
            exit_first_replacement_before_verification: false,
            exit_during_repair: false,
            boundary_fault: None,
            authentic_resume_hash: None,
        }
    }

    async fn exit(&mut self) {
        let child = &mut self.worker.session.as_mut().expect("owned worker").child;
        self.trace.exits.push(child.id().expect("owned process"));
        child.kill().await.expect("kill and reap the owned child");
    }
}

impl MarketPort for InterruptedWorker<'_> {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a> {
        Box::pin(async move {
            if self.exit_during_repair
                && operation == "market.readHistoryPage"
                && payload["page_size"] == 2
            {
                self.exit().await;
                let result = self.worker.request(operation, payload).await;
                assert!(matches!(&result, Err(error) if error.code == ErrorCode::WORKEREXITED));
                return result;
            }
            let is_resume_read =
                operation == "market.readHistoryPage" && payload.get("expected_revision").is_some();
            if is_resume_read
                && std::mem::take(&mut self.exit_first_replacement_before_verification)
            {
                self.exit().await;
                let result = self.worker.request(operation, payload).await;
                assert!(matches!(&result, Err(error) if error.code == ErrorCode::WORKEREXITED));
                return result;
            }
            let mut result = self.worker.request(operation, payload).await?;
            let pid = self.worker.process_id().expect("owned process");
            if self.trace.processes.last() != Some(&pid) {
                self.trace.processes.push(pid);
            }
            if is_resume_read {
                match self.boundary_fault {
                    Some(BoundaryFault::Epoch) => {
                        result["stream_position"]["epoch"] = json!("different-epoch");
                    }
                    Some(BoundaryFault::Sequence) => {
                        result["stream_position"]["sequence"] = json!(0);
                    }
                    Some(BoundaryFault::ClosedHistory) => {
                        assert_eq!(result["bars"][0]["volume"], 120.0);
                        self.authentic_resume_hash =
                            Some(result["snapshot"]["content_hash"].as_str().unwrap().into());
                        result["bars"][0]["volume"] = json!(121.0);
                        // history-chain-v1 for the sequence-1 page with only the first closed bar's volume changed.
                        result["snapshot"]["content_hash"] = json!(
                            "39ed9072c90dc545c479ebdd26997c5ebdd21a07d2758d2c026f32435b30547c"
                        );
                    }
                    _ => {}
                }
            }
            Ok(result)
        })
    }
}

impl SubscriptionPort for InterruptedWorker<'_> {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput> {
        let pid = self.worker.process_id().expect("owned process");
        assert!(self.worker.subscription.is_none());
        self.trace.opened.push((pid, input.clone()));
        self.exit_before_read = self.exit_each_resume && input.after.sequence == 1;
        if let Some(hash) = self.authentic_resume_hash.take() {
            // Model a worker that accepts its own rewritten snapshot while the
            // owned Python process still produces the genuine remaining events.
            let mut authentic = input.clone();
            authentic.snapshot.content_hash = hash;
            return Box::pin(async move { self.worker.open_subscription(&authentic).await });
        }
        self.worker.open_subscription(input)
    }

    fn next_event(&mut self) -> SubscriptionFuture<'_, Message> {
        Box::pin(async {
            if std::mem::take(&mut self.exit_before_read) {
                self.exit().await;
                let result = self.worker.next_event().await;
                assert!(matches!(&result, Err(error) if error.code == ErrorCode::WORKEREXITED));
                return result;
            }
            let mut event = self.worker.next_event().await?;
            if !self.exit_during_repair
                && let Message::MarketBarUpdateEvent(update) = &mut event
                && update.sequence == 1
            {
                self.exit_before_read = true;
                if matches!(self.boundary_fault, Some(BoundaryFault::ConfirmedBar)) {
                    update.payload.bar.close = 3501.1;
                }
            }
            Ok(event)
        })
    }

    fn restart_subscription(&mut self, attempt: u32) -> SubscriptionFuture<'_, ()> {
        self.trace.restarts.push(attempt);
        self.worker.restart_subscription(attempt)
    }

    fn complete_subscription(&mut self) {
        self.worker.complete_subscription();
    }
}

impl MarketLifecycle for InterruptedWorker<'_> {
    fn status(&self) -> MarketStatus {
        self.worker.status()
    }

    fn shutdown(&mut self) -> ShutdownFuture<'_> {
        Box::pin(async {
            self.worker.shutdown().await?;
            assert!(self.worker.session.is_none());
            assert!(self.worker.subscription.is_none());
            assert!(self.worker.cleanup.is_none());
            self.trace.cleaned = true;
            Ok(())
        })
    }
}

#[tokio::test]
async fn replacement_cannot_rewrite_a_confirmed_closed_bar() {
    let initial = sample_futures_page().await;
    let mut trace = Trace::default();
    let mut worker = InterruptedWorker::new(&mut trace);
    worker.boundary_fault = Some(BoundaryFault::ClosedHistory);
    let mut frames = Vec::new();
    let result = run_owned_subscription(
        worker,
        config().deadline,
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
        result,
        json!({"status":"gapped", "reason":"divergent_duplicate"})
    );
    assert_eq!(trace.opened.len(), 1);
    assert_eq!(trace.exits, trace.processes[..1]);
    assert_eq!(trace.restarts, [1]);
    assert!(trace.cleaned);
    assert_eq!(frames.len(), 3);
    assert_eq!(frames[1]["event"]["sequence"], 1);
    assert_eq!(frames[2]["recovery"]["status"], "reconnecting");
}

#[tokio::test]
async fn owned_worker_exit_during_repair_preserves_gap_and_exact_error() {
    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let mut args = query("SIM.CN.FUTURE.001", "1m", "contracts");
    args["expected_revision"] = json!("scopehub-synthetic-v1:20241013:repairable");
    let initial = reads
        .execute(Actor::User, "market.readHistoryPage", args)
        .await
        .unwrap();
    reads.shutdown().await.unwrap();
    let mut trace = Trace::default();
    let mut worker = InterruptedWorker::new(&mut trace);
    worker.exit_during_repair = true;
    let mut frames = Vec::new();
    let result = run_owned_subscription(
        worker,
        config().deadline,
        Actor::User,
        subscription(&initial),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await;
    assert_eq!(result, Err(failure(ErrorCode::WORKEREXITED)));
    assert_eq!(trace.opened.len(), 1);
    assert_eq!(trace.exits, trace.processes);
    assert!(trace.restarts.is_empty());
    assert!(trace.cleaned);
    assert_eq!(frames.len(), 3);
    assert_eq!(frames[1]["event"]["sequence"], 1);
    assert_eq!(frames[2]["event"]["event"], "market.stream_gap");
    assert_eq!(frames[2]["event"]["payload"]["lost_count"], 1);
    assert_eq!(
        frames[2]["event"]["payload"]["affected"],
        json!({"from_ms":1704330480000_i64,"to_ms":1704330540000_i64})
    );
}

async fn sample_futures_page() -> Value {
    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let page = reads
        .execute(
            Actor::User,
            "market.readHistoryPage",
            query("SIM.CN.FUTURE.001", "1m", "contracts"),
        )
        .await
        .unwrap();
    reads.shutdown().await.unwrap();
    page
}

#[tokio::test]
async fn resumes_confirmed_futures_bar_after_owned_worker_exits() {
    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let query = query("SIM.CN.FUTURE.001", "1m", "contracts");
    let initial = reads
        .execute(Actor::User, "market.readHistoryPage", query.clone())
        .await
        .unwrap();
    let read_pid = reads.status().process_id.unwrap();
    let mut trace = Trace::default();
    let mut frames = Vec::new();
    let result = run_owned_subscription(
        InterruptedWorker::new(&mut trace),
        config().deadline,
        Actor::User,
        subscription(&initial),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await;
    assert_eq!(
        result.map_err(|error| error.code),
        Ok(json!({"status": "complete"}))
    );
    let [(initial_pid, start), (resume_pid, resume)] = trace.opened.as_slice() else {
        panic!("one initial worker and one replacement subscription");
    };
    assert_ne!(initial_pid, resume_pid);
    assert_eq!(trace.processes, [*initial_pid, *resume_pid]);
    assert_eq!(trace.exits, [*initial_pid]);
    assert_eq!(trace.restarts, [1]);
    assert!(trace.cleaned);
    assert_eq!(start.after.sequence, 0);
    assert_eq!(resume.after.sequence, 1);
    assert_eq!(resume.after.epoch, start.after.epoch);
    assert_eq!(resume.after.stream_id, start.after.stream_id);
    assert_eq!(resume.series, start.series);
    assert_eq!(
        resume.snapshot.dataset_revision,
        start.snapshot.dataset_revision
    );
    assert_eq!(resume.snapshot.as_of_ms, 1704330585000);

    assert_eq!(
        frames.len(),
        5,
        "one acceptance, two recovery notices, and two updates"
    );
    assert_eq!(
        frames[0]["subscription"]["from_position"],
        initial["stream_position"]
    );
    assert_eq!(
        frames[2],
        json!({"recovery": {"status": "reconnecting", "position": resume.after}})
    );
    assert_eq!(
        frames[3],
        json!({"recovery": {"status": "resumed", "position": resume.after}})
    );
    for (frame, sequence, observed, close, volume, closed) in [
        (&frames[1], 1, 1704330585000_i64, 3501.2, 120.0, false),
        (&frames[4], 2, 1704330600000_i64, 3501.0, 130.0, true),
    ] {
        let event = &frame["event"];
        assert_eq!(event["sequence"], sequence);
        assert_eq!(event["payload"]["observed_at_ms"], observed);
        let bar = &event["payload"]["bar"];
        assert_eq!(bar["time_ms"], 1704330540000_i64);
        assert_eq!(bar["close"], close);
        assert_eq!(bar["volume"].as_f64(), Some(volume));
        assert_eq!(bar["is_closed"], closed);
    }

    let mut pinned = query;
    pinned["snapshot"] = initial["snapshot"].clone();
    let frozen = reads
        .execute(Actor::User, "market.readHistoryPage", pinned)
        .await
        .unwrap();
    assert_eq!(reads.status().process_id, Some(read_pid));
    assert_eq!(frozen, initial);
    let bar = frozen["bars"].as_array().unwrap().last().unwrap();
    assert_eq!(bar["close"], 3500.8);
    assert_eq!(bar["volume"].as_f64(), Some(110.0));
    assert_eq!(bar["is_closed"], false);
    reads.shutdown().await.unwrap();
}

#[tokio::test]
async fn replacement_requires_the_confirmed_epoch_sequence_and_bar() {
    let page = sample_futures_page().await;
    for (fault, reason) in [
        (BoundaryFault::Epoch, "epoch_changed"),
        (BoundaryFault::Sequence, "sequence_gap"),
        (BoundaryFault::ConfirmedBar, "divergent_duplicate"),
    ] {
        let mut trace = Trace::default();
        let mut worker = InterruptedWorker::new(&mut trace);
        worker.boundary_fault = Some(fault);
        let mut frames = Vec::new();
        let result = run_owned_subscription(
            worker,
            config().deadline,
            Actor::User,
            subscription(&page),
            std::future::pending(),
            |frame| {
                frames.push(frame);
                Ok(())
            },
        )
        .await
        .unwrap();
        assert_eq!(result, json!({"status": "gapped", "reason": reason}));
        assert_eq!(trace.processes.len(), 2);
        assert_eq!(trace.opened.len(), 1);
        assert!(trace.cleaned);
        assert_eq!(frames.len(), 3);
        assert_eq!(frames[1]["event"]["sequence"], 1);
        assert_eq!(frames[2]["recovery"]["status"], "reconnecting");
    }
}

#[tokio::test]
async fn retries_a_replacement_exit_during_verification_under_one_reconnect_notice() {
    let page = sample_futures_page().await;
    let mut trace = Trace::default();
    let mut worker = InterruptedWorker::new(&mut trace);
    worker.exit_first_replacement_before_verification = true;
    let mut frames = Vec::new();
    let outcome = run_owned_subscription(
        worker,
        Duration::from_secs(10),
        Actor::User,
        subscription(&page),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await
    .unwrap();
    assert_eq!(outcome, json!({"status": "complete"}));
    assert_eq!(trace.restarts, [1, 2]);
    assert_eq!(trace.processes.len(), 3);
    assert_eq!(trace.exits, trace.processes[..2]);
    assert_eq!(
        trace.opened.iter().map(|(pid, _)| *pid).collect::<Vec<_>>(),
        [trace.processes[0], trace.processes[2]]
    );
    assert!(trace.cleaned);
    assert_eq!(frames.len(), 5);
    assert_eq!(frames[2]["recovery"]["status"], "reconnecting");
    assert_eq!(frames[3]["recovery"]["status"], "resumed");
    assert_eq!(frames[4]["event"]["sequence"], 2);
}

#[tokio::test]
async fn five_restarts_total_even_when_every_replacement_accepts_before_exiting() {
    let page = sample_futures_page().await;
    let mut trace = Trace::default();
    let mut worker = InterruptedWorker::new(&mut trace);
    worker.exit_each_resume = true;
    let mut frames = Vec::new();
    let result = run_owned_subscription(
        worker,
        Duration::from_secs(15),
        Actor::User,
        subscription(&page),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await;
    assert_eq!(result.unwrap_err().code, ErrorCode::WORKEREXITED);
    assert_eq!(trace.restarts, [1, 2, 3, 4, 5]);
    assert_eq!(trace.processes.len(), 6);
    assert_eq!(trace.processes, trace.exits);
    assert!(trace.cleaned);
    let mut opened = trace.opened.iter();
    assert_eq!(opened.next().unwrap().1.after.sequence, 0);
    assert_eq!(
        opened
            .map(|(_, input)| input.after.sequence)
            .collect::<Vec<_>>(),
        [1; 5]
    );
    assert_eq!(
        frames
            .iter()
            .filter(|frame| frame.get("subscription").is_some())
            .count(),
        1
    );
    assert_eq!(
        frames
            .iter()
            .filter_map(|frame| frame["event"]["sequence"].as_u64())
            .collect::<Vec<_>>(),
        [1]
    );
    assert_eq!(frames.len(), 12);
}

#[tokio::test]
async fn cancellation_during_recovery_cleans_up_before_a_new_selection() {
    let page = sample_futures_page().await;
    let mut trace = Trace::default();
    let (cancel, cancelled) = tokio::sync::oneshot::channel();
    let mut cancel = Some(cancel);
    let mut frames = Vec::new();
    let result = run_owned_subscription(
        InterruptedWorker::new(&mut trace),
        config().deadline,
        Actor::User,
        subscription(&page),
        async { cancelled.await.unwrap() },
        |frame| {
            if frame["recovery"]["status"] == "reconnecting" {
                cancel.take().unwrap().send(()).unwrap();
            }
            frames.push(frame);
            Ok(())
        },
    )
    .await;
    assert_eq!(result.unwrap_err().code, ErrorCode::CANCELLED);
    assert_eq!(trace.restarts, [1]);
    assert_eq!(trace.processes, trace.exits);
    assert_eq!(trace.opened.len(), 1);
    assert!(trace.cleaned);
    assert_eq!(frames.len(), 3);
    assert_eq!(frames[1]["event"]["sequence"], 1);
    assert_eq!(frames[2]["recovery"]["status"], "reconnecting");

    let mut reads = MarketRegistry::new(MarketWorker::new(Some(config())));
    let stock = reads
        .execute(
            Actor::User,
            "market.readHistoryPage",
            query("SIM.CN.STOCK.001", "5m", "shares"),
        )
        .await
        .unwrap();
    reads.shutdown().await.unwrap();
    let mut frames = Vec::new();
    let outcome = run_subscription(
        Some(config()),
        Actor::User,
        subscription(&stock),
        std::future::pending(),
        |frame| {
            frames.push(frame);
            Ok(())
        },
    )
    .await
    .unwrap();
    assert_eq!(outcome, json!({"status": "complete"}));
    assert_eq!(frames.len(), 3);
    assert_eq!(
        frames[0]["subscription"]["from_position"],
        stock["stream_position"]
    );
    for (index, frame) in frames[1..].iter().enumerate() {
        let event = &frame["event"];
        assert_eq!(event["sequence"], index + 1);
        assert_eq!(event["payload"]["series"], stock["snapshot"]["series"]);
    }
    assert_eq!(frames[2]["event"]["payload"]["bar"]["close"], 12.16);
}
