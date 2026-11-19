use std::{future::Future, io, time::Duration};

use tokio::process::Child;

use crate::ProtocolError;

trait Process: Send {
    fn start_kill(&mut self) -> io::Result<()>;
    fn wait(&mut self) -> impl Future<Output = io::Result<()>> + Send;
}

impl Process for Child {
    fn start_kill(&mut self) -> io::Result<()> {
        Child::start_kill(self)
    }

    async fn wait(&mut self) -> io::Result<()> {
        Child::wait(self).await.map(|_| ())
    }
}

pub(super) async fn reclaim(child: Child, grace: Duration) {
    reap(child, grace).await;
}

async fn reap(mut process: impl Process, grace: Duration) {
    let mut reported = false;
    loop {
        let kill_failed = process.start_kill().is_err();
        let wait = tokio::time::timeout(grace, process.wait()).await;
        if matches!(wait, Ok(Ok(()))) {
            return;
        }
        if !reported {
            let wait_failed = wait.is_ok();
            eprintln!(
                r#"{{"level":"warning","code":"MARKET_WORKER_REAP_RETRY","kill_failed":{kill_failed},"wait_failed":{wait_failed}}}"#
            );
            reported = true;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

/// The reaper keeps its process and admission permit if its caller's budget
/// expires or the caller is cancelled while awaiting cleanup.
pub(super) async fn wait_for_cleanup(
    cleanup: impl Future<Output = ()> + Send + 'static,
    deadline: Duration,
) -> Result<(), ProtocolError> {
    match tokio::time::timeout(deadline, tokio::spawn(cleanup)).await {
        Ok(Ok(())) => Ok(()),
        Ok(Err(_)) => Err(ProtocolError::Io),
        Err(_) => Err(ProtocolError::Timeout),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    use tokio::sync::oneshot;

    use super::*;

    struct DelayedProcess {
        attempts: Arc<AtomicUsize>,
        can_reap: oneshot::Receiver<()>,
        dropped: Option<oneshot::Sender<()>>,
    }

    impl Process for DelayedProcess {
        fn start_kill(&mut self) -> io::Result<()> {
            if self.attempts.fetch_add(1, Ordering::SeqCst) == 0 {
                return Err(io::ErrorKind::PermissionDenied.into());
            }
            Ok(())
        }

        async fn wait(&mut self) -> io::Result<()> {
            if self.attempts.load(Ordering::SeqCst) == 1 {
                return Err(io::ErrorKind::Interrupted.into());
            }
            (&mut self.can_reap).await.map_err(io::Error::other)
        }
    }

    impl Drop for DelayedProcess {
        fn drop(&mut self) {
            let _ = self.dropped.take().unwrap().send(());
        }
    }

    #[tokio::test]
    async fn delayed_and_failed_cleanup_returns_on_budget_but_keeps_ownership() {
        let (allow_reap, can_reap) = oneshot::channel();
        let (dropped, mut did_drop) = oneshot::channel();
        let attempts = Arc::new(AtomicUsize::new(0));
        let process = DelayedProcess {
            attempts: attempts.clone(),
            can_reap,
            dropped: Some(dropped),
        };
        assert_eq!(
            wait_for_cleanup(
                reap(process, Duration::from_millis(20)),
                Duration::from_millis(150)
            )
            .await,
            Err(ProtocolError::Timeout)
        );
        assert_eq!(
            did_drop.try_recv(),
            Err(oneshot::error::TryRecvError::Empty)
        );
        allow_reap.send(()).unwrap();
        tokio::time::timeout(Duration::from_secs(1), did_drop)
            .await
            .unwrap()
            .unwrap();
        assert!(attempts.load(Ordering::SeqCst) >= 2);
    }
}
