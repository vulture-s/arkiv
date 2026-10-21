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

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Holds the spawned backend so we can kill it on exit.
struct Backend(Mutex<Option<Child>>);

/// How to start the backend: (python, PYTHONPATH, working dir). Resolved once at
/// startup and kept, because switching library (#561) starts it again.
#[derive(Clone)]
struct Spec {
    python: String,
    pythonpath: String,
    src_dir: String,
}

/// Which library the running backend serves, and on which port.
///
/// #561: the app used to have exactly one library, `app_local_data_dir/arkiv`,
/// and the only way to another was `ARKIV_PROJECT_ROOT` + a restart — nothing a
/// desktop user can do. The backend reads its library from that env var once, at
/// import, so "switching" is: stop it, start it again with the new root, point the
/// window at the new port. `active` is persisted so the next launch reopens it.
struct Library(Mutex<LibState>);

struct LibState {
    spec: Option<Spec>,
    active: PathBuf,
    default: PathBuf,
    state_file: PathBuf,
    port: u16,
}

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

/// Start the backend for `root` on `port`, logging to `<root>/logs/backend.log`.
fn spawn_backend(spec: &Spec, root: &Path, port: u16) -> std::io::Result<Child> {
    let _ = std::fs::create_dir_all(root);
    eprintln!(
        "[arkiv-tauri] starting backend: {} (cwd={}) on 127.0.0.1:{port}, root={}",
        spec.python,
        spec.src_dir,
        root.display()
    );

    // Capture the backend's stdout+stderr to a log file under the writable
    // project root. Without this a Finder-launched .app throws every uvicorn
    // access line, print(), and traceback into the void — so a broken tester
    // box is un-debuggable remotely. One-file rotation keeps the previous run.
    let log_dir = root.join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let log_path = log_dir.join("backend.log");
    let _ = std::fs::rename(&log_path, log_dir.join("backend.log.prev"));
    let (stdout_cfg, stderr_cfg) = match std::fs::File::create(&log_path) {
        Ok(f) => {
            // header first (shared O_APPEND-less fd; written before the child starts)
            let mut hdr: &std::fs::File = &f;
            let _ = writeln!(
                hdr,
                "[arkiv-tauri] backend {} (cwd={}) 127.0.0.1:{port} root={}",
                spec.python,
                spec.src_dir,
                root.display()
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

    let mut cmd = Command::new(&spec.python);
    cmd.args([
        "-m",
        "uvicorn",
        "server:app",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
        // Never trust X-Forwarded-For: nothing proxies the sidecar, and
        // uvicorn's default lets a loopback peer forge its IP (audit 2026-10-09).
        "--no-proxy-headers",
    ])
    .current_dir(&spec.src_dir)
    .env("PYTHONPATH", &spec.pythonpath)
    .env("ARKIV_PROJECT_ROOT", root)
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
    cmd.spawn()
}

/// The library the previous session left open, if it still exists; else `default`.
/// A library on an unmounted NAS share must not strand the app on a dead root —
/// falling back is the only way the user can get back in to pick another.
fn read_active_library(state_file: &Path, default: &Path) -> PathBuf {
    match std::fs::read_to_string(state_file) {
        Ok(s) => {
            let p = PathBuf::from(s.trim());
            if !s.trim().is_empty() && p.is_dir() {
                p
            } else {
                default.to_path_buf()
            }
        }
        Err(_) => default.to_path_buf(),
    }
}

fn same_dir(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(x), Ok(y)) => x == y,
        _ => a == b,
    }
}

/// Minimal HTTP/1.0 GET against the local backend; returns the body.
/// No HTTP client crate for one loopback request whose answer is a short JSON.
fn http_get_body(port: u16, path: &str) -> Option<String> {
    let addr = format!("127.0.0.1:{port}").parse().ok()?;
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(800)).ok()?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    write!(s, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").ok()?;
    let mut buf = String::new();
    s.read_to_string(&mut buf).ok()?;
    let (head, body) = buf.split_once("\r\n\r\n")?;
    if !head.starts_with("HTTP/1.1 200") && !head.starts_with("HTTP/1.0 200") {
        return None;
    }
    Some(body.to_string())
}

fn kill_backend(app: &tauri::AppHandle) {
    if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait(); // reap, don't leave a zombie
    }
}

/// Start the backend for `root` on a fresh port and wait for it. Ok(port) or Err.
fn start_and_wait(app: &tauri::AppHandle, spec: &Spec, root: &Path) -> Result<u16, String> {
    let port = free_port();
    let child = spawn_backend(spec, root, port).map_err(|e| format!("無法啟動後端：{e}"))?;
    app.state::<Backend>().0.lock().unwrap().replace(child);
    if wait_ready(port, Duration::from_secs(45)) {
        Ok(port)
    } else {
        kill_backend(app);
        Err("後端 45 秒內沒有啟動".into())
    }
}

