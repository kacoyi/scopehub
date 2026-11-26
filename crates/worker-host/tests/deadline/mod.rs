use std::{
    collections::BTreeSet,
    fs, future, io,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use scopehub_domain::protocol::ErrorCode;
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
    time::{self, Instant},
};

use super::{Fixture, compute, request};
use process_observer::{ProcessObserver, SETUP_LIMIT};

mod process_observer;

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

    let mut observer = ProcessObserver::start(&fixture.config.python).await;
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
                let pids = before
                    .pids()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let all_live = observer.arm(&pids).await;
                return (pids, all_live);
            }
            time::sleep(Duration::from_millis(10)).await;
        }
    };
    let (live_processes, early_result) = tokio::select! {
        biased;
        result = time::timeout_at(watchdog, &mut operation) => (None, Some((Instant::now(), result))),
        processes = observe => (Some(processes), None),
    };
    let observation_elapsed = started.elapsed();
    let boot_dir = before.booted.first().map(|(_, _, path)| path);
    let live_boot_scratch = live_processes.as_ref().and_then(|_| boot_dir).map(|path| {
        fs::symlink_metadata(path.join("deadline-boot")).map(|metadata| metadata.is_file())
    });
    let live_child_scratch = live_processes
        .as_ref()
        .and_then(|_| before.sleeping.first())
        .map(|(_, _, path)| {
            fs::symlink_metadata(path.join("deadline-child")).map(|metadata| metadata.is_file())
        });

    let (returned_at, terminal) = match early_result {
        Some(result) => result,
        None => {
            let result = time::timeout_at(watchdog, operation).await;
            (Instant::now(), result)
        }
    };
    // Issue the process probe first and inspect scratch without waiting for
    // the reply. Both observations precede any witness reread.
    let (exit_sample, scratch) = tokio::join!(
        biased;
        async {
            if terminal.is_ok() && matches!(&live_processes, Some((_, Ok(true)))) {
                Some(observer.sample(returned_at).await)
            } else {
                None
            }
        },
        async { boot_dir.map(fs::symlink_metadata) },
    );
    let after = Witnesses::read(&fixture.directory, false).unwrap();
    let deadline_elapsed = returned_at - started;
    eprintln!(
        "{phase:?}: observed={before:?}, observation_elapsed={observation_elapsed:?}, live_processes={live_processes:?}, boot_scratch={live_boot_scratch:?}, child_scratch={live_child_scratch:?}"
    );
    eprintln!(
        "{phase:?}: terminal={terminal:?}, deadline_elapsed={deadline_elapsed:?}, exit_sample={exit_sample:?}, scratch_at_return={scratch:?}, witnesses_after_terminal={after:?}"
    );

    let failure = terminal
        .expect("deadline and cleanup must return within the watchdog")
        .expect_err("a deadline must deliver no artifact");
    assert_eq!(failure.code, ErrorCode::TIMEOUT);
    assert!(!failure.retryable && failure.details.is_empty());
    assert!(deadline_elapsed >= TEST_DEADLINE);
    let (pids, all_live) = live_processes.expect("operation ended during phase observation");
    assert!(all_live.expect("open the witnessed process references"));
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
            assert_eq!(pids.len(), 2);
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
            assert_eq!(pids.len(), 3);
        }
    }
    assert_eq!(
        scratch.unwrap().unwrap_err().kind(),
        io::ErrorKind::NotFound
    );
    assert!(
        exit_sample
            .unwrap()
            .all_exited
            .expect("sample armed process references within the evidence window"),
        "a witnessed process was still alive at the first terminal sample"
    );
    // Linux pidfds signal on exit even before the process has been reaped.
    #[cfg(target_os = "linux")]
    assert!(pids.iter().all(|&pid| !super::process_exists(pid)));
    observer.finish().await;
}

#[tokio::test]
async fn a_deadline_reaps_running_code_without_retry_or_artifact() {
    observe_deadline(Phase::RunningChild).await;
}

#[tokio::test]
async fn a_deadline_before_hello_reaps_the_started_worker_without_running_code() {
    observe_deadline(Phase::BeforeHello).await;
}

async fn gated_child(python: &Path) -> (u32, Child) {
    let mut child = Command::new(python)
        .args([
            "-I",
            "-u",
            "-c",
            "import os, sys; print(os.getpid()); sys.stdin.buffer.read(1)",
        ])
        .kill_on_drop(true)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let mut pid = String::new();
    time::timeout(SETUP_LIMIT, output.read_line(&mut pid))
        .await
        .unwrap()
        .unwrap();
    (
        pid.trim()
            .parse()
            .expect("gated child must report its own PID"),
        child,
    )
}

async fn release(child: &mut Child) {
    drop(child.stdin.take());
    assert!(
        time::timeout(SETUP_LIMIT, child.wait())
            .await
            .unwrap()
            .unwrap()
            .success()
    );
}

#[tokio::test]
async fn the_terminal_observer_rejects_a_live_child_and_accepts_the_exited_set() {
    let config = super::config();
    let (exited_pid, mut exited) = gated_child(&config.python).await;
    let (gated_pid, mut gated) = gated_child(&config.python).await;
    let pids = [exited_pid, gated_pid];
    let mut observer = ProcessObserver::start(&config.python).await;
    assert!(
        time::timeout(SETUP_LIMIT, observer.arm(&pids))
            .await
            .unwrap()
            .unwrap()
    );
    release(&mut exited).await;

    let request = request(super::SOURCE);
    let result = compute(&config, &request, future::pending()).await;
    let living = observer.sample(Instant::now()).await;
    eprintln!(
        "Terminal observer control: pids={pids:?}, held_live={gated_pid}, return_to_reply={:?}, all_exited={:?}",
        living.return_to_reply, living.all_exited
    );
    assert!(result.is_ok(), "control compute must complete normally");
    assert!(!living.all_exited.unwrap());

    release(&mut gated).await;
    let result = compute(&config, &request, future::pending()).await;
    let exited = observer.sample(Instant::now()).await;
    eprintln!(
        "Terminal observer control: pids={pids:?}, both_exited, return_to_reply={:?}, all_exited={:?}",
        exited.return_to_reply, exited.all_exited
    );
    assert!(result.is_ok(), "control compute must complete normally");
    assert!(exited.all_exited.unwrap());
    observer.finish().await;
}
