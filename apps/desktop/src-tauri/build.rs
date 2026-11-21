fn main() {
    #[cfg(windows)]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "execute_operation",
            "configure_openai_credential",
            "execute_market_subscription",
            "cancel_market_subscription",
            "prepare_market_subscription",
            "cancel_market_preparation",
            "cancel_local_import_preview",
            "choose_local_csv_folder",
        ]),
    ))
    .expect("build desktop manifest");
}
