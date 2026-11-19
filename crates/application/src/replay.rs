//! Frozen snapshot handoff and bounded event validation for the synthetic replay.
use scopehub_domain::{
    protocol::{
        Bar, Error, ErrorCode, MarketHistoryPageOutput, MarketStreamBarUpdate,
        MarketSubscriptionInput, MarketSubscriptionOutput, Message,
    },
    wire::{POLICY, normalize_integers},
};
use serde_json::{Value, json};

use crate::{MarketPort, MarketRegistry, PortFuture, authorized_operation, failure, history};

pub const SUBSCRIBE: &str = "market.subscribe";
pub type SubscriptionFuture<'a, T> = PortFuture<'a, T>;

pub trait SubscriptionPort: MarketPort {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput>;
    fn next_event(&mut self) -> SubscriptionFuture<'_, Message>;
    fn complete_subscription(&mut self);
}

fn invalid() -> Error {
    failure(ErrorCode::OUTPUTINVALID)
}

fn gapped(reason: &str) -> Value {
    json!({"status": "gapped", "reason": reason})
}

impl<P: SubscriptionPort> MarketRegistry<P> {
    /// Forward only progress associated with this authorized frozen observation.
    /// The transport owns cancellation and shuts down the dedicated replay port.
    pub async fn subscribe(
        &mut self,
        actor: crate::Actor,
        mut payload: Value,
        mut publish: impl FnMut(Value) -> Result<(), Error> + Send,
    ) -> Result<Value, Error> {
        let operation = authorized_operation(actor, SUBSCRIBE)?;
        if !operation.input.is_valid(&payload) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut payload);
        let input: MarketSubscriptionInput =
            serde_json::from_value(payload).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let snapshot = &input.snapshot;
        let mut frozen = self.execute(actor, "market.readHistoryPage", json!({
            "series": input.series, "from_ms": snapshot.from_ms, "to_ms": snapshot.to_ms,
            "warmup_bars": snapshot.warmup_bars, "snapshot": snapshot, "include_stream_position": true,
        })).await?;
        normalize_integers(&mut frozen);
        let page: MarketHistoryPageOutput =
            serde_json::from_value(frozen).map_err(|_| invalid())?;
        // All delivered fixtures fit one verified page. A larger input cannot
        // establish an accepted replay boundary through a partial verification.
        if page.next_cursor.is_some() {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let reason = match &page.stream_position {
            Some(position) if position == &input.after => None,
            Some(position) if position.stream_id == input.after.stream_id => {
                Some(if position.epoch == input.after.epoch {
                    "sequence_gap"
                } else {
                    "epoch_changed"
                })
            }
            _ => Some("coverage_unknown"),
        };
        if let Some(reason) = reason {
            let recover = page
                .bars
                .last()
                .map_or(snapshot.from_ms, |bar| bar.time_ms.max(snapshot.from_ms));
            publish(json!({"subscription": {
                "status": "backfill_required", "reason": reason, "recover_from_ms": recover,
            }}))?;
            return Ok(gapped(reason));
        }
        let bar = page.bars.into_iter().last().ok_or_else(invalid)?;
        let closes_at = close_time(&bar, &input.series.timeframe, &page.dataset.provenance)
            .ok_or_else(invalid)?;
        if bar.time_ms < snapshot.from_ms || bar.is_closed != (snapshot.as_of_ms >= closes_at) {
            return Err(invalid());
        }
        let reply = self.port.open_subscription(&input).await?;
        if !operation.output.is_valid(&json!(reply)) {
            return Err(invalid());
        }
        match &reply {
            MarketSubscriptionOutput::StreamResumeAccepted(accepted)
                if accepted.from_position == input.after =>
            {
                publish(json!({"subscription": reply}))?;
            }
            MarketSubscriptionOutput::StreamBackfillRequired(backfill)
                if (snapshot.from_ms..snapshot.to_ms).contains(&backfill.recover_from_ms) =>
            {
                publish(json!({"subscription": reply}))?;
                return Ok(gapped(backfill.reason.as_str()));
            }
            _ => return Err(invalid()),
        }
        if bar.is_closed {
            self.port.complete_subscription();
            return Ok(json!({"status": "complete"}));
        }
        let mut state = Replay {
            input,
            boundary_bar: bar.clone(),
            bar,
            closes_at,
            updates: Vec::new(),
        };
        let budget = POLICY["max_pending_requests"]
            .as_u64()
            .expect("frame budget");
        for _ in 0..budget {
            if let Some(result) = state.event(self.port.next_event().await?, &mut publish)? {
                if result["status"] == "complete" {
                    self.port.complete_subscription();
                }
                return Ok(result);
            }
        }
        Ok(gapped("overflow"))
    }
}

