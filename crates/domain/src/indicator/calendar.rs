//! Expected bar buckets from an adapter's explicit, ordered session evidence.

use super::{IndicatorError, MAX_BARS, Result};

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TradingSession {
    pub start_ms: i64,
    pub end_ms: i64,
    pub trading_day: String,
}

enum Evidence {
    Unverified { period_ms: i64 },
    Scheduled { starts: Vec<i64> },
}

pub struct BucketCalendar(Evidence);

impl BucketCalendar {
    /// A source calendar name or timestamp by itself cannot establish closures.
    pub fn unverified(period_ms: i64) -> Result<Self> {
        if period_ms <= 0 {
            return Err(IndicatorError::Invalid);
        }
        Ok(Self(Evidence::Unverified { period_ms }))
    }

    /// Supply only the explicit sessions from the frozen input's trusted adapter.
    pub fn from_sessions(timeframe: &str, sessions: &[TradingSession]) -> Result<Self> {
        let period_ms = match timeframe {
            "1m" => 60_000,
            "5m" => 300_000,
            "1d" => 86_400_000,
            _ => return Err(IndicatorError::Invalid),
        };
        if sessions.is_empty() {
            return Err(IndicatorError::Invalid);
        }
        let mut starts = Vec::new();
        let mut previous: Option<&TradingSession> = None;
        for session in sessions {
            if session.start_ms >= session.end_ms
                || session.trading_day.is_empty()
                || previous.is_some_and(|last| {
                    last.end_ms > session.start_ms || last.trading_day > session.trading_day
                })
            {
                return Err(IndicatorError::Invalid);
            }
            if timeframe == "1d" {
                if previous.is_none_or(|last| last.trading_day != session.trading_day) {
                    starts.push(session.start_ms);
                }
            } else {
                for start in (session.start_ms..session.end_ms).step_by(period_ms as usize) {
                    if starts.len() == MAX_BARS {
                        return Err(IndicatorError::Limit);
                    }
                    starts.push(start);
                }
            }
            if starts.len() > MAX_BARS {
                return Err(IndicatorError::Limit);
            }
            previous = Some(session);
        }
        Ok(Self(Evidence::Scheduled { starts }))
    }

    pub(super) fn verified(&self) -> bool {
        matches!(self.0, Evidence::Scheduled { .. })
    }

    pub(super) fn contains(&self, time_ms: i64) -> bool {
        match &self.0 {
            Evidence::Unverified { .. } => true,
            Evidence::Scheduled { starts } => starts.binary_search(&time_ms).is_ok(),
        }
    }

    pub(super) fn actual_successor(&self, previous: i64, next: i64) -> bool {
        match &self.0 {
            Evidence::Unverified { period_ms } => previous.checked_add(*period_ms) == Some(next),
            Evidence::Scheduled { starts } => {
                starts
                    .binary_search(&previous)
                    .ok()
                    .and_then(|index| starts.get(index + 1))
                    .copied()
                    == Some(next)
            }
        }
    }
}
