//! Structural validation for the test witness's managed leaf ACL comparison.
#[derive(Debug, PartialEq, Eq)]
pub(super) enum AceError {
    Malformed,
    UnsupportedEffectiveAccess,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct Allow<'a> {
    pub flags: u8,
    pub mask: u32,
    pub sid: &'a [u8],
}

#[derive(PartialEq, Eq)]
enum Kind {
    Allow,
    NonGrant,
    UnsupportedAccess,
}

enum Layout {
    Common,
    Object,
    Compound,
}

const INHERIT_ONLY: u8 = 0x08;
const ACL_REVISION_DS: u8 = 4;
const SID_HEADER_SIZE: usize = 8;
const MAX_SUB_AUTHORITIES: usize = 15;
const MAX_SID_SIZE: usize = SID_HEADER_SIZE + MAX_SUB_AUTHORITIES * 4;
// The managed constructors reserve space for the full SID and object fields.
const COMMON_MAX_OPAQUE: usize = u16::MAX as usize - 8 - MAX_SID_SIZE;
const OBJECT_MAX_OPAQUE: usize = COMMON_MAX_OPAQUE - 4 - 2 * 16;

pub(super) fn read_leaf_allow(
    bytes: &[u8],
    acl_revision: u8,
) -> Result<Option<Allow<'_>>, AceError> {
    use AceError::Malformed;
    let [
        ace_type,
        flags,
        size_low,
        size_high,
        m0,
        m1,
        m2,
        m3,
        body @ ..,
    ] = bytes
    else {
        return Err(Malformed);
    };
    if usize::from(u16::from_le_bytes([*size_low, *size_high])) != bytes.len()
        || !bytes.len().is_multiple_of(4)
    {
        return Err(Malformed);
    }

    // ACE_HEADER.AceType values from MS-DTYP. Only ordinary allows supply grants.
    let (kind, layout) = match *ace_type {
        0 => (Kind::Allow, Layout::Common), // ordinary allow
        1..=3 | 13 | 14 => (Kind::NonGrant, Layout::Common), // deny, audit and alarm
        4 => (Kind::UnsupportedAccess, Layout::Compound), // compound allow
        5 | 6 | 11 | 12 => (Kind::UnsupportedAccess, Layout::Object), // object access
        7 | 8 | 15 | 16 => (Kind::NonGrant, Layout::Object), // object audit and alarm
        9 | 10 => (Kind::UnsupportedAccess, Layout::Common), // callback access
        _ => return Err(Malformed),
    };
    let (sid_and_data, limit) = match layout {
        Layout::Common => (body, COMMON_MAX_OPAQUE),
        Layout::Object => {
            let [f0, f1, f2, f3, object @ ..] = body else {
                return Err(Malformed);
            };
            let object_flags = u32::from_le_bytes([*f0, *f1, *f2, *f3]);
            if acl_revision != ACL_REVISION_DS || object_flags & !0b11 != 0 {
                return Err(Malformed);
            }
            let selectors = object_flags.count_ones() as usize * 16;
            (object.get(selectors..).ok_or(Malformed)?, OBJECT_MAX_OPAQUE)
        }
        Layout::Compound => {
            // COMPOUND_ACE_IMPERSONATION and a zero reserved word.
            let [1, 0, 0, 0, sid @ ..] = body else {
                return Err(Malformed);
            };
            (sid, 0)
        }
    };
    let [1, count, ..] = sid_and_data else {
        return Err(Malformed);
    };
    let count = usize::from(*count);
    let sid_size = SID_HEADER_SIZE + count * 4;
    let sid = sid_and_data.get(..sid_size).ok_or(Malformed)?;
    if count > MAX_SUB_AUTHORITIES || sid_and_data.len() - sid_size > limit {
        return Err(Malformed);
    }

    // Preserve the prior type restriction for the red regression.
    if kind == Kind::UnsupportedAccess {
        return Err(AceError::UnsupportedEffectiveAccess);
    }
    let mask = u32::from_le_bytes([*m0, *m1, *m2, *m3]);
    if mask == 0 || flags & INHERIT_ONLY != 0 || kind == Kind::NonGrant {
        return Ok(None);
    }
    Ok(Some(Allow {
        flags: *flags,
        mask,
        sid,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    const WORLD: [u8; 12] = [1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0];
    const CONDITION: [u8; 32] = [
        0x61, 0x72, 0x74, 0x78, 0xf8, 0x0a, 0, 0, 0, 0x54, 0, 0x69, 0, 0x74, 0, 0x6c, 0, 0x65, 0,
        0x10, 4, 0, 0, 0, 0x56, 0, 0x50, 0, 0x80, 0, 0, 0,
    ];

    fn fixture(kind: u8, flags: u8, mask: u32, prefix: &[u8], tail: &[u8]) -> Vec<u8> {
        let mut bytes = vec![kind, flags];
        let size = u16::try_from(8 + prefix.len() + WORLD.len() + tail.len()).unwrap();
        bytes.extend(size.to_le_bytes());
        bytes.extend(mask.to_le_bytes());
        bytes.extend(prefix);
        bytes.extend(WORLD);
        bytes.extend(tail);
        bytes
    }

    #[test]
    fn zero_mask_callback_allow_is_ignored() {
        let ace = fixture(9, 0, 0, &[], &CONDITION);
        assert_eq!(read_leaf_allow(&ace, 2), Ok(None));
    }

    #[test]
    fn inherit_only_callback_allow_is_ignored() {
        let ace = fixture(9, 9, 1, &[], &CONDITION);
        assert_eq!(read_leaf_allow(&ace, 2), Ok(None));
    }

    #[test]
    fn real_regular_file_layout_matrix() {
        for (kind, fields, callback) in [
            (0, None, false),
            (1, None, false),
            (9, None, true),
            (10, None, true),
            (5, Some(3u32), false),
            (6, Some(1), false),
            (11, Some(2), true),
            (12, Some(0), true),
        ] {
            let mut prefix = Vec::new();
            if let Some(fields) = fields {
                prefix.extend(fields.to_le_bytes());
                prefix.resize(4 + 16 * fields.count_ones() as usize, 1);
            }
            let tail: &[u8] = if callback { &CONDITION } else { &[] };
            for (flags, mask) in [(0, 0), (9, 1), (1, 1)] {
                let ace = fixture(kind, flags, mask, &prefix, tail);
                let expected = match (kind, flags, mask) {
                    (_, _, 0) | (_, 9, _) | (1, _, _) => Ok(None),
                    (0, _, _) => Ok(Some(Allow {
                        flags,
                        mask,
                        sid: &WORLD,
                    })),
                    _ => Err(AceError::UnsupportedEffectiveAccess),
                };
                assert_eq!(
                    read_leaf_allow(&ace, 4),
                    expected,
                    "type {kind}, flags {flags}, mask {mask}"
                );
            }
        }
    }

    #[test]
    fn compound_access_and_known_audit_entries_follow_the_leaf_filter() {
        for (flags, mask, expected) in [
            (0, 0, Ok(None)),
            (9, 1, Ok(None)),
            (0, 1, Err(AceError::UnsupportedEffectiveAccess)),
        ] {
            let ace = fixture(4, flags, mask, &[1, 0, 0, 0], &[]);
            assert_eq!(read_leaf_allow(&ace, 4), expected);
        }
        for kind in [2, 3, 7, 8, 13, 14, 15, 16] {
            let object_prefix = [0; 4];
            let prefix: &[u8] = if matches!(kind, 7 | 8 | 15 | 16) {
                &object_prefix
            } else {
                &[]
            };
            let ace = fixture(kind, 0, 1, prefix, &[]);
            assert_eq!(read_leaf_allow(&ace, 4), Ok(None));
        }
    }

    #[test]
    fn an_effective_allow_retains_its_inherited_flag_and_rights() {
        let ace = fixture(0, 16, 0x0003_019f, &[], &[]);
        assert_eq!(
            read_leaf_allow(&ace, 2),
            Ok(Some(Allow {
                flags: 16,
                mask: 0x0003_019f,
                sid: &WORLD
            }))
        );
    }

    #[test]
    fn malformed_entries_fail_even_when_inert() {
        let callback = fixture(9, 9, 0, &[], &CONDITION);
        let mut bad_sid = callback.clone();
        bad_sid[9] = 15;
        let mut bad_revision = callback.clone();
        bad_revision[8] = 2;
        let unknown = fixture(0x7f, 9, 0, &[], &[]);
        let bad_object_flags = fixture(5, 9, 0, &[4, 0, 0, 0], &[]);
        let missing_object_guids = fixture(11, 9, 0, &[3, 0, 0, 0], &[]);
        let wrong_object_revision = fixture(11, 9, 0, &[0, 0, 0, 0], &[]);
        let bad_compound = fixture(4, 9, 0, &[1, 0, 1, 0], &[]);
        let long_compound = fixture(4, 9, 0, &[1, 0, 0, 0], &[0; 4]);
        let unaligned = fixture(9, 9, 0, &[], &[0]);
        for (bytes, revision) in [
            (&callback[..51], 4),
            (&bad_sid, 4),
            (&bad_revision, 4),
            (&unknown, 4),
            (&bad_object_flags, 4),
            (&missing_object_guids, 4),
            (&wrong_object_revision, 2),
            (&bad_compound, 4),
            (&long_compound, 4),
            (&unaligned, 4),
        ] {
            assert_eq!(read_leaf_allow(bytes, revision), Err(AceError::Malformed));
        }
    }

    #[test]
    fn managed_opaque_bounds_are_enforced_before_filtering() {
        for (kind, prefix, largest) in [(9, &[][..], 65_456), (11, &[0; 4][..], 65_420)] {
            let accepted = fixture(kind, 0, 0, prefix, &vec![0; largest]);
            let rejected = fixture(kind, 0, 0, prefix, &vec![0; largest + 4]);
            assert_ne!(read_leaf_allow(&accepted, 4), Err(AceError::Malformed));
            assert_eq!(read_leaf_allow(&rejected, 4), Err(AceError::Malformed));
        }
    }
}
