use std::{
    env, fs,
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Command, Stdio},
};

use super::*;
use crate::workspace_acl::{check, dispatch_test, use_test_host};

#[test]
fn ordinary_profile_chain_keeps_one_drive_and_rejects_ambiguous_names() {
    let path = Path::new(r"C:\Users\A Data Folder\LOCAL~1\工作区");
    assert_eq!(
        profile_chain(path),
        Ok([
            r"C:\",
            r"C:\Users",
            r"C:\Users\A Data Folder",
            r"C:\Users\A Data Folder\LOCAL~1",
            r"C:\Users\A Data Folder\LOCAL~1\工作区",
        ]
        .map(Path::new)
        .to_vec())
    );
    for invalid in [
        r"C:relative",
        r"C:\",
        r"\\server\share\profile",
        r"\\?\C:\profile",
        r"C:\first\..\profile",
        r"C:\first\.\profile",
        r"C:\profile.",
        r"C:\profile ",
        r"C:\first\NUL.txt",
    ] {
        assert_eq!(profile_chain(Path::new(invalid)), Err(21), "{invalid}");
    }
}

#[test]
fn sid_documented_header_and_subauthority_extent() {
    assert!(sid_fits(1, 1, 12));
    assert!(sid_fits(1, 15, 68));
    assert!(!sid_fits(1, 0, 7));
    assert!(!sid_fits(1, 1, 11));
    assert!(!sid_fits(1, 16, 72));
    assert!(!sid_fits(2, 1, 12));
}

#[test]
fn managed_principal_order_preserves_order_dependent_pairs() {
    let user = user_order("S-1-5-21-10-20-30-1001").unwrap();
    let inherited = |omitted: &[u32], add_system: bool| {
        let mut grants = UserInheritance::new(user);
        for &mask in omitted {
            grants.record(user, CHILD_INHERITANCE, FILE_ALL_ACCESS & !mask);
        }
        if add_system {
            grants.record(SYSTEM_ORDER, CHILD_INHERITANCE, FILE_ALL_ACCESS);
        }
        grants.sufficient(true)
    };
    assert!(inherited(&[1, 2, 3], false));
    assert!(!inherited(&[1, 2, 3], true));
    assert!(!inherited(&[3, 5, 6], false));
}

#[test]
fn policy_worker() {
    dispatch_test(&["workspace.sqlite3"]);
}

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn assert_raw_ace_count(path: &Path, count: u16) -> u32 {
    let file = open_entry(path, Entry::Profile).unwrap().unwrap();
    let mut dacl = null_mut();
    let mut descriptor = Allocation(null_mut());
    // SAFETY: the file and output parameters are live during the ACL query.
    assert_eq!(
        unsafe {
            GetSecurityInfo(
                file.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                null_mut(),
                null_mut(),
                &mut dacl,
                null_mut(),
                &mut descriptor.0,
            )
        },
        ERROR_SUCCESS
    );
    assert!(!dacl.is_null());
    // SAFETY: the raw ACL header belongs to the live Windows descriptor.
    assert_eq!(unsafe { (*dacl).AceCount }, count);
    let mut first = null_mut();
    // SAFETY: the ACL and output remain live and GetAce validates the index.
    assert_ne!(unsafe { GetAce(dacl, 0, &mut first) }, 0);
    assert!(!first.is_null());
    // SAFETY: GetAce returned a raw ACE within the live descriptor.
    u32::from(unsafe { (*first.cast::<ACE_HEADER>()).AceFlags })
}

