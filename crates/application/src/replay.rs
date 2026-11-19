//! Frozen snapshot handoff and bounded event validation for the synthetic replay.
use scopehub_domain::{
    protocol::{
        Bar, Error, ErrorCode, FrozenSnapshot, MarketHistoryPageOutput, MarketQualityState,
        MarketQualityStateCoverage, MarketStreamBarUpdate, MarketStreamGapEvent,
        MarketStreamGapReason, MarketSubscriptionInput, MarketSubscriptionOutput, Message,
        StreamPosition, VisibleRange,
    },
    wire::{POLICY, normalize_integers},
};
use serde_json::{Value, json};

use crate::{
    MarketPort, MarketRegistry, PortFuture, ReadFailure, authorized_operation, failure, history,
};

pub const SUBSCRIBE: &str = "market.subscribe";
const MINUTE_MS: i64 = 60_000;
pub type SubscriptionFuture<'a, T> = PortFuture<'a, T>;

pub trait SubscriptionPort: MarketPort {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput>;
    fn next_event(&mut self) -> SubscriptionFuture<'_, Message>;
    /// Retire the failed session before backing off. The next read negotiates a new worker.
    fn restart_subscription(&mut self, attempt: u32) -> SubscriptionFuture<'_, ()>;
    fn complete_subscription(&mut self);
}

fn invalid() -> Error {
    failure(ErrorCode::OUTPUTINVALID)
}

fn gapped(reason: &str) -> Value {
    json!({"status": "gapped", "reason": reason})
}

fn position_gap(
    expected: &StreamPosition,
    actual: Option<&StreamPosition>,
) -> Option<&'static str> {
    match actual {
        Some(position) if position == expected => None,
        Some(position) if position.stream_id == expected.stream_id => {
            Some(if position.epoch == expected.epoch {
                "sequence_gap"
            } else {
                "epoch_changed"
            })
        }
        _ => Some("coverage_unknown"),
    }
}

impl<P: SubscriptionPort> MarketRegistry<P> {
    /// Forward only progress associated with this authorized frozen observation.
    /// Each replay has one initial acceptance. Recovery progress carries its
    /// `status` and the last confirmed `position` under the `recovery` key.
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
        if input.series != snapshot.series {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let page = self
            .read_replay_page(actor, snapshot, snapshot.as_of_ms)
            .await?;
        if let Some(reason) = position_gap(&input.after, page.stream_position.as_ref()) {
            let recover = page
                .bars
                .last()
                .map_or(snapshot.from_ms, |bar| bar.time_ms.max(snapshot.from_ms));
            publish(json!({"subscription": {
                "status": "backfill_required", "reason": reason, "recover_from_ms": recover,
            }}))?;
            return Ok(gapped(reason));
        }
        let mut bars = page.bars;
        let bar = bars.pop().ok_or_else(invalid)?;
        let closes_at = close_time(&bar, &input.series.timeframe, &page.dataset.provenance)
            .ok_or_else(invalid)?;
        if bar.time_ms < snapshot.from_ms || bar.is_closed != (snapshot.as_of_ms >= closes_at) {
            return Err(invalid());
        }
        let coverage = SelectedCoverage::at(snapshot, &page.quality_state);
        let repair = match page.quality_state.gaps.as_slice() {
            [missing]
                if snapshot.series.timeframe == "1m"
                    && page.quality_state.coverage.as_str() == "gapped"
                    && missing.from_ms == bar.time_ms - MINUTE_MS
                    && missing.to_ms == bar.time_ms
                    && closes_at == bar.time_ms + MINUTE_MS
                    && missing.from_ms >= snapshot.from_ms
                    && closes_at <= snapshot.to_ms
                    && bars.last().is_none_or(|bar| bar.time_ms < missing.from_ms) =>
            {
                Some(missing.clone())
            }
            _ => None,
        };
        let reply = self.open_checked_subscription(operation, &input).await?;
        publish(json!({"subscription": reply}))?;
        if let MarketSubscriptionOutput::StreamBackfillRequired(backfill) = reply {
            return Ok(gapped(backfill.reason.as_str()));
        }
        if bar.is_closed {
            return Ok(self.finish_closed_replay(coverage.gap_reason()));
        }
        let mut state = Replay {
            input,
            closed_bars: bars,
            boundary_bar: bar,
            closes_at,
            updates: Vec::new(),
            timezone: page.dataset.timezone,
            repairable_gap: repair,
            coverage,
        };
        let mut frames_left = POLICY["max_pending_requests"]
            .as_u64()
            .expect("frame budget");
        let mut restarts = 0;
        while frames_left > 0 {
            let frame = match self.port.next_event().await {
                Ok(frame) => frame,
                Err(error) if error.code == ErrorCode::WORKEREXITED => {
                    if let Some(gap) = self
                        .recover(actor, &state, &mut restarts, error, &mut publish)
                        .await?
                    {
                        return Ok(gap);
                    }
                    continue;
                }
                Err(error) => return Err(error),
            };
            frames_left -= 1;
            match state.event(frame, &mut publish)? {
                None => {}
                Some(ReplayEnd::Gap(reason)) => return Ok(gapped(reason)),
                Some(ReplayEnd::Closed) => {
                    return Ok(self.finish_closed_replay(state.coverage.gap_reason()));
                }
                Some(ReplayEnd::ReportedGap(event)) => {
                    publish(json!({"event": event}))?;
                    if let Some(repair) = self.repair_closed_bar(actor, &state, &event).await? {
                        publish(json!({"repair": repair}))?;
                        return Ok(self.finish_closed_replay(None));
                    }
                    return Ok(gapped(event.payload.reason.as_str()));
                }
            }
        }
        Ok(gapped("overflow"))
    }

