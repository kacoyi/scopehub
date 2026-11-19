//! Typed, bounded evaluation over a single immutable bar series.

mod calendar;
pub use calendar::{BucketCalendar, TradingSession};

use std::collections::{BTreeMap, HashSet};

use serde_json::{Value, json};

use crate::protocol::{
    Arithmetic11Op, Bar, BooleanBinary11Op, BooleanExpression11, Comparison11Op,
    DeclarativeSource11, Diagnostic, FieldExpressionField, IndicatorOutputSpec11,
    IndicatorProvenance, IndicatorResult, IndicatorSeries, Marker, NumberExpression11,
    NumericSeries11Kind, ParameterDeclaration11, ParameterOverride11, Point, Rolling11Op,
    SeriesKind, Severity, WindowLength11,
};

pub const MAX_BARS: usize = 100_000;
const MAX_NODES: usize = 256;
const MAX_DEPTH: usize = 32;
const MAX_MARKERS: usize = 2_000;

#[derive(Debug, PartialEq, Eq)]
pub enum IndicatorError {
    Invalid,
    Limit,
    Nonfinite,
    Cancelled,
}

type Result<T> = std::result::Result<T, IndicatorError>;

#[derive(Clone, Copy)]
enum Parameter {
    Number(f64),
    Integer(i64),
    Boolean(bool),
}

type Parameters = BTreeMap<String, Parameter>;

impl Parameter {
    fn override_with(&mut self, override_value: &ParameterOverride11) -> Result<()> {
        *self = match (*self, override_value) {
            (Self::Number(_), ParameterOverride11::NumericOverride11(value)) => {
                Self::Number(finite(value.value).map_err(|_| IndicatorError::Invalid)?)
            }
            (Self::Integer(_), ParameterOverride11::NumericOverride11(value))
                if value.value.fract() == 0.0 && value.value.abs() <= 9_007_199_254_740_991.0 =>
            {
                Self::Integer(value.value as i64)
            }
            (Self::Boolean(_), ParameterOverride11::BooleanOverride11(value)) => {
                Self::Boolean(value.value)
            }
            _ => return Err(IndicatorError::Invalid),
        };
        Ok(())
    }

    fn value(self) -> Value {
        match self {
            Self::Number(value) => json!(if value == 0.0 { 0.0 } else { value }),
            Self::Integer(value) => json!(value),
            Self::Boolean(value) => json!(value),
        }
    }
}

/// The program owns the resolved bindings used both for evaluation and hashing.
pub struct Program {
    source: DeclarativeSource11,
    parameters: Parameters,
    warmup_bars: usize,
}

impl Program {
    pub fn prepare(source: DeclarativeSource11, overrides: &[ParameterOverride11]) -> Result<Self> {
        let mut parameters = BTreeMap::new();
        for declaration in &source.parameters {
            let (name, value) = match declaration {
                ParameterDeclaration11::NumberParameter11(p) => {
                    (&p.name, Parameter::Number(finite(p.default)?))
                }
                ParameterDeclaration11::IntegerParameter11(p) => {
                    (&p.name, Parameter::Integer(p.default))
                }
                ParameterDeclaration11::BooleanParameter11(p) => {
                    (&p.name, Parameter::Boolean(p.default))
                }
            };
            if parameters.insert(name.clone(), value).is_some() {
                return Err(IndicatorError::Invalid);
            }
        }
        let mut names = HashSet::new();
        for override_value in overrides {
            let name = match override_value {
                ParameterOverride11::NumericOverride11(p) => &p.name,
                ParameterOverride11::BooleanOverride11(p) => &p.name,
            };
            if !names.insert(name) {
                return Err(IndicatorError::Invalid);
            }
            parameters
                .get_mut(name)
                .ok_or(IndicatorError::Invalid)?
                .override_with(override_value)?;
        }
        let mut validation = Validation {
            parameters: &parameters,
            nodes: 0,
        };
        let mut warmup_bars = 0;
        let mut ids = HashSet::new();
        for output in &source.outputs {
            let extra = match output {
                IndicatorOutputSpec11::NumericSeries11(spec) => {
                    if !ids.insert(&spec.id) {
                        return Err(IndicatorError::Invalid);
                    }
                    validation.number(&spec.value, 1)?
                }
                IndicatorOutputSpec11::ConditionalMarker11(spec) => {
                    validation.boolean(&spec.when, 1)?
                }
            };
            warmup_bars = warmup_bars.max(extra);
        }
        if warmup_bars >= MAX_BARS {
            return Err(IndicatorError::Limit);
        }
        Ok(Self {
            source,
            parameters,
            warmup_bars,
        })
    }

