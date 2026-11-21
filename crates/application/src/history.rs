//! Frozen history validation at the trusted registry boundary.
use scopehub_domain::protocol::{
    Bar, Error, ErrorCode, Instrument, MarketHistoryPageInput, MarketHistoryPageOutput, SeriesKey,
};
use sha2::{Digest, Sha256};

use crate::failure;

fn invalid() -> Error {
    failure(ErrorCode::OUTPUTINVALID)
}

pub(super) fn validate_bars(
    bars: &[Bar],
    from: i64,
    to: i64,
    warmup: u64,
    offset: u64,
    total: u64,
) -> Result<(), Error> {
    let mut previous = None;
    for (index, bar) in bars.iter().enumerate() {
        let absolute = offset + index as u64;
        if [bar.open, bar.high, bar.low, bar.close]
            .iter()
            .any(|value| !value.is_finite())
            || bar.volume.is_some_and(|v| !v.is_finite() || v < 0.0)
            || bar.low > bar.open.min(bar.close)
            || bar.high < bar.open.max(bar.close)
            || previous.is_some_and(|time| time >= bar.time_ms)
            || (bar.time_ms < from && absolute >= warmup)
            || bar.time_ms >= to
            || (!bar.is_closed && absolute + 1 != total)
        {
            return Err(invalid());
        }
        previous = Some(bar.time_ms);
    }
    Ok(())
}

fn put_text(hash: &mut Sha256, text: &str) {
    hash.update((text.len() as u32).to_be_bytes());
    hash.update(text.as_bytes());
}

fn initial_hash(result: &MarketHistoryPageOutput) -> [u8; 32] {
    snapshot_seed(&result.snapshot, &result.dataset.timezone)
}

pub(crate) fn snapshot_seed(
    snapshot: &scopehub_domain::protocol::FrozenSnapshot,
    timezone: &str,
) -> [u8; 32] {
    let key = &snapshot.series;
    let mut hash = Sha256::new();
    hash.update(b"ScopeHub/history-chain-v1\0");
    for text in [
        &key.instrument_id,
        &key.provider_id,
        &key.timeframe,
        &key.price_adjustment,
        &key.session_scope,
        &key.calendar_id,
        &key.quantity_unit,
        &snapshot.dataset_id,
        &snapshot.dataset_revision,
        timezone,
    ] {
        put_text(&mut hash, text);
    }
    for number in [
        snapshot.from_ms,
        snapshot.to_ms,
        snapshot.warmup_bars as i64,
        snapshot.as_of_ms,
    ] {
        hash.update(number.to_be_bytes());
    }
    hash.finalize().into()
}

pub(crate) fn snapshot_hash(
    snapshot: &scopehub_domain::protocol::FrozenSnapshot,
    timezone: &str,
    bars: &[Bar],
) -> String {
    hex::encode(bars.iter().fold(snapshot_seed(snapshot, timezone), add_bar))
}

fn put_number(hash: &mut Sha256, value: f64) {
    hash.update((if value == 0.0 { 0.0 } else { value }).to_be_bytes());
}

pub(crate) fn add_bar(prefix: [u8; 32], bar: &Bar) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(prefix);
    hash.update(bar.time_ms.to_be_bytes());
    put_text(&mut hash, &bar.trading_day);
    for price in [bar.open, bar.high, bar.low, bar.close] {
        put_number(&mut hash, price);
    }
    hash.update([u8::from(bar.volume.is_some())]);
    if let Some(volume) = bar.volume {
        put_number(&mut hash, volume);
    }
    hash.update([u8::from(bar.is_closed)]);
    hash.finalize().into()
}