    fn finish_closed_replay(&mut self, gap_reason: Option<&str>) -> Value {
        self.port.complete_subscription();
        gap_reason.map_or_else(|| json!({"status": "complete"}), gapped)
    }

    async fn read_replay_page(
        &mut self,
        actor: crate::Actor,
        snapshot: &FrozenSnapshot,
        observed_at_ms: i64,
    ) -> Result<MarketHistoryPageOutput, Error> {
        let mut query = json!({
            "series": snapshot.series, "from_ms": snapshot.from_ms, "to_ms": snapshot.to_ms,
            "warmup_bars": snapshot.warmup_bars, "include_stream_position": true,
        });
        if observed_at_ms == snapshot.as_of_ms {
            query["snapshot"] = json!(snapshot);
        } else {
            query["expected_revision"] = json!(snapshot.dataset_revision);
            query["as_of_ms"] = json!(observed_at_ms);
        }
        self.read_complete_page(actor, query)
            .await
            .map_err(ReadFailure::into_error)
    }

    async fn read_complete_page(
        &mut self,
        actor: crate::Actor,
        query: Value,
    ) -> Result<MarketHistoryPageOutput, ReadFailure> {
        let mut frozen = self
            .execute_read(actor, "market.readHistoryPage", query)
            .await?;
        normalize_integers(&mut frozen);
        let page: MarketHistoryPageOutput =
            serde_json::from_value(frozen).map_err(|_| invalid())?;
        // A partial page cannot establish the final bar or verify the snapshot hash.
        if page.next_cursor.is_some() {
            return Err(failure(ErrorCode::RESOURCELIMIT).into());
        }
        Ok(page)
    }

