use std::{
    io::{self, BufRead, Read, Write},
    process::ExitCode,
};

use scopehub_domain::protocol::{Error, ErrorCode, IndicatorCompute11, Message};
use serde_json::{Value, json};
use tokio::{sync::oneshot, time::timeout};

use super::{compute, encode_frame, error};
use crate::{decode_line, market::WorkerConfig, max_message_bytes};

/// Private native CLI carrier for an already confirmed run. The parent keeps
/// stdin open after its request and closes it to cancel or signal parent loss.
/// Call from a standalone process, outside an async runtime.
pub fn run_child(config: &WorkerConfig) -> ExitCode {
    let result = run(config);
    let frame = match result {
        Ok(result) => encode_frame(&result),
        Err(failure) => encode_frame(&json!({"error": failure})),
    };
    let Ok(frame) = frame else {
        return ExitCode::FAILURE;
    };
    let mut output = io::stdout().lock();
    if output
        .write_all(&frame)
        .and_then(|()| output.flush())
        .is_ok()
    {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

fn run(config: &WorkerConfig) -> Result<scopehub_domain::protocol::IndicatorComputed11, Error> {
    let (input, frame) = oneshot::channel();
    let (cancel, eof) = oneshot::channel();
    // A blocked stdin read must not delay process exit after a successful reply.
    std::thread::Builder::new()
        .name("indicator-parent-input".into())
        .spawn(move || {
            let mut stdin = io::stdin().lock();
            let frame = read_request(&mut stdin);
            if input.send(frame).is_ok() {
                // EOF, an input error or any additional input cancels this run.
                let _ = stdin.read(&mut [0]);
                let _ = cancel.send(());
            }
        })
        .map_err(|_| error(ErrorCode::WORKEREXITED))?;
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| error(ErrorCode::WORKEREXITED))?
        .block_on(async {
            let request = timeout(config.deadline, frame)
                .await
                .map_err(|_| error(ErrorCode::TIMEOUT))?
                .map_err(|_| error(ErrorCode::WORKEREXITED))??;
            compute(config, &request, async {
                let _ = eof.await;
            })
            .await
        })
}

fn read_request(input: &mut impl BufRead) -> Result<IndicatorCompute11, Error> {
    let mut frame = Vec::new();
    input
        .take((max_message_bytes() + 1) as u64)
        .read_until(b'\n', &mut frame)
        .map_err(|_| error(ErrorCode::INVALIDARGUMENT))?;
    if frame.len() > max_message_bytes() {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    let invalid = || error(ErrorCode::INVALIDARGUMENT);
    if !frame.ends_with(b"\n") {
        return Err(error(ErrorCode::CANCELLED));
    }
    let Message::IndicatorCompute11(mut request) = decode_line(&frame).map_err(|_| invalid())?
    else {
        return Err(invalid());
    };
    if let Ok(exact) = serde_json::from_slice(&frame) {
        return Ok(exact);
    }
    // Shared wire decoding normalizes integral numbers. Preserve exact numeric
    // types inside opaque parameters for the approved hash and Python call.
    let mut raw: Value = serde_json::from_slice(&frame).map_err(|_| invalid())?;
    let Value::Object(params) = raw["resolved_params"].take() else {
        return Err(invalid());
    };
    request.resolved_params = params;
    Ok(*request)
}