#[test]
fn native_profile_inheritance_matches_managed_ordinary_split_rules() {
    use windows_sys::Win32::Storage::FileSystem::FILE_GENERIC_READ;

    use_test_host("workspace_acl::windows::tests::policy_worker");
    let parent = env::temp_dir().join(format!("scopehub-acl-split-{}", uuid::Uuid::new_v4()));
    check(&parent, true, 0).unwrap();
    let scratch = Scratch(parent);
    let user = current_user().unwrap();
    let read = FILE_GENERIC_READ;
    let remaining = FILE_ALL_ACCESS & !read;
    let grant = |flags: &'static str, mask| (flags, mask, user.as_str());
    let controls = [
        (
            "same-scope",
            vec![grant("OICI", read), grant("OICI", remaining)],
            true,
        ),
        (
            "three-same-scope",
            [1, 2, 4]
                .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
                .to_vec(),
            true,
        ),
        (
            "three-incomplete-pairs",
            [3, 5, 6]
                .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
                .to_vec(),
            false,
        ),
        (
            "first-pair-complete",
            [1, 2, 3]
                .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
                .to_vec(),
            true,
        ),
        (
            "first-pair-incomplete",
            [1, 3, 2]
                .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
                .to_vec(),
            false,
        ),
        (
            "four-ordered-same-scope",
            [3, 5, 8, 16]
                .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
                .to_vec(),
            true,
        ),
        (
            "object-and-container",
            vec![grant("OI", FILE_ALL_ACCESS), grant("CI", FILE_ALL_ACCESS)],
            true,
        ),
        (
            "different-scopes",
            vec![grant("OICI", read), grant("OI", remaining)],
            false,
        ),
        (
            "different-depths",
            vec![grant("OINP", FILE_ALL_ACCESS), grant("CI", FILE_ALL_ACCESS)],
            false,
        ),
    ];
    let mut roots = Vec::new();
    let add_interleaved_system = (user_order(&user).unwrap() > SYSTEM_ORDER).then(|| {
        let mut rules = [1, 2, 3]
            .map(|omitted| grant("OICI", FILE_ALL_ACCESS & !omitted))
            .to_vec();
        rules.push(("OICI", FILE_ALL_ACCESS, SYSTEM));
        ("mixed-principals-change-first-pair", rules, false)
    });
    for (name, rules, admitted) in controls.into_iter().chain(add_interleaved_system) {
        let path = scratch.0.join(name);
        let mut sddl = format!("O:{user}D:P");
        use std::fmt::Write;
        for (flags, mask, principal) in &rules {
            write!(sddl, "(A;{flags};0x{mask:x};;;{principal})").unwrap();
        }
        create_directory(&path, &security_descriptor(&sddl).unwrap()).unwrap();
        assert_raw_ace_count(&path, u16::try_from(rules.len()).unwrap());
        roots.push((path, admitted));
    }
    let inherited = scratch.0.join("inherited-private-control");
    create_directory(
        &inherited,
        &security_descriptor(&format!("O:{user}")).unwrap(),
    )
    .unwrap();
    assert_ne!(assert_raw_ace_count(&inherited, 1) & INHERITED_ACE, 0);
    roots.push((inherited, true));

    for (path, admitted) in &roots {
        let native = check(path, false, 0);
        if *admitted {
            native.unwrap();
        } else {
            assert_eq!(native.unwrap_err().to_string(), "owner-or-inheritance");
        }
    }

    eprintln!(
        "Native profile admission matched all {} fixture outcomes.",
        roots.len()
    );

    // The retired check used DirectorySecurity's managed descriptor export.
    // Observe that exact predicate once after the managed export's normalization.
    let powershell = PathBuf::from(env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut comparison = Command::new(powershell);
    comparison.args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            r"
            $ErrorActionPreference = 'Stop'
            $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
            $full = [int][Security.AccessControl.FileSystemRights]::FullControl
            for ($index = 0; $index -lt [int]$env:SCOPEHUB_ACL_CASES; $index++) {
                $path = [Environment]::GetEnvironmentVariable('SCOPEHUB_ACL_CASE_' + $index)
                $acl = [IO.Directory]::GetAccessControl($path)
                $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
                $applies = $false
                $inherits = $false
                foreach ($ace in $raw.DiscretionaryAcl) {
                    if ($ace -is [Security.AccessControl.CommonAce] -and !$ace.IsCallback -and
                        $ace.AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessAllowed -and
                        $ace.SecurityIdentifier -eq $user) {
                        $flags = [int]$ace.AceFlags
                        $applies = $applies -or (($flags -band 8) -eq 0)
                        $inherits = $inherits -or ((($ace.AccessMask -band $full) -eq $full) -and
                            (($flags -band 3) -eq 3))
                    }
                }
                [Console]::WriteLine(($applies -and $inherits))
            }
            ",
        ])
        .env("SCOPEHUB_ACL_CASES", roots.len().to_string());
    for (index, (path, _)) in roots.iter().enumerate() {
        comparison.env(format!("SCOPEHUB_ACL_CASE_{index}"), path);
    }
    let mut child = comparison
        .creation_flags(0x0800_0000)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    if !wait_for_exit(&child, 15_000) {
        let _ = child.kill();
        assert!(
            wait_for_exit(&child, 1_000),
            "managed ACL observer did not exit"
        );
        let _ = child.wait();
        panic!("managed ACL observer timed out");
    }
    let managed = child.wait_with_output().unwrap();
    assert!(
        managed.status.success(),
        "managed ACL comparison failed: {}",
        String::from_utf8_lossy(&managed.stderr)
    );
    let observations = String::from_utf8_lossy(&managed.stdout);
    let expected: Vec<_> = roots
        .iter()
        .map(|(_, admitted)| if *admitted { "True" } else { "False" })
        .collect();
    assert_eq!(observations.lines().collect::<Vec<_>>(), expected);
    eprintln!("The legacy managed descriptor gave the same profile decisions.");
}
