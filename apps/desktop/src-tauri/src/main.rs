#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(windows))]
compile_error!(
    "ScopeHub v1 desktop builds require native Windows x64; use the local protocol harness on other hosts."
);

#[cfg(windows)]
fn main() {
    use scopehub_desktop::bridge;
    use tauri::Manager;

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            bridge::execute_operation,
            bridge::execute_market_subscription,
            bridge::cancel_market_subscription,
            bridge::prepare_market_subscription,
            bridge::cancel_market_preparation,
            bridge::cancel_local_import_preview,
            bridge::choose_local_csv_folder
        ])
        .setup(|app| {
            let local_data = app.path().app_local_data_dir().ok();
            app.manage(bridge::state_with_local_profile(local_data.as_deref()));
            tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                .on_navigation(|url| {
                    let local_asset = (url.scheme() == "tauri"
                        && url.host_str() == Some("localhost"))
                        || (matches!(url.scheme(), "http" | "https")
                            && url.host_str() == Some("tauri.localhost"));
                    let dev = cfg!(debug_assertions)
                        && url.scheme() == "http"
                        && url.host_str() == Some("127.0.0.1")
                        && url.port() == Some(1420);
                    local_asset || dev
                })
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("ScopeHub desktop startup failed")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<bridge::State>();
                tauri::async_runtime::block_on(async {
                    state.shutdown().await;
                });
            }
        });
}
