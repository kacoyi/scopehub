//! A desktop profile's identity travels with the complete private directory.

use std::{fs::File, io, io::Read};

use super::{
    OpenWorkspaceError,
    storage::{PROFILE_ANCHOR, ProfileStorage, publish_anchor},
};

const VERSION: &str = "scopehub-desktop-profile-v1";
const PROFILE_PREFIX: &str = "desktop-development:";
const MAX_ANCHOR_BYTES: u64 = 256;

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
    let Some(owner) = location.owner.as_deref().filter(|_| location.exists) else {
        return Err(OpenWorkspaceError::ProfileMismatch);
    };
    let mut fields = content.split_terminator('\n');
    if content.len() as u64 <= MAX_ANCHOR_BYTES
        && fields.next() == Some(VERSION)
        && fields.next() == Some(owner)
        && let Some(profile_id) = fields.next()
        && valid_profile_id(profile_id)
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

pub(super) fn valid_profile_id(profile_id: &str) -> bool {
    profile_id.strip_prefix(PROFILE_PREFIX).is_some_and(|id| {
        id.len() == 64
            && id
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}
