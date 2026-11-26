use std::{
    collections::BTreeSet,
    fs, future, io,
    path::{Path, PathBuf},
    time::Duration,
};

use scopehub_domain::protocol::ErrorCode;
use tokio::time::{self, Instant};

use super::{Fixture, compute, request};

// Allow for cold interpreter and multiprocessing startup on native runners.
// This is a fixture budget. Production still counts startup against its deadline.
const TEST_DEADLINE: Duration = Duration::from_secs(15);
const BOOT_WITNESS: &str = r#"import json, os, pathlib, time
path = pathlib.Path(__file__).parents[2]
pathlib.Path('deadline-boot').write_text('synthetic', encoding='ascii')
with (path / 'booted').open('a', encoding='utf-8') as witness:
    witness.write(json.dumps([os.getpid(), os.getppid(), os.getcwd()]) + '\n')
if (path / 'hold-before-hello').exists():
    time.sleep(60)
"#;
const CHILD_FIXTURE: &str = r#"import json, os
from multiprocessing import get_context
from pathlib import Path
from time import sleep

def sleep_with_witness():
    Path('deadline-child').write_text('synthetic', encoding='ascii')
    path = Path(__file__).parents[1] / 'sleeping'
    with path.open('a', encoding='utf-8') as witness:
        witness.write(json.dumps([os.getpid(), os.getppid(), os.getcwd()]) + '\n')
    sleep(60)

def start_sleeping():
    get_context('spawn').Process(target=sleep_with_witness).start()
"#;
const SOURCE: &str = r#"def compute(bars, params):
    from indicator_deadline_fixture import start_sleeping
    from time import sleep
    start_sleeping()
    sleep(60)
    raise AssertionError('synthetic deadline did not fire')
"#;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    BeforeHello,
    RunningChild,
}

type ProcessWitness = (u32, u32, PathBuf);

#[derive(Debug, Default, PartialEq, Eq)]
struct Witnesses {
    booted: Vec<ProcessWitness>,
    entered: Vec<ProcessWitness>,
    sleeping: Vec<ProcessWitness>,
}

impl Witnesses {
    fn read(directory: &Path, in_flight: bool) -> io::Result<Self> {
        let read = |name| -> io::Result<Vec<ProcessWitness>> {
            let text = match fs::read_to_string(directory.join(name)) {
                Ok(text) => text,
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
                Err(error) => return Err(error),
            };
            text.split_inclusive('\n')
                .filter(|line| !in_flight || line.ends_with('\n'))
                .map(|line| serde_json::from_str(line).map_err(io::Error::other))
                .collect()
        };
        Ok(Self {
            booted: read("booted")?,
            entered: read("entered")?,
            sleeping: read("sleeping")?,
        })
    }

    fn pids(&self) -> impl Iterator<Item = u32> + '_ {
        [&self.booted, &self.entered, &self.sleeping]
            .into_iter()
            .flatten()
            .flat_map(|(pid, parent, _)| [*pid, *parent])
    }
}

async fn check_pids(pids: impl Iterator<Item = u32>) -> Vec<(u32, bool)> {
    let mut states = Vec::new();
    for pid in pids.collect::<BTreeSet<_>>() {
        #[cfg(target_os = "linux")]
        let alive = super::process_exists(pid);
        #[cfg(windows)]
        let alive = {
            let output = time::timeout(
                Duration::from_secs(2),
                tokio::process::Command::new("tasklist.exe")
                    .args(["/NH", "/FO", "CSV", "/FI", &format!("PID eq {pid}")])
                    .kill_on_drop(true)
                    .output(),
            )
            .await
            .expect("tasklist must return within the process observation budget")
            .unwrap();
            assert!(output.status.success());
            String::from_utf8_lossy(&output.stdout).contains(&format!(",\"{pid}\","))
        };
        states.push((pid, alive));
    }
    states
}

