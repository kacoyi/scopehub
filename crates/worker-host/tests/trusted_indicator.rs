use std::{
    fs, future, io,
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};

use scopehub_domain::protocol::{ErrorCode, IndicatorCompute11};
use scopehub_worker_host::{
    indicator::{ENGINE, compute},
    market::WorkerConfig,
    max_message_bytes,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::Command,
    sync::oneshot,
    time::{sleep, timeout},
};
use uuid::Uuid;

#[cfg(any(target_os = "linux", windows))]
mod deadline;

const SOURCE: &str = r#"def compute(bars, params):
    print("synthetic stdout")
    assert type(params['gain']) is float
    prior, current, last = bars
    assert str(last['close']) == '-0.0'
    facteur = params['gain']
    return {
        'series': [{'id': 'écart', 'pane': 'price', 'type': 'line',
                    'style': {'color': '#123456', 'width': 1},
                    'points': [{'time_ms': current['time_ms'],
                                'value': (current['close'] - prior['close']) * facteur},
                               {'time_ms': last['time_ms'], 'value': None}]},
                   {'id': 'signed-zero', 'pane': 'price', 'type': 'line',
                    'style': {'color': '#123456', 'width': 1},
                    'points': [{'time_ms': last['time_ms'], 'value': last['close']}]}],
        'markers': [], 'regions': [], 'diagnostics': [],
    }
"#;
const LOOP: &str = "def compute(bars, params):\n    while True:\n        1 + 1\n";
const SPAWNING_LOOP: &str = r#"def compute(bars, params):
    from indicator_sleep_fixture import start_sleeping
    start_sleeping()
    while True:
        1 + 1
"#;
const THROWING: &str =
    "def compute(bars, params):\n    raise ValueError('synthetic private exception')\n";
const BYTE_BUDGET: &str = r#"def compute(bars, params):
    points = [{'time_ms': 200, 'value': 0.12345678909876543}] * 7000
    series = [{'id': str(i), 'pane': 'price', 'type': 'line',
               'style': {'color': '#123456', 'width': 1}, 'points': points}
              for i in range(16)]
    return {'series': series, 'markers': [], 'regions': [], 'diagnostics': []}
"#;

#[cfg(any(target_os = "linux", windows))]
const OBSERVE_MEMORY_LIMIT: &str = r#"    import sys
    if sys.platform == 'linux':
        import resource
        assert resource.getrlimit(resource.RLIMIT_AS) == (512 << 20, 512 << 20)
    else:
        import ctypes
        from ctypes import wintypes
        from scripts.windows_job import ExtendedLimits

        # A null handle queries this interpreter's immediate job, including
        # inside the outer hosted verification job.
        query = ctypes.WinDLL('kernel32', use_last_error=True).QueryInformationJobObject
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.c_void_p]
        query.restype = wintypes.BOOL
        limits = ExtendedLimits()
        assert query(None, 9, ctypes.byref(limits), ctypes.sizeof(limits), None)
        assert limits.basic.flags & 0x0200
        assert limits.job_memory == 512 << 20
    assert len(bytearray(1 << 20)) == 1 << 20"#;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}

