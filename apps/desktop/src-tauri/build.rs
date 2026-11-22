fn main() {
    println!("cargo:rustc-check-cfg=cfg(scopehub_desktop_host)");
    #[cfg(any(windows, all(target_os = "linux", feature = "ubuntu-desktop")))]
    println!("cargo:rustc-cfg=scopehub_desktop_host");
    #[cfg(any(windows, all(target_os = "linux", feature = "ubuntu-desktop")))]
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
