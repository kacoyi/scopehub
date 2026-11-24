"""Exercise local CSV workflows through the built UI and Rust desktop state."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks = {
        'publication': ('local-import-publication', '@profile-local-csv'),
        'folder-refresh': ('local-folder-refresh-preview', '@profile-folder-refresh'),
        'folder-import': ('local-folder-import', '@profile-folder-import'),
        'local-api': ('local-api-connection', '@profile-local-api'),
    }
    args = sys.argv[1:]
    if len(args) > 1 or (args and args[0] not in checks):
        raise SystemExit('Choose publication, folder-refresh, folder-import, or local-api.')
    report, tag = checks[args[0] if args else 'publication']
    env = {**os.environ, 'CARGO_BUILD_JOBS': '2'}
    env['SCOPEHUB_LOCAL_IMPORT_REPORT'] = report
    target = (ROOT / env.get('CARGO_TARGET_DIR', 'target/m1-008')).resolve()
    binary = 'local_import_bridge.exe' if os.name == 'nt' else 'local_import_bridge'
    env['SCOPEHUB_LOCAL_IMPORT_BRIDGE'] = str(target / 'debug' / 'examples' / binary)
    cargo = shutil.which('cargo')
    pnpm = shutil.which('pnpm')
    if not cargo or not pnpm:
        raise SystemExit('CSV publication browser check needs cargo and pnpm.')
    build = subprocess.run([
        cargo, 'build', '--locked', '--target-dir', str(target),
        '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
        '--features', 'portable-test-bridge',
    ], cwd=ROOT, env=env, check=False)
    if build.returncode:
        return build.returncode
    print('Chromium uses native Rust profile and operation admission through an IPC bridge.', flush=True)
    return subprocess.run([
        pnpm, 'exec', 'playwright', 'test', '--config', 'tests/ui/local-import.playwright.config.mjs',
        '--grep', tag,
    ], cwd=ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
