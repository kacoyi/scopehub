"""Read-only preflight for the repository's actual pinned build tools."""

import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def tool_version(tool, root=ROOT):
    executable = shutil.which(tool)
    if not executable:
        return 'unavailable'
    try:
        result = subprocess.run([executable, '--version'], cwd=root, timeout=10,
                                capture_output=True, text=True, encoding='utf-8', errors='replace')
    except (OSError, subprocess.SubprocessError):
        return 'unavailable'
    return result.stdout.strip() if result.returncode == 0 else 'unavailable'


def diagnose(root=ROOT):
    package = json.loads((root / 'package.json').read_text())
    rust = tomllib.loads((root / 'rust-toolchain.toml').read_text())['toolchain']['channel']
    node_range = re.fullmatch(r'>=(\d+) <(\d+)', package['engines']['node'])
    if not node_range or int(node_range[2]) != int(node_range[1]) + 1:
        return {'status': 'failed', 'errors': ['Declare one supported Node major in package.json engines.']}
    expected = {'python': (root / '.python-version').read_text().strip(),
                'node': node_range[1], 'pnpm': package['packageManager'].split('@')[1],
                'rustc': rust, 'cargo': rust}
    observed = {'python': platform.python_version(),
                **{name: tool_version(name, root) for name in ['node', 'pnpm', 'rustc', 'cargo', 'uv', 'git']}}
    errors = []
    for name, version in expected.items():
        match = re.search(r'\d+\.\d+\.\d+', observed[name])
        if not match or match[0].split('.')[:len(version.split('.'))] != version.split('.'):
            errors.append(f'{name}: expected {version}, observed {observed[name]}')
    for name in ['uv', 'git']:
        if observed[name] == 'unavailable':
            errors.append(f'{name}: executable unavailable')
    for path in ['Cargo.lock', 'pnpm-lock.yaml', 'uv.lock', 'node_modules/@tauri-apps/cli/config.schema.json']:
        if not (root / path).is_file():
            errors.append(f'Missing build input: {path}')
    return {'status': 'failed' if errors else 'passed', 'tools': observed,
            'python_executable': sys.executable, 'errors': errors,
            'setup': ['uv sync --locked', 'pnpm install --frozen-lockfile'],
            'native_windows_available': platform.system() == 'Windows' and platform.machine().lower() in {'amd64', 'x86_64'}}


if __name__ == '__main__':
    result = diagnose()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
