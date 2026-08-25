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

/// Holds an Ollama we started ourselves. `None` when Ollama was already running —
/// killing someone else's daemon on quit would be worse than not starting one.
struct Ollama(Mutex<Option<Child>>);

/// Ollama's fixed local port. Probed rather than configured: if the user already
/// has it running (the normal case for anyone who installed it themselves), we
/// must not start a second one.
const OLLAMA_PORT: u16 = 11434;

/// PATH for child processes.
///
/// A Finder-launched `.app` inherits a minimal PATH — roughly
/// `/usr/bin:/bin:/usr/sbin:/sbin` — not the shell's. Homebrew is not on it. So
/// `faster-whisper` calling a bare `ffmpeg`, and every `ollama` lookup, fail with
/// `[Errno 2] No such file or directory` inside the packaged app while working
/// perfectly from a terminal. That difference is invisible during development,
/// which is exactly why it shipped.
fn augmented_path() -> String {
    let current = std::env::var("PATH").unwrap_or_default();
    #[allow(unused_mut)]
    let mut parts: Vec<String> = Vec::new();
    #[cfg(unix)]
    {
        let home = std::env::var("HOME").unwrap_or_default();
        #[cfg(target_os = "macos")]
        {
            parts.push("/opt/homebrew/bin".into()); // Apple Silicon Homebrew
            parts.push("/usr/local/bin".into()); // Intel Homebrew / manual installs
        }
        #[cfg(target_os = "linux")]
        parts.push("/usr/local/bin".into());
        if !home.is_empty() {
            parts.push(format!("{home}/.local/bin"));
        }
    }
    #[cfg(windows)]
    {
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            parts.push(format!("{local}\\Programs\\Ollama"));
        }
    }
    let sep = if cfg!(windows) { ";" } else { ":" };
    // Existing entries win: never shadow a binary the user has deliberately put
    // earlier on their own PATH.
    let mut out = current.clone();
    for p in parts {
        if !current.split(sep).any(|e| e == p) {
            out.push_str(sep);
            out.push_str(&p);
        }
    }
    out
}

/// Is something listening on `port`?
fn port_open(port: u16) -> bool {
    format!("127.0.0.1:{port}")
        .parse()
        .ok()
        .map(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok())
        .unwrap_or(false)
}

/// Start `ollama serve` if nothing is already serving, returning the child we own.
///
/// The packaged app never did this — `main.rs` contained the string "ollama" zero
/// times — so vision tagging, chat and embeddings all silently degraded inside the
/// bundle while working from a dev shell. Silently, because each of those paths
/// soft-fails by design: the clip just comes back with no description.
fn spawn_ollama_if_needed(log_dir: &std::path::Path) -> Option<Child> {
    if port_open(OLLAMA_PORT) {
        eprintln!("[arkiv-tauri] ollama already running on {OLLAMA_PORT}");
        return None;
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let mut candidates: Vec<String> = vec![
        "/opt/homebrew/bin/ollama".into(),
        "/usr/local/bin/ollama".into(),
        "/Applications/Ollama.app/Contents/Resources/ollama".into(),
    ];
    if !home.is_empty() {
        candidates.push(format!("{home}/.local/bin/ollama"));
    }
    #[cfg(windows)]
    {
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            candidates.push(format!("{local}\\Programs\\Ollama\\ollama.exe"));
        }
    }
    // Last resort: whatever PATH resolves, using the augmented one.
    candidates.push("ollama".into());

    let log = std::fs::File::create(log_dir.join("ollama.log")).ok();
    for bin in candidates {
        if bin.contains(std::path::MAIN_SEPARATOR) && !std::path::Path::new(&bin).exists() {
            continue;
        }
        let mut cmd = Command::new(&bin);
        cmd.arg("serve").env("PATH", augmented_path());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        if let Some(f) = log.as_ref().and_then(|f| f.try_clone().ok()) {
            let err = f.try_clone().ok();
            cmd.stdout(Stdio::from(f));
            if let Some(e) = err {
                cmd.stderr(Stdio::from(e));
            }
        }
        match cmd.spawn() {
            Ok(child) => {
                eprintln!("[arkiv-tauri] started ollama: {bin}");
                return Some(child);
            }
            Err(e) => eprintln!("[arkiv-tauri] ollama spawn failed ({bin}): {e}"),
        }
    }
    // Not fatal. arkiv indexes, searches and transcribes without Ollama; only the
    // LLM-backed features degrade, and they already say so.
    eprintln!("[arkiv-tauri] no ollama binary found; vision/chat/embed will be unavailable");
    None
}

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
    // python-build-standalone lays the interpreter out differently per platform:
    // `python/bin/python3` on Unix, `python\python.exe` on Windows (there is no
    // bin/ there at all). Same tarball family, different shape.
    let python = if cfg!(windows) {
        backend.join("python").join("python.exe")
    } else {
        backend.join("python").join("bin").join("python3")
    };
    let site = backend.join("site-packages");
    let src = backend.join("src");
    // join_paths, not format!("{}:{}") — the PYTHONPATH separator is ';' on
    // Windows and ':' elsewhere, and std already knows which.
    let pythonpath = std::env::join_paths([&site, &src])
        .map_err(|e| format!("join_paths(PYTHONPATH): {e}"))?
        .to_string_lossy()
        .into_owned();
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
        .manage(Ollama(Mutex::new(None)))
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
            .env("ARKIV_TRUST_LOOPBACK", "1")
            // Without this the bundled app cannot find ffmpeg (Homebrew is not on
            // a Finder-launched process's PATH), and faster-whisper's bare
            // `ffmpeg` call takes the whole batch down with [Errno 2].
            .env("PATH", augmented_path());
            // `windows_subsystem = "windows"` (top of this file) only silences OUR
            // console. Spawning python.exe from a GUI process still allocates a new
            // one, so without this flag every launch parks a black console window
            // next to the app for the life of the backend.
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                cmd.creation_flags(CREATE_NO_WINDOW);
            }
            if let Some(out) = stdout_cfg {
                cmd.stdout(out);
            }
            if let Some(err) = stderr_cfg {
                cmd.stderr(err);
            }
            // Ollama first: the backend probes it during startup, and starting it
            // after would leave the first minute of a session with vision off.
            if let Some(o) = spawn_ollama_if_needed(&log_dir) {
                app.state::<Ollama>().0.lock().unwrap().replace(o);
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
            // Kill our children when the app is exiting, so no orphan uvicorn (or
            // Ollama) survives the window closing.
            //
            // BOTH events, and that is the fix rather than a belt-and-braces: with
            // only `ExitRequested`, quitting through an Apple Event — which is what
            // ⌘Q and `osascript -e 'quit app "arkiv"'` send — never reached this
            // code at all, and every such quit left a uvicorn holding its port.
            // Measured: quit the packaged app, `pgrep` still finds the backend.
            // `Exit` is the last event before the process ends and fires on every
            // path. `take()` makes the pair idempotent when both fire.
            match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                    if let Some(state) = app_handle.try_state::<Backend>() {
                        if let Some(mut child) = state.0.lock().unwrap().take() {
                            let _ = child.kill();
                            let _ = child.wait(); // reap, don't leave a zombie
                        }
                    }
                    // Only the Ollama we started. If it was already running when we
                    // launched, the state holds None and the user's daemon — very
                    // possibly serving something else — is left alone.
                    if let Some(state) = app_handle.try_state::<Ollama>() {
                        if let Some(mut child) = state.0.lock().unwrap().take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
                _ => {}
            }
        });
}
