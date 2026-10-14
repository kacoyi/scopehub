#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(windows))]
compile_error!(
    "ScopeHub v1 desktop builds require native Windows x64; use the local protocol harness on other hosts."
);

#[cfg(windows)]
mod bridge;

#[cfg(windows)]
fn main() {
    use tauri::Manager;

    tauri::Builder::default()
        .manage(bridge::state())
        .invoke_handler(tauri::generate_handler![bridge::execute_operation])
        .setup(|app| {
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
                    state.lock().await.port_mut().shutdown().await;
                });
            }
        });
}