fn config() -> WorkerConfig {
    let mut config = WorkerConfig::development(&repo());
    config.deadline = Duration::from_secs(10);
    config
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn request(source: &str) -> IndicatorCompute11 {
    let params = json!({"gain": 2.0});
    let bar = |time, close| {
        json!({"time_ms": time, "trading_day": "2026-02-09", "open": close, "high": close,
               "low": close, "close": close, "volume": null, "is_closed": true})
    };
    serde_json::from_value(json!({
        "protocol_version": "1.1", "message_id": Uuid::new_v4().to_string(), "timestamp_ms": 301,
        "type": "request", "operation": "indicator.compute",
        "request_id": Uuid::new_v4().to_string(), "task_id": Uuid::new_v4().to_string(), "draft_id": "synthetic-draft",
        "source": {"language": "python", "source": source, "params": {}},
        "resolved_params": params, "code_hash": sha(source.as_bytes()),
        "params_hash": sha(&serde_json::to_vec(&params).unwrap()),
        "snapshot": {
            "dataset_id": Uuid::new_v4().to_string(), "dataset_revision": "synthetic-revision",
            "from_ms": 200, "to_ms": 400, "warmup_bars": 1, "as_of_ms": 301,
            "content_hash": "ab".repeat(32),
            "series": {"instrument_id": "synthetic", "provider_id": "fixture",
                       "timeframe": "1m", "price_adjustment": "unadjusted",
                       "session_scope": "regular", "calendar_id": "fixture",
                       "quantity_unit": "shares"}
        },
        "input": {"mode": "inline", "bars": [bar(100, 7.25), bar(200, 9.5), bar(300, -0.0)]}
    }))
    .unwrap()
}

#[tokio::test]
async fn approved_two_bar_example_has_independent_expected_values() {
    let code = include_str!("../../../tests/fixtures/indicators/two-bar-mean.py");
    let mut frame = serde_json::to_value(request(code)).unwrap();
    frame["resolved_params"] = json!({"divisor": 2});
    frame["params_hash"] = json!(sha(b"{\"divisor\":2}"));
    frame["snapshot"]["warmup_bars"] = json!(0);
    let bars = frame["input"]["bars"].as_array_mut().unwrap();
    bars.remove(0);
    for (bar, close) in bars.iter_mut().zip([10.0, 14.0]) {
        for field in ["open", "high", "low", "close"] {
            bar[field] = json!(close);
        }
    }
    let request = serde_json::from_value(frame).unwrap();
    let result = compute(&config(), &request, future::pending())
        .await
        .unwrap();
    let output = serde_json::to_value(result).unwrap();
    assert_eq!(
        output["artifact"]["result"]["series"][0]["points"],
        json!([{"time_ms": 200, "value": null}, {"time_ms": 300, "value": 12.0}]),
    );
}

fn assert_result(reply: &Value, request: &IndicatorCompute11) {
    assert_eq!(reply["request_id"], request.request_id);
    assert_eq!(reply["task_id"], request.task_id);
    assert_eq!(reply["draft_id"], request.draft_id);
    assert_eq!(reply["snapshot_hash"], request.snapshot.content_hash);
    assert_eq!(reply["artifact"]["storage"], "inline");
    let expected = json!({
        "series": [{"id": "écart", "pane": "price", "type": "line",
                    "style": {"color": "#123456", "width": 1},
                    "points": [{"time_ms": 200, "value": 4.5}, {"time_ms": 300, "value": null}]},
                   {"id": "signed-zero", "pane": "price", "type": "line",
                    "style": {"color": "#123456", "width": 1},
                    "points": [{"time_ms": 300, "value": -0.0}]}],
        "markers": [], "regions": [], "diagnostics": [],
        "provenance": {"code_hash": request.code_hash, "params_hash": request.params_hash,
                       "dataset_id": request.snapshot.dataset_id,
                       "dataset_revision": "synthetic-revision",
                       "engine_version": "scopehub-trusted-python-1"}
    });
    assert_eq!(reply["artifact"]["result"], expected);
    assert_eq!(
        reply["artifact"]["result_hash"],
        sha(&serde_json::to_vec(&expected).unwrap())
    );
}

#[tokio::test]
async fn executes_exact_utf8_code_on_ordered_bars_and_hashes_the_typed_result() {
    let request = request(SOURCE);
    let reply = compute(&config(), &request, future::pending())
        .await
        .unwrap();
    assert_result(&serde_json::to_value(reply).unwrap(), &request);
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn approved_interpreter_observes_the_configured_memory_limit() {
    let source = SOURCE.replace("    print(\"synthetic stdout\")", OBSERVE_MEMORY_LIMIT);
    let request = request(&source);
    let reply = compute(&config(), &request, future::pending())
        .await
        .unwrap();
    assert_result(&serde_json::to_value(reply).unwrap(), &request);
}

#[tokio::test]
async fn classifies_sanitized_exceptions_invalid_values_and_excessive_results() {
    let cases = [
        (THROWING.to_owned(), ErrorCode::INTERNALERROR),
        (
            SOURCE.replace(
                "(current['close'] - prior['close']) * facteur",
                "float('nan')",
            ),
            ErrorCode::OUTPUTINVALID,
        ),
        (
            SOURCE.replace("(current['close'] - prior['close']) * facteur", "True"),
            ErrorCode::OUTPUTINVALID,
        ),
        (
            SOURCE.replace("current['time_ms']", "prior['time_ms']"),
            ErrorCode::OUTPUTINVALID,
        ),
        (BYTE_BUDGET.into(), ErrorCode::RESOURCELIMIT),
    ];
    for (source, expected) in cases {
        let failure = compute(&config(), &request(&source), future::pending())
            .await
            .unwrap_err();
        assert_eq!(failure.code, expected);
        assert!(failure.details.is_empty());
        assert!(!failure.retryable);
        assert!(!failure.message.contains("synthetic private exception"));
    }
}

struct Fixture {
    directory: PathBuf,
    config: WorkerConfig,
}

impl Fixture {
    fn new(script: &str) -> Self {
        let mut config = config();
        let directory = std::env::temp_dir().join(format!("ScopeHub worker {}", Uuid::new_v4()));
        config.root = directory.clone();
        let module = directory.join("python/worker_runtime/indicator.py");
        fs::create_dir_all(module.parent().unwrap()).unwrap();
        fs::write(module, script).unwrap();
        for path in [
            "python/worker_runtime/indicator_guard.py",
            "scripts/process_tree.py",
            "scripts/windows_job.py",
        ] {
            let destination = directory.join(path);
            fs::create_dir_all(destination.parent().unwrap()).unwrap();
            fs::copy(repo().join(path), destination).unwrap();
        }
        Self { directory, config }
    }

    fn observe_real_worker() -> Self {
        // The fixture records an entry witness outside the scratch directory.
        // The actual selected function only performs synthetic arithmetic.
        let repository = serde_json::to_string(repo().to_str().unwrap()).unwrap();
        let fixture = Self::new(&format!(
            r#"import json, os, pathlib, runpy, sys, worker_runtime
repo = pathlib.Path({repository})
sys.path[:0] = [str(repo), str(repo / 'python')]
worker_runtime.__path__ = [str(repo / 'python/worker_runtime')]
worker = runpy.run_path(str(repo / 'python/worker_runtime/indicator.py'))
serve = worker['serve']
evaluate = serve.__globals__['evaluate']
def observe(*args):
    path = pathlib.Path(__file__).parents[2] / 'entered'
    facts = [os.getpid(), os.getppid(), os.getcwd()]
    with path.open('a', encoding='utf-8') as witness:
        witness.write(json.dumps(facts) + '\n')
    return evaluate(*args)
serve.__globals__['evaluate'] = observe
os._exit(worker['main']())
"#
        ));
        fs::write(
            fixture.directory.join("python/indicator_sleep_fixture.py"),
            r#"from multiprocessing import get_context
from pathlib import Path
from time import sleep

def sleep_with_witness(ready):
    ready.set()
    sleep(60)

def start_sleeping():
    context = get_context('spawn')
    ready = context.Event()
    process = context.Process(target=sleep_with_witness, args=(ready,))
    process.start()
    assert ready.wait(6)
    Path(__file__).parents[1].joinpath('spawned').write_text(str(process.pid), encoding='ascii')
"#,
        )
        .unwrap();
        fixture
    }

    #[cfg(any(target_os = "linux", windows))]
    fn use_test_memory_limit(&self) {
        let guard = self
            .directory
            .join("python/worker_runtime/indicator_guard.py");
        fs::rename(&guard, guard.with_file_name("indicator_guard_original.py")).unwrap();
        fs::write(
            guard,
            r#"import runpy
import sys
from pathlib import Path

assert int(sys.argv[2]) == 512 << 20
sys.argv[2] = str(64 << 20)
runpy.run_path(str(Path(__file__).with_name('indicator_guard_original.py')), run_name='__main__')
"#,
        )
        .unwrap();
    }

    async fn witness(&self) -> (u32, u32, PathBuf) {
        timeout(Duration::from_secs(8), async {
            loop {
                if let Ok(text) = fs::read_to_string(self.directory.join("entered"))
                    && let Some(line) = text.lines().next()
                    && let Ok(witness) = serde_json::from_str(line)
                {
                    return witness;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("selected code must reach the real interpreter")
    }

    fn assert_reaped(&self, pid: u32, parent: u32, scratch: PathBuf) {
        // Capture the directory state before querying the processes so those
        // queries cannot give an outstanding deletion extra time to finish.
        let scratch_state = fs::symlink_metadata(&scratch);
        let interpreter_alive = process_exists(pid);
        let supervisor_alive = process_exists(parent);
        eprintln!(
            "synthetic worker cleanup: interpreter_alive={interpreter_alive}, \
             supervisor_alive={supervisor_alive}, scratch={scratch_state:?}"
        );
        assert_ne!(
            parent,
            std::process::id(),
            "the supervisor owns the interpreter"
        );
        assert!(
            matches!(&scratch_state, Err(error) if error.kind() == io::ErrorKind::NotFound),
            "the scratch directory must be removed: {scratch_state:?}"
        );
        assert_eq!(
            fs::read_to_string(self.directory.join("entered"))
                .unwrap()
                .lines()
                .count(),
            1,
            "an interrupted run must never invoke the code again"
        );
        assert_ne!(pid, parent);
        assert!(!interpreter_alive, "interpreter {pid} survived");
        assert!(!supervisor_alive, "supervisor {parent} survived");
    }

    async fn sleeping_child(&self) -> u32 {
        timeout(Duration::from_secs(8), async {
            loop {
                if let Ok(text) = fs::read_to_string(self.directory.join("spawned"))
                    && let Ok(pid) = text.parse()
                {
                    return pid;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the spawned child must have entered its synthetic calculation")
    }
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn allocation_over_the_test_memory_limit_fails_and_reaps_the_worker() {
    let fixture = Fixture::observe_real_worker();
    fixture.use_test_memory_limit();
    let source = SOURCE.replace(
        "    print(\"synthetic stdout\")",
        "    assert len(bytearray(1 << 20)) == 1 << 20\n    budget = bytearray(72 << 20)",
    );
    let failure = compute(&fixture.config, &request(&source), future::pending())
        .await
        .unwrap_err();
    assert_eq!(failure.code, ErrorCode::RESOURCELIMIT);
    assert!(!failure.retryable && failure.details.is_empty());
    let (pid, parent, scratch) = fixture.witness().await;
    fixture.assert_reaped(pid, parent, scratch);
}

fn process_exists(pid: u32) -> bool {
    #[cfg(target_os = "linux")]
    {
        PathBuf::from(format!("/proc/{pid}")).exists()
    }
    #[cfg(windows)]
    {
        let status = std::process::Command::new("tasklist.exe")
            .args(["/NH", "/FO", "CSV", "/FI", &format!("PID eq {pid}")])
            .output()
            .unwrap();
        assert!(status.status.success());
        String::from_utf8_lossy(&status.stdout).contains(&format!(",\"{pid}\","))
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        let _ = pid;
        false
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.directory).unwrap();
    }
}

#[tokio::test]
async fn cancels_running_code_reaps_its_interpreter_and_delivers_no_artifact() {
    let fixture = Fixture::observe_real_worker();
    let request = request(SPAWNING_LOOP);
    let (cancel, cancelled) = oneshot::channel();
    let operation = compute(&fixture.config, &request, async {
        cancelled.await.unwrap();
    });
    tokio::pin!(operation);
    let ((pid, parent, scratch), sleeping) = tokio::select! {
        result = &mut operation => panic!("computation ended before cancellation: {result:?}"),
        witness = async { tokio::join!(fixture.witness(), fixture.sleeping_child()) } => witness,
    };
    assert!(process_exists(sleeping));
    cancel.send(()).unwrap();
    let failure = timeout(Duration::from_secs(2), operation)
        .await
        .unwrap()
        .unwrap_err();
    assert_eq!(failure.code, ErrorCode::CANCELLED);
    fixture.assert_reaped(pid, parent, scratch);
    assert!(
        !process_exists(sleeping),
        "the sleeping child survived cancellation"
    );
}

#[tokio::test]
async fn succeeds_only_after_reaping_an_ordinary_multiprocessing_child() {
    let fixture = Fixture::observe_real_worker();
    let source = SOURCE.replace(
        "    print(\"synthetic stdout\")",
        "    from indicator_sleep_fixture import start_sleeping\n    start_sleeping()",
    );
    let request = request(&source);
    let reply = compute(&fixture.config, &request, future::pending())
        .await
        .unwrap();
    assert_result(&serde_json::to_value(reply).unwrap(), &request);
    let (pid, parent, scratch) = fixture.witness().await;
    let sleeping = fixture.sleeping_child().await;
    fixture.assert_reaped(pid, parent, scratch);
    assert!(
        !process_exists(sleeping),
        "the sleeping child survived success"
    );
}

#[tokio::test]
async fn dropping_the_native_future_still_reaps_the_running_interpreter() {
    let fixture = Fixture::observe_real_worker();
    let request = request(LOOP);
    let mut operation = Box::pin(compute(&fixture.config, &request, future::pending()));
    let (pid, parent, scratch) = tokio::select! {
        result = &mut operation => panic!("computation ended before drop: {result:?}"),
        witness = fixture.witness() => witness,
    };
    drop(operation);
    timeout(Duration::from_secs(2), async {
        while scratch.exists() {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    fixture.assert_reaped(pid, parent, scratch);
}

const SIMULATED_WIRE: &str = r#"import hashlib, json, sys, uuid
envelope = dict(protocol_version='1.1', message_id=str(uuid.uuid4()), timestamp_ms=302)
hello = dict(envelope, type='hello', role='indicator', worker_version='scopehub-trusted-python-1',
             supported_protocols=['1.0', '1.1'], capabilities=['indicator.compute'])
print(json.dumps(hello), flush=True)
req = json.load(sys.stdin)
provenance = dict(code_hash=req['code_hash'], params_hash=req['params_hash'],
                  dataset_id=req['snapshot']['dataset_id'], dataset_revision=req['snapshot']['dataset_revision'],
                  engine_version=hello['worker_version'])
result = dict(series=[], markers=[], regions=[], diagnostics=[], provenance=provenance)
body = json.dumps(result)
response = dict(envelope, type='response', operation='indicator.compute', request_id=req['request_id'],
                task_id=req['task_id'], draft_id=req['draft_id'], snapshot_hash=req['snapshot']['content_hash'],
                artifact=dict(storage='inline', result_hash=hashlib.sha256(body.encode()).hexdigest(),
                              result=result))
"#;

#[tokio::test]
async fn rejects_old_mismatched_trailing_and_oversized_wire_responses() {
    let request = request(SOURCE);
    for (suffix, expected) in [
        (
            "print(json.dumps(dict(envelope, protocol_version='1.0', type='response', request_id=req['request_id'], ok=False, result=None, error={'code':'CANCELLED','message':'old','retryable':False,'details':{}})))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "response['request_id'] = str(uuid.uuid4())\nprint(json.dumps(response))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "response['task_id'] = str(uuid.uuid4())\nprint(json.dumps(response))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "response['draft_id'] = 'older-draft'\nprint(json.dumps(response))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "response['snapshot_hash'] = '0' * 64\nprint(json.dumps(response))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "response['artifact']['result_hash'] = '0' * 64\nprint(json.dumps(response))\n",
            ErrorCode::OUTPUTINVALID,
        ),
        (
            "print(json.dumps(response))\nprint('synthetic extra frame')\n",
            ErrorCode::OUTPUTINVALID,
        ),
        ("print('x' * 4194304)\n", ErrorCode::RESOURCELIMIT),
    ] {
        let fixture = Fixture::new(&format!("{SIMULATED_WIRE}{suffix}"));
        let failure = compute(&fixture.config, &request, future::pending())
            .await
            .unwrap_err();
        assert_eq!(failure.code, expected, "{suffix}");
    }
}

#[tokio::test]
async fn cancellation_discards_a_correlated_result_from_a_child_that_has_not_exited() {
    let fixture = Fixture::new(&format!(
        "{SIMULATED_WIRE}{}",
        r#"import os, pathlib
response['message_id'] = str(uuid.uuid4())
print(json.dumps(response), flush=True)
pathlib.Path('pending-output').write_text('synthetic scratch data', encoding='utf-8')
path = pathlib.Path(__file__).parents[2] / 'entered'
path.write_text(json.dumps([os.getpid(), os.getppid(), os.getcwd()]), encoding='utf-8')
while True:
    1 + 1
"#
    ));
    let request = request(SOURCE);
    let (cancel, cancelled) = oneshot::channel();
    let operation = compute(&fixture.config, &request, async {
        cancelled.await.unwrap();
    });
    tokio::pin!(operation);
    let (pid, parent, scratch) = tokio::select! {
        result = &mut operation => panic!("result escaped before worker exit: {result:?}"),
        witness = fixture.witness() => witness,
    };
    assert_eq!(
        fs::read_to_string(scratch.join("pending-output")).unwrap(),
        "synthetic scratch data"
    );
    let started = Instant::now();
    cancel.send(()).unwrap();
    let failure = operation.await.unwrap_err();
    eprintln!(
        "synthetic cancellation returned after {:?}",
        started.elapsed()
    );
    assert_eq!(failure.code, ErrorCode::CANCELLED);
    assert!(!failure.retryable && failure.details.is_empty());
    fixture.assert_reaped(pid, parent, scratch);
}

#[tokio::test]
async fn cancelling_one_live_computation_preserves_adjacent_scratch_and_result() {
    let cancelled = Fixture::observe_real_worker();
    let adjacent = Fixture::observe_real_worker();
    let cancelled_request = request(LOOP);
    let adjacent_source = SOURCE.replace(
        "    print(\"synthetic stdout\")",
        r#"    from pathlib import Path
    from time import sleep
    Path('pending-output').write_text('adjacent synthetic output', encoding='utf-8')
    while not Path('allow-result').exists():
        sleep(0.01)"#,
    );
    let adjacent_request = request(&adjacent_source);
    let (cancel, cancellation) = oneshot::channel();
    let first = compute(&cancelled.config, &cancelled_request, async {
        cancellation.await.unwrap();
    });
    let second = compute(&adjacent.config, &adjacent_request, future::pending());
    tokio::pin!(first, second);

    let ((pid, parent, scratch), (adjacent_pid, adjacent_parent, adjacent_scratch)) = tokio::select! {
        result = &mut first => panic!("first computation ended before cancellation: {result:?}"),
        result = &mut second => panic!("adjacent computation ended before release: {result:?}"),
        witnesses = async { tokio::join!(cancelled.witness(), adjacent.witness()) } => witnesses,
    };
    assert_ne!(scratch, adjacent_scratch);
    let pending_output = adjacent_scratch.join("pending-output");
    timeout(Duration::from_secs(2), async {
        while !pending_output.exists() {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the adjacent computation must write its own scratch output");

    cancel.send(()).unwrap();
    let failure = first.await.unwrap_err();
    assert_eq!(failure.code, ErrorCode::CANCELLED);
    cancelled.assert_reaped(pid, parent, scratch);
    assert_eq!(
        fs::read_to_string(pending_output).unwrap(),
        "adjacent synthetic output"
    );
    fs::write(adjacent_scratch.join("allow-result"), b"").unwrap();

    let reply = second.await.unwrap();
    assert_result(&serde_json::to_value(reply).unwrap(), &adjacent_request);
    adjacent.assert_reaped(adjacent_pid, adjacent_parent, adjacent_scratch);
}

async fn carrier(request: &IndicatorCompute11, cancel: bool) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_scopehub-indicator-child"))
        .arg("--root")
        .arg(repo())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = child
        .stdout
        .take()
        .unwrap()
        .take((max_message_bytes() + 1) as u64);
    let mut frame = serde_json::to_vec(request).unwrap();
    frame.push(b'\n');
    input.write_all(&frame).await.unwrap();
    if cancel {
        drop(input);
    }
    let mut reply = Vec::new();
    let (bytes, status) = timeout(Duration::from_secs(8), async {
        tokio::join!(output.read_to_end(&mut reply), child.wait())
    })
    .await
    .unwrap();
    assert!(status.unwrap().success());
    assert!(bytes.unwrap() <= max_message_bytes());
    let stderr = child.wait_with_output().await.unwrap().stderr;
    assert!(stderr.is_empty());
    assert_eq!(reply.iter().filter(|byte| **byte == b'\n').count(), 1);
    serde_json::from_slice(&reply).unwrap()
}

#[tokio::test]
async fn private_cli_delivers_one_terminal_result_with_parent_input_open() {
    let request = request(SOURCE);
    assert_result(&carrier(&request, false).await, &request);
}

#[tokio::test]
async fn private_cli_rejects_an_exact_source_change_after_hashing() {
    let mut request = request(&format!("{SOURCE}\n"));
    request.code_hash = sha(SOURCE.as_bytes());
    let reply = carrier(&request, false).await;
    assert_eq!(reply["error"]["code"], "INVALID_ARGUMENT");
    assert_eq!(reply.as_object().unwrap().len(), 1);
    assert!(reply.get("artifact").is_none());
}

#[tokio::test]
async fn private_cli_eof_returns_only_cancellation_without_a_persistable_result() {
    let reply = carrier(&request(LOOP), true).await;
    assert_eq!(reply["error"]["code"], "CANCELLED");
    assert_eq!(reply.as_object().unwrap().len(), 1);
    assert!(reply.get("artifact").is_none());
}

#[tokio::test]
async fn rejects_tampered_code_and_params_without_launching_the_worker() {
    let fixture = Fixture::observe_real_worker();
    for change in ["code", "params"] {
        let mut request = request(SOURCE);
        if change == "code" {
            request.code_hash = "0".repeat(64);
        } else {
            request.resolved_params["gain"] = json!(8.0);
        }
        let failure = compute(&fixture.config, &request, future::pending())
            .await
            .unwrap_err();
        assert_eq!(failure.code, ErrorCode::INVALIDARGUMENT);
    }
    let mut request = request(SOURCE);
    request
        .resolved_params
        .insert("oversized".into(), json!("x".repeat(max_message_bytes())));
    let failure = compute(&fixture.config, &request, future::pending())
        .await
        .unwrap_err();
    assert_eq!(failure.code, ErrorCode::RESOURCELIMIT);
    assert!(!fixture.directory.join("entered").exists());
    assert_eq!(ENGINE, "scopehub-trusted-python-1");
}
