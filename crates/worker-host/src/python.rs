use std::path::{Path, PathBuf};

#[cfg(windows)]
#[path = "local_import_windows.rs"]
mod windows;
#[cfg(windows)]
pub(super) use windows::direct_interpreter;

pub(super) fn development_venv(python: &Path) -> Option<&Path> {
    let scripts = python.parent()?;
    let venv = scripts.parent()?;
    let (bin, name) = if cfg!(windows) {
        ("Scripts", "python.exe")
    } else {
        ("bin", "python")
    };
    (python.is_absolute()
        && python.file_name()?.eq_ignore_ascii_case(name)
        && scripts.file_name()?.eq_ignore_ascii_case(bin)
        && venv.file_name()?.eq_ignore_ascii_case(".venv"))
    .then_some(venv)
}

const MAX_VENV_CONFIGURATION: u64 = 4096;

fn managed_cpython_home(venv: &Path) -> Option<PathBuf> {
    use std::{fs::File, io::Read};

    let mut text = String::new();
    File::open(venv.join("pyvenv.cfg"))
        .ok()?
        .take(MAX_VENV_CONFIGURATION + 1)
        .read_to_string(&mut text)
        .ok()?;
    interpreter_home(&text)?.canonicalize().ok()
}

fn interpreter_home(text: &str) -> Option<&Path> {
    if text.len() as u64 > MAX_VENV_CONFIGURATION {
        return None;
    }
    let mut home = None;
    let mut python_312 = false;
    let mut isolated = false;
    for (key, value) in text
        .trim_start_matches('\u{feff}')
        .lines()
        .filter_map(|line| line.split_once('='))
    {
        let key = key.trim();
        let value = value.trim();
        if key.eq_ignore_ascii_case("home") && home.replace(Path::new(value)).is_some() {
            return None;
        }
        if key.eq_ignore_ascii_case("version") || key.eq_ignore_ascii_case("version_info") {
            let version = value.strip_prefix("3.12")?;
            if !version.is_empty()
                && !version.strip_prefix('.').is_some_and(|patch| {
                    !patch.is_empty() && patch.bytes().all(|byte| byte.is_ascii_digit())
                })
            {
                return None;
            }
            python_312 = true;
        }
        if key.eq_ignore_ascii_case("implementation") && !value.eq_ignore_ascii_case("CPython") {
            return None;
        }
        if key.eq_ignore_ascii_case("include-system-site-packages") {
            if !value.eq_ignore_ascii_case("false") {
                return None;
            }
            isolated = true;
        }
    }
    home.filter(|path| path.is_absolute() && python_312 && isolated)
}

#[cfg(not(windows))]
pub(super) fn native_development_interpreter(python: &Path) -> bool {
    let Some(venv) = development_venv(python) else {
        return false;
    };
    let Some(home) = managed_cpython_home(venv) else {
        return false;
    };
    // uv and standard POSIX symlinks resolve to the actual interpreter named
    // by pyvenv.cfg. Independently configured copies or wrappers stay one-shot.
    let (Ok(actual), Ok(declared)) = (
        python.canonicalize(),
        home.join("python3.12").canonicalize(),
    ) else {
        return false;
    };
    actual == declared && actual.parent() == Some(home.as_path()) && actual.is_file()
}