    pub fn warmup_bars(&self) -> usize {
        self.warmup_bars
    }

    pub fn resolved_parameters(&self) -> Value {
        Value::Object(
            self.parameters
                .iter()
                .map(|(name, value)| (name.clone(), value.value()))
                .collect(),
        )
    }

    pub fn evaluate_with_calendar(
        &self,
        bars: &[Bar],
        visible_start: usize,
        calendar: &BucketCalendar,
        cancelled: impl Fn() -> bool,
        provenance: IndicatorProvenance,
    ) -> Result<IndicatorResult> {
        if bars.len() > MAX_BARS {
            return Err(IndicatorError::Limit);
        }
        if bars.iter().any(|bar| !calendar.contains(bar.time_ms)) {
            return Err(IndicatorError::Invalid);
        }
        let follows = |a: &Bar, b: &Bar| calendar.actual_successor(a.time_ms, b.time_ms);
        let mut result = self.evaluate(bars, visible_start, follows, cancelled, provenance)?;
        if self.warmup_bars > 0 {
            if calendar.verified() {
                result.diagnostics.push(Diagnostic {
                    code: "session_schedule_applied".into(),
                    message: "The source's scheduled closures add no indicator buckets.".into(),
                    severity: Severity::Info,
                });
                if bars.windows(2).any(|pair| !follows(&pair[0], &pair[1])) {
                    result.diagnostics.push(Diagnostic {
                        code: "missing_expected_buckets".into(),
                        message: "One or more expected market buckets are missing. Affected windows are unknown.".into(),
                        severity: Severity::Warning,
                    });
                }
            } else {
                result.diagnostics.push(Diagnostic {
                    code: "calendar_unverified".into(),
                    message: "This source has no verified session schedule. Indicator windows restart after timestamp gaps.".into(),
                    severity: Severity::Info,
                });
            }
        }
        Ok(result)
    }

    fn evaluate(
        &self,
        bars: &[Bar],
        visible_start: usize,
        is_successor: impl Fn(&Bar, &Bar) -> bool,
        cancelled: impl Fn() -> bool,
        provenance: IndicatorProvenance,
    ) -> Result<IndicatorResult> {
        if bars.len() > MAX_BARS || visible_start >= bars.len() {
            return Err(IndicatorError::Limit);
        }
        let mut run = 0;
        let mut continuity = Vec::with_capacity(bars.len());
        for (index, bar) in bars.iter().enumerate() {
            run = if index > 0 && is_successor(&bars[index - 1], bar) {
                run + 1
            } else {
                1
            };
            continuity.push(run);
        }
        let mut context = Evaluation {
            parameters: &self.parameters,
            bars,
            continuity,
            cancelled: &cancelled,
            divide_by_zero: false,
        };
        let mut result = IndicatorResult {
            series: vec![],
            markers: vec![],
            regions: vec![],
            diagnostics: vec![],
            provenance,
        };
        for output in &self.source.outputs {
            match output {
                IndicatorOutputSpec11::NumericSeries11(spec) => {
                    let values = context.number(&spec.value)?;
                    let points = bars[visible_start..]
                        .iter()
                        .zip(&values[visible_start..])
                        .map(|(bar, value)| Point {
                            time_ms: bar.time_ms,
                            value: *value,
                        })
                        .collect();
                    result.series.push(IndicatorSeries {
                        id: spec.id.clone(),
                        pane: spec.pane.clone(),
                        style: spec.style.clone(),
                        r#type: match spec.kind {
                            NumericSeries11Kind::Line => SeriesKind::Line,
                            NumericSeries11Kind::Histogram => SeriesKind::Histogram,
                        },
                        points,
                    });
                }
                IndicatorOutputSpec11::ConditionalMarker11(spec) => {
                    let values = context.boolean(&spec.when)?;
                    for (bar, value) in bars[visible_start..].iter().zip(&values[visible_start..]) {
                        if *value != Some(true) {
                            continue;
                        }
                        if result.markers.len() == MAX_MARKERS {
                            return Err(IndicatorError::Limit);
                        }
                        result.markers.push(Marker {
                            time_ms: bar.time_ms,
                            position: spec.position.clone(),
                            label: spec.label.clone(),
                            color: spec.color.clone(),
                        });
                    }
                }
            }
        }
        context.check_cancelled()?;
        result.markers.sort_by_key(|marker| marker.time_ms);
        if context.divide_by_zero {
            result.diagnostics.push(Diagnostic {
                code: "division_by_zero".into(),
                message: "Division by zero produced unknown values.".into(),
                severity: Severity::Warning,
            });
        }
        Ok(result)
    }
}

