use std::{
    env, fs,
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Command, Stdio},
};

use super::*;
use crate::workspace_acl::{check, dispatch_test, use_test_host};
use windows_sys::Win32::Security::{
    ACCESS_ALLOWED_ACE, PROTECTED_DACL_SECURITY_INFORMATION, SetFileSecurityW,
};

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
fn common_ace_rejects_malformed_bounds_and_unknown_layouts_before_normalization() {
    let mut bytes = [
        9, 8, 52, 0, 0, 0, 0, 0, // Callback allow, inherit-only, zero access.
        1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, // Everyone SID.
        0x61, 0x72, 0x74, 0x78, 0xf8, 0x0a, 0, 0, // Conditional expression.
        0, 0x54, 0, 0x69, 0, 0x74, 0, 0x6c, 0, 0x65, 0, 0x10, 4, 0, 0, 0, 0x56, 0, 0x50, 0, 0x80,
        0, 0, 0,
    ];
    let ace = common_ace(&bytes).unwrap();
    assert_eq!(ace.kind, CommonAceKind::AccessCallback);
    assert_eq!(ace.sid, &bytes[8..20]);
    assert_eq!(
        normalize_access_flags(Entry::StorageFile, ace.flags, ace.mask),
        None
    );
    assert_eq!(common_ace(&bytes[..51]).err(), Some(27));
    bytes[2] = 51;
    assert_eq!(common_ace(&bytes[..51]).err(), Some(27));
    bytes[2] = 52;
    bytes[9] = 15;
    assert_eq!(common_ace(&bytes).err(), Some(27));
    bytes[9] = 1;
    // The known object-specific callback layout is outside this common parser.
    bytes[0] = 11;
    assert_eq!(common_ace(&bytes).err(), Some(27));
    bytes[0] = 0xff;
    assert_eq!(common_ace(&bytes).err(), Some(27));
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

fn raw_access_entries(path: &Path, kind: Entry) -> Vec<(u32, u32, u32)> {
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
            assert!(usize::from(header.AceSize) >= size_of::<ACCESS_ALLOWED_ACE>());
            assert!(entry.cast::<ACCESS_ALLOWED_ACE>().is_aligned());
            // SAFETY: the fixture common entry has the expected size and alignment.
            let ace = unsafe { &*entry.cast::<ACCESS_ALLOWED_ACE>() };
            (
                u32::from(header.AceType),
                u32::from(header.AceFlags),
                ace.Mask,
            )
        })
        .collect()
}

#[test]
fn native_storage_matches_managed_common_rules() {
    use windows_sys::Win32::Storage::FileSystem::{FILE_GENERIC_READ, FILE_READ_DATA};

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
    assert_ne!(inherited_entries[0].1 & INHERITED_ACE, 0);
    roots.push((inherited, Entry::Profile, None));

    // Use a data-only mask so even a conditional deny does not interfere
    // with the policy worker's READ_CONTROL handle.
    let scopes = [
        ("directory-zero", Entry::Profile, "", 0, 0, false),
        (
            "directory-inherit-only",
            Entry::Profile,
            "OIIO",
            OBJECT_INHERIT_ACE | INHERIT_ONLY_ACE,
            FILE_READ_DATA,
            true,
        ),
        ("file-zero", Entry::StorageFile, "", 0, 0, false),
        (
            "file-inherit-only",
            Entry::StorageFile,
            "OIIO",
            OBJECT_INHERIT_ACE | INHERIT_ONLY_ACE,
            FILE_READ_DATA,
            false,
        ),
        (
            "file-effective",
            Entry::StorageFile,
            "OI",
            OBJECT_INHERIT_ACE,
            FILE_READ_DATA,
            true,
        ),
    ];
    let everyone = ";(Member_of {SID(S-1-1-0)})";
    let ace_types = [
        ("A", ACCESS_ALLOWED_ACE_TYPE, "", Some("shared-access")),
        ("D", ACCESS_DENIED_ACE_TYPE, "", None),
        (
            "XA",
            ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
            everyone,
            Some("acl-entry"),
        ),
        (
            "XD",
            ACCESS_DENIED_CALLBACK_ACE_TYPE,
            everyone,
            Some("acl-entry"),
        ),
    ];
    for (scope, kind, flags, raw_flags, mask, effective) in scopes {
        for (ace, raw_type, condition, effective_refusal) in ace_types {
            let name = format!("{scope}-{ace}");
            let directory = scratch.0.join(&name);
            let fixture_ace = format!("({ace};{flags};0x{mask:x};;;WD{condition})");
            let owner_flags = if kind == Entry::Profile { "OICI" } else { "" };
            let owner_ace = format!("(A;{owner_flags};FA;;;{user})");
            let dacl = if matches!(ace, "D" | "XD") {
                format!("D:P{fixture_ace}{owner_ace}")
            } else {
                format!("D:P{owner_ace}{fixture_ace}")
            };
            let path = if kind == Entry::Profile {
                let descriptor = security_descriptor(&format!("O:{user}{dacl}")).unwrap();
                create_directory(&directory, &descriptor).unwrap();
                directory
            } else {
                create_directory(&directory, &private_descriptor(&user).unwrap()).unwrap();
                let path = directory.join("workspace.sqlite3");
                fs::write(&path, b"synthetic ACL control").unwrap();
                let descriptor = security_descriptor(&dacl).unwrap();
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
            assert!(
                entries.contains(&(raw_type, raw_flags, mask)),
                "{name}: {entries:?}"
            );
            let refusal = if effective { effective_refusal } else { None };
            roots.push((path, kind, refusal));
        }
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
                if ($null -eq $raw.DiscretionaryAcl -or $null -eq $raw.Owner) {
                    [Console]::WriteLine('acl-read')
                    continue
                }
                $allowedOwners = if ($directory) { @($user.Value) } else { $privatePrincipals }
                if ($raw.Owner.Value -notin $allowedOwners) {
                    [Console]::WriteLine('profile-or-file-owner')
                    continue
                }
                $applies = $false
                $inherits = $false
                $verdict = 'admitted'
                foreach ($ace in $raw.DiscretionaryAcl) {
                    if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) {
                        $verdict = 'acl-entry'
                        break
                    }
                    if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { continue }
                    if ($ace.SecurityIdentifier.Value -notin $privatePrincipals) {
                        $verdict = 'shared-access'
                        break
                    }
                    if ($ace.SecurityIdentifier -ne $user) { continue }
                    $flags = [int]$ace.AceFlags
                    $applies = $applies -or (($flags -band 8) -eq 0)
                    $inherits = $inherits -or ((($ace.AccessMask -band $full) -eq $full) -and
                        (($flags -band 3) -eq 3))
                }
                if ($verdict -eq 'admitted' -and (!$applies -or ($directory -and !$inherits))) {
                    $verdict = 'owner-or-inheritance'
                }
                [Console]::WriteLine($verdict)
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
        .map(|(_, _, refusal)| refusal.unwrap_or("admitted"))
        .collect();
    assert_eq!(observations.lines().collect::<Vec<_>>(), expected);
    eprintln!("The legacy managed descriptors gave the same directory and file decisions.");
}
