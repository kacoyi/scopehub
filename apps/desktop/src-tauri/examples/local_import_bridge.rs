//! Test bridge for the built UI with a synthetic folder and profile chosen by the harness.

use std::{
    env,
    error::Error as StdError,
    io::{self, BufRead, Write},
    path::Path,
};

use scopehub_application::failure;
use scopehub_desktop::bridge::{State, state_with_development_workspace, state_with_local_profile};
use scopehub_domain::protocol::{Error, ErrorCode};
use serde_json::{Value, json};

async fn dispatch(state: &State, folder: &Path, command: Value) -> Result<Value, Error> {
    let mut args = command["args"].clone();
    match command["command"].as_str() {
        Some("choose_local_csv_folder") => Ok(state
            .choose_local_csv_folder(|| async { Some(folder.to_path_buf()) })
            .await?
            .unwrap_or(Value::Null)),
        Some("execute_operation") => {
            let payload = args["payload"].take();
            state
                .execute_test_window_operation(
                    args["operation"].as_str(),
                    payload,
                    args["requestId"].as_str(),
                )
                .await
        }
        _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn StdError>> {
    let arguments: Vec<_> = env::args_os().skip(1).collect();
    let (profile, folder, workspace) = match arguments.as_slice() {
        [profile, folder] => (profile, folder, false),
        [profile, folder, option] if option == "--workspace" => (profile, folder, true),
        _ => return Err(
            "Usage: local_import_bridge <absolute-profile> <absolute-fixture-folder> [--workspace]"
                .into(),
        ),
    };
    let profile = Some(Path::new(profile));
    let state = if workspace {
        state_with_development_workspace(profile)
    } else {
        state_with_local_profile(profile)
    };
    let mut output = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let command = serde_json::from_str(&line?)?;
        let reply = match dispatch(&state, Path::new(folder), command).await {
            Ok(result) => json!({ "result": result }),
            Err(error) => json!({ "error": error }),
        };
        writeln!(output, "{reply}")?;
        output.flush()?;
    }
    state.shutdown().await;
    Ok(())
}
