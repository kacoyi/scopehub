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

# Only delay the explicitly selected synthetic call in this disposable runner.
instrument("crates/persistence/src/storage.rs", [
    ('.env("SCOPEHUB_STORAGE_ACTION", action)', '''.env("SCOPEHUB_STORAGE_ACTION", action)
        .env("SCOPEHUB_DIAG_DELAY", if action == "prepare" && (
            (std::env::var("SCOPEHUB_DIAG_MODE").as_deref() == Ok("fixture")
            && thread::current().name().is_some_and(|n| n.ends_with("desktop_dispatch_restores_replays_and_undoes_with_one_owned_connection") || n.ends_with("copied_workspace_bytes_cannot_enable_a_grant_or_cross_native_profiles"))
            && path.file_name().is_some_and(|n| n.to_string_lossy().starts_with("scopehub-desktop-test-")))
            || (std::env::var("SCOPEHUB_DIAG_MODE").as_deref() == Ok("blocked-application") && path.parent().is_some_and(|p|p.is_file()))
        ) { "1" } else { "0" })''')
])
instrument("crates/persistence/src/storage_acl.ps1", [
    ("function Stop-Storage", '''if ($env:SCOPEHUB_DIAG_DELAY -eq "1") {
    [Console]::Error.WriteLine(("ACL synthetic-delay {0}" -f $PID))
    [Threading.Thread]::Sleep(15000)
}
function Stop-Storage''')
])

fixture = Path("apps/desktop/src-tauri/src/bridge_tests.rs")
candidate = fixture.read_bytes()
original = subprocess.check_output(["git","show","cf1abbc8c5c608da85b486de09f39806978b492a:"+fixture.as_posix()])
exact = ["desktop_dispatch_restores_replays_and_undoes_with_one_owned_connection",
         "copied_workspace_bytes_cannot_enable_a_grant_or_cross_native_profiles"]
summary = []
import re
import psutil
for version in ("baseline", "candidate"):
    if version == "baseline":
        fixture.write_bytes(original)
    else:
        fixture.write_bytes(candidate)
    args = ["cargo", "test", "--locked", "-p", "scopehub-desktop", "--lib",
            "--no-run", "--message-format=json"]
    build = subprocess.run(args, stdout=subprocess.PIPE, check=True)
    binary, = [m["executable"] for line in build.stdout.splitlines() if (m:=json.loads(line)).get("executable")
               and m["profile"]["test"] and "scopehub-desktop" in m["package_id"]]
    cases = [("ordinary", "", 0)]
    cases.extend(("fixture", name, 101 if version == "baseline" else 0) for name in exact)
    if version == "candidate":
        cases.append(("blocked-application","reviewed_mapping_save_recovers_after_workspace_provisioning_refusal",0))
    for mode, name, expected in cases:
        command = [binary,"--nocapture"]
        if name:
            command.extend(["--exact","bridge::tests::"+name])
        log = out / f"{version}-{mode}-{name}.log"
        with log.open("wb") as stream:
            rc = subprocess.run(command, env=dict(os.environ,SCOPEHUB_DIAG_MODE=mode),
                                stdout=stream, stderr=subprocess.STDOUT, timeout=300).returncode
        observed = log.read_text(errors="replace")
        stops = [int(n) for n in re.findall(r"ACL stop (\d+) \d+ powershell-timeout",observed)]
        delayed = re.findall(r"ACL synthetic-delay (\d+)",observed)
        expected_delay = (mode=="fixture" and version=="baseline") or mode=="blocked-application"
        assert len(stops)==len(delayed)==int(expected_delay), (mode,name,stops,delayed)
        assert set(stops)==set(map(int,delayed))
        assert not any(psutil.pid_exists(pid) for pid in stops), stops
        assert rc==expected and (not name or "running 1 test" in observed), (mode,name,rc)
        summary.append(dict(version=version,mode=mode,test=name,rc=rc,killed=stops,
                            results=re.findall(r"test result: [^\n]+",observed)))
        (out / "summary.json").write_text(json.dumps(summary,indent=2))
