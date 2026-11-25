//! Supervised Windows workspace ACL admission shared by host executables.

mod windows;

use std::{
    env,
    ffi::OsStr,
    io::{self, BufRead, BufReader, Read, Write},
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex, OnceLock, mpsc},
    thread,
    time::{Duration, Instant},
};

use windows::{Job, wait_for_exit};

const WORKER: &str = "--scopehub-workspace-acl";
const TEST_REQUEST: &str = "SCOPEHUB_ACL_TEST_REQUEST";
const ACTION: &str = "SCOPEHUB_STORAGE_ACTION";
const PROFILE: &str = "SCOPEHUB_STORAGE_PATH";
const COUNT: &str = "SCOPEHUB_STORAGE_PROFILE_COUNT";
const WORKER_DEADLINE: &str = "SCOPEHUB_STORAGE_DEADLINE_MS";
const PHASE_PREFIX: &str = "scopehub-storage-phase=";
const DEADLINE: Duration = Duration::from_secs(10);
static TEST_HOST: OnceLock<&'static str> = OnceLock::new();

#[derive(Clone, Copy)]
enum Phase {
    Ready,
    Identity,
    Volume,
    Ancestors,
    Profile,
    Children,
    Files,
    Complete,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Identity => "identity",
            Self::Volume => "volume",
            Self::Ancestors => "ancestors",
            Self::Profile => "profile",
            Self::Children => "children",
            Self::Files => "files",
            Self::Complete => "complete",
        }
    }
}

const PHASES: [Phase; 8] = [
    Phase::Ready,
    Phase::Identity,
    Phase::Volume,
    Phase::Ancestors,
    Phase::Profile,
    Phase::Children,
    Phase::Files,
    Phase::Complete,
];

#[derive(Default)]
struct Progress {
    ready: Option<Duration>,
    phase: Option<(Phase, Duration)>,
}

impl Progress {
    fn record(&mut self, phase: Phase, elapsed: Duration) {
        if matches!(phase, Phase::Ready) {
            self.ready = Some(elapsed);
        }
        self.phase = Some((phase, elapsed));
    }

    fn report(&self, action: &str, elapsed: Duration, spawn_delay: Option<Duration>) {
        let (phase, at) = self.phase.map_or(("none", None), |(phase, at)| {
            (phase.name(), Some(at.as_millis()))
        });
        eprintln!(
            "Workspace storage worker ({action}) spawn-to-ready-ms={:?} entry-to-ready-ms={:?} phase={phase} phase-ms={at:?} elapsed-ms={}",
            self.ready
                .zip(spawn_delay)
                .map(|(ready, delay)| ready.saturating_sub(delay).as_millis()),
            self.ready.map(|time| time.as_millis()),
            elapsed.as_millis(),
        );
    }
}

fn refusal_stage(exit: Option<i32>) -> &'static str {
    match exit {
        Some(20) => "account",
        Some(21) => "local-path",
        Some(22) => "local-ntfs",
        Some(23) => "action",
        Some(24) => "metadata",
        Some(25) => "path-type",
        Some(26) => "acl-read",
        Some(27) => "acl-entry",
        Some(28) => "ancestor-owner",
        Some(29) => "replaceable-ancestor",
        Some(30) => "shared-access",
        Some(31) => "owner-or-inheritance",
        Some(32) => "private-creation",
        Some(33) => "missing-directory",
        Some(34) => "profile-or-file-owner",
        Some(35) => "worker-control",
        Some(36) => "worker-timeout",
        _ => "worker-execution",
    }
}

fn refused(action: &str, stage: &'static str, kind: io::ErrorKind) -> io::Error {
    eprintln!("Workspace storage ACL check refused ({action}, {stage}).");
    io::Error::new(kind, stage)
}

fn permission_refused(action: &str, stage: &'static str) -> io::Error {
    refused(action, stage, io::ErrorKind::PermissionDenied)
}

/// Run the current host executable in its early workspace ACL worker mode.
pub fn dispatch(files: &[&str]) -> Option<i32> {
    let flag = env::args_os().nth(1)?;
    (flag == WORKER).then(|| worker(files))
}

/// Configure a trusted libtest executable to expose the same early worker.
#[doc(hidden)]
pub fn use_test_host(filter: &'static str) {
    assert_eq!(*TEST_HOST.get_or_init(|| filter), filter);
}

/// The ordinary libtest case has no work. A supervisor-owned invocation uses
/// the same input gate, platform policy, and exit code as an executable worker.
#[doc(hidden)]
pub fn dispatch_test(files: &[&str]) {
    if env::var_os(TEST_REQUEST).as_deref() == Some(OsStr::new("1")) {
        std::process::exit(worker(files));
    }
}