struct Validation<'a> {
    parameters: &'a Parameters,
    nodes: usize,
}

impl Validation<'_> {
    fn node(&mut self, depth: usize) -> Result<()> {
        self.nodes += 1;
        if self.nodes > MAX_NODES || depth > MAX_DEPTH {
            return Err(IndicatorError::Limit);
        }
        Ok(())
    }

    fn number(&mut self, expression: &NumberExpression11, depth: usize) -> Result<usize> {
        self.node(depth)?;
        let next = depth + 1;
        Ok(match expression {
            NumberExpression11::FieldExpression(_) => 0,
            NumberExpression11::ConstantExpression(node) => {
                finite(node.value)?;
                0
            }
            NumberExpression11::NumberReference11(node) => {
                numeric_parameter(self.parameters, &node.name)?;
                0
            }
            NumberExpression11::Arithmetic11(node) => self
                .number(&node.left, next)?
                .max(self.number(&node.right, next)?),
            NumberExpression11::Absolute11(node) => self.number(&node.value, next)?,
            NumberExpression11::Delay11(node) => {
                self.number(&node.value, next)? + node.periods as usize
            }
            NumberExpression11::Rolling11(node) => {
                self.number(&node.value, next)? + periods(self.parameters, &node.window)? - 1
            }
        })
    }

    fn boolean(&mut self, expression: &BooleanExpression11, depth: usize) -> Result<usize> {
        self.node(depth)?;
        let next = depth + 1;
        Ok(match expression {
            BooleanExpression11::BooleanConstant11(_) => 0,
            BooleanExpression11::BooleanReference11(node) => {
                boolean_parameter(self.parameters, &node.name)?;
                0
            }
            BooleanExpression11::Comparison11(node) => self
                .number(&node.left, next)?
                .max(self.number(&node.right, next)?),
            BooleanExpression11::BooleanBinary11(node) => self
                .boolean(&node.left, next)?
                .max(self.boolean(&node.right, next)?),
        })
    }
}

fn finite(value: f64) -> Result<f64> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(IndicatorError::Nonfinite)
    }
}

fn numeric_parameter(parameters: &Parameters, name: &str) -> Result<f64> {
    match parameters.get(name) {
        Some(Parameter::Number(value)) => Ok(*value),
        Some(Parameter::Integer(value)) => Ok(*value as f64),
        _ => Err(IndicatorError::Invalid),
    }
}

fn boolean_parameter(parameters: &Parameters, name: &str) -> Result<bool> {
    match parameters.get(name) {
        Some(Parameter::Boolean(value)) => Ok(*value),
        _ => Err(IndicatorError::Invalid),
    }
}

fn periods(parameters: &Parameters, window: &WindowLength11) -> Result<usize> {
    let value = match window {
        WindowLength11::WindowLiteral11(window) => window.periods as i64,
        WindowLength11::WindowParameter11(window) => match parameters.get(&window.parameter) {
            Some(Parameter::Integer(value)) => *value,
            _ => return Err(IndicatorError::Invalid),
        },
    };
    if !(1..=MAX_BARS as i64).contains(&value) {
        return Err(IndicatorError::Invalid);
    }
    Ok(value as usize)
}