fn close_time(
    bar: &Bar,
    timeframe: &str,
    provenance: &serde_json::Map<String, Value>,
) -> Option<i64> {
    let sessions = provenance
        .get("sessions")?
        .as_array()?
        .iter()
        .filter_map(|session| {
            (session["trading_day"] == bar.trading_day)
                .then_some((session["start_ms"].as_i64()?, session["end_ms"].as_i64()?))
        });
    match timeframe {
        "1d" => sessions.map(|(_, end)| end).max(),
        "1m" | "5m" => {
            let width_ms = if timeframe == "1m" { 60_000 } else { 300_000 };
            sessions
                .filter(|(start, end)| (*start..*end).contains(&bar.time_ms))
                .map(|(_, end)| end.min(bar.time_ms + width_ms))
                .next()
        }
        _ => None,
    }
}

struct Replay {
    input: MarketSubscriptionInput,
    boundary_bar: Bar,
    bar: Bar,
    closes_at: i64,
    updates: Vec<MarketStreamBarUpdate>,
}

impl Replay {
    fn event(
        &mut self,
        event: Message,
        publish: &mut impl FnMut(Value) -> Result<(), Error>,
    ) -> Result<Option<Value>, Error> {
        let (series, stream, epoch) = match &event {
            Message::MarketBarUpdateEvent(event) => {
                (&event.payload.series, &event.stream_id, &event.epoch)
            }
            Message::MarketBarCorrectionEvent(event) => {
                (&event.payload.series, &event.stream_id, &event.epoch)
            }
            Message::MarketStreamGapEvent(event) => {
                (&event.payload.series, &event.stream_id, &event.epoch)
            }
            _ => return Err(invalid()),
        };
        let after = &self.input.after;
        if series != &self.input.series || stream != &after.stream_id {
            return Ok(None); // A delayed event from a previous selection.
        }
        if epoch != &after.epoch {
            return Ok(Some(gapped("epoch_reset")));
        }
        let expected = after.sequence + self.updates.len() as u64 + 1;
        let update = match event {
            Message::MarketBarUpdateEvent(event) => event,
            Message::MarketStreamGapEvent(event) => {
                if event.sequence < after.sequence {
                    return Ok(None);
                }
                if event.sequence < expected {
                    return Ok(Some(gapped("divergent_duplicate")));
                }
                if event.payload.expected_sequence != expected
                    || event.payload.affected.from_ms >= event.payload.affected.to_ms
                {
                    return Err(invalid());
                }
                let result = gapped(event.payload.reason.as_str());
                publish(json!({"event": event}))?;
                return Ok(Some(result));
            }
            Message::MarketBarCorrectionEvent(event) => {
                return Ok((event.sequence >= after.sequence).then(|| {
                    gapped(if event.sequence < expected {
                        "divergent_duplicate"
                    } else {
                        "correction_requires_backfill"
                    })
                }));
            }
            _ => unreachable!("market event checked above"),
        };
        if update.sequence < after.sequence {
            return Ok(None);
        }
        if update.sequence == after.sequence {
            let boundary = &self.input.snapshot;
            // The bar retained here may already carry a subsequent observation.
            // Compare against the frozen input, including its cutoff.
            let exact = update.payload.bar == self.boundary_bar
                && update.payload.observed_at_ms == boundary.as_of_ms
                && update.payload.dataset_revision == boundary.dataset_revision;
            return Ok((!exact).then(|| gapped("divergent_duplicate")));
        }
        if update.sequence < expected {
            let index = (update.sequence - after.sequence - 1) as usize;
            return Ok(
                (self.updates[index] != update.payload).then(|| gapped("divergent_duplicate"))
            );
        }
        if update.sequence != expected {
            return Ok(Some(gapped("sequence_gap")));
        }
        let payload = &update.payload;
        let observed = self
            .updates
            .last()
            .map_or(self.input.snapshot.as_of_ms, |last| last.observed_at_ms);
        if payload.observed_at_ms <= observed {
            return Ok(Some(gapped("stale_observation")));
        }
        let bar = &payload.bar;
        if payload.dataset_revision != self.input.snapshot.dataset_revision
            || bar.is_closed != (payload.observed_at_ms >= self.closes_at)
            || bar.time_ms != self.bar.time_ms
            || bar.trading_day != self.bar.trading_day
            || bar.open != self.bar.open
            || bar.high < self.bar.high
            || bar.low > self.bar.low
            || matches!((self.bar.volume, bar.volume), (Some(previous), Some(next)) if next < previous)
            || self.bar.volume.is_some() != bar.volume.is_some()
        {
            return Err(invalid());
        }
        history::validate_bars(
            std::slice::from_ref(bar),
            self.input.snapshot.from_ms,
            self.input.snapshot.to_ms,
            0,
            0,
            1,
        )?;
        self.bar = bar.clone();
        self.updates.push(payload.clone());
        publish(json!({"event": update}))?;
        Ok(bar.is_closed.then(|| json!({"status": "complete"})))
    }
}
