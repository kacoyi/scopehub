use std::{
    os::windows::ffi::OsStrExt,
    path::{Component, Path, PathBuf, Prefix},
};

use crate::market::WorkerConfig;
use tokio::process::Command;

use super::{development_venv, managed_cpython_home};

pub(super) fn direct_interpreter(config: &WorkerConfig, launcher: &Command) -> Option<Command> {
    let venv = development_venv(&config.python)?;
    let home = managed_cpython_home(venv)?;
    let interpreter = home.join("python.exe").canonicalize().ok()?;
    // A supported external interpreter sits beside its actual Python 3.12 DLL.
    // Preserve the existing one-shot launcher for unknown venv layouts.
    if interpreter.parent() != Some(home.as_path())
        || !interpreter.file_name()?.eq_ignore_ascii_case("python.exe")
        || !interpreter.is_file()
        || !home.join("python312.dll").is_file()
        || interpreter.starts_with(venv.canonicalize().ok()?)
    {
        return None;
    }

    // CPython 3.12's Windows redirector sets this value before starting
    // home/python.exe. Keep the original venv resolution and own its actual child.
    let alias = config
        .python
        .parent()?
        .canonicalize()
        .ok()?
        .join(config.python.file_name()?);
    let resolved_alias = alias.canonicalize().ok()?;
    let mut command = Command::new(compatible_spelling(&interpreter, &interpreter));
    command
        .env_clear()
        .current_dir(&config.root)
        .envs(
            launcher
                .as_std()
                .get_envs()
                .filter_map(|(key, value)| value.map(|value| (key, value))),
        )
        .env(
            "__PYVENV_LAUNCHER__",
            compatible_spelling(&alias, &resolved_alias),
        );
    Some(command)
}

fn compatible_spelling(path: &Path, expected: &Path) -> PathBuf {
    // Simplify only when the ordinary path resolves to the same Windows
    // object. Keep the verbatim form for long or namespace-sensitive names.
    ordinary_spelling(path)
        .filter(|candidate| candidate.canonicalize().ok().as_deref() == Some(expected))
        .unwrap_or_else(|| path.to_owned())
}

fn ordinary_spelling(path: &Path) -> Option<PathBuf> {
    let Component::Prefix(prefix) = path.components().next()? else {
        return None;
    };
    let text = path.to_str()?;
    let candidate = match prefix.kind() {
        Prefix::VerbatimDisk(_) => PathBuf::from(text.strip_prefix(r"\\?\")?),
        Prefix::VerbatimUNC(..) => PathBuf::from(format!(r"\\{}", text.strip_prefix(r"\\?\UNC\")?)),
        _ => return None,
    };
    (candidate.as_os_str().encode_wide().count() < 260).then_some(candidate)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_spelling_supports_short_unc_and_preserves_long_or_device_names() {
        assert_eq!(
            ordinary_spelling(Path::new(r"\\?\C:\ScopeHub\python.exe")).as_deref(),
            Some(Path::new(r"C:\ScopeHub\python.exe"))
        );
        assert_eq!(
            ordinary_spelling(Path::new(r"\\?\UNC\server\share\python.exe")).as_deref(),
            Some(Path::new(r"\\server\share\python.exe"))
        );
        let long = format!(r"\\?\C:\{}\{}\python.exe", "x".repeat(180), "y".repeat(100));
        assert!(ordinary_spelling(Path::new(&long)).is_none());
        assert!(ordinary_spelling(Path::new(r"\\?\Volume{1234}\python.exe")).is_none());
    }
}
