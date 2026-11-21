use scopehub_platform::model_credentials::{
    CredentialError, CredentialLifetime, OpenAiCredentials,
};

const FIRST_PROFILE: &str = "synthetic-first-profile";
const SECOND_PROFILE: &str = "synthetic-second-profile";
const FIRST_TOKEN: &str = "synthetic-first-token";
const SECOND_TOKEN: &str = "synthetic-second-token";

#[test]
fn session_credential_revision_is_profile_bound_and_revocable() {
    let credentials = OpenAiCredentials::session_only();
    assert_eq!(credentials.lifetime(), CredentialLifetime::Session);
    assert_eq!(credentials.revision(FIRST_PROFILE).unwrap(), None);

    let first = credentials
        .replace(FIRST_PROFILE, FIRST_TOKEN.to_owned())
        .unwrap();
    let other = credentials
        .replace(SECOND_PROFILE, SECOND_TOKEN.to_owned())
        .unwrap();
    assert_ne!(first, other);
    assert_eq!(
        credentials.revision(FIRST_PROFILE).unwrap(),
        Some(first.clone())
    );
    assert!(
        credentials
            .resolve(SECOND_PROFILE, &first)
            .unwrap()
            .is_none()
    );
    assert!(
        credentials
            .resolve(FIRST_PROFILE, &other)
            .unwrap()
            .is_none()
    );
    let token = credentials.resolve(FIRST_PROFILE, &first).unwrap().unwrap();
    assert!(token.as_bearer_token() == FIRST_TOKEN);
    assert_eq!(format!("{token:?}"), "OpenAiCredential([redacted])");

    let replacement = credentials
        .replace(FIRST_PROFILE, SECOND_TOKEN.to_owned())
        .unwrap();
    assert_ne!(first, replacement);
    assert!(
        credentials
            .resolve(FIRST_PROFILE, &first)
            .unwrap()
            .is_none()
    );
    let token = credentials
        .resolve(FIRST_PROFILE, &replacement)
        .unwrap()
        .unwrap();
    assert!(token.as_bearer_token() == SECOND_TOKEN);

    credentials.remove(FIRST_PROFILE).unwrap();
    credentials.remove(FIRST_PROFILE).unwrap();
    assert!(
        credentials
            .resolve(FIRST_PROFILE, &replacement)
            .unwrap()
            .is_none()
    );
    assert_eq!(credentials.revision(SECOND_PROFILE).unwrap(), Some(other));
    assert_eq!(
        OpenAiCredentials::session_only()
            .revision(SECOND_PROFILE)
            .unwrap(),
        None
    );
}

#[test]
fn invalid_tokens_leave_the_current_credential_intact() {
    let credentials = OpenAiCredentials::session_only();
    let revision = credentials
        .replace(FIRST_PROFILE, FIRST_TOKEN.to_owned())
        .unwrap();
    for invalid in [
        "".to_owned(),
        "embedded\r\nheader".to_owned(),
        "invalid:key".to_owned(),
        "a".repeat(2049),
    ] {
        let result = credentials.replace(FIRST_PROFILE, invalid);
        assert_eq!(result, Err(CredentialError::InvalidSecret));
    }
    assert_eq!(credentials.revision(FIRST_PROFILE).unwrap(), Some(revision));
}

#[cfg(not(windows))]
#[test]
fn unsupported_native_persistence_uses_explicit_session_lifetime() {
    assert_eq!(
        OpenAiCredentials::default().lifetime(),
        CredentialLifetime::Session
    );
}

#[cfg(windows)]
#[test]
#[ignore = "requires an interactive Windows user credential store"]
fn windows_credential_manager_persists_replaces_and_removes_synthetic_keys() {
    struct Cleanup(String);

    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = OpenAiCredentials::default().remove(&self.0);
        }
    }

    let profile = Cleanup(format!("synthetic-native-check-{}", uuid::Uuid::new_v4()));
    let credentials = OpenAiCredentials::default();
    assert_eq!(
        credentials.lifetime(),
        CredentialLifetime::WindowsCredentialManager
    );
    assert_eq!(credentials.revision(&profile.0).unwrap(), None);
    let first = credentials.replace(&profile.0, FIRST_TOKEN.into()).unwrap();
    drop(credentials);

    let reopened = OpenAiCredentials::default();
    assert_eq!(reopened.revision(&profile.0).unwrap(), Some(first.clone()));
    let other_profile = format!("{}-other", profile.0);
    assert!(reopened.resolve(&other_profile, &first).unwrap().is_none());
    let token = reopened.resolve(&profile.0, &first).unwrap().unwrap();
    assert!(token.as_bearer_token() == FIRST_TOKEN);
    let replaced = reopened.replace(&profile.0, SECOND_TOKEN.into()).unwrap();
    assert_ne!(first, replaced);
    assert!(reopened.resolve(&profile.0, &first).unwrap().is_none());
    let token = reopened.resolve(&profile.0, &replaced).unwrap().unwrap();
    assert!(token.as_bearer_token() == SECOND_TOKEN);
    reopened.remove(&profile.0).unwrap();
    reopened.remove(&profile.0).unwrap();
    assert_eq!(
        OpenAiCredentials::default().revision(&profile.0).unwrap(),
        None
    );
}
