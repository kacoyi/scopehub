//! Shared operation entry for trusted transports. Historical reads only.

use std::{collections::HashSet, future::Future, pin::Pin, sync::LazyLock};

use scopehub_domain::{
    protocol::{Error, ErrorCode, InstrumentSearchOutput, MarketGetBarsInput, MarketGetBarsOutput},
    wire::{SCHEMA, normalize_integers},
};
use serde_json::{Value, json};

pub const DEMO_PROVIDER: &str = "scopehub.synthetic";
pub const READ_OPERATIONS: [&str; 2] = ["instrument.search", "market.getBars"];

/// Assigned by the transport, never deserialized from a worker or UI payload.
#[derive(Clone, Copy)]
pub enum Actor {
    User,
    Ai,
    System,
}

impl Actor {
    fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Ai => "ai",
            Self::System => "system",
        }
    }
}

pub fn failure(code: ErrorCode) -> Error {
    let message = match code {
        ErrorCode::PROVIDERNOTCONFIGURED => "No data source is configured for this request.",
        ErrorCode::UNSUPPORTEDCAPABILITY => "This operation or timeframe is not implemented.",
        ErrorCode::INVALIDARGUMENT => "The request arguments are invalid.",
        ErrorCode::PERMISSIONDENIED => "This caller cannot perform the operation.",
        ErrorCode::DATAUNAVAILABLE => "No data is available for this instrument.",
        ErrorCode::TIMEOUT => "The market worker did not respond before the deadline.",
        ErrorCode::WORKEREXITED => "The market worker is unavailable.",
        ErrorCode::OUTPUTINVALID => "The market response failed validation.",
        ErrorCode::PROTOCOLMISMATCH => "The market worker is incompatible.",
        ErrorCode::RESOURCELIMIT => "The market request exceeded a resource limit.",
        _ => "The operation could not be completed.",
    };
    Error {
        retryable: matches!(code, ErrorCode::TIMEOUT | ErrorCode::WORKEREXITED),
        code,
        message: message.into(),
        details: Default::default(),
    }
}

pub type MarketFuture<'a> = Pin<Box<dyn Future<Output = Result<Value, Error>> + Send + 'a>>;

pub trait MarketPort: Send {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a>;
}

struct Operation {
    name: String,
    actors: Vec<String>,
    input: jsonschema::Validator,
    output: jsonschema::Validator,
}

fn validator(definition: &str) -> jsonschema::Validator {
    let mut schema = SCHEMA.clone();
    schema["$ref"] = json!(format!("#/$defs/{definition}"));
    schema
        .as_object_mut()
        .expect("schema object")
        .remove("oneOf");
    jsonschema::options()
        .should_validate_formats(true)
        .build(&schema)
        .expect("embedded operation schema")
}

static OPERATIONS: LazyLock<Vec<Operation>> = LazyLock::new(|| {
    let manifest: Value = serde_json::from_str(include_str!("../../../contracts/contract.json"))
        .expect("embedded operation catalog");
    manifest["operations"]
        .as_array()
        .expect("operation array")
        .iter()
        .map(|op| Operation {
            name: op["name"].as_str().expect("operation name").into(),
            actors: serde_json::from_value(op["actors"].clone()).expect("actor list"),
            input: validator(op["input"].as_str().expect("input definition")),
            output: validator(op["output"].as_str().expect("output definition")),
        })
        .collect()
});

pub struct MarketRegistry<P> {
    port: P,
}

impl<P: MarketPort> MarketRegistry<P> {
    pub fn new(port: P) -> Self {
        Self { port }
    }

    pub fn port_mut(&mut self) -> &mut P {
        &mut self.port
    }

    pub async fn execute(
        &mut self,
        actor: Actor,
        name: &str,
        mut payload: Value,
    ) -> Result<Value, Error> {
        let op = OPERATIONS
            .iter()
            .find(|op| op.name == name)
            .ok_or_else(|| failure(ErrorCode::UNSUPPORTEDCAPABILITY))?;
        if !op.actors.iter().any(|allowed| allowed == actor.as_str()) {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if !READ_OPERATIONS.contains(&name) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        if !op.input.is_valid(&payload) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut payload);
        if name == "market.getBars" {
            if payload["from_ms"].as_f64() >= payload["to_ms"].as_f64() {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            if payload["provider_id"] != DEMO_PROVIDER {
                return Err(failure(ErrorCode::PROVIDERNOTCONFIGURED));
            }
        }
        let output = self.port.request(name, &payload).await?;
        if !op.output.is_valid(&output) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        let mut checked = output.clone();
        normalize_integers(&mut checked);
        validate_market(name, &payload, &checked)?;
        Ok(output)
    }
}

fn validate_market(name: &str, payload: &Value, output: &Value) -> Result<(), Error> {
    let invalid = || failure(ErrorCode::OUTPUTINVALID);
    if name == "instrument.search" {
        let result: InstrumentSearchOutput =
            serde_json::from_value(output.clone()).map_err(|_| invalid())?;
        let mut ids = HashSet::new();
        if result.instruments.len() > payload["limit"].as_u64().unwrap_or(0) as usize
            || result.instruments.iter().any(|item| {
                !ids.insert(&item.id)
                    || item.tradability.as_str() != "non_tradable"
                    || item.metadata.get("synthetic") != Some(&json!(true))
                    || !item
                        .provider_mappings
                        .iter()
                        .any(|mapping| mapping.provider_id == DEMO_PROVIDER)
            })
        {
            return Err(invalid());
        }
    } else {
        let input: MarketGetBarsInput = serde_json::from_value(payload.clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let result: MarketGetBarsOutput =
            serde_json::from_value(output.clone()).map_err(|_| invalid())?;
        let data = &result.dataset;
        if data.instrument_id != input.instrument_id
            || data.provider_id != input.provider_id
            || data.timeframe != input.timeframe
            || data.mode.as_str() != "simulated"
            || data.price_adjustment != "raw"
            || data.provenance.get("synthetic") != Some(&json!(true))
        {
            return Err(invalid());
        }
        let mut previous = None;
        for (index, bar) in result.bars.iter().enumerate() {
            if [bar.open, bar.high, bar.low, bar.close]
                .iter()
                .any(|price| !price.is_finite())
                || bar
                    .volume
                    .is_some_and(|volume| !volume.is_finite() || volume < 0.0)
                || bar.time_ms < input.from_ms
                || bar.time_ms >= input.to_ms
                || previous.is_some_and(|time| time >= bar.time_ms)
                || bar.low > bar.open.min(bar.close)
                || bar.high < bar.open.max(bar.close)
                || (!bar.is_closed && index + 1 != result.bars.len())
            {
                return Err(invalid());
            }
            previous = Some(bar.time_ms);
        }
    }
    Ok(())
}
