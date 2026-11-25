//! Native resources owned by the trusted application host.

#![deny(unsafe_code)]

pub mod local_folder;
pub mod model_credentials;

#[cfg(windows)]
pub mod workspace_acl;