fn worker(files: &[&str]) -> i32 {
    let mut output = io::stdout().lock();
    let mut phase = |phase: Phase| {
        writeln!(output, "{PHASE_PREFIX}{}", phase.name())
            .and_then(|()| output.flush())
            .map_err(|_| 35u8)
    };
    let mut permit = [0];
    if phase(Phase::Ready).is_err()
        || io::stdin().read_exact(&mut permit).is_err()
        || permit != *b"1"
    {
        return 35;
    }
    let Some(deadline_ms) = env::var(WORKER_DEADLINE)
        .ok()
        .and_then(|deadline| deadline.parse::<u64>().ok())
    else {
        return 35;
    };
    if windows::tick_ms() >= deadline_ms {
        return 36;
    }
    let action = env::var(ACTION);
    let prepare = match action.as_deref() {
        Ok("prepare") => true,
        Ok("open") => false,
        _ => return 23,
    };
    let Some(children) = env::var(COUNT)
        .ok()
        .and_then(|count| count.parse::<u8>().ok())
    else {
        return 23;
    };
    if children > 64 || (!prepare && children != 0) {
        return 23;
    }
    let Some(path) = env::var_os(PROFILE)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
    else {
        return 21;
    };
    let mut working_phase = |stage| {
        if windows::tick_ms() >= deadline_ms {
            return Err(36);
        }
        phase(stage)
    };
    match windows::validate(
        &path,
        prepare,
        children,
        files,
        deadline_ms,
        &mut working_phase,
    )
    .and_then(|()| working_phase(Phase::Complete))
    {
        Ok(()) => 0,
        Err(exit) => i32::from(exit),
    }
}

/// Check or provision a Windows workspace through a killable same-image child.
pub fn check(path: &Path, prepare: bool, children: u8) -> io::Result<()> {
    let action = if prepare { "prepare" } else { "open" };
    let executable =
        env::current_exe().map_err(|_| permission_refused(action, "worker-location"))?;
    let mut command = Command::new(executable);
    if let Some(filter) = TEST_HOST.get() {
        command
            .args(["--exact", filter, "--nocapture", "--quiet"])
            .env(TEST_REQUEST, "1");
    } else {
        command.arg(WORKER).env_remove(TEST_REQUEST);
    }
    command
        .env(ACTION, action)
        .env(PROFILE, path)
        .env(COUNT, children.to_string());
    supervise(action, command, DEADLINE)
}

/// Admit a Windows profile and derive its stable original-account fingerprint.
pub fn check_profile_owner(path: &Path) -> io::Result<String> {
    check(path, false, 0)?;
    // The supervised child runs with the same primary token as this process.
    let user = windows::current_user().map_err(|_| permission_refused("open", "account"))?;
    Ok(profile_owner_fingerprint(&user))
}

fn profile_owner_fingerprint(user: &str) -> String {
    use sha2::{Digest, Sha256};

    let mut digest = Sha256::new();
    digest.update(b"ScopeHub desktop profile owner v1\0windows:");
    digest.update(user.as_bytes());
    format!("{:x}", digest.finalize())
}

struct Launch {
    job: Job,
    child: Child,
    spawn_delay: Duration,
}

impl Launch {
    fn stop_before_grant(self) -> bool {
        let Self { job, mut child, .. } = self;
        drop(child.stdin.take());
        drop(job);
        finish_exit(&mut child)
    }
}

fn finish_exit(child: &mut Child) -> bool {
    if wait_for_exit(child, 1000) {
        let _ = child.wait();
        true
    } else {
        false
    }
}

fn launch(
    action: &'static str,
    mut command: Command,
    started: Instant,
    deadline: Duration,
) -> io::Result<Launch> {
    let job = Job::new().map_err(|_| permission_refused(action, "worker-job"))?;
    if started.elapsed() >= deadline {
        return Err(refused(
            action,
            "worker-launch-timeout",
            io::ErrorKind::TimedOut,
        ));
    }
    command
        .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = command.spawn().inspect_err(|_| {
        permission_refused(action, "worker-spawn");
    })?;
    let spawn_delay = started.elapsed();
    if job.contain(&child).is_err() {
        // Without containment the pipe never receives authorization.
        drop(child.stdin.take());
        let _ = child.kill();
        if !finish_exit(&mut child) {
            refused(action, "worker-cleanup", io::ErrorKind::TimedOut);
        }
        return Err(permission_refused(action, "worker-job"));
    }
    Ok(Launch {
        job,
        child,
        spawn_delay,
    })
}