fn navigate_main(app: &tauri::AppHandle, port: u16) {
    if let Some(w) = app.get_webview_window("main") {
        if let Ok(url) = format!("http://127.0.0.1:{port}").parse() {
            let _ = w.navigate(url);
        }
    }
}

/// `{active, default}` — the sidebar needs `default` to offer a way back to the
/// built-in library, which is usually not in the project registry at all.
#[tauri::command]
fn library_state(lib: tauri::State<'_, Library>) -> HashMap<&'static str, String> {
    let st = lib.0.lock().unwrap();
    let mut out = HashMap::new();
    out.insert("active", st.active.to_string_lossy().into_owned());
    out.insert("default", st.default.to_string_lossy().into_owned());
    out
}

/// Restart the backend on another library and reload the window onto it (#561).
///
/// Refuses while a long job is running (it would be killed mid-way — the "silent
/// interruption" the issue rules out); `/api/library/busy` reads the jobs' own
/// single-flight slots. If the new library's backend does not come up, the old
/// library is started again so the user is never left with no backend at all.
/// `async` so the up-to-45 s wait runs off the main thread and the window stays
/// responsive.
#[tauri::command(async)]
fn switch_library(app: tauri::AppHandle, path: String) -> Result<String, String> {
    let target = PathBuf::from(path.trim());
    if !target.is_absolute() || !target.is_dir() {
        return Err("找不到這個資料夾，或它不是資料夾".into());
    }
    let lib = app.state::<Library>();
    let mut st = lib.0.lock().unwrap();
    let spec = st.spec.clone().ok_or("後端尚未就緒")?;
    if same_dir(&st.active, &target) {
        return Ok(format!("http://127.0.0.1:{}", st.port));
    }
    match http_get_body(st.port, "/api/library/busy") {
        Some(body) if body.contains("\"busy\":true") => {
            return Err(format!("有工作正在進行，等它完成再切換素材庫：{body}"));
        }
        Some(_) => {}
        // The old backend is unreachable: nothing of its can be interrupted, so
        // switching is the way out rather than something to refuse.
        None => eprintln!("[arkiv-tauri] busy probe failed on {}; switching anyway", st.port),
    }
    kill_backend(&app);
    match start_and_wait(&app, &spec, &target) {
        Ok(port) => {
            st.active = target.clone();
            st.port = port;
            let _ = std::fs::write(&st.state_file, target.to_string_lossy().as_bytes());
            navigate_main(&app, port);
            Ok(format!("http://127.0.0.1:{port}"))
        }
        Err(e) => {
            let back = st.active.clone();
            match start_and_wait(&app, &spec, &back) {
                Ok(port) => {
                    st.port = port;
                    navigate_main(&app, port);
                    Err(format!("{e}；已回到原本的素材庫"))
                }
                Err(e2) => Err(format!("{e}；原本的素材庫也起不來：{e2}")),
            }
        }
    }
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
        .invoke_handler(tauri::generate_handler![library_state, switch_library])
        .setup(|app| {
            let port = free_port();

            let (python, pythonpath, src_dir) = match resolve_backend(app) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("[arkiv-tauri] cannot resolve backend: {e}");
                    return Err(e.into());
                }
            };
            let spec = Spec { python, pythonpath, src_dir };

            // Project root must be OUTSIDE the read-only .app bundle. app_local_data_dir
            // is per-user writable; the server's init_db creates the DB/dirs on first run.
            let data_dir = app.path().app_local_data_dir().ok();
            let default_root = data_dir
                .as_ref()
                .map(|d| d.join("arkiv"))
                .unwrap_or_else(|| {
                    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".arkiv")
                });
            let _ = std::fs::create_dir_all(&default_root);
            let state_file = data_dir
                .map(|d| d.join("active-library.txt"))
                .unwrap_or_else(|| default_root.join("active-library.txt"));
            let proj_root = read_active_library(&state_file, &default_root);

            // Ollama first: the backend probes it during startup, and starting it
            // after would leave the first minute of a session with vision off.
            let log_dir = proj_root.join("logs");
            let _ = std::fs::create_dir_all(&log_dir);
            if let Some(o) = spawn_ollama_if_needed(&log_dir) {
                app.state::<Ollama>().0.lock().unwrap().replace(o);
            }

            let child = match spawn_backend(&spec, &proj_root, port) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[arkiv-tauri] failed to spawn backend: {e}");
                    return Err(Box::new(e));
                }
            };
            app.state::<Backend>().0.lock().unwrap().replace(child);
            app.manage(Library(Mutex::new(LibState {
                spec: Some(spec),
                active: proj_root,
                default: default_root,
                state_file,
                port,
            })));

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
