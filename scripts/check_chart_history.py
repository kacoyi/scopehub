"""Build and exercise real history reads with a browser transport substitute."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    output = ROOT / '.reports/chart-history/runs' / run_id
    output.mkdir(parents=True)
    env = {**os.environ, 'SCOPEHUB_HISTORY_REPORT_DIR': str(output)}
    commands = [
        ['cargo', 'test', '--locked', '-p', 'scopehub-worker-host', '--test', 'history'],
        ['cargo', 'build', '--locked', '-p', 'scopehub-worker-host', '--example', 'history_bridge'],
        ['pnpm', 'build'],
        ['pnpm', 'exec', 'playwright', 'test', '--config', 'tests/ui/playwright.config.mjs'],
    ]
    outcomes = []
    for command in commands:
        executable = shutil.which(command[0])
        if not executable:
            raise SystemExit(f'Missing required executable: {command[0]}')
        result = subprocess.run([executable, *command[1:]], cwd=ROOT, env=env, check=False)
        outcomes.append({'command': command, 'exit_code': result.returncode})
        if result.returncode:
            break
    report = {
        'status': 'passed' if len(outcomes) == len(commands) and all(item['exit_code'] == 0 for item in outcomes) else 'failed',
        'claim_scope': 'Read-only synthetic history through real Rust/Python and Chromium. Native WebView transport, live updates, automatic reconnect and packaged installation are not tested here.',
        'commands': outcomes,
        'artifacts': [{'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                      for path in sorted(output.rglob('*')) if path.is_file()],
    }
    contents = json.dumps(report, indent=2) + '\n'
    (output / 'summary.json').write_text(contents, encoding='utf-8')
    (ROOT / '.reports/chart-history/latest.json').write_text(contents, encoding='utf-8')
    print(json.dumps({'report': (output / 'summary.json').relative_to(ROOT).as_posix(), **report}), flush=True)
    raise SystemExit(0 if report['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
