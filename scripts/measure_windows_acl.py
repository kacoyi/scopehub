"""Temporary same-runner comparison for M0-071."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

out = Path(".reports/acl-measure")
out.mkdir(parents=True, exist_ok=True)


def instrument(path, replacements):
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    path.write_text(source, encoding="utf-8")


instrument("crates/persistence/src/storage.rs", [
    (".stderr(Stdio::null())", ".stderr(Stdio::inherit())"),
    ("let deadline = Instant::now() + Duration::from_secs(10);", """
        let started = Instant::now();
        let epoch = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis();
        eprintln!("ACL start {} {} {} {:?}", child.id(), epoch, action, thread::current().name());
        let deadline = started + Duration::from_secs(10);"""),
    ("Ok(Some(status)) if status.success() => return Ok(()),", """Ok(Some(status)) if status.success() => {
        eprintln!("ACL pass {} {}", child.id(), started.elapsed().as_millis()); return Ok(());
    },"""),
    ("let _ = child.kill();", 'eprintln!("ACL stop {} {} {}", child.id(), started.elapsed().as_millis(), stage); let _ = child.kill();'),
])
instrument("crates/persistence/src/storage_acl.ps1", [
    ("$ErrorActionPreference = 'Stop'", """$ErrorActionPreference = 'Stop'
[Console]::Error.WriteLine(("ACL script {0} {1}" -f $PID,[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()))"""),
])
instrument("apps/desktop/src-tauri/src/bridge_tests.rs", [
    ("with_profile_preparation(&SLOT, prepare)", """let wait = std::time::Instant::now();
        with_profile_preparation(&SLOT, || {
            eprintln!("ACL slot {} {:?}", wait.elapsed().as_millis(), std::thread::current().name());
            prepare()
        })"""),
])

import threading
import psutil
import check

subprocess.run(["cargo", "fmt", "--all"], check=True)

# Run the original build window through desktop tests on a fresh hosted VM.
# Only synthetic, process-family aggregate samples and the active Python test are recorded.
out = out.resolve()
env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, [Path.cwd(), Path.cwd() / "python"])),
           SCOPEHUB_PYTHON=sys.executable, SCOPEHUB_RUN_DIR=str(out), PYTHONUNBUFFERED="1",
           RUST_TEST_NOCAPTURE="1")
catalog = json.loads(Path("project/checks.json").read_text())["checks"]
first = next(i for i, item in enumerate(catalog) if item["id"] == "python-contracts-workers")
last = next(i for i, item in enumerate(catalog) if item["id"] == "rust-contracts-workers")
window = catalog[first:last+1]
done = threading.Event()


def sample():
    with (out / "samples.jsonl").open("w") as stream:
        while not done.wait(1):
            names = [p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info["name"]]
            phase = ""
            log = out / "python-contracts-workers.log"
            if log.exists():
                with log.open("rb") as f:
                    f.seek(max(0, f.seek(0,2) - 400))
                    lines = f.read().decode(errors="replace").splitlines()
                    phase = lines[-1][-300:] if lines else ""
            stream.write(json.dumps({"time":time.time(),"cpu":psutil.cpu_percent(),
                "available_mb":psutil.virtual_memory().available//1048576,"python_phase":phase,
                "powershell":names.count("powershell.exe"),"rustc":names.count("rustc.exe"),
                "python":names.count("python.exe")})+"\n")
            stream.flush()


monitor = threading.Thread(target=sample, daemon=True)
monitor.start()
try:
    results = check.run_overlapped(window, env, out)
finally:
    done.set()
    monitor.join()
(out / "summary.json").write_text(json.dumps(results, indent=2))
