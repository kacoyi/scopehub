//! A desktop profile's identity travels with the complete private directory.

use std::{fs::File, io, io::Read};

use super::{
    OpenWorkspaceError,
    storage::{PROFILE_ANCHOR, ProfileStorage, publish_anchor},
};

const VERSION: &str = "scopehub-desktop-profile-v1";
const LEGACY_PREFIX: &str = "desktop-development:";
const NEW_PREFIX: &str = "desktop-profile:";
const MAX_ANCHOR_BYTES: u64 = 320;

pub(super) fn read(location: &ProfileStorage) -> Result<Option<String>, OpenWorkspaceError> {
    let path = location.database.with_file_name(PROFILE_ANCHOR);
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(OpenWorkspaceError::Database),
    };
    let mut content = String::new();
    file.take(MAX_ANCHOR_BYTES + 1)
        .read_to_string(&mut content)
        .map_err(|_| OpenWorkspaceError::Database)?;
    let Some(owner) = location
        .owner
        .as_deref()
        .filter(|_| location.main_len.is_some_and(|size| size > 0))
    else {
        return Err(OpenWorkspaceError::ProfileMismatch);
    };
    let mut fields = content.split_terminator('\n');
    if content.len() as u64 <= MAX_ANCHOR_BYTES
        && fields.next() == Some(VERSION)
        && fields.next() == Some(owner)
        && let Some(profile_id) = fields.next()
        && valid_profile_id(profile_id, owner)
        && fields.next().is_none()
    {
        return Ok(Some(profile_id.into()));
    }
    Err(OpenWorkspaceError::ProfileMismatch)
}

pub(super) fn publish(
    location: &ProfileStorage,
    profile_id: &str,
) -> Result<(), OpenWorkspaceError> {
    // Hosts without a stable OS account identity keep the original path binding.
    if let Some(owner) = &location.owner {
        let content = format!("{VERSION}\n{owner}\n{profile_id}\n");
        let path = location.database.with_file_name(PROFILE_ANCHOR);
        publish_anchor(&path, content.as_bytes()).map_err(|_| OpenWorkspaceError::Database)?;
    }
    Ok(())
}

pub(super) fn legacy_path(legacy_id: &str) -> Option<&str> {
    legacy_id
        .strip_prefix(LEGACY_PREFIX)
        .filter(|digest| valid_digest(digest))
}

pub(super) fn fresh_id(legacy_id: &str, owner: &str) -> String {
    let path = legacy_path(legacy_id).expect("validated legacy path identity");
    format!("{NEW_PREFIX}{path}:{owner}:{}", uuid::Uuid::new_v4())
}

pub(super) fn matches_origin(profile_id: &str, legacy_id: &str, owner: Option<&str>) -> bool {
    fresh_parts(profile_id).is_some_and(|(path, original_owner)| {
        original_owner.is_some() && original_owner == owner && legacy_path(legacy_id) == Some(path)
    })
}

fn valid_profile_id(profile_id: &str, owner: &str) -> bool {
    legacy_path(profile_id).is_some()
        || fresh_parts(profile_id)
            .is_some_and(|(_, original_owner)| original_owner.is_none_or(|saved| saved == owner))
}

fn fresh_parts(profile_id: &str) -> Option<(&str, Option<&str>)> {
    let (path, identity) = profile_id.strip_prefix(NEW_PREFIX)?.split_once(':')?;
    // Identities allocated before origin attribution still reopen with their
    // existing private anchor. They cannot recreate a missing anchor.
    let (owner, random) = match identity.split_once(':') {
        Some((owner, random)) if valid_digest(owner) => (Some(owner), random),
        Some(_) => return None,
        None => (None, identity),
    };
    let id = uuid::Uuid::parse_str(random).ok()?;
    (valid_digest(path)
        && id.get_version() == Some(uuid::Version::Random)
        && id.to_string() == random)
        .then_some((path, owner))
}

fn valid_digest(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