struct Evaluation<'a> {
    parameters: &'a Parameters,
    bars: &'a [Bar],
    continuity: Vec<usize>,
    cancelled: &'a dyn Fn() -> bool,
    divide_by_zero: bool,
}

impl Evaluation<'_> {
    fn check_cancelled(&self) -> Result<()> {
        if (self.cancelled)() {
            Err(IndicatorError::Cancelled)
        } else {
            Ok(())
        }
    }

    fn number(&mut self, expression: &NumberExpression11) -> Result<Vec<Option<f64>>> {
        self.check_cancelled()?;
        let values = match expression {
            NumberExpression11::FieldExpression(node) => self
                .bars
                .iter()
                .map(|bar| match node.field {
                    FieldExpressionField::Open => Some(bar.open),
                    FieldExpressionField::High => Some(bar.high),
                    FieldExpressionField::Low => Some(bar.low),
                    FieldExpressionField::Close => Some(bar.close),
                    FieldExpressionField::Volume => bar.volume,
                })
                .collect(),
            NumberExpression11::ConstantExpression(node) => vec![Some(node.value); self.bars.len()],
            NumberExpression11::NumberReference11(node) => {
                vec![Some(numeric_parameter(self.parameters, &node.name)?); self.bars.len()]
            }
            NumberExpression11::Arithmetic11(node) => {
                let mut left = self.number(&node.left)?;
                let right = self.number(&node.right)?;
                for (left, right) in left.iter_mut().zip(right) {
                    *left = match left.zip(right) {
                        None => None,
                        Some((_, 0.0)) if matches!(node.op, Arithmetic11Op::Divide) => {
                            self.divide_by_zero = true;
                            None
                        }
                        Some((a, b)) => Some(finite(match node.op {
                            Arithmetic11Op::Add => a + b,
                            Arithmetic11Op::Subtract => a - b,
                            Arithmetic11Op::Multiply => a * b,
                            Arithmetic11Op::Divide => a / b,
                        })?),
                    };
                }
                left
            }
            NumberExpression11::Absolute11(node) => {
                let mut values = self.number(&node.value)?;
                for value in values.iter_mut().flatten() {
                    *value = value.abs();
                }
                values
            }
            NumberExpression11::Delay11(node) => {
                let delay = node.periods as usize;
                let values = self.number(&node.value)?;
                self.continuity
                    .iter()
                    .enumerate()
                    .map(|(index, run)| {
                        if *run > delay {
                            values[index - delay]
                        } else {
                            None
                        }
                    })
                    .collect()
            }
            NumberExpression11::Rolling11(node) => {
                let size = periods(self.parameters, &node.window)?;
                let values = self.number(&node.value)?;
                self.rolling(values, size, &node.op)?
            }
        };
        Ok(values)
    }

    fn rolling(
        &self,
        mut values: Vec<Option<f64>>,
        size: usize,
        op: &Rolling11Op,
    ) -> Result<Vec<Option<f64>>> {
        let deviation = matches!(op, Rolling11Op::RollingStd);
        let mut block = Vec::new();
        let mut suffixes = Vec::new();
        let mut prefix = Moments::default();
        for (index, value) in values.iter_mut().enumerate() {
            if index % 1024 == 0 {
                self.check_cancelled()?;
            }
            if self.continuity[index] == 1 || value.is_none() {
                block.clear();
                suffixes.clear();
                prefix = Moments::default();
            }
            let Some(sample) = *value else { continue };

            // A window crosses at most two blocks. Each suffix contains only observations
            // still in that window, so an expired price contributes no rounding residue.
            if block.len() == size {
                let mut suffix = Moments::default();
                for previous in block.drain(..).rev() {
                    suffix = Moments::single(previous).combine(suffix, deviation);
                    suffixes.push(suffix);
                }
                prefix = Moments::default();
            }
            suffixes.pop();
            block.push(sample);
            prefix = prefix.combine(Moments::single(sample), deviation);
            *value = if block.len() + suffixes.len() == size {
                let suffix = suffixes.last().copied().unwrap_or_default();
                let total = suffix.combine(prefix, deviation);
                Some(finite(if deviation {
                    total.population_deviation()
                } else {
                    total.mean()
                })?)
            } else {
                None
            };
        }
        Ok(values)
    }

    fn boolean(&mut self, expression: &BooleanExpression11) -> Result<Vec<Option<bool>>> {
        self.check_cancelled()?;
        Ok(match expression {
            BooleanExpression11::BooleanConstant11(node) => vec![Some(node.value); self.bars.len()],
            BooleanExpression11::BooleanReference11(node) => {
                vec![Some(boolean_parameter(self.parameters, &node.name)?); self.bars.len()]
            }
            BooleanExpression11::Comparison11(node) => {
                let left = self.number(&node.left)?;
                let right = self.number(&node.right)?;
                left.into_iter()
                    .zip(right)
                    .map(|(left, right)| {
                        left.zip(right).map(|(a, b)| match node.op {
                            Comparison11Op::Gt => a > b,
                            Comparison11Op::Lt => a < b,
                        })
                    })
                    .collect()
            }
            BooleanExpression11::BooleanBinary11(node) => {
                let left = self.boolean(&node.left)?;
                let right = self.boolean(&node.right)?;
                left.into_iter()
                    .zip(right)
                    .map(|(left, right)| match (&node.op, left, right) {
                        (BooleanBinary11Op::And, Some(false), _)
                        | (BooleanBinary11Op::And, _, Some(false)) => Some(false),
                        (BooleanBinary11Op::Or, Some(true), _)
                        | (BooleanBinary11Op::Or, _, Some(true)) => Some(true),
                        (_, Some(value), Some(_)) => Some(value),
                        _ => None,
                    })
                    .collect()
            }
        })
    }
}