    async fn repair_closed_bar(
        &mut self,
        actor: crate::Actor,
        state: &Replay,
        event: &MarketStreamGapEvent,
    ) -> Result<Option<Value>, Error> {
        let Some(missing_range) = &state.repairable_gap else {
            return Ok(None);
        };
        let gap = &event.payload;
        let checkpoint = state.checkpoint();
        if gap.reason != MarketStreamGapReason::SequenceGap
            || gap.lost_count != 1
            || &gap.affected != missing_range
            || checkpoint.observed_at_ms >= state.closes_at
        {
            return Ok(None);
        }
        let original = &state.input.snapshot;
        // Read once at the last bar's verified close. The range is limited to
        // the absent minute and the final observation of the retained bar.
        let result = self
            .read_complete_page(
                actor,
                json!({
                    "series": original.series, "expected_revision": original.dataset_revision,
                    "from_ms": missing_range.from_ms, "to_ms": state.closes_at,
                    "warmup_bars": 0, "as_of_ms": state.closes_at,
                    "include_stream_position": true, "page_size": 2,
                }),
            )
            .await;
        let page = match result {
            Ok(page) => page,
            Err(error) if error.error().code == ErrorCode::SNAPSHOTUNAVAILABLE => return Ok(None),
            Err(ReadFailure::Local(error))
                if matches!(
                    error.code,
                    ErrorCode::OUTPUTINVALID | ErrorCode::RESOURCELIMIT
                ) =>
            {
                return Ok(None);
            }
            Err(error) => return Err(error.into_error()),
        };
        let (Some(position), [missing, final_bar]) = (&page.stream_position, page.bars.as_slice())
        else {
            return Ok(None);
        };
        if position.stream_id != event.stream_id
            || position.epoch != event.epoch
            || position.sequence != event.sequence
            || page.dataset.timezone != state.timezone
            || page.quality_state.coverage.as_str() != "observed"
            || !page.quality_state.gaps.is_empty()
            || !missing.is_closed
            || missing.time_ms != missing_range.from_ms
            || missing.trading_day != checkpoint.bar.trading_day
            || close_time(missing, "1m", &page.dataset.provenance) != Some(missing_range.to_ms)
            || close_time(final_bar, "1m", &page.dataset.provenance) != Some(state.closes_at)
            || !final_bar.is_closed
            || !continues_bar(checkpoint.bar, final_bar)
        {
            return Ok(None);
        }
        Ok(Some(
            json!({"bars": page.bars, "snapshot": page.snapshot, "position": position, "quality_state": page.quality_state}),
        ))
    }

    async fn open_checked_subscription(
        &mut self,
        operation: &crate::Operation,
        input: &MarketSubscriptionInput,
    ) -> Result<MarketSubscriptionOutput, Error> {
        let reply = self.port.open_subscription(input).await?;
        if !operation.output.is_valid(&json!(reply)) {
            return Err(invalid());
        }
        match &reply {
            MarketSubscriptionOutput::StreamResumeAccepted(accepted)
                if accepted.from_position == input.after =>
            {
                Ok(reply)
            }
            MarketSubscriptionOutput::StreamBackfillRequired(backfill)
                if (input.snapshot.from_ms..input.snapshot.to_ms)
                    .contains(&backfill.recover_from_ms) =>
            {
                Ok(reply)
            }
            _ => Err(invalid()),
        }
    }

    async fn recover(
        &mut self,
        actor: crate::Actor,
        state: &Replay,
        restarts: &mut u32,
        mut interrupted: Error,
        publish: &mut (impl FnMut(Value) -> Result<(), Error> + Send),
    ) -> Result<Option<Value>, Error> {
        let limit = POLICY["market_restart_max_attempts"]
            .as_u64()
            .and_then(|value| u32::try_from(value).ok())
            .expect("market restart limit");
        if *restarts >= limit {
            return Err(interrupted);
        }
        let checkpoint = state.checkpoint();
        let mut position = state.input.after.clone();
        position.sequence = checkpoint.sequence;
        publish(json!({"recovery": {"status": "reconnecting", "position": position}}))?;
        while *restarts < limit {
            *restarts += 1;
            self.port.restart_subscription(*restarts).await?;
            match self.resume(actor, state, &position, &checkpoint).await {
                Ok(None) => {
                    publish(json!({"recovery": {"status": "resumed", "position": position}}))?;
                    return Ok(None);
                }
                Err(error)
                    if matches!(error.code, ErrorCode::WORKEREXITED | ErrorCode::TIMEOUT) =>
                {
                    interrupted = error;
                }
                result => return result,
            }
        }
        Err(interrupted)
    }

