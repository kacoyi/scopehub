//! Project scheduled inputs from the trusted, immutable offline market adapter.

use scopehub_domain::{
    indicator::{BucketCalendar, TradingSession},
    protocol::{Error, ErrorCode, FrozenSnapshot, MarketHistoryPageInput, MarketHistoryPageOutput},
    wire::normalize_integers,
};
use serde_json::{Value, json};

use super::{Input, evaluator_error, failure, hash, operation};
use crate::{Actor, DEMO_PROVIDER, MarketPort, MarketRegistry, RUN_INDICATOR_PREVIEW, history};

pub const READ_INDICATOR_SAMPLE_HISTORY: &str = "indicator.loadSampleHistory";
const MAX_SAMPLE_HISTORY_BARS: u64 = 200;

pub fn is_sample_indicator_preview(command: &Value) -> bool {
    command["intent"]["operation"] == RUN_INDICATOR_PREVIEW
        && command["intent"]["arguments"]["snapshot"]["series"]["provider_id"] == DEMO_PROVIDER
}

impl<P: MarketPort> MarketRegistry<P> {
    /// The composition root calls this only for a validated local preview intent.
    /// This one fixed adapter reads reproducible demo data, including earlier warm-up.
    pub async fn read_sample_indicator_history(&mut self, command: Value) -> Result<Value, Error> {
        if !is_sample_indicator_preview(&command)
            || command["intent"]["arguments"]
                .get("python_params")
                .is_some()
        {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let op = operation(RUN_INDICATOR_PREVIEW)?;
        if !op.wire_validators().0.is_valid(&command) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let mut source = command["intent"]["arguments"]["snapshot"].clone();
        normalize_integers(&mut source);
        let source: FrozenSnapshot =
            serde_json::from_value(source).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        self.execute(
            Actor::User,
            "market.readHistoryPage",
            json!(query(&source)?),
        )
        .await
    }
}

fn query(source: &FrozenSnapshot) -> Result<MarketHistoryPageInput, Error> {
    if source.series.provider_id != DEMO_PROVIDER
        || source.warmup_bars != 0
        || source.from_ms < 0
        || source.to_ms <= source.from_ms
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(MarketHistoryPageInput {
        series: source.series.clone(),
        from_ms: 0,
        to_ms: source.to_ms,
        warmup_bars: 0,
        page_size: Some(MAX_SAMPLE_HISTORY_BARS),
        as_of_ms: Some(source.as_of_ms),
        expected_revision: Some(source.dataset_revision.clone()),
        snapshot: None,
        cursor: None,
        include_stream_position: None,
    })
}

pub(super) fn freeze_sample(
    source: &FrozenSnapshot,
    range: &scopehub_domain::protocol::VisibleRange,
    page: Value,
    warmup: usize,
) -> Result<Input, Error> {
    let page: MarketHistoryPageOutput =
        serde_json::from_value(page).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    history::validate_page(&query(source)?, &page)?;
    if page.next_cursor.is_some() || page.total_bars > MAX_SAMPLE_HISTORY_BARS {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if page.snapshot.dataset_id != source.dataset_id
        || page.dataset.provider_id != DEMO_PROVIDER
        || page.dataset.mode.as_str() != "simulated"
        || page.dataset.instrument_id != source.series.instrument_id
        || page.dataset.calendar_id != source.series.calendar_id
    {
        return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
    }

    let chart_start = page
        .bars
        .partition_point(|bar| bar.time_ms < source.from_ms);
    let chart_hash =
        history::snapshot_hash(source, &page.dataset.timezone, &page.bars[chart_start..]);
    if chart_hash != source.content_hash {
        return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
    }

    let first_visible = page.bars.partition_point(|bar| bar.time_ms < range.from_ms);
    let end = page.bars.partition_point(|bar| bar.time_ms < range.to_ms);
    if first_visible == end {
        return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
    }
    let first = first_visible.saturating_sub(warmup);
    let bars = page.bars[first..end].to_vec();
    let snapshot = FrozenSnapshot {
        from_ms: range.from_ms,
        to_ms: range.to_ms,
        warmup_bars: (first_visible - first) as u64,
        content_hash: String::new(),
        ..source.clone()
    };

    let sessions = page
        .dataset
        .provenance
        .get("sessions")
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
    let schedule: Vec<TradingSession> =
        serde_json::from_value(sessions.clone()).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    let calendar = BucketCalendar::from_sessions(&source.series.timeframe, &schedule)
        .map_err(evaluator_error)?;
    let calendar_hash = hash(sessions)?;
    let history_hash = history::snapshot_hash(&snapshot, &page.dataset.timezone, &bars);
    let content_hash = hash(&json!([
        "scopehub/indicator-scheduled-input-v1",
        history_hash,
        calendar_hash
    ]))?;
    Ok(Input {
        snapshot: FrozenSnapshot {
            content_hash,
            ..snapshot
        },
        bars,
        first_visible: first_visible - first,
        calendar,
        calendar_hash: Some(calendar_hash),
    })
}
