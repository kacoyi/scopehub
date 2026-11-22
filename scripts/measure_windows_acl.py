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

build = subprocess.run(["cargo", "test", "--locked", "-p", "scopehub-desktop", "--lib",
                        "--no-run", "--message-format=json"], stdout=subprocess.PIPE, check=True)
binary, = [msg["executable"] for line in build.stdout.splitlines() if (msg := json.loads(line)).get("executable")
           and msg["profile"]["test"] and msg["target"]["src_path"].replace("\\", "/").endswith("/src/lib.rs")
           and "scopehub-desktop" in msg["package_id"]]

results = {"cpu_count": os.cpu_count(), "head": os.environ["GITHUB_SHA"], "variants": []}
python_log = out / "python.log"


def desktop(label, width=None, python=None):
    clock = time.monotonic()
    row = {"label": label, "started_epoch": time.time(), "width": width,
           "python_active": python is not None and python.poll() is None}
    if row["python_active"]:
        lines = python_log.read_bytes().splitlines()
        row["python_phase"] = lines[-1].decode(errors="replace")[:250] if lines else ""
    with (out / (label + ".log")).open("wb") as stream:
        args = [binary, "--nocapture"]
        if width:
            args.append(f"--test-threads={width}")
        row["rc"] = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=300).returncode
    row["seconds"] = round(time.monotonic() - clock, 3)
    results["variants"].append(row)
    (out / "summary.json").write_text(json.dumps(results, indent=2))
    print(row, flush=True)


desktop("alone")
with python_log.open("wb") as log:
    start = time.monotonic()
    python = subprocess.Popen([sys.executable, "-u", "-m", "unittest", "discover", "-s", "tests",
                               "-t", ".", "-p", "test_*.py", "-v", "--durations", "10"],
                              stdout=log, stderr=subprocess.STDOUT)
    try:
        desktop("overlap-early", python=python)
        time.sleep(max(0, 160 - (time.monotonic() - start)))
        desktop("overlap-matched", python=python)
        desktop("overlap-two", width=2, python=python)
        desktop("overlap-late", python=python)
        results["python_rc"] = python.wait(timeout=1000)
    finally:
        if python.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(python.pid)])
            python.wait()
desktop("alone-after")