    async fn resume(
        &mut self,
        actor: crate::Actor,
        state: &Replay,
        position: &StreamPosition,
        checkpoint: &ReplayCheckpoint<'_>,
    ) -> Result<Option<Value>, Error> {
        let operation = authorized_operation(actor, SUBSCRIBE)?;
        let snapshot = &state.input.snapshot;
        let page = match self
            .read_replay_page(actor, snapshot, checkpoint.observed_at_ms)
            .await
        {
            Ok(page) => page,
            Err(error) if error.code == ErrorCode::SNAPSHOTUNAVAILABLE => {
                return Ok(Some(gapped("coverage_unknown")));
            }
            Err(error) => return Err(error),
        };
        if let Some(reason) = position_gap(position, page.stream_position.as_ref()) {
            return Ok(Some(gapped(reason)));
        }
        // The newer cutoff advances the retained bar only. Compare against
        // the verified original closed rows and the latest confirmed update.
        if page.bars.split_last() != Some((checkpoint.bar, state.closed_bars.as_slice()))
            || page.dataset.timezone != state.timezone
            || close_time(
                checkpoint.bar,
                &snapshot.series.timeframe,
                &page.dataset.provenance,
            ) != Some(state.closes_at)
        {
            return Ok(Some(gapped("divergent_duplicate")));
        }
        let coverage = SelectedCoverage::at(snapshot, &page.quality_state);
        if coverage != state.coverage {
            let reason = if coverage.gaps.is_empty() && state.coverage.gaps.is_empty() {
                "coverage_unknown"
            } else {
                "source_gap"
            };
            return Ok(Some(gapped(reason)));
        }
        let resumed = MarketSubscriptionInput {
            series: snapshot.series.clone(),
            snapshot: page.snapshot,
            after: position.clone(),
        };
        // The replacement handshake is internal to the same logical subscription.
        match self.open_checked_subscription(operation, &resumed).await? {
            MarketSubscriptionOutput::StreamResumeAccepted(_) => Ok(None),
            MarketSubscriptionOutput::StreamBackfillRequired(backfill) => {
                Ok(Some(gapped(backfill.reason.as_str())))
            }
        }
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
            let width_ms = if timeframe == "1m" {
                MINUTE_MS
            } else {
                5 * MINUTE_MS
            };
            sessions
                .filter(|(start, end)| (*start..*end).contains(&bar.time_ms))
                .map(|(_, end)| end.min(bar.time_ms + width_ms))
                .next()
        }
        _ => None,
    }
}

fn continues_bar(previous: &Bar, next: &Bar) -> bool {
    next.time_ms == previous.time_ms
        && next.trading_day == previous.trading_day
        && next.open == previous.open
        && next.high >= previous.high
        && next.low <= previous.low
        && next.volume.is_some() == previous.volume.is_some()
        && !matches!((previous.volume, next.volume), (Some(before), Some(after)) if after < before)
}

enum ReplayEnd {
    Closed,
    Gap(&'static str),
    ReportedGap(Box<MarketStreamGapEvent>),
}

#[derive(PartialEq, Eq)]
struct SelectedCoverage {
    gaps: Vec<(i64, i64)>,
    unknown: bool,
}

impl SelectedCoverage {
    fn at(snapshot: &FrozenSnapshot, quality: &MarketQualityState) -> Self {
        // Source quality can describe gaps across the dataset. Compare the
        // union of their half-open intersections with the selected range.
        let mut clipped = Vec::new();
        let mut unknown = quality.coverage == MarketQualityStateCoverage::Unknown
            || (quality.coverage == MarketQualityStateCoverage::Gapped && quality.gaps.is_empty());
        for gap in &quality.gaps {
            if gap.from_ms >= gap.to_ms {
                unknown = true;
                continue;
            }
            let from = gap.from_ms.max(snapshot.from_ms);
            let to = gap.to_ms.min(snapshot.to_ms);
            if from < to {
                clipped.push((from, to));
            }
        }
        clipped.sort_unstable();
        let mut gaps: Vec<(i64, i64)> = Vec::with_capacity(clipped.len());
        for (from, to) in clipped {
            if let Some((_, previous_to)) = gaps.last_mut()
                && from <= *previous_to
            {
                *previous_to = (*previous_to).max(to);
            } else {
                gaps.push((from, to));
            }
        }
        Self { gaps, unknown }
    }

