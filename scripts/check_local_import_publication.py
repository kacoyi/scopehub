"""Exercise complete CSV publication through the built UI and Rust desktop state."""

import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = {**os.environ, 'CARGO_BUILD_JOBS': '2'}
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
    return subprocess.run([
        pnpm, 'exec', 'playwright', 'test', '--config', 'tests/ui/local-import.playwright.config.mjs',
        '--grep', '@profile-local-csv',
    ], cwd=ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
