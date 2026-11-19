use std::{ffi::OsStr, path::PathBuf, process::ExitCode, time::Duration};

use scopehub_worker_host::{POLICY, indicator::run_child, market::WorkerConfig};

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let [flag, path] = args.as_slice() else {
        return ExitCode::from(2);
    };
    let root = PathBuf::from(path);
    if flag != OsStr::new("--root") || !root.is_absolute() {
        return ExitCode::from(2);
    }
    let mut config = WorkerConfig::development(&root);
    config.deadline = Duration::from_millis(
        POLICY["indicator_timeout_ms"]
            .as_u64()
            .expect("configured indicator timeout"),
    );
    run_child(&config)
}
