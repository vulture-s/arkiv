// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// arkiv desktop shell (Option 1 packaging, B3).
//
// The app is self-starting: on launch it spawns the bundled Python backend
// (`python-build-standalone` + the site-packages, NOT PyInstaller — the spike
// showed the native-heavy tree, torch/mlx/chromadb, loads cleanly under a stock
// portable interpreter) on a negotiated free port, waits for it to accept
// connections, then opens the WebView pointed at it. The child is killed on exit.
//
// Backend location is resolved in this order:
//   1. env override (dev): ARKIV_SIDECAR_PYTHON / ARKIV_SIDECAR_PYTHONPATH /
//      ARKIV_SIDECAR_SRC — lets `cargo tauri dev` drive the real spawn path
//      against a dev checkout without bundling 1.5 GB.
//   2. bundled resources: <resources>/backend/{python,site-packages,src}.

use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Holds the spawned backend so we can kill it on exit.
struct Backend(Mutex<Option<Child>>);

/// Ask the OS for a free TCP port (bind :0, read the assigned port, drop).
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8501)
}

/// Poll until the backend accepts a TCP connection, or the timeout elapses.
fn wait_ready(port: u16, timeout: Duration) -> bool {
    let start = Instant::now();
    let addr = format!("127.0.0.1:{port}").parse().unwrap();
    while start.elapsed() < timeout {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// Resolve (python_binary, PYTHONPATH, working_dir) for the backend.
fn resolve_backend(app: &tauri::App) -> Result<(String, String, String), String> {
    if let Ok(py) = std::env::var("ARKIV_SIDECAR_PYTHON") {
        // dev / override path
        let pp = std::env::var("ARKIV_SIDECAR_PYTHONPATH").unwrap_or_default();
        let src = std::env::var("ARKIV_SIDECAR_SRC").unwrap_or_default();
        return Ok((py, pp, src));
    }
    // bundled resources: <resources>/backend/{python,site-packages,src}
    let res = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir: {e}"))?;
    let backend = res.join("backend");
    let python = backend.join("python").join("bin").join("python3");
    let site = backend.join("site-packages");
    let src = backend.join("src");
    let pythonpath = format!("{}:{}", site.display(), src.display());
    Ok((
        python.to_string_lossy().into_owned(),
        pythonpath,
        src.to_string_lossy().into_owned(),
    ))
}

fn main() {
    std::panic::set_hook(Box::new(|info| {
        eprintln!("[arkiv-tauri panic] {}", info);
    }));

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let port = free_port();

            let (python, pythonpath, src_dir) = match resolve_backend(app) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("[arkiv-tauri] cannot resolve backend: {e}");
                    return Err(e.into());
                }
            };

            // Project root must be OUTSIDE the read-only .app bundle. app_local_data_dir
            // is per-user writable; the server's init_db creates the DB/dirs on first run.
            let proj_root = app
                .path()
                .app_local_data_dir()
                .map(|d| d.join("arkiv"))
                .unwrap_or_else(|_| {
                    std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default())
                        .join(".arkiv")
                });
            let _ = std::fs::create_dir_all(&proj_root);

            eprintln!(
                "[arkiv-tauri] starting backend: {python} (cwd={src_dir}) on 127.0.0.1:{port}, root={}",
                proj_root.display()
            );

            // Capture the backend's stdout+stderr to a log file under the writable
            // project root. Without this a Finder-launched .app throws every uvicorn
            // access line, print(), and traceback into the void — so a broken tester
            // box is un-debuggable remotely. One-file rotation keeps the previous run.
            let log_dir = proj_root.join("logs");
            let _ = std::fs::create_dir_all(&log_dir);
            let log_path = log_dir.join("backend.log");
            let _ = std::fs::rename(&log_path, log_dir.join("backend.log.prev"));
            let (stdout_cfg, stderr_cfg) = match std::fs::File::create(&log_path) {
                Ok(f) => {
                    // header first (shared O_APPEND-less fd; written before the child starts)
                    let mut hdr: &std::fs::File = &f;
                    use std::io::Write;
                    let _ = writeln!(
                        hdr,
                        "[arkiv-tauri] backend {python} (cwd={src_dir}) 127.0.0.1:{port} root={}",
                        proj_root.display()
                    );
                    // dup the handle so stdout+stderr share one file offset → interleave cleanly
                    let err = f.try_clone().ok();
                    (Some(Stdio::from(f)), err.map(Stdio::from))
                }
                Err(e) => {
                    eprintln!("[arkiv-tauri] could not open {}: {e}; backend output not captured", log_path.display());
                    (None, None)
                }
            };

            let mut cmd = Command::new(&python);
            cmd.args([
                "-m",
                "uvicorn",
                "server:app",
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
            ])
            .current_dir(&src_dir)
            .env("PYTHONPATH", &pythonpath)
            .env("ARKIV_PROJECT_ROOT", &proj_root)
            .env("ARKIV_PORT", port.to_string())
            .env("ARKIV_TRUST_LOOPBACK", "1");
            if let Some(out) = stdout_cfg {
                cmd.stdout(out);
            }
            if let Some(err) = stderr_cfg {
                cmd.stderr(err);
            }
            let child = cmd.spawn();

            let child = match child {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[arkiv-tauri] failed to spawn backend: {e}");
                    return Err(Box::new(e));
                }
            };
            app.state::<Backend>().0.lock().unwrap().replace(child);

            if !wait_ready(port, Duration::from_secs(45)) {
                eprintln!("[arkiv-tauri] backend not ready after 45s on port {port} — opening anyway");
            }

            let url = format!("http://127.0.0.1:{port}");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().unwrap()))
                .title("arkiv")
                .inner_size(1400.0, 900.0)
                .min_inner_size(900.0, 600.0)
                .build()?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Kill the backend child when the app is exiting, so no orphan uvicorn
            // survives the window closing.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
