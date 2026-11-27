use std::{io, path::Path, process::Stdio, time::Duration};

use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin, ChildStdout, Command},
    time::{self, Instant},
};

pub(super) const SETUP_LIMIT: Duration = Duration::from_secs(5);
// Bound the evidence window, including pipe transport and scheduling.
// The native probe itself always uses a zero wait.
const TERMINAL_SAMPLE_LIMIT: Duration = Duration::from_millis(100);

pub(super) struct ProcessObserver {
    child: Child,
    input: ChildStdin,
    output: ChildStdout,
}

#[derive(Debug)]
pub(super) struct TerminalSample {
    pub(super) return_to_reply: Duration,
    pub(super) all_exited: io::Result<bool>,
}

impl ProcessObserver {
    pub(super) async fn start(python: &Path) -> Self {
        let mut child = Command::new(python)
            .args(["-I", "-B", "-u", "-c", include_str!("observe_processes.py")])
            .kill_on_drop(true)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let mut output = child.stdout.take().unwrap();
        let ready = time::timeout(SETUP_LIMIT, output.read_u8())
            .await
            .expect("process observer startup must be bounded")
            .unwrap();
        assert_eq!(ready, b'R', "expected process observer readiness");
        Self {
            child,
            input,
            output,
        }
    }

    pub(super) async fn arm(&mut self, pids: &[u32]) -> io::Result<bool> {
        let roster = pids
            .iter()
            .map(u32::to_string)
            .collect::<Vec<_>>()
            .join(" ");
        self.exchange(format!("{roster}\n").as_bytes()).await
    }

    pub(super) async fn sample(&mut self, returned_at: Instant) -> TerminalSample {
        let result =
            time::timeout_at(returned_at + TERMINAL_SAMPLE_LIMIT, self.exchange(b"?")).await;
        let return_to_reply = returned_at.elapsed();
        let all_exited = if return_to_reply > TERMINAL_SAMPLE_LIMIT {
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "late process observation",
            ))
        } else {
            result.map_err(io::Error::other).and_then(|reply| reply)
        };
        TerminalSample {
            return_to_reply,
            all_exited,
        }
    }

    async fn exchange(&mut self, command: &[u8]) -> io::Result<bool> {
        self.input.write_all(command).await?;
        self.input.flush().await?;
        match self.output.read_u8().await? {
            b'1' => Ok(true),
            b'0' => Ok(false),
            _ => Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid process observation",
            )),
        }
    }

    pub(super) async fn finish(self) {
        let Self {
            mut child,
            input,
            output: _,
        } = self;
        drop(input);
        assert!(
            time::timeout(SETUP_LIMIT, child.wait())
                .await
                .expect("process observer must exit when its pipe closes")
                .unwrap()
                .success()
        );
    }
}
