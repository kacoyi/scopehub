fn main() {
    #[cfg(windows)]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "execute_operation",
            "execute_market_subscription",
            "cancel_market_subscription",
            "prepare_market_subscription",
            "cancel_market_preparation",
            "cancel_local_import_preview",
        ]),
    ))
    .expect("build desktop manifest");
}
