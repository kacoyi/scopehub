"""Identify operational source and configuration shared by worktree checks."""

from pathlib import PurePosixPath


SOURCE_ROOTS = ('scripts', 'apps', 'crates', 'packages', 'python')
ROOT_CONFIGS = ('.gitmodules', 'Cargo.toml', 'package.json', 'pnpm-workspace.yaml', 'pyproject.toml',
                'rust-toolchain.toml')
OPERATIONAL_PATHS = (*SOURCE_ROOTS, '.github', '.cargo', *ROOT_CONFIGS)
SOURCE_SUFFIXES = {
    '.py', '.sh', '.bash', '.fish', '.nu', '.ps1', '.cmd', '.bat',
    '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts', '.html', '.rs', '.sql',
}
SOURCE_CONFIGS = {'Cargo.toml', 'package.json', 'pyproject.toml', 'tauri.conf.json', 'tsconfig.json'}
NON_OPERATIONAL_DIRS = {
    '.cache', '.pnpm', '.reports', '.venv', '__pycache__', 'dist', 'fixtures', 'generated', 'node_modules',
    'target', 'vendor',
}


def is_operational_path(relative):
    path = PurePosixPath(relative)
    if not path.parts or NON_OPERATIONAL_DIRS.intersection(path.parts[:-1]):
        return False
    if relative in ROOT_CONFIGS or relative in {'.cargo/config', '.cargo/config.toml'}:
        return True
    root = path.parts[0]
    if root == '.github':
        return path.suffix in SOURCE_SUFFIXES | {'.yml', '.yaml'}
    return root in SOURCE_ROOTS and (path.suffix in SOURCE_SUFFIXES or path.name in SOURCE_CONFIGS)
