use scopehub_domain::{
    protocol::{
        Error, ErrorCode, ResearchSampleCommand11, ResearchSampleInput,
        ResearchSampleInputAction as Action, ResearchSampleOutputAccess as Access, Revision,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};

use super::{MAX_REVISION, WorkspaceAccess, WorkspaceReply, WorkspaceStore, permits_scope};
use crate::{OperationRegistry, failure, operation};

pub const RESEARCH_SAMPLE: &str = "research.sample";

pub struct ResearchSampleState {
    pub access: Access,
    pub revision: Revision,
    pub refreshed: bool,
}

impl Default for ResearchSampleState {
    fn default() -> Self {
        Self {
            access: Access::Unconfigured,
            revision: 0,
            refreshed: false,
        }
    }
}

// Immutable local source records. The editable layout contains only record identities.
// Times below are offsets from 2024-01-02T00:00:00Z, independent of the host locale.
const DAY_START_MS: i64 = 1_704_153_600_000;
struct Record {
    id: &'static str,
    issuer: &'static str,
    minute_utc: i64,
    title: &'static str,
    body: &'static str,
}
const NOTES: [Record; 2] = [
    Record {
        id: "note-a",
        issuer: "A",
        minute_utc: 540,
        title: "Issuer A review",
        body: "Synthetic research note. Review the next published update on issuer A's local inventory.",
    },
    Record {
        id: "note-b",
        issuer: "B",
        minute_utc: 570,
        title: "Issuer B review",
        body: "Synthetic research note. Check new issuer B disclosures after this review.",
    },
];
const DISCLOSURES: [Record; 4] = [
    Record {
        id: "a-0855",
        issuer: "A",
        minute_utc: 535,
        title: "Inventory baseline",
        body: "Synthetic issuer A disclosure. Opening inventory was 120 units.",
    },
    Record {
        id: "b-0905",
        issuer: "B",
        minute_utc: 545,
        title: "Dispatch update",
        body: "Synthetic issuer B disclosure. The first dispatch carried 18 units.",
    },
    Record {
        id: "a-0910",
        issuer: "A",
        minute_utc: 550,
        title: "Inventory update",
        body: "Synthetic issuer A disclosure. A local inventory recount recorded 125 units.",
    },
    Record {
        id: "a-0920",
        issuer: "A",
        minute_utc: 560,
        title: "Revised dispatch total",
        body: "Synthetic issuer A disclosure. The confirmed dispatch total was 24 units.",
    },
];

impl Record {
    fn project(&self, time_field: &str, original: bool) -> Value {
        let mut row = json!({
            "id": self.id, "issuer": self.issuer, "title": self.title,
            time_field: DAY_START_MS + self.minute_utc * 60_000
        });
        if original {
            row["body"] = json!(self.body);
        }
        row
    }
}

impl ResearchSampleState {
    fn update(&mut self, args: &ResearchSampleInput) -> Result<(), Error> {
        if args.expected_revision != Some(self.revision) {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        match args.action {
            Action::Connect => self.access = Access::Connected,
            Action::Disconnect if self.access == Access::Connected => {
                self.access = Access::Disconnected
            }
            Action::Revoke if matches!(self.access, Access::Connected | Access::Disconnected) => {
                self.access = Access::Revoked
            }
            Action::Refresh if self.access == Access::Connected => self.refreshed = true,
            _ => return Err(failure(ErrorCode::STALECONTEXT)),
        }
        if self.revision >= MAX_REVISION {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        self.revision += 1;
        Ok(())
    }

    fn project(&self, args: &ResearchSampleInput) -> Result<Value, Error> {
        let mut result = json!({"access": self.access, "revision": self.revision, "notes": [], "disclosures": []});
        if self.access != Access::Connected {
            return Ok(result);
        }
        result["notes"] = Value::Array(
            NOTES
                .iter()
                .map(|note| note.project("reviewed_at_ms", false))
                .collect(),
        );
        if !matches!(args.action, Action::Read | Action::Original) {
            return Ok(result);
        }

        let note = NOTES
            .iter()
            .find(|note| Some(note.id) == args.note_id.as_deref())
            .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
        let available = if self.refreshed {
            &DISCLOSURES[..]
        } else {
            &DISCLOSURES[..3]
        };
        if args.action == Action::Original {
            if args.expected_revision != Some(self.revision) {
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            let original = available
                .iter()
                .find(|record| Some(record.id) == args.record_id.as_deref())
                .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
            result["original"] = original.project("published_at_ms", true);
        } else {
            result["note"] = note.project("reviewed_at_ms", true);
            result["disclosures"] = Value::Array(
                available
                    .iter()
                    .map(|row| row.project("published_at_ms", false))
                    .collect(),
            );
        }
        Ok(result)
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn execute_research_sample(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
    ) -> Result<Value, Error> {
        let op = operation(RESEARCH_SAMPLE)?;
        if !op.wire_validators().0.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);
        let command: ResearchSampleCommand11 =
            serde_json::from_value(request).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let reply = WorkspaceReply::new(op, &command.request_id)?;
        let result = op.authorize(access.actor).and_then(|()| {
            if !super::evidence::local_display(access) || !permits_scope(access, self.port.binding())
                || command.scope.workspace_id != access.workspace_id || !access.disclose_workspace_state
                || !access.operations.contains(&RESEARCH_SAMPLE)
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let args = &command.intent.arguments;
            let complete = |state: &ResearchSampleState| {
                reply.validated(json!({"state":"completed", "operation":RESEARCH_SAMPLE, "result":state.project(args)?}))
            };
            match args.action {
                Action::Read | Action::Original | Action::Status => self.port.inspect(|tx| complete(&tx.research_sample()?)),
                _ => self.port.transact(|tx| {
                    let mut state = tx.research_sample()?;
                    state.update(args)?;
                    tx.write_research_sample(&state)?;
                    complete(&state)
                }),
            }
        });
        match result {
            Ok(value) => Ok(value),
            Err(error) => reply.failed(&error),
        }
    }
}