#[derive(Clone, Copy, Default)]
struct Moments {
    count: usize,
    // Express the mean relative to an actual observation to retain small price differences.
    origin: f64,
    offset: f64,
    scale: f64,
    squared_deviations: f64,
}

impl Moments {
    fn single(value: f64) -> Self {
        Self {
            count: 1,
            origin: value,
            ..Self::default()
        }
    }

    fn mean(self) -> f64 {
        self.origin + self.offset
    }

    fn population_deviation(self) -> f64 {
        (self.squared_deviations / self.count as f64).sqrt() * self.scale
    }

    fn combine(self, other: Self, deviation: bool) -> Self {
        if self.count == 0 {
            return other;
        }
        if other.count == 0 {
            return self;
        }
        let count = self.count + other.count;
        let left_weight = self.count as f64 / count as f64;
        let right_weight = other.count as f64 / count as f64;
        let delta = (other.origin - self.origin) + (other.offset - self.offset);
        let offset = self.offset + delta * right_weight;
        let (origin, offset) = if offset.is_finite() {
            (self.origin, offset)
        } else {
            // Two finite observations can be farther apart than f64::MAX.
            (0.0, self.mean() * left_weight + other.mean() * right_weight)
        };
        // Merge centered sums of squares in units of the largest contributing spread.
        // This avoids both underflow for tiny price changes and overflow when squaring.
        let (mut scale, mut squared_deviations) = (0.0, 0.0);
        if deviation {
            let (spread, factor) = if delta.is_finite() {
                (delta, 1.0)
            } else {
                (other.mean() * 0.5 - self.mean() * 0.5, 2.0)
            };
            scale = self.scale.max(other.scale).max(spread.abs());
            if scale > 0.0 {
                let between = (spread / scale) * factor;
                squared_deviations = self.squared_deviations * (self.scale / scale).powi(2)
                    + other.squared_deviations * (other.scale / scale).powi(2)
                    + between.powi(2) * (self.count as f64 * right_weight);
            }
        }
        Self {
            count,
            origin,
            offset,
            scale,
            squared_deviations,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source() -> DeclarativeSource11 {
        serde_json::from_value(json!({
            "parameters": [
                {"type": "integer", "name": "period", "default": 2},
                {"type": "number", "name": "threshold", "default": 3},
                {"type": "boolean", "name": "markers", "default": true},
            ],
            "outputs": [
                {"kind": "line", "id": "mean", "pane": "price", "style": {"color": "#0000FF", "width": 2},
                 "value": {"op": "rolling_mean", "value": {"op": "field", "field": "close"}, "window": {"parameter": "period"}}},
                {"kind": "line", "id": "std", "pane": "deviation", "style": {"color": "#FF0000", "width": 1},
                 "value": {"op": "rolling_std", "value": {"op": "field", "field": "close"}, "window": {"periods": 2}}},
                {"kind": "histogram", "id": "vol", "pane": "volume", "style": {"color": "#00FF00", "width": 3},
                 "value": {"op": "field", "field": "volume"}},
                {"kind": "marker_when", "position": "above", "label": "Above threshold", "color": "#FFFFFF",
                 "when": {"op": "and", "left": {"op": "boolean_parameter", "name": "markers"}, "right": {
                    "op": "gt", "left": {"op": "field", "field": "close"},
                    "right": {"op": "number_parameter", "name": "threshold"}
                 }}},
            ],
        })).unwrap()
    }

    fn bars() -> [Bar; 4] {
        [
            (0, 1.0, Some(10.0)),
            (1, 3.0, None),
            (2, 5.0, Some(20.0)),
            (4, 9.0, Some(40.0)),
        ]
        .map(|(time_ms, close, volume)| Bar {
            time_ms,
            trading_day: "2026-02-03".into(),
            open: close,
            high: close,
            low: close,
            close,
            volume,
            is_closed: true,
        })
    }

    fn provenance() -> IndicatorProvenance {
        IndicatorProvenance {
            code_hash: "1".repeat(64),
            params_hash: "2".repeat(64),
            dataset_id: "data".into(),
            dataset_revision: "fixture".into(),
            engine_version: "fixture".into(),
        }
    }

    fn evaluate(program: &Program, first_visible: usize) -> IndicatorResult {
        program
            .evaluate(
                &bars(),
                first_visible,
                |a, b| b.time_ms == a.time_ms + 1,
                || false,
                provenance(),
            )
            .unwrap()
    }

    fn values(series: &IndicatorSeries) -> Vec<Option<f64>> {
        series.points.iter().map(|point| point.value).collect()
    }

    #[test]
    fn indicator_preview_scenario_calculates_exact_values_and_preserves_nulls() {
        let program = Program::prepare(source(), &[]).unwrap();
        assert_eq!(
            program.resolved_parameters(),
            json!({"period": 2, "threshold": 3.0, "markers": true})
        );
        assert_eq!(program.warmup_bars(), 1);
        let result = evaluate(&program, 0);
        assert_eq!(
            values(&result.series[0]),
            [None, Some(2.0), Some(4.0), None]
        );
        assert_eq!(
            values(&result.series[1]),
            [None, Some(1.0), Some(1.0), None]
        );
        assert_eq!(
            values(&result.series[2]),
            [Some(10.0), None, Some(20.0), Some(40.0)]
        );
        assert_eq!(
            result.markers.iter().map(|m| m.time_ms).collect::<Vec<_>>(),
            [2, 4]
        );

        let overrides =
            serde_json::from_value::<Vec<_>>(json!([{"name": "period", "value": 3}])).unwrap();
        let program = Program::prepare(source(), &overrides).unwrap();
        assert_eq!(program.warmup_bars(), 2);
        assert_eq!(
            values(&evaluate(&program, 0).series[0]),
            [None, None, Some(3.0), None]
        );
        assert_eq!(
            values(&evaluate(&program, 1).series[0]),
            [None, Some(3.0), None]
        );
    }

    #[test]
    fn rolling_deviation_forgets_expired_prices_without_hiding_small_changes() {
        let deviation = json!({"op":"rolling_std","value":{"op":"field","field":"close"},"window":{"periods":2}});
        let zero = json!({"op":"constant","value":0.0});
        let line = |id, value| json!({"kind":"line","id":id,"pane":"risk","style":{"color":"#0022FF","width":1},"value":value});
        let program = Program::prepare(serde_json::from_value(json!({
            "parameters": [],
            "outputs": [
                line("std", deviation.clone()),
                line("reciprocal", json!({"op":"divide","left":{"op":"constant","value":1},"right":deviation})),
                {"kind":"marker_when","position":"above","color":"#FFFFFF","label":"Nonzero volatility",
                 "when":{"op":"gt","left":deviation,"right":zero}}
            ]
        })).unwrap(), &[]).unwrap();
        let flat: f64 = 120.21;
        let next = flat.next_up();
        let bars: Vec<_> = [133.33, flat, flat, next, flat, flat]
            .into_iter()
            .enumerate()
            .map(|(index, price)| Bar {
                time_ms: index as i64,
                open: price,
                high: price,
                low: price,
                close: price,
                ..bars()[0].clone()
            })
            .collect();
        let evaluate = |bars: &[Bar], visible| {
            program
                .evaluate(
                    bars,
                    visible,
                    |a, b| b.time_ms == a.time_ms + 1,
                    || false,
                    provenance(),
                )
                .unwrap()
        };
        let full = evaluate(&bars, 0);
        for index in [2, 5] {
            let warm = evaluate(&bars[index - 1..=index], 1);
            assert_eq!(full.series[0].points[index].value, Some(0.0));
            assert_eq!(full.series[1].points[index].value, None);
            assert_eq!(full.series[0].points[index], warm.series[0].points[0]);
            assert_eq!(full.series[1].points[index], warm.series[1].points[0]);
            assert!(warm.markers.is_empty());
            assert!(
                warm.diagnostics
                    .iter()
                    .any(|d| d.code == "division_by_zero")
            );
        }
        for index in [3, 4] {
            // The population standard deviation of a pair is half its absolute difference.
            assert_eq!(
                full.series[0].points[index].value,
                Some((next - flat) / 2.0)
            );
        }
        assert_eq!(
            full.markers.iter().map(|m| m.time_ms).collect::<Vec<_>>(),
            [1, 3, 4]
        );
    }

    #[test]
    fn rolling_population_deviation_matches_each_current_window() {
        let prices = [
            144, 111, 102, 82, 91, 70, 64, 67, 65, 64, 64, 64, 64, 64, 64, 64, 86, 65,
        ];
        let parameters = Parameters::new();
        let context = Evaluation {
            parameters: &parameters,
            bars: &[],
            continuity: (1..=prices.len()).collect(),
            cancelled: &|| false,
            divide_by_zero: false,
        };
        for size in [1, 2, 3, 5, 7] {
            let inputs = prices
                .iter()
                .map(|&p| Some(1.0e12 + f64::from(p) / 8.0))
                .collect();
            let values = context
                .rolling(inputs, size, &Rolling11Op::RollingStd)
                .unwrap();
            assert!(values[..size - 1].iter().all(Option::is_none));
            for (index, window) in prices.windows(size).enumerate() {
                // With integer eighths, the sum of squared pair differences is exact.
                let mut squares = 0i32;
                for (i, left) in window.iter().enumerate() {
                    for right in &window[i + 1..] {
                        squares += (left - right) * (left - right);
                    }
                }
                let expected = f64::from(squares).sqrt() / (size as f64 * 8.0);
                let actual = values[index + size - 1].unwrap();
                assert!(
                    (actual - expected).abs() <= expected * 8.0 * f64::EPSILON,
                    "window {window:?}: expected {expected}, got {actual}"
                );
            }
        }
        for magnitude in [1.0e-240, f64::MAX] {
            let pair = Moments::single(-magnitude).combine(Moments::single(magnitude), true);
            assert_eq!(pair.mean(), 0.0);
            assert_eq!(pair.population_deviation(), magnitude);
        }
    }

    #[test]
    fn indicator_preview_rejects_invalid_bindings_and_cancels() {
        for value in [
            json!([{"name": "absent", "value": 2}]),
            json!([{"name": "period", "value": true}]),
            json!([{"name": "period", "value": 2.5}]),
            json!([{"name": "period", "value": 0}]),
            json!([{"name": "period", "value": 2}, {"name": "period", "value": 2}]),
        ] {
            let bindings = serde_json::from_value::<Vec<_>>(value).unwrap();
            assert!(matches!(
                Program::prepare(source(), &bindings),
                Err(IndicatorError::Invalid)
            ));
        }
        let program = Program::prepare(source(), &[]).unwrap();
        assert_eq!(
            program.evaluate(&bars(), 0, |_, _| true, || true, provenance()),
            Err(IndicatorError::Cancelled),
        );
    }
}
