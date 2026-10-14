use std::path::PathBuf;
use std::time::Duration;

use scopehub_worker_host::{ProtocolError, probe_handshake};
use tokio::process::Command;

fn command() -> Command {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let python = std::env::var_os("SCOPEHUB_PYTHON").unwrap_or_else(|| {
        root.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        })
        .into_os_string()
    });
    let mut command = Command::new(python);
    command.env_clear().current_dir(&root);
    for key in ["SYSTEMROOT", "WINDIR", "TEMP", "TMP"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command.env(
        "PYTHONPATH",
        std::env::join_paths([root.clone(), root.join("python")]).unwrap(),
    );
    command.env("PYTHONIOENCODING", "utf-8");
    command
}

#[tokio::test]
async fn rust_starts_python_and_validates_the_handshake() {
    let mut worker = command();
    worker.args(["-m", "worker_runtime", "--role", "market"]);
    let hello = probe_handshake(&mut worker, "market", Duration::from_secs(5))
        .await
        .unwrap();
    assert_eq!(hello.protocol_version, "1.0");
    assert!(hello.capabilities.is_empty());
}

#[tokio::test]
async fn wrong_role_fails_closed() {
    let mut worker = command();
    worker.args(["-m", "worker_runtime", "--role", "ai"]);
    assert_eq!(
        probe_handshake(&mut worker, "market", Duration::from_secs(5)).await,
        Err(ProtocolError::IncompatibleWorker)
    );
}

#[tokio::test]
async fn timeout_and_oversized_or_invalid_hello_are_reaped() {
    for (script, expected) in [
        ("import time; time.sleep(60)", ProtocolError::Timeout),
        (
            "import sys,time; sys.stdout.write('x' * 4194305); sys.stdout.flush(); time.sleep(60)",
            ProtocolError::ResourceLimit,
        ),
        (
            "import time; print('{}', flush=True); time.sleep(60)",
            ProtocolError::InvalidMessage,
        ),
    ] {
        let mut worker = command();
        worker.args(["-c", script]);
        let started = std::time::Instant::now();
        assert_eq!(
            probe_handshake(&mut worker, "market", Duration::from_secs(1)).await,
            Err(expected)
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        // probe_handshake waits for the child after killing it; returning proves it was reaped.
    }
}
