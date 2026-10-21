fn main() {
    // App commands get ACL permissions (allow-library-state / allow-switch-library),
    // granted to the backend-served webview in capabilities/default.json (#561).
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["library_state", "switch_library"]),
        ),
    )
    .expect("failed to run tauri-build")
}
