use std::{
    env, fs,
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Command, Stdio},
};

use super::*;
use crate::workspace_acl::{check, dispatch_test, use_test_host};
use uuid::Uuid;
use windows_sys::Win32::Security::{
    ACL_REVISION, AddAce, GetSecurityDescriptorDacl, InitializeAcl, InitializeSecurityDescriptor,
    PROTECTED_DACL_SECURITY_INFORMATION, SECURITY_DESCRIPTOR, SetFileSecurityW,
    SetSecurityDescriptorDacl,
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

const EVERYONE_SID: [u8; 12] = [1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0];
// MS-DTYP conditional expression example for a synthetic title.
const CONDITION: [u8; 32] = [
    0x61, 0x72, 0x74, 0x78, 0xf8, 0x0a, 0, 0, 0, 0x54, 0, 0x69, 0, 0x74, 0, 0x6c, 0, 0x65, 0, 0x10,
    4, 0, 0, 0, 0x56, 0, 0x50, 0, 0x80, 0, 0, 0,
];

fn fixture_ace(
    ace_type: u32,
    flags: u32,
    mask: u32,
    layout: &[u8],
    application_data: &[u8],
) -> Vec<u8> {
    let size =
        u16::try_from(8 + layout.len() + EVERYONE_SID.len() + application_data.len()).unwrap();
    let mut bytes = vec![
        u8::try_from(ace_type).unwrap(),
        u8::try_from(flags).unwrap(),
    ];
    bytes.extend(size.to_le_bytes());
    bytes.extend(mask.to_le_bytes());
    bytes.extend(layout);
    bytes.extend(EVERYONE_SID);
    bytes.extend(application_data);
    bytes
}

fn object_fields(guid_flags: u32) -> Vec<u8> {
    let mut fields = guid_flags.to_le_bytes().to_vec();
    if guid_flags & ACE_OBJECT_TYPE_PRESENT != 0 {
        fields.extend(Uuid::from_u128(0xee0570e9_82d5_481f_9529_ecb79d9b9945).to_bytes_le());
    }
    if guid_flags & ACE_INHERITED_OBJECT_TYPE_PRESENT != 0 {
        fields.extend(Uuid::from_u128(0x204ec986_64f0_4bf8_b9c5_6a4befb47f12).to_bytes_le());
    }
    fields
}

#[test]
fn rejects_malformed_ace_bounds_and_unknown_layouts_before_normalization() {
    let mut bytes = fixture_ace(
        ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
        INHERIT_ONLY_ACE,
        0,
        &[],
        &CONDITION,
    );
    let ace = decode_ace(&bytes, ACL_REVISION).unwrap();
    assert_eq!(ace.kind, AceKind::UnsupportedAccess);
    assert_eq!(ace.sid, EVERYONE_SID);
    assert_eq!(
        normalize_access_flags(Entry::StorageFile, ace.flags, ace.mask),
        None
    );
    assert_eq!(decode_ace(&bytes[..51], ACL_REVISION).err(), Some(27));
    bytes[2] = 51;
    assert_eq!(decode_ace(&bytes[..51], ACL_REVISION).err(), Some(27));
    bytes[2] = 52;
    bytes[9] = 15;
    assert_eq!(decode_ace(&bytes, ACL_REVISION).err(), Some(27));
    bytes[9] = 1;
    bytes[0] = 0xff;
    assert_eq!(decode_ace(&bytes, ACL_REVISION).err(), Some(27));

    let short_object = fixture_ace(
        ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        0,
        0,
        &3u32.to_le_bytes(),
        &[],
    );
    assert_eq!(decode_ace(&short_object, ACL_REVISION_DS).err(), Some(27));
    let unknown_flags = fixture_ace(
        ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        0,
        0,
        &4u32.to_le_bytes(),
        &[],
    );
    assert_eq!(decode_ace(&unknown_flags, ACL_REVISION_DS).err(), Some(27));

    for compound in [[2, 0, 0, 0], [1, 0, 1, 0]] {
        let bytes = fixture_ace(ACCESS_ALLOWED_COMPOUND_ACE_TYPE, 0, 0, &compound, &[]);
        assert_eq!(decode_ace(&bytes, ACL_REVISION_DS).err(), Some(27));
    }
    let oversized_compound = fixture_ace(
        ACCESS_ALLOWED_COMPOUND_ACE_TYPE,
        0,
        0,
        &[1, 0, 0, 0],
        &[0; 4],
    );
    assert_eq!(
        decode_ace(&oversized_compound, ACL_REVISION_DS).err(),
        Some(27)
    );
}

#[test]
fn decodes_managed_known_layouts_and_each_object_guid_combination() {
    for (ace_type, expected) in [
        (ACCESS_ALLOWED_ACE_TYPE, AceKind::Allow),
        (ACCESS_DENIED_ACE_TYPE, AceKind::Deny),
        (ACCESS_ALLOWED_CALLBACK_ACE_TYPE, AceKind::UnsupportedAccess),
        (ACCESS_DENIED_CALLBACK_ACE_TYPE, AceKind::UnsupportedAccess),
        (SYSTEM_AUDIT_ACE_TYPE, AceKind::Audit),
        (SYSTEM_ALARM_ACE_TYPE, AceKind::Audit),
        (SYSTEM_AUDIT_CALLBACK_ACE_TYPE, AceKind::Audit),
        (SYSTEM_ALARM_CALLBACK_ACE_TYPE, AceKind::Audit),
    ] {
        let bytes = fixture_ace(ace_type, 0, 1, &[], &[]);
        let ace = decode_ace(&bytes, ACL_REVISION).unwrap();
        assert_eq!(ace.kind, expected);
        assert_eq!(ace.sid, EVERYONE_SID);
    }
    for (ace_type, expected) in [
        (ACCESS_ALLOWED_OBJECT_ACE_TYPE, AceKind::UnsupportedAccess),
        (ACCESS_DENIED_OBJECT_ACE_TYPE, AceKind::UnsupportedAccess),
        (
            ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
            AceKind::UnsupportedAccess,
        ),
        (
            ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
            AceKind::UnsupportedAccess,
        ),
        (SYSTEM_AUDIT_OBJECT_ACE_TYPE, AceKind::Audit),
        (SYSTEM_ALARM_OBJECT_ACE_TYPE, AceKind::Audit),
        (SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE, AceKind::Audit),
        (SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE, AceKind::Audit),
    ] {
        for flags in 0..=3 {
            let bytes = fixture_ace(ace_type, 0, 1, &object_fields(flags), &CONDITION);
            let ace = decode_ace(&bytes, ACL_REVISION_DS).unwrap();
            assert_eq!(ace.kind, expected);
            assert_eq!(ace.sid, EVERYONE_SID);
            assert_eq!(decode_ace(&bytes, ACL_REVISION).err(), Some(27));
        }
    }
    let bytes = fixture_ace(ACCESS_ALLOWED_COMPOUND_ACE_TYPE, 0, 0, &[1, 0, 0, 0], &[]);
    let ace = decode_ace(&bytes, ACL_REVISION_DS).unwrap();
    assert_eq!(ace.kind, AceKind::UnsupportedAccess);
    assert_eq!(ace.sid, EVERYONE_SID);
    assert_eq!(
        normalize_access_flags(Entry::Profile, ace.flags, ace.mask),
        None
    );
}

fn opaque_boundary_cases() -> Vec<(Vec<u8>, bool)> {
    let mut cases = Vec::new();
    for (ace_type, max_aligned, fields) in [
        (ACCESS_ALLOWED_ACE_TYPE, 65_456, Vec::new()),
        (ACCESS_DENIED_CALLBACK_ACE_TYPE, 65_456, Vec::new()),
        (ACCESS_ALLOWED_OBJECT_ACE_TYPE, 65_420, object_fields(3)),
        (
            ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
            65_420,
            object_fields(0),
        ),
    ] {
        for length in [max_aligned - 4, max_aligned, max_aligned + 4] {
            let bytes = fixture_ace(ace_type, 0, 0, &fields, &vec![0; length]);
            cases.push((bytes, length <= max_aligned));
        }
    }
    cases
}

#[test]
fn enforces_managed_opaque_lengths_before_inert_entry_normalization() {
    for (bytes, admitted) in opaque_boundary_cases() {
        for kind in [Entry::Profile, Entry::StorageFile] {
            let result =
                decode_ace(&bytes, ACL_REVISION_DS).and_then(|ace| normalized_access(kind, &ace));
            assert_eq!(
                result,
                if admitted { Ok(None) } else { Err(27) },
                "ACE type {} with length {}",
                bytes[0],
                bytes.len()
            );
        }
    }
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

fn raw_acl_entries(path: &Path, kind: Entry) -> (u32, Vec<Vec<u8>>) {
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
    let (count, revision) =
        unsafe { (u32::from((*dacl).AceCount), u32::from((*dacl).AclRevision)) };
    let entries = (0..count)
        .map(|index| {
            let mut entry = null_mut();
            // SAFETY: the ACL and output remain live and GetAce validates the index.
            assert_ne!(unsafe { GetAce(dacl, index, &mut entry) }, 0);
            assert!(!entry.is_null());
            // SAFETY: GetAce returned a complete ACE in the fixture's live descriptor.
            let size = usize::from(unsafe { (*entry.cast::<ACE_HEADER>()).AceSize });
            unsafe { std::slice::from_raw_parts(entry.cast::<u8>(), size) }.to_vec()
        })
        .collect();
    (revision, entries)
}

fn set_fixture_descriptor(path: &Path, descriptor: *mut c_void) {
    let filename = wide(path.as_os_str()).unwrap();
    // SAFETY: callers provide a live descriptor and wide() NUL-terminates the path.
    assert_ne!(
        unsafe {
            SetFileSecurityW(
                filename.as_ptr(),
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor,
            )
        },
        0,
        "{}: {}",
        path.display(),
        io::Error::last_os_error()
    );
}

fn install_object_fixture(path: &Path, owner_flags: &str, user: &str, entry: &[u8], deny: bool) {
    let owner = security_descriptor(&format!("D:P(A;{owner_flags};FA;;;{user})")).unwrap();
    let mut owner_acl = null_mut();
    let mut present = 0;
    let mut defaulted = 0;
    let mut owner_entry = null_mut();
    // SAFETY: the pointer outputs borrow from the live SDDL descriptor.
    unsafe {
        assert_ne!(
            GetSecurityDescriptorDacl(owner.0, &mut present, &mut owner_acl, &mut defaulted),
            0
        );
        assert_ne!(present, 0);
        assert_ne!(GetAce(owner_acl, 0, &mut owner_entry), 0);
    }
    assert!(!owner_entry.is_null());
    // SAFETY: the known owner ACE remains live for the following copy.
    let owner_bytes = unsafe {
        let size = usize::from((*owner_entry.cast::<ACE_HEADER>()).AceSize);
        std::slice::from_raw_parts(owner_entry.cast::<u8>(), size)
    };

    let mut buffer = [0u32; 128];
    let acl = buffer.as_mut_ptr().cast::<ACL>();
    let mut descriptor = SECURITY_DESCRIPTOR::default();
    let descriptor_ptr = (&raw mut descriptor).cast();
    let entries = if deny {
        [entry, owner_bytes]
    } else {
        [owner_bytes, entry]
    };
    // SAFETY: the aligned ACL buffer, ACE slices and descriptor all remain live
    // through initialization and application of the protected test DACL.
    unsafe {
        assert_ne!(
            InitializeAcl(acl, size_of_val(&buffer) as u32, ACL_REVISION_DS),
            0
        );
        for bytes in entries {
            assert_ne!(
                AddAce(
                    acl,
                    ACL_REVISION_DS,
                    u32::MAX,
                    bytes.as_ptr().cast(),
                    bytes.len() as u32
                ),
                0
            );
        }
        assert_ne!(IsValidAcl(acl), 0);
        assert_ne!(InitializeSecurityDescriptor(descriptor_ptr, 1), 0);
        assert_ne!(SetSecurityDescriptorDacl(descriptor_ptr, 1, acl, 0), 0);
    }
    set_fixture_descriptor(path, descriptor_ptr);
}

type ManagedDescriptorCase = (Entry, Vec<u8>, &'static str, Result<(), u8>);

fn managed_descriptor_cases() -> Vec<ManagedDescriptorCase> {
    let mut cases = Vec::new();
    for kind in [Entry::Profile, Entry::StorageFile] {
        for ace_type in [
            SYSTEM_AUDIT_ACE_TYPE,
            SYSTEM_ALARM_ACE_TYPE,
            SYSTEM_AUDIT_CALLBACK_ACE_TYPE,
            SYSTEM_ALARM_CALLBACK_ACE_TYPE,
        ] {
            cases.push((
                kind,
                fixture_ace(ace_type, 0, 1, &[], &[]),
                "admitted",
                Ok(()),
            ));
        }
        for ace_type in [
            SYSTEM_AUDIT_OBJECT_ACE_TYPE,
            SYSTEM_ALARM_OBJECT_ACE_TYPE,
            SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE,
            SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE,
        ] {
            let bytes = fixture_ace(ace_type, 0, 1, &object_fields(3), &[]);
            cases.push((kind, bytes, "admitted", Ok(())));
        }
        for (flags, mask, inert) in [
            (0, 0, true),
            (INHERIT_ONLY_ACE, 1, true),
            (
                INHERIT_ONLY_ACE | OBJECT_INHERIT_ACE,
                1,
                kind == Entry::StorageFile,
            ),
            (0, 1, false),
        ] {
            let bytes = fixture_ace(
                ACCESS_ALLOWED_COMPOUND_ACE_TYPE,
                flags,
                mask,
                &[1, 0, 0, 0],
                &[],
            );
            let (managed, native) = if inert {
                ("admitted", Ok(()))
            } else {
                ("acl-entry", Err(27))
            };
            cases.push((kind, bytes, managed, native));
        }
        // The legacy generic inheritance filter can discard an opaque CustomAce.
        // Native admission cannot establish the meaning of an unknown layout.
        cases.push((kind, vec![0x7f, 8, 8, 0, 0, 0, 0, 0], "admitted", Err(27)));
        cases.push((kind, vec![0x7f, 0, 8, 0, 0, 0, 0, 0], "acl-entry", Err(27)));
    }
    cases
}

#[test]
fn native_storage_matches_managed_access_rules() {
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
        assert_eq!(raw_acl_entries(&path, Entry::Profile).1.len(), rules.len());
        let refusal = (!admitted).then_some("owner-or-inheritance");
        roots.push((path, Entry::Profile, refusal));
    }
    let inherited = scratch.0.join("inherited-private-control");
    create_directory(
        &inherited,
        &security_descriptor(&format!("O:{user}")).unwrap(),
    )
    .unwrap();
    let (_, inherited_entries) = raw_acl_entries(&inherited, Entry::Profile);
    assert_eq!(inherited_entries.len(), 1);
    assert_ne!(u32::from(inherited_entries[0][1]) & INHERITED_ACE, 0);
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
                set_fixture_descriptor(&path, descriptor.0);
                path
            };
            let (_, entries) = raw_acl_entries(&path, kind);
            assert_eq!(entries.len(), 2, "{name}");
            assert!(
                entries.iter().any(|entry| {
                    u32::from(entry[0]) == raw_type
                        && u32::from(entry[1]) == raw_flags
                        && entry[4..8] == mask.to_le_bytes()
                }),
                "{name}: {entries:?}"
            );
            let refusal = if effective { effective_refusal } else { None };
            roots.push((path, kind, refusal));
        }
    }

    for (label, ace_type, guids, deny, app_data) in [
        (
            "object-allow",
            ACCESS_ALLOWED_OBJECT_ACE_TYPE,
            3,
            false,
            &[][..],
        ),
        ("object-deny", ACCESS_DENIED_OBJECT_ACE_TYPE, 1, true, &[]),
        (
            "object-callback-allow",
            ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
            2,
            false,
            &CONDITION,
        ),
        (
            "object-callback-deny",
            ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
            0,
            true,
            &CONDITION,
        ),
    ] {
        for (scope, kind, _, flags, mask, effective) in scopes {
            let name = format!("{scope}-{label}");
            let directory = scratch.0.join(&name);
            create_directory(&directory, &private_descriptor(&user).unwrap()).unwrap();
            let (path, owner_flags) = if kind == Entry::Profile {
                (directory, "OICI")
            } else {
                let path = directory.join("workspace.sqlite3");
                fs::write(&path, b"synthetic object ACL control").unwrap();
                (path, "")
            };
            let entry = fixture_ace(ace_type, flags, mask, &object_fields(guids), app_data);
            install_object_fixture(&path, owner_flags, &user, &entry, deny);
            let (revision, entries) = raw_acl_entries(&path, kind);
            assert_eq!(revision, ACL_REVISION_DS, "{name}");
            assert_eq!(entries.len(), 2, "{name}");
            assert!(entries.contains(&entry), "{name}: {entries:?}");
            roots.push((path, kind, effective.then_some("acl-entry")));
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

    let memory = managed_descriptor_cases();
    for (kind, bytes, _, expected) in &memory {
        let actual =
            decode_ace(bytes, ACL_REVISION_DS).and_then(|ace| normalized_access(*kind, &ace));
        assert_eq!(actual, expected.map(|()| None), "descriptor {bytes:?}");
    }

    // Use files for the large synthetic buffers to keep command environment
    // values below Windows limits. They are never installed as filesystem ACLs.
    let boundaries = opaque_boundary_cases();
    let boundary_paths: Vec<_> = boundaries
        .iter()
        .enumerate()
        .map(|(index, (bytes, _))| {
            let path = scratch.0.join(format!("opaque-boundary-{index}.bin"));
            fs::write(&path, bytes).unwrap();
            path
        })
        .collect();

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
            function Confirm-Private($raw, [bool]$directory) {
                if ($null -eq $raw.DiscretionaryAcl -or $null -eq $raw.Owner) {
                    return 'acl-read'
                }
                $allowedOwners = if ($directory) { @($user.Value) } else { $privatePrincipals }
                if ($raw.Owner.Value -notin $allowedOwners) {
                    return 'profile-or-file-owner'
                }
                $applies = $false
                $inherits = $false
                foreach ($ace in $raw.DiscretionaryAcl) {
                    if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) {
                        return 'acl-entry'
                    }
                    if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { continue }
                    if ($ace.SecurityIdentifier.Value -notin $privatePrincipals) {
                        return 'shared-access'
                    }
                    if ($ace.SecurityIdentifier -ne $user) { continue }
                    $flags = [int]$ace.AceFlags
                    $applies = $applies -or (($flags -band 8) -eq 0)
                    $inherits = $inherits -or ((($ace.AccessMask -band $full) -eq $full) -and
                        (($flags -band 3) -eq 3))
                }
                if (!$applies -or ($directory -and !$inherits)) {
                    return 'owner-or-inheritance'
                }
                return 'admitted'
            }
            function Confirm-InMemory([byte[]]$bytes, [bool]$directory) {
                $user = [Security.Principal.SecurityIdentifier]::new('S-1-5-21-1-2-3-1000')
                $privatePrincipals = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
                $dacl = [Security.AccessControl.RawAcl]::new([byte]4, 2)
                $flags = [Security.AccessControl.AceFlags] $(if ($directory) { 3 } else { 0 })
                $allow = [Security.AccessControl.AceQualifier]::AccessAllowed
                $dacl.InsertAce(0, [Security.AccessControl.CommonAce]::new($flags, $allow, $full, $user, $false, $null))
                try {
                    $ace = [Security.AccessControl.GenericAce]::CreateFromBinaryForm($bytes, 0)
                } catch {
                    $cause = $_.Exception.GetBaseException()
                    if ($cause -is [ArgumentOutOfRangeException] -and $cause.ParamName -eq 'opaque') {
                        return 'acl-read'
                    }
                    throw
                }
                $dacl.InsertAce(1, $ace)
                $present = [Security.AccessControl.ControlFlags]::DiscretionaryAclPresent
                $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($present, $user, $null, $null, $dacl)
                $managed = [Security.AccessControl.CommonSecurityDescriptor]::new($directory, $false, $descriptor)
                $export = New-Object byte[] $managed.BinaryLength
                $managed.GetBinaryForm($export, 0)
                return (Confirm-Private ([Security.AccessControl.RawSecurityDescriptor]::new($export, 0)) $directory)
            }
            for ($index = 0; $index -lt [int]$env:SCOPEHUB_ACL_CASES; $index++) {
                $path = [Environment]::GetEnvironmentVariable('SCOPEHUB_ACL_CASE_' + $index)
                $directory = [IO.Directory]::Exists($path)
                $acl = if ($directory) { [IO.Directory]::GetAccessControl($path) } else { [IO.File]::GetAccessControl($path) }
                $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
                [Console]::WriteLine((Confirm-Private $raw $directory))
            }
            for ($index = 0; $index -lt [int]$env:SCOPEHUB_ACL_MEMORY_CASES; $index++) {
                $spec = [Environment]::GetEnvironmentVariable('SCOPEHUB_ACL_MEMORY_' + $index)
                $directory = $spec[0] -eq 'D'
                $encoded = $spec.Substring(1)
                $bytes = New-Object byte[] ($encoded.Length / 2)
                for ($offset = 0; $offset -lt $bytes.Length; $offset++) {
                    $bytes[$offset] = [Convert]::ToByte($encoded.Substring($offset * 2, 2), 16)
                }
                [Console]::WriteLine((Confirm-InMemory $bytes $directory))
            }
            for ($index = 0; $index -lt [int]$env:SCOPEHUB_ACL_BOUNDARY_CASES; $index++) {
                $path = [Environment]::GetEnvironmentVariable('SCOPEHUB_ACL_BOUNDARY_' + $index)
                $bytes = [IO.File]::ReadAllBytes($path)
                foreach ($directory in @($true, $false)) {
                    [Console]::WriteLine((Confirm-InMemory $bytes $directory))
                }
            }
            ",
        ])
        .env("SCOPEHUB_ACL_CASES", roots.len().to_string())
        .env("SCOPEHUB_ACL_MEMORY_CASES", memory.len().to_string())
        .env("SCOPEHUB_ACL_BOUNDARY_CASES", boundaries.len().to_string());
    for (index, (path, _, _)) in roots.iter().enumerate() {
        comparison.env(format!("SCOPEHUB_ACL_CASE_{index}"), path);
    }
    for (index, (kind, bytes, _, _)) in memory.iter().enumerate() {
        use std::fmt::Write;

        let mut encoded = String::from(if *kind == Entry::Profile { "D" } else { "F" });
        for byte in bytes {
            write!(encoded, "{byte:02x}").unwrap();
        }
        comparison.env(format!("SCOPEHUB_ACL_MEMORY_{index}"), encoded);
    }

    for (index, path) in boundary_paths.iter().enumerate() {
        comparison.env(format!("SCOPEHUB_ACL_BOUNDARY_{index}"), path);
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
        .chain(memory.iter().map(|(_, _, expected, _)| *expected))
        .chain(
            boundaries
                .iter()
                .flat_map(|(_, admitted)| [if *admitted { "admitted" } else { "acl-read" }; 2]),
        )
        .collect();
    assert_eq!(observations.lines().collect::<Vec<_>>(), expected);
    eprintln!("The legacy managed descriptors gave the same directory and file decisions.");
}
