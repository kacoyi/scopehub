use std::{ffi::OsStr, path::PathBuf, process::ExitCode};

use scopehub_worker_host::{
    market::WorkerConfig,
    text_model::{TURN_DEADLINE, run_child},
};

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
    config.deadline = TURN_DEADLINE;
    run_child(&config)
}
