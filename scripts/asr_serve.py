#!/usr/bin/env python3
"""A thin OpenAI-compatible ASR endpoint over a locally-installed engine.

Why this exists: the second pass in a dual-transcript setup needs an engine that
only one machine has (Qwen3-ASR wants CUDA). arkiv's client side already speaks
`/audio/transcriptions` — this is the other half, so a Mac can use the PC's GPU.

Why not QwenASR's own server: measured 2026-08-25, its `api_server.py` calls the
engine with an `out_format` argument that the CUDA `GPUASREngine.process_file()`
does not accept and cannot honour — its docs only promise the OpenVINO and chatllm
engines. Every request 500s. This wrapper calls `process_file()` the way that
engine actually works and converts the result itself.

    python scripts/asr_serve.py --engine qwen --model-dir <QwenASR cudagpu dir>
    python scripts/asr_serve.py --engine qwen --token mytoken --port 11500

**Binds to 127.0.0.1 unless told otherwise, and always requires a token.** This
runs a model over whatever it is sent; an unauthenticated one on 0.0.0.0 is a
GPU someone else can drive. `--host 0.0.0.0` is deliberate and explicit.
Prefer `ARKIV_ASR_SERVE_TOKEN` over `--token`: argv shows up in `ps`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # an hour of 16k mono wav is ~115 MB

# How much of a body we will read purely to keep a connection usable after
# refusing the request. Draining is a courtesy; reading half a gigabyte we have
# already said no to is not.
DRAIN_LIMIT_BYTES = 1024 * 1024

# Per-connection socket timeout. Without one, a client that opens a connection and
# sends a byte an hour holds a thread forever, and it does so BEFORE authenticating.
REQUEST_TIMEOUT_S = 60


# ── multipart (stdlib `cgi` is deprecated; this is the slice we need) ─────────

def parse_multipart(body: bytes, boundary: bytes):
    """Returns (fields, files) where files maps name → (filename, bytes).

    Deliberately small: enough for the one shape an OpenAI-compatible client
    sends. Anything it cannot parse yields empty dicts rather than a guess.
    """
    fields, files = {}, {}
    sep = b"--" + boundary
    for part in body.split(sep):
        if not part.strip() or part.strip() == b"--":
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if not _:
            continue
        data = data[:-2] if data.endswith(b"\r\n") else data
        head_text = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', head_text)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', head_text)
        if filename:
            files[name.group(1)] = (filename.group(1), data)
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace").strip()
    return fields, files


# ── engines ──────────────────────────────────────────────────────────────────

def load_qwen(model_dir: str):
    """Return `transcribe(wav_path, language) -> [segments]` for QwenASR's CUDA
    engine, loaded by path because `app-gpu.py` is not an importable module name.

    Its GUI lives behind a `__main__` guard, so importing it costs nothing but the
    class definitions.
    """
    import importlib.util

    base = Path(model_dir).resolve()
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("qwen_appgpu", base / "app-gpu.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qwen_appgpu"] = mod
    spec.loader.exec_module(mod)

    engine = mod.GPUASREngine()
    engine.load("cuda")
    print("[asr-serve] qwen engine ready", flush=True)

    import asr_api

    def transcribe(wav_path: str, language: str | None):
        # Qwen3-ASR wants a language NAME, not an ISO code, and its list has no
        # Taiwanese — faithfulness there comes from transcribing as Chinese
        # without flattening, not from a dedicated mode.
        lang = {"zh": "Chinese", "en": "English", "yue": "Cantonese",
                "ja": "Japanese", "ko": "Korean"}.get((language or "").lower(),
                                                      language or "Chinese")
        out = engine.process_file(Path(wav_path), language=lang)
        if not out or not Path(out).exists():
            return []
        text = Path(out).read_text(encoding="utf-8", errors="replace")
        try:
            Path(out).unlink()
        except OSError:
            pass
        # Reuse arkiv's own SRT parser, so both ends of the wire agree by
        # construction rather than by two implementations that happen to match.
        return asr_api.parse_srt(text)

    return transcribe


ENGINES = {"qwen": load_qwen}


# ── server ───────────────────────────────────────────────────────────────────

def make_handler(transcribe, token):
    # One engine, one GPU. `ThreadingHTTPServer` runs `do_POST` concurrently, so
    # without this two uploads call `process_file()` on the same engine object at
    # the same time. Serialising is not a compromise here — it is what a single
    # card wants — and the alternative is a class of failure that shows up as a
    # garbled transcript rather than an error.
    engine_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S

        def _send(self, code, payload, content_type="application/json"):
            body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if content_type == "application/json" else payload.encode("utf-8"))
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _content_length(self):
            """The declared body size, or None if the header is not a number."""
            raw = self.headers.get("Content-Length")
            if raw is None:
                return 0
            try:
                return int(raw)
            except ValueError:
                return None

        def _refuse(self, code, payload):
            """Answer a request whose body we never read.

            HTTP/1.1 keep-alive means the next request comes off the same socket,
            so an early return that leaves the body in the stream desyncs it: the
            server parses `--B` as a request line and answers the client's NEXT
            request with a 400 it did not cause, then stops answering entirely.
            Reproduced with a raw socket — arkiv's own client escapes it only
            because `requests.post` opens a fresh connection every call.

            Small bodies are drained so the connection survives; anything larger
            (or a length we cannot read) closes it instead.
            """
            length = self._content_length()
            if length is None or length > DRAIN_LIMIT_BYTES:
                self.close_connection = True
            else:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            return self._send(code, payload)

        def _authorised(self):
            got = self.headers.get("Authorization", "")
            return secrets.compare_digest(got, "Bearer " + token)

        def log_message(self, fmt, *args):
            print("[asr-serve] " + (fmt % args), flush=True)

        def do_GET(self):
            if self.path.split("?")[0] != "/health":
                return self._refuse(404, {"error": "not found"})
            if not self._authorised():
                return self._refuse(401, {"error": "unauthorised"})
            self._send(200, {"status": "ok", "model_ready": True})

        def do_POST(self):
            if self.path.split("?")[0] not in ("/audio/transcriptions",
                                               "/v1/audio/transcriptions"):
                return self._refuse(404, {"error": "not found"})
            if not self._authorised():
                return self._refuse(401, {"error": "unauthorised"})
            length = self._content_length()
            if length is None:
                # `int("abc")` used to raise here, unhandled: the connection was
                # dropped with no response at all and a traceback per request.
                return self._refuse(400, {"error": "bad Content-Length"})
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                return self._refuse(413, {"error": "bad or oversized upload"})
            ctype = self.headers.get("Content-Type", "")
            m = re.search(r"boundary=([^;]+)", ctype)
            if not m:
                return self._refuse(400, {"error": "expected multipart/form-data"})
            fields, files = parse_multipart(self.rfile.read(length),
                                            m.group(1).strip('"').encode())
            if "file" not in files:
                return self._send(400, {"error": "no file part"})
            _name, blob = files["file"]
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                Path(tmp).write_bytes(blob)
                with engine_lock:
                    segments = transcribe(tmp, fields.get("language"))
            except Exception as exc:  # a bad clip must not take the server down
                # The detail goes to the operator's log, not down the wire: the
                # exception text carries local paths and model internals, and the
                # client can do nothing with either.
                self.log_message("transcribe failed: %s: %s", type(exc).__name__, exc)
                return self._send(500, {"error": {"message": "transcription failed"}})
            finally:
                Path(tmp).unlink(missing_ok=True)
            text = " ".join(s["text"] for s in segments)
            # verbose_json is what arkiv asks for; anything else still gets the
            # timings, because throwing them away is never the helpful answer.
            self._send(200, {"text": text, "segments": segments})

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="qwen", choices=sorted(ENGINES))
    ap.add_argument("--model-dir", required=True,
                    help="the engine's install directory")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 exposes a GPU to your network — be deliberate")
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--token", default=None,
                    help="generated and printed if omitted; ARKIV_ASR_SERVE_TOKEN "
                         "is read first and keeps it out of `ps`")
    args = ap.parse_args(argv)

    # argv is world-readable in `ps` on every machine this is likely to run on.
    # The env var is the way to supply a token that a colleague on the same box
    # cannot simply read; `--token` stays for convenience on a private machine.
    token = os.getenv("ARKIV_ASR_SERVE_TOKEN") or args.token or secrets.token_urlsafe(12)
    transcribe = ENGINES[args.engine](args.model_dir)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(transcribe, token))
    print("[asr-serve] listening on http://{0}:{1}  token={2}".format(
        args.host, args.port, token), flush=True)
    # In the MAIN thread, not a spawned one. The previous version started a
    # non-daemon thread and returned, so `main` was finished before the first
    # request arrived: there was no shutdown path at all, and Ctrl-C was
    # delivered to a thread that had already exited.
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[asr-serve] stopping", flush=True)
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
