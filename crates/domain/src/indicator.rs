//! Typed, bounded evaluation over a single immutable bar series.

use std::collections::{BTreeMap, HashSet, VecDeque};

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

    pub fn evaluate(
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
        let mut queue = VecDeque::new();
        let mut moments = Moments::default();
        let mut shifts = 0;
        let deviation = matches!(op, Rolling11Op::RollingStd);
        for (index, value) in values.iter_mut().enumerate() {
            if index % 1024 == 0 {
                self.check_cancelled()?;
            }
            if self.continuity[index] == 1 || value.is_none() {
                queue.clear();
                moments = Moments::default();
                shifts = 0;
            }
            if let Some(sample) = *value {
                if queue.len() == size {
                    let oldest = queue.pop_front().expect("complete rolling window");
                    if queue.is_empty() {
                        moments = Moments::default();
                    } else {
                        moments.remove(oldest, queue.len(), deviation);
                    }
                    shifts += 1;
                }
                moments.push(sample, queue.len(), deviation);
                queue.push_back(sample);
                // Bound accumulated error from repeated removals, in amortized linear time.
                if shifts == size {
                    moments = Moments::default();
                    for (index, sample) in queue.iter().copied().enumerate() {
                        moments.push(sample, index, deviation);
                    }
                    shifts = 0;
                }
            }
            *value = if queue.len() < size {
                None
            } else {
                let estimate = if deviation {
                    finite(moments.sum_squares)?;
                    (moments.sum_squares.max(0.0) / size as f64).sqrt()
                } else {
                    moments.mean
                };
                Some(finite(estimate)?)
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

#[derive(Default)]
struct Moments {
    mean: f64,
    sum_squares: f64,
}

impl Moments {
    fn push(&mut self, value: f64, previous_count: usize, deviation: bool) {
        let delta = value - self.mean;
        self.mean += delta / (previous_count + 1) as f64;
        if deviation {
            self.sum_squares += delta * (value - self.mean);
        }
    }

    fn remove(&mut self, value: f64, remaining: usize, deviation: bool) {
        let delta = value - self.mean;
        self.mean -= delta / remaining as f64;
        if deviation {
            self.sum_squares -= delta * (value - self.mean);
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