    fn gap_reason(&self) -> Option<&'static str> {
        if !self.gaps.is_empty() {
            Some("source_gap")
        } else {
            self.unknown.then_some("coverage_unknown")
        }
    }
}

struct Replay {
    input: MarketSubscriptionInput,
    closed_bars: Vec<Bar>,
    boundary_bar: Bar,
    closes_at: i64,
    updates: Vec<MarketStreamBarUpdate>,
    timezone: String,
    repairable_gap: Option<VisibleRange>,
    coverage: SelectedCoverage,
}

struct ReplayCheckpoint<'a> {
    sequence: u64,
    observed_at_ms: i64,
    bar: &'a Bar,
}

impl Replay {
    fn checkpoint(&self) -> ReplayCheckpoint<'_> {
        let (observed_at_ms, bar) = self
            .updates
            .last()
            .map_or((self.input.snapshot.as_of_ms, &self.boundary_bar), |last| {
                (last.observed_at_ms, &last.bar)
            });
        ReplayCheckpoint {
            sequence: self.input.after.sequence + self.updates.len() as u64,
            observed_at_ms,
            bar,
        }
    }

    fn event(
        &mut self,
        event: Message,
        publish: &mut impl FnMut(Value) -> Result<(), Error>,
    ) -> Result<Option<ReplayEnd>, Error> {
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
            return Ok(Some(ReplayEnd::Gap("epoch_reset")));
        }
        let checkpoint = self.checkpoint();
        let expected = checkpoint.sequence + 1;
        let update = match event {
            Message::MarketBarUpdateEvent(event) => event,
            Message::MarketStreamGapEvent(event) => {
                if event.sequence < after.sequence {
                    return Ok(None);
                }
                if event.sequence < expected {
                    return Ok(Some(ReplayEnd::Gap("divergent_duplicate")));
                }
                if event.payload.expected_sequence != expected
                    || event.payload.affected.from_ms >= event.payload.affected.to_ms
                    || (event.payload.reason == MarketStreamGapReason::SequenceGap
                        && event.sequence - expected != event.payload.lost_count)
                {
                    return Err(invalid());
                }
                return Ok(Some(ReplayEnd::ReportedGap(event)));
            }
            Message::MarketBarCorrectionEvent(event) => {
                return Ok((event.sequence >= after.sequence).then_some({
                    ReplayEnd::Gap(if event.sequence < expected {
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
            return Ok((!exact).then_some(ReplayEnd::Gap("divergent_duplicate")));
        }
        if update.sequence < expected {
            let index = (update.sequence - after.sequence - 1) as usize;
            return Ok((self.updates[index] != update.payload)
                .then_some(ReplayEnd::Gap("divergent_duplicate")));
        }
        if update.sequence != expected {
            return Ok(Some(ReplayEnd::Gap("sequence_gap")));
        }
        let payload = &update.payload;
        if payload.observed_at_ms <= checkpoint.observed_at_ms {
            return Ok(Some(ReplayEnd::Gap("stale_observation")));
        }
        let bar = &payload.bar;
        if payload.dataset_revision != self.input.snapshot.dataset_revision
            || bar.is_closed != (payload.observed_at_ms >= self.closes_at)
            || !continues_bar(checkpoint.bar, bar)
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
        self.updates.push(payload.clone());
        publish(json!({"event": update}))?;
        Ok(bar.is_closed.then_some(ReplayEnd::Closed))
    }
}