fn supervise(action: &'static str, command: Command, deadline: Duration) -> io::Result<()> {
    supervise_with_launcher(action, command, deadline, launch)
}

fn supervise_with_launcher(
    action: &'static str,
    mut command: Command,
    deadline: Duration,
    launcher: impl FnOnce(&'static str, Command, Instant, Duration) -> io::Result<Launch>
    + Send
    + 'static,
) -> io::Result<()> {
    let started = Instant::now();
    let cutoff = windows::tick_ms().saturating_add(deadline.as_millis() as u64);
    command.env(WORKER_DEADLINE, cutoff.to_string());
    let (offer, receive) = mpsc::sync_channel(0);
    // Windows Job creation or CreateProcess may block. The launching thread owns
    // the child until delivery and never grants ACL work. A late launch is stopped
    // by that thread when it finds the caller has withdrawn its rendezvous.
    thread::Builder::new()
        .name("scopehub-acl-launch".into())
        .spawn(move || {
            if let Err(mpsc::SendError(Ok(late))) =
                offer.send(launcher(action, command, started, deadline))
                && !late.stop_before_grant()
            {
                refused(action, "worker-cleanup", io::ErrorKind::TimedOut);
            }
        })
        .map_err(|_| permission_refused(action, "worker-launch"))?;
    let Launch {
        job,
        mut child,
        spawn_delay,
    } = match receive.recv_timeout(deadline.saturating_sub(started.elapsed())) {
        Ok(result) => result?,
        Err(mpsc::RecvTimeoutError::Timeout) => {
            Progress::default().report(action, started.elapsed(), None);
            return Err(refused(
                action,
                "worker-launch-timeout",
                io::ErrorKind::TimedOut,
            ));
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            return Err(permission_refused(action, "worker-launch"));
        }
    };
    let mut input = child.stdin.take();
    let output = child.stdout.take().expect("piped worker stdout");
    let progress = Arc::new(Mutex::new(Progress {
        ready: None,
        phase: None,
    }));
    let worker_progress = Arc::clone(&progress);
    let reader = thread::spawn(move || {
        for line in BufReader::new(output).lines().map_while(Result::ok) {
            let Some(phase) = line.strip_prefix(PHASE_PREFIX) else {
                continue;
            };
            if let Some(&phase) = PHASES.iter().find(|kind| kind.name() == phase) {
                worker_progress
                    .lock()
                    .expect("workspace ACL progress")
                    .record(phase, started.elapsed());
            }
        }
    });

    let result = loop {
        if started.elapsed() >= deadline {
            break Err(refused(action, "worker-timeout", io::ErrorKind::TimedOut));
        }
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => {}
            Err(error) => {
                refused(action, "worker-wait", error.kind());
                break Err(error);
            }
        }
        if input.is_some()
            && progress
                .lock()
                .expect("workspace ACL progress")
                .ready
                .is_some()
        {
            if started.elapsed() >= deadline {
                continue;
            }
            if input
                .take()
                .expect("piped worker stdin")
                .write_all(b"1")
                .is_err()
            {
                break Err(permission_refused(action, "worker-control"));
            }
        }
        thread::sleep(Duration::from_millis(5));
    };
    let authorized = input.is_none();
    drop(input);
    drop(job); // The job is non-inheritable and contains only this invocation.
    let result = result.map_err(|error| {
        if finish_exit(&mut child) {
            error
        } else {
            refused(action, "worker-cleanup", io::ErrorKind::TimedOut);
            io::Error::new(error.kind(), format!("{error}; worker exit unconfirmed"))
        }
    });
    if result.as_ref().is_ok_and(|status| status.success()) {
        // The actual worker has no descendants. Do not wait beyond the
        // admission budget if its progress pipe cannot be confirmed closed.
        while !reader.is_finished() && started.elapsed() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        if !reader.is_finished() || reader.join().is_err() {
            return Err(refused(action, "worker-control", io::ErrorKind::TimedOut));
        }
    }
    let progress = progress.lock().expect("workspace ACL progress");
    let success = result.as_ref().is_ok_and(|status| status.success());
    if !success || env::var_os("SCOPEHUB_WORKSPACE_ACL_PHASES").as_deref() == Some(OsStr::new("1"))
    {
        progress.report(action, started.elapsed(), Some(spawn_delay));
    }
    let status = result?;
    if !status.success() {
        let stage = refusal_stage(status.code());
        let kind = if status.code() == Some(36) {
            io::ErrorKind::TimedOut
        } else {
            io::ErrorKind::PermissionDenied
        };
        return Err(refused(action, stage, kind));
    }
    if started.elapsed() >= deadline {
        return Err(refused(action, "worker-timeout", io::ErrorKind::TimedOut));
    }
    if !authorized || !matches!(progress.phase, Some((Phase::Complete, _))) {
        return Err(permission_refused(action, "worker-control"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn profile_owner_fingerprint_preserves_the_existing_windows_format() {
        assert_eq!(
            super::profile_owner_fingerprint("S-1-5-21-101-202-303-404"),
            "6334a64abfec3fba17ca5fa5ac01187f27d09f7be5d478ab0e3ff0aba982084e"
        );
    }
    use super::*;

    const PROBE: &str = "SCOPEHUB_ACL_STALL_PROBE";

    #[test]
    fn held_worker() {
        let Some(marker) = env::var_os(PROBE).map(PathBuf::from) else {
            return;
        };
        println!("{PHASE_PREFIX}ready");
        io::stdout().flush().unwrap();
        let mut permit = [0];
        io::stdin().read_exact(&mut permit).unwrap();
        assert_eq!(permit, *b"1");
        std::fs::write(&marker, b"granted").unwrap();
        let until = Instant::now() + Duration::from_secs(15);
        while Instant::now() < until {
            if marker.with_extension("release").exists() {
                std::fs::write(marker.with_extension("late"), b"late write").unwrap();
                return;
            }
            thread::sleep(Duration::from_millis(5));
        }
    }

    #[test]
    fn timed_out_worker_has_exited_before_control_returns() {
        use std::os::windows::io::AsHandle;

        let fixture = env::temp_dir().join(format!("scopehub-acl-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&fixture).unwrap();
        struct Cleanup(Option<PathBuf>);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                if let Some(path) = &self.0 {
                    let _ = std::fs::remove_dir_all(path);
                }
            }
        }
        let mut cleanup = Cleanup(Some(fixture.clone()));
        let marker = fixture.join("worker");
        let mut command = Command::new(env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "workspace_acl::tests::held_worker",
                "--nocapture",
                "--quiet",
            ])
            .env(PROBE, &marker);
        let (observe, worker_handle) = mpsc::sync_channel(1);
        let error = supervise_with_launcher(
            "prepare",
            command,
            Duration::from_secs(2),
            move |action, command, started, deadline| {
                let pending = launch(action, command, started, deadline)?;
                let worker = pending.child.as_handle().try_clone_to_owned()?;
                observe.send(worker).expect("retain exact worker handle");
                Ok(pending)
            },
        )
        .unwrap_err();
        let worker = worker_handle.try_recv().expect("observe launched worker");
        let exited = wait_for_exit(&worker, 0);
        let detail = error.to_string();
        if !exited || detail.contains("exit unconfirmed") {
            cleanup.0 = None;
        }
        assert!(exited, "worker was still running when control returned");
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(detail, "worker-timeout");
        assert_eq!(std::fs::read(&marker).unwrap(), b"granted");
        std::fs::write(marker.with_extension("release"), b"release after exit").unwrap();
        assert!(!marker.with_extension("late").exists());
    }

    #[test]
    fn cold_launch_cannot_grant_after_the_caller_withdraws() {
        use std::os::windows::io::AsHandle;

        let fixture = env::temp_dir().join(format!("scopehub-acl-cold-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&fixture).unwrap();
        let marker = fixture.join("worker");
        let mut command = Command::new(env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "workspace_acl::tests::held_worker",
                "--nocapture",
                "--quiet",
            ])
            .env(PROBE, &marker);
        let (opened, watch) = mpsc::channel();
        let (release, held) = mpsc::channel();
        let caller = thread::spawn(move || {
            supervise_with_launcher(
                "prepare",
                command,
                Duration::from_secs(2),
                move |action, command, started, deadline| {
                    let pending = launch(action, command, started, deadline)?;
                    let worker = pending.child.as_handle().try_clone_to_owned()?;
                    let _ = opened.send(worker);
                    let _ = held.recv_timeout(Duration::from_secs(5));
                    Ok(pending)
                },
            )
        });
        let worker = watch.recv_timeout(Duration::from_secs(5)).unwrap();
        let error = caller.join().unwrap().unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(error.to_string(), "worker-launch-timeout");
        assert!(
            !marker.exists(),
            "ACL worker gained authority before handoff"
        );
        release.send(()).unwrap();
        assert!(
            wait_for_exit(&worker, 2_000),
            "late ungranted worker did not exit"
        );
        assert!(
            !marker.exists(),
            "ACL worker gained authority after the deadline"
        );
        std::fs::remove_dir_all(fixture).unwrap();
    }
}
