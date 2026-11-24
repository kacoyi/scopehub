//! Settings and read admission for the fixed local market sample rule.

use scopehub_domain::protocol::{Error, ErrorCode};
use serde_json::{Value, json};
use uuid::Uuid;

use super::{WorkspaceAccess, WorkspaceStore, permits_scope};
use crate::{OperationRegistry, authorized_operation, failure};

pub const LOCAL_API_CONNECTION: &str = "market.localApiConnection";
const RULE_VERSION: u32 = 1;

#[derive(Clone)]
pub struct LocalApiConnection {
    pub id: String,
    pub name: String,
    pub origin: String,
    pub rule_version: u32,
}

impl LocalApiConnection {
    fn view(&self) -> Value {
        json!({"id": self.id, "name": self.name, "origin": self.origin, "rule_version": self.rule_version})
    }
}

pub enum LocalApiAdmission {
    Complete(Value),
    Trial(LocalApiTrial),
}

pub struct LocalApiTrial(LocalApiConnection);

impl LocalApiTrial {
    pub fn destination(&self) -> String {
        format!("{}/market-sample", self.0.origin)
    }
}

fn output(value: Value) -> Result<Value, Error> {
    if crate::operation(LOCAL_API_CONNECTION)?
        .output
        .is_valid(&value)
    {
        Ok(value)
    } else {
        Err(failure(ErrorCode::OUTPUTINVALID))
    }
}

fn valid_origin(origin: &str) -> bool {
    let Some(port) = origin.strip_prefix("http://127.0.0.1:") else {
        return false;
    };
    port.parse::<u16>()
        .is_ok_and(|number| number > 0 && port == number.to_string())
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub fn complete_local_api_trial(
        &self,
        trial: LocalApiTrial,
        sample: Value,
    ) -> Result<Value, Error> {
        self.port.inspect(|tx| {
            let reviewed = trial.0;
            if !tx.local_api_connection()?.is_some_and(|current| {
                current.id == reviewed.id
                    && current.origin == reviewed.origin
                    && current.rule_version == reviewed.rule_version
            }) {
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            output(json!({"action": "trial", "connection": reviewed.view(), "sample": sample}))
        })
    }

    pub fn local_api_connection(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<LocalApiAdmission, Error> {
        let op = authorized_operation(access.actor, LOCAL_API_CONNECTION)?;
        if !permits_scope(access, self.port.binding())
            || access.paired_client_id.is_some()
            || access.principal_id != access.profile_id
            || !access.operations.contains(&LOCAL_API_CONNECTION)
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if !op.input.is_valid(request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        match request["action"].as_str() {
            Some("status") => self.port.inspect(|tx| {
                let connection = tx
                    .local_api_connection()?
                    .as_ref()
                    .map(LocalApiConnection::view);
                output(json!({"action": "status", "connection": connection}))
                    .map(LocalApiAdmission::Complete)
            }),
            Some("save") => {
                let name = request["name"].as_str().unwrap_or_default().trim();
                let origin = request["origin"].as_str().unwrap_or_default();
                if name.is_empty() || name.chars().any(char::is_control) || !valid_origin(origin) {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                self.port.transact(|tx| {
                    let current = tx.local_api_connection()?;
                    if current.as_ref().map(|c| c.id.as_str()) != request["expected_id"].as_str() {
                        return Err(failure(ErrorCode::STALECONTEXT));
                    }
                    let connection = LocalApiConnection {
                        id: Uuid::new_v4().to_string(),
                        name: name.into(),
                        origin: origin.into(),
                        rule_version: RULE_VERSION,
                    };
                    let result =
                        output(json!({"action": "save", "connection": connection.view()}))?;
                    tx.save_local_api_connection(&connection)?;
                    Ok(LocalApiAdmission::Complete(result))
                })
            }
            Some("trial") => self.port.inspect(|tx| {
                let connection = tx
                    .local_api_connection()?
                    .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
                if request["id"].as_str() != Some(&connection.id) {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                if connection.rule_version != RULE_VERSION || !valid_origin(&connection.origin) {
                    return Err(failure(ErrorCode::OUTPUTINVALID));
                }
                Ok(LocalApiAdmission::Trial(LocalApiTrial(connection)))
            }),
            _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
        }
    }
}

#[cfg(test)]
#[test]
fn local_api_admits_only_exact_loopback_and_local_human() {
    use crate::Actor;
    assert!(valid_origin("http://127.0.0.1:65535"));
    for invalid in [
        "http://127.0.0.1:65536",
        "http://127.0.0.1:00080",
        "http://localhost:8070",
        "http://127.1:8070",
        "http://127.0.0.1:8070/",
        "http://127.0.0.1:8070?secret=x",
        "http://secret@127.0.0.1:8070",
        "https://127.0.0.1:8070",
    ] {
        assert!(!valid_origin(invalid));
    }
    assert!(authorized_operation(Actor::LocalUser, LOCAL_API_CONNECTION).is_ok());
    for actor in [Actor::User, Actor::Ai, Actor::System] {
        assert_eq!(
            authorized_operation(actor, LOCAL_API_CONNECTION)
                .err()
                .unwrap()
                .code,
            ErrorCode::PERMISSIONDENIED
        );
    }
}