async fn observe_deadline(phase: Phase) {
    let mut fixture = Fixture::observe_real_worker();
    fixture.config.deadline = TEST_DEADLINE;
    if phase == Phase::BeforeHello {
        fs::write(fixture.directory.join("hold-before-hello"), "").unwrap();
    }
    let worker = fixture.directory.join("python/worker_runtime/indicator.py");
    let script = fs::read_to_string(&worker).unwrap();
    fs::write(worker, format!("{BOOT_WITNESS}{script}")).unwrap();
    fs::write(
        fixture
            .directory
            .join("python/indicator_deadline_fixture.py"),
        CHILD_FIXTURE,
    )
    .unwrap();

    let request = request(SOURCE);
    let operation = compute(&fixture.config, &request, future::pending());
    tokio::pin!(operation);
    let started = Instant::now();
    let watchdog = started + TEST_DEADLINE + Duration::from_secs(5);
    let mut before = Witnesses::default();
    let observe = async {
        loop {
            before = Witnesses::read(&fixture.directory, true).unwrap();
            let ready = match phase {
                Phase::BeforeHello => !before.booted.is_empty(),
                Phase::RunningChild => {
                    !before.booted.is_empty()
                        && !before.entered.is_empty()
                        && !before.sleeping.is_empty()
                }
            };
            if ready {
                return check_pids(before.pids()).await;
            }
            time::sleep(Duration::from_millis(10)).await;
        }
    };
    let (before_pids, early_result) = tokio::select! {
        biased;
        result = time::timeout_at(watchdog, &mut operation) => (None, Some(result)),
        pids = observe => (Some(pids), None),
    };
    let boot_dir = before.booted.first().map(|(_, _, path)| path);
    let live_boot_scratch = boot_dir.map(|path| {
        fs::symlink_metadata(path.join("deadline-boot")).map(|metadata| metadata.is_file())
    });
    let live_child_scratch = before.sleeping.first().map(|(_, _, path)| {
        fs::symlink_metadata(path.join("deadline-child")).map(|metadata| metadata.is_file())
    });
    let observation_elapsed = started.elapsed();
    eprintln!(
        "{phase:?}: observed={before:?}, observation_elapsed={observation_elapsed:?}, live_pids={before_pids:?}, boot_scratch={live_boot_scratch:?}, child_scratch={live_child_scratch:?}"
    );

    let terminal = match early_result {
        Some(result) => result,
        None => time::timeout_at(watchdog, operation).await,
    };
    let deadline_elapsed = started.elapsed();
    let scratch = boot_dir.map(fs::symlink_metadata);
    let after = Witnesses::read(&fixture.directory, false).unwrap();
    let scratch = scratch.or_else(|| {
        after
            .booted
            .first()
            .map(|(_, _, path)| fs::symlink_metadata(path))
    });
    let final_pids = check_pids(after.pids().chain(before.pids())).await;
    eprintln!(
        "{phase:?}: terminal={terminal:?}, deadline_elapsed={deadline_elapsed:?}, witnesses_at_return={after:?}, scratch_at_return={scratch:?}, remaining_pids={final_pids:?}"
    );

    let failure = terminal
        .expect("deadline and cleanup must return within the watchdog")
        .expect_err("a deadline must deliver no artifact");
    assert_eq!(failure.code, ErrorCode::TIMEOUT);
    assert!(!failure.retryable && failure.details.is_empty());
    assert!(deadline_elapsed >= TEST_DEADLINE);
    let before_pids = before_pids.expect("operation ended during phase observation");
    assert!(
        observation_elapsed < TEST_DEADLINE,
        "phase observation was late"
    );
    let [(pid, supervisor, working_dir)] = before.booted.as_slice() else {
        panic!("expected one interpreter startup witness");
    };
    assert_ne!(pid, supervisor);
    assert_ne!(*supervisor, std::process::id());
    assert!(live_boot_scratch.unwrap().unwrap());
    assert_eq!(after, before);
    match phase {
        Phase::BeforeHello => {
            assert!(after.entered.is_empty() && after.sleeping.is_empty());
            assert_eq!(before_pids.len(), 2);
        }
        Phase::RunningChild => {
            assert_eq!(before.entered, before.booted);
            let [(child, parent, child_dir)] = before.sleeping.as_slice() else {
                panic!("expected one running child witness");
            };
            assert_eq!(parent, pid);
            assert_ne!(child, pid);
            assert_eq!(child_dir, working_dir);
            assert!(live_child_scratch.unwrap().unwrap());
            assert_eq!(before_pids.len(), 3);
        }
    }
    assert!(before_pids.iter().all(|(_, exists)| *exists));
    assert_eq!(
        scratch.unwrap().unwrap_err().kind(),
        io::ErrorKind::NotFound
    );
    assert!(final_pids.iter().all(|(_, exists)| !exists));
}

#[tokio::test]
async fn a_deadline_reaps_running_code_without_retry_or_artifact() {
    observe_deadline(Phase::RunningChild).await;
}

#[tokio::test]
async fn a_deadline_before_hello_reaps_the_started_worker_without_running_code() {
    observe_deadline(Phase::BeforeHello).await;
}
