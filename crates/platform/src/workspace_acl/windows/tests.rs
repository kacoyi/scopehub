use std::{
    env, fs,
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Command, Stdio},
};

use super::*;
use crate::workspace_acl::{check, dispatch_test, use_test_host};
use windows_sys::Win32::Security::{PROTECTED_DACL_SECURITY_INFORMATION, SetFileSecurityW};

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

fn raw_access_entries(path: &Path, kind: Entry) -> Vec<(u32, u32)> {
    let file = open_entry(path, kind).unwrap().unwrap();
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
    let count = u32::from(unsafe { (*dacl).AceCount });
    (0..count)
        .map(|index| {
            let mut entry = null_mut();
            // SAFETY: the ACL and output remain live and GetAce validates the index.
            assert_ne!(unsafe { GetAce(dacl, index, &mut entry) }, 0);
            assert!(!entry.is_null());
            // SAFETY: GetAce returned an ACE header within the live descriptor.
            let header = unsafe { &*entry.cast::<ACE_HEADER>() };
            assert_eq!(u32::from(header.AceType), ACCESS_ALLOWED_ACE_TYPE);
            assert!(usize::from(header.AceSize) >= size_of::<ACCESS_ALLOWED_ACE>());
            assert!(entry.cast::<ACCESS_ALLOWED_ACE>().is_aligned());
            // SAFETY: the fixture allow entry has the expected size and alignment.
            let ace = unsafe { &*entry.cast::<ACCESS_ALLOWED_ACE>() };
            (u32::from(header.AceFlags), ace.Mask)
        })
        .collect()
}

#[test]
fn native_storage_matches_managed_ordinary_rules() {
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
        assert_eq!(raw_access_entries(&path, Entry::Profile).len(), rules.len());
        let refusal = (!admitted).then_some("owner-or-inheritance");
        roots.push((path, Entry::Profile, refusal));
    }
    let inherited = scratch.0.join("inherited-private-control");
    create_directory(
        &inherited,
        &security_descriptor(&format!("O:{user}")).unwrap(),
    )
    .unwrap();
    let inherited_entries = raw_access_entries(&inherited, Entry::Profile);
    assert_eq!(inherited_entries.len(), 1);
    assert_ne!(inherited_entries[0].0 & INHERITED_ACE, 0);
    roots.push((inherited, Entry::Profile, None));

    for (name, kind, flags, raw_flags, mask, refusal) in [
        ("directory-zero", Entry::Profile, "", 0, 0, None),
        (
            "directory-inherit-only",
            Entry::Profile,
            "OIIO",
            OBJECT_INHERIT_ACE | INHERIT_ONLY_ACE,
            read,
            Some("shared-access"),
        ),
        ("file-zero", Entry::StorageFile, "", 0, 0, None),
        (
            "file-inherit-only",
            Entry::StorageFile,
            "OIIO",
            OBJECT_INHERIT_ACE | INHERIT_ONLY_ACE,
            read,
            None,
        ),
        (
            "file-shared",
            Entry::StorageFile,
            "OI",
            OBJECT_INHERIT_ACE,
            read,
            Some("shared-access"),
        ),
    ] {
        let directory = scratch.0.join(name);
        let path = if kind == Entry::Profile {
            let sddl = format!("O:{user}D:P(A;OICI;FA;;;{user})(A;{flags};0x{mask:x};;;WD)");
            create_directory(&directory, &security_descriptor(&sddl).unwrap()).unwrap();
            directory
        } else {
            check(&directory, true, 0).unwrap();
            let path = directory.join("workspace.sqlite3");
            fs::write(&path, b"synthetic ACL control").unwrap();
            let sddl = format!("D:P(A;;FA;;;{user})(A;{flags};0x{mask:x};;;WD)");
            let descriptor = security_descriptor(&sddl).unwrap();
            let filename = wide(path.as_os_str()).unwrap();
            // SAFETY: the protected descriptor and terminated file name remain live.
            assert_ne!(
                unsafe {
                    SetFileSecurityW(
                        filename.as_ptr(),
                        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                        descriptor.0,
                    )
                },
                0,
                "{name}: {}",
                io::Error::last_os_error()
            );
            path
        };
        let entries = raw_access_entries(&path, kind);
        assert_eq!(entries.len(), 2, "{name}");
        assert!(entries.contains(&(raw_flags, mask)), "{name}: {entries:?}");
        roots.push((path, kind, refusal));
    }

    for (path, kind, refusal) in &roots {
        let profile = if *kind == Entry::StorageFile {
            path.parent().unwrap()
        } else {
            path
        };
        let native = check(profile, false, 0);
        if let Some(reason) = refusal {
            assert_eq!(native.unwrap_err().to_string(), *reason);
        } else {
            native.unwrap();
        }
    }

    eprintln!(
        "Native profile admission matched all {} fixture outcomes.",
        roots.len()
    );

    // The retired check used DirectorySecurity or FileSecurity's managed export.
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
            $privatePrincipals = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
            $full = [int][Security.AccessControl.FileSystemRights]::FullControl
            for ($index = 0; $index -lt [int]$env:SCOPEHUB_ACL_CASES; $index++) {
                $path = [Environment]::GetEnvironmentVariable('SCOPEHUB_ACL_CASE_' + $index)
                $directory = [IO.Directory]::Exists($path)
                $acl = if ($directory) { [IO.Directory]::GetAccessControl($path) } else { [IO.File]::GetAccessControl($path) }
                $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
                $applies = $false
                $inherits = $false
                $shared = $false
                foreach ($ace in $raw.DiscretionaryAcl) {
                    if ($ace -is [Security.AccessControl.CommonAce] -and !$ace.IsCallback -and
                        $ace.AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessAllowed) {
                        $shared = $shared -or ($ace.SecurityIdentifier.Value -notin $privatePrincipals)
                        if ($ace.SecurityIdentifier -ne $user) { continue }
                        $flags = [int]$ace.AceFlags
                        $applies = $applies -or (($flags -band 8) -eq 0)
                        $inherits = $inherits -or ((($ace.AccessMask -band $full) -eq $full) -and
                            (($flags -band 3) -eq 3))
                    }
                }
                [Console]::WriteLine((!$shared -and $applies -and (!$directory -or $inherits)))
            }
            ",
        ])
        .env("SCOPEHUB_ACL_CASES", roots.len().to_string());
    for (index, (path, _, _)) in roots.iter().enumerate() {
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
        .map(|(_, _, refusal)| if refusal.is_none() { "True" } else { "False" })
        .collect();
    assert_eq!(observations.lines().collect::<Vec<_>>(), expected);
    eprintln!("The legacy managed descriptors gave the same directory and file decisions.");
}