pub(super) fn validate_page_input(input: &MarketHistoryPageInput) -> Result<(), Error> {
    if input.from_ms >= input.to_ms || input.cursor.is_some() && input.snapshot.is_none() {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    if let Some(snapshot) = &input.snapshot
        && (snapshot.series != input.series
            || snapshot.from_ms != input.from_ms
            || snapshot.to_ms != input.to_ms
            || snapshot.warmup_bars != input.warmup_bars
            || input.as_of_ms.is_some_and(|time| time != snapshot.as_of_ms)
            || input
                .expected_revision
                .as_ref()
                .is_some_and(|r| r != &snapshot.dataset_revision)
            || input
                .cursor
                .as_ref()
                .is_some_and(|c| c.snapshot_hash != snapshot.content_hash))
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(())
}

pub(super) fn validate_series(key: &SeriesKey, instrument: &Instrument) -> Result<(), Error> {
    if key.price_adjustment != "raw"
        || key.session_scope != "sample-sessions"
        || key.quantity_unit != instrument.quantity_unit
        || key.calendar_id != instrument.calendar_id
    {
        return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
    }
    Ok(())
}

pub(super) fn validate_page(
    input: &MarketHistoryPageInput,
    result: &MarketHistoryPageOutput,
) -> Result<(), Error> {
    let snapshot = &result.snapshot;
    let quality = &result.quality_state;
    let count = result.bars.len() as u64;
    let end = result.offset + count;
    if snapshot.series != input.series
        || snapshot.dataset_id != result.dataset.id
        || snapshot.dataset_revision != result.dataset.revision
        || snapshot.from_ms != input.from_ms
        || snapshot.to_ms != input.to_ms
        || snapshot.warmup_bars != input.warmup_bars
        || input.as_of_ms.is_some_and(|time| time != snapshot.as_of_ms)
        || input
            .expected_revision
            .as_ref()
            .is_some_and(|rev| rev != &snapshot.dataset_revision)
        || input
            .snapshot
            .as_ref()
            .is_some_and(|frozen| frozen != snapshot)
        || result.offset != input.cursor.as_ref().map_or(0, |cursor| cursor.next_index)
        || count > input.page_size.unwrap_or(100000)
        || end > result.total_bars
        || result.next_cursor.is_none() != (end == result.total_bars)
        || count == 0 && result.total_bars != 0
        || quality.as_of_ms != snapshot.as_of_ms
        || quality
            .last_observed_ms
            .is_some_and(|last| last > quality.as_of_ms)
        || result
            .bars
            .last()
            .is_some_and(|bar| bar.time_ms > snapshot.as_of_ms)
        || quality.source_kind.as_str() != "synthetic"
        || quality.connection.as_str() != "offline"
        || result.stream_position.as_ref().is_some_and(|position| {
            !input.include_stream_position.unwrap_or(false)
                || position.stream_id != snapshot.dataset_id
                || quality.last_observed_ms != Some(snapshot.as_of_ms)
        })
    {
        return Err(invalid());
    }
    validate_bars(
        &result.bars,
        input.from_ms,
        input.to_ms,
        input.warmup_bars,
        result.offset,
        result.total_bars,
    )?;
    let mut prefix = if let Some(cursor) = &input.cursor {
        if result
            .bars
            .first()
            .is_some_and(|bar| bar.time_ms <= cursor.previous_time_ms)
        {
            return Err(invalid());
        }
        let mut bytes = [0; 32];
        hex::decode_to_slice(&cursor.prefix_hash, &mut bytes).map_err(|_| invalid())?;
        bytes
    } else {
        initial_hash(result)
    };
    for bar in &result.bars {
        prefix = add_bar(prefix, bar);
    }
    let expected = match &result.next_cursor {
        Some(cursor) => {
            if cursor.snapshot_hash != snapshot.content_hash
                || cursor.next_index != end
                || result
                    .bars
                    .last()
                    .is_none_or(|bar| bar.time_ms != cursor.previous_time_ms)
            {
                return Err(invalid());
            }
            &cursor.prefix_hash
        }
        None => &snapshot.content_hash,
    };
    if &hex::encode(prefix) != expected {
        return Err(invalid());
    }
    Ok(())
}
