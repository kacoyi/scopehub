fn main() {
    #[cfg(windows)]
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["execute_operation"])),
    )
    .expect("build desktop manifest");
}
