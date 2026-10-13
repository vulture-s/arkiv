"""The other half of the wire: a thin OpenAI-compatible endpoint over a local engine.

Exists because the second pass needs an engine only one machine has, and because
QwenASR's own server cannot drive its CUDA engine (measured: it passes an
`out_format` the engine has no parameter for, and every request 500s).

The tests that matter here are the ones about **what this refuses to do**. It is a
network service that runs a model over whatever it is sent, so the interesting
failures are "listens on 0.0.0.0 by accident", "serves without a token", and
"takes the whole server down on one bad clip".
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "asr_serve", _ROOT / "scripts" / "asr_serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srv = _load()


# ── multipart ────────────────────────────────────────────────────────────────

def _body(parts, boundary=b"B"):
    out = b""
    for name, filename, data in parts:
        out += b"--" + boundary + b"\r\n"
        disp = 'form-data; name="{0}"'.format(name)
        if filename:
            disp += '; filename="{0}"'.format(filename)
        out += ("Content-Disposition: " + disp + "\r\n\r\n").encode()
        out += data + b"\r\n"
    return out + b"--" + boundary + b"--\r\n"


def test_fields_and_files_are_separated():
    fields, files = srv.parse_multipart(
        _body([("model", None, b"whisper-1"), ("file", "a.wav", b"RIFFDATA")]), b"B")
    assert fields == {"model": "whisper-1"}
    assert files == {"file": ("a.wav", b"RIFFDATA")}


def test_binary_payloads_survive_intact():
    """A wav is not text. Any decode/encode round trip in the parser corrupts it,
    and the corruption shows up as a mysteriously bad transcript rather than an
    error."""
    blob = bytes(range(256)) * 4
    _f, files = srv.parse_multipart(_body([("file", "a.wav", blob)]), b"B")
    assert files["file"][1] == blob


def test_unparseable_input_yields_nothing_rather_than_a_guess():
    assert srv.parse_multipart(b"not multipart at all", b"B") == ({}, {})


# ── the service's refusals ───────────────────────────────────────────────────

class _FakeEngine:
    def __init__(self, segments=None, raises=None):
        self.segments = segments or [{"start": 0.0, "end": 1.0, "text": "測試"}]
        self.raises = raises
        self.calls = []

    def __call__(self, wav_path, language):
        self.calls.append((wav_path, language))
        if self.raises:
            raise self.raises
        return self.segments


@pytest.fixture
def server():
    from http.server import ThreadingHTTPServer
    started = {}

    def _start(engine, token="tok"):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(engine, token))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started["httpd"] = httpd
        return "http://127.0.0.1:{0}".format(httpd.server_address[1])

    yield _start
    if "httpd" in started:
        started["httpd"].shutdown()


def _post(url, token=None, parts=None, ctype="multipart/form-data; boundary=B"):
    body = _body(parts if parts is not None else [("file", "a.wav", b"RIFF")])
    req = urllib.request.Request(url + "/audio/transcriptions", data=body, method="POST")
    req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return urllib.request.urlopen(req, timeout=10)


def test_a_request_without_a_token_is_refused(server):
    url = server(_FakeEngine())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url)
    assert e.value.code == 401


def test_a_wrong_token_is_refused(server):
    url = server(_FakeEngine())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="wrong")
    assert e.value.code == 401


def test_health_also_requires_the_token(server):
    """An unauthenticated health probe tells an outsider a GPU is here."""
    url = server(_FakeEngine())
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/health", timeout=10)
    assert e.value.code == 401


def test_a_good_request_returns_segments(server):
    engine = _FakeEngine([{"start": 1.0, "end": 2.0, "text": "第一句"}])
    url = server(engine)

    payload = json.loads(_post(url, token="tok").read())

    assert payload["segments"] == [{"start": 1.0, "end": 2.0, "text": "第一句"}]
    assert payload["text"] == "第一句"


def test_the_language_field_reaches_the_engine(server):
    engine = _FakeEngine()
    url = server(engine)
    _post(url, token="tok", parts=[("language", None, b"zh"),
                                   ("file", "a.wav", b"RIFF")])
    assert engine.calls[0][1] == "zh"


def test_one_bad_clip_does_not_take_the_server_down(server):
    """A batch feeding this will hit a corrupt file eventually. Answering 500 for
    that one and staying up is the difference between losing a clip and losing the
    run."""
    engine = _FakeEngine(raises=RuntimeError("bad audio"))
    url = server(engine)

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="tok")
    assert e.value.code == 500

    engine.raises = None
    assert json.loads(_post(url, token="tok").read())["segments"]


def test_a_request_with_no_file_is_a_400_not_a_crash(server):
    url = server(_FakeEngine())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="tok", parts=[("model", None, b"whisper-1")])
    assert e.value.code == 400


def test_a_non_multipart_body_is_a_400(server):
    url = server(_FakeEngine())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="tok", ctype="application/json")
    assert e.value.code == 400


def test_the_uploaded_temp_file_is_removed(server):
    engine = _FakeEngine()
    url = server(engine)
    _post(url, token="tok")
    assert not Path(engine.calls[0][0]).exists(), "upload left on disk"


def test_the_v1_prefix_is_accepted_too(server):
    """OpenAI's own path is `/v1/audio/transcriptions`; clients send both."""
    url = server(_FakeEngine())
    req = urllib.request.Request(url + "/v1/audio/transcriptions",
                                 data=_body([("file", "a.wav", b"RIFF")]), method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=B")
    req.add_header("Authorization", "Bearer tok")
    assert urllib.request.urlopen(req, timeout=10).status == 200


# ── the defaults that keep a GPU off the network ─────────────────────────────

def test_it_binds_to_loopback_unless_told_otherwise():
    src = (_ROOT / "scripts" / "asr_serve.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--host", default="127.0.0.1"' in src


def test_a_token_is_always_required():
    """Not "generated if you ask" — generated if omitted, and compared on every
    request. There is no unauthenticated path."""
    src = (_ROOT / "scripts" / "asr_serve.py").read_text(encoding="utf-8")
    assert "secrets.token_urlsafe" in src
    assert "compare_digest" in src, "token compared with ==, not constant-time"


def test_an_oversized_upload_is_refused_without_being_read(server, monkeypatch):
    """This replaces a test that grepped the source for `length > MAX_UPLOAD_BYTES`.

    That one passed with the cap multiplied by 1024² — 512 TB — because the string
    it looked for was still there. The cap could have regressed to nothing and the
    suite would have stayed green, which is the same as having no test.

    The cap is lowered rather than sending half a gigabyte: what is under test is
    the comparison, not the number.
    """
    monkeypatch.setattr(srv, "MAX_UPLOAD_BYTES", 64)
    engine = _FakeEngine()
    url = server(engine)

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="tok", parts=[("file", "a.wav", b"R" * 200)])

    assert e.value.code == 413
    assert engine.calls == [], "the engine must not see a body we refused"


def test_an_upload_under_the_cap_still_goes_through(server, monkeypatch):
    """The other half — a cap that refuses everything would also pass the test
    above."""
    monkeypatch.setattr(srv, "MAX_UPLOAD_BYTES", 4096)
    engine = _FakeEngine()
    url = server(engine)

    assert _post(url, token="tok").status == 200
    assert len(engine.calls) == 1


# ── the HTTP layer, which is where a thin server gets its bugs ───────────────

def _port(url):
    return int(url.rsplit(":", 1)[1])


def _statuses(raw):
    """Status codes in the order they came back.

    By regex over the whole stream, not `splitlines()`: a JSON body carries no
    trailing newline, so the next response's status line is glued to the end of
    the previous body (`{"error": "unauthorised"}HTTP/1.1 200 OK`) and a
    line-oriented reader sees one response where there are two.
    """
    import re as _re
    return _re.findall(r"HTTP/1\.[01] (\d{3})", raw)


def _raw(url, payload, want=1, timeout=5):
    """Speak HTTP by hand — `requests`/`urllib` open a fresh connection per call,
    which is exactly what hides a keep-alive bug."""
    import socket
    s = socket.create_connection(("127.0.0.1", _port(url)), timeout=timeout)
    try:
        s.sendall(payload)
        out = b""
        while out.count(b"HTTP/1.") < want and len(out) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
        return out.decode("utf-8", "replace")
    finally:
        s.close()


def _raw_post(body=None, token=None, length=None, ctype="multipart/form-data; boundary=B"):
    body = _body([("file", "a.wav", b"RIFF")]) if body is None else body
    head = ("POST /audio/transcriptions HTTP/1.1\r\nHost: x\r\n"
            "Content-Type: {0}\r\nContent-Length: {1}\r\n".format(
                ctype, len(body) if length is None else length))
    if token:
        head += "Authorization: Bearer {0}\r\n".format(token)
    return head.encode() + b"\r\n" + body


def test_a_refused_request_does_not_desync_the_connection(server):
    """The bug: an early return that never reads the body leaves those bytes in
    the stream. The server then reads `--B` as the next request line, so the
    client's NEXT request is answered with a 400 it did not cause — and the one
    after that is never answered at all.

    arkiv's own client escapes it only because `requests.post` opens a fresh
    connection every time. Any `requests.Session` or curl keep-alive hits it.
    """
    url = server(_FakeEngine())

    out = _raw(url, _raw_post() + _raw_post(token="tok"), want=2)

    assert _statuses(out) == ["401", "200"], out[:400]


def test_a_body_too_large_to_drain_closes_the_connection_instead(server):
    """Draining is a courtesy. Reading half a gigabyte we already refused is not,
    so the other honest answer is to tell the client the connection is over."""
    url = server(_FakeEngine())
    huge = srv.DRAIN_LIMIT_BYTES + 1

    out = _raw(url, _raw_post(token="wrong", length=huge))

    assert _statuses(out) == ["401"]
    assert "connection: close" in out.lower()


def test_a_non_numeric_content_length_is_a_400_not_a_dropped_connection(server):
    """`int("abc")` raised inside the handler: no response at all, one traceback
    per request. A client cannot tell that apart from the server being down."""
    url = server(_FakeEngine())

    out = _raw(url, _raw_post(token="tok", length="abc"))

    assert _statuses(out) == ["400"], out[:200]
    # ...and the connection must end there: without a readable length we cannot
    # know where the body stops, so the stream can never be resynced. Found by
    # reading the diff — `if not drainable and length:` skipped this case because
    # None is falsy, leaving the very desync this method exists to prevent.
    assert "connection: close" in out.lower(), out[:300]


def test_the_engine_is_never_called_by_two_requests_at_once(server):
    """`ThreadingHTTPServer` handles requests concurrently and there is one engine
    object holding one GPU. Overlapping `process_file()` calls on a single CUDA
    engine produce a garbled transcript rather than an error, which is the worst
    way for this to fail."""
    import time

    state = {"now": 0, "peak": 0}
    guard = threading.Lock()

    def engine(wav_path, language):
        with guard:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.15)
        with guard:
            state["now"] -= 1
        return [{"start": 0.0, "end": 1.0, "text": "x"}]

    url = server(engine)
    threads = [threading.Thread(target=lambda: _post(url, token="tok")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert state["peak"] == 1, "engine saw {0} concurrent calls".format(state["peak"])


def test_a_failed_transcription_does_not_send_its_exception_text_to_the_client(server):
    """The exception carries local paths and model internals, and the client can
    do nothing with either. The operator's log gets the detail."""
    url = server(_FakeEngine(raises=RuntimeError("/Users/someone/models/secret.bin missing")))

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, token="tok")

    body = e.value.read().decode("utf-8")
    assert e.value.code == 500
    assert "secret.bin" not in body and "/Users/" not in body, body


def test_a_connection_that_never_finishes_its_request_does_not_hold_a_thread():
    """Pre-auth, so it costs an attacker nothing. Without a socket timeout each
    half-open connection holds a worker thread until the process dies."""
    assert srv.REQUEST_TIMEOUT_S > 0
    handler = srv.make_handler(_FakeEngine(), "tok")
    assert handler.timeout == srv.REQUEST_TIMEOUT_S


def test_the_server_runs_in_the_calling_thread_and_shuts_down(monkeypatch):
    """`main` used to start a non-daemon thread and return, so it was finished
    before the first request arrived: there was no shutdown path at all, and a
    Ctrl-C was delivered to a thread that had already exited.

    Asserted on the thread identity rather than on "does it block", because a test
    that hangs proves nothing about why.
    """
    seen = {}

    class _FakeServer:
        def __init__(self, addr, handler):
            seen["addr"] = addr

        def serve_forever(self):
            seen["thread"] = threading.get_ident()
            raise KeyboardInterrupt

        def shutdown(self):
            seen["shutdown"] = True

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setitem(srv.ENGINES, "qwen", lambda model_dir: _FakeEngine())

    rc = srv.main(["--model-dir", "/nowhere", "--port", "0", "--token", "t"])

    assert rc == 0
    assert seen["thread"] == threading.get_ident(), "serve_forever ran off the main thread"
    assert seen.get("shutdown") and seen.get("closed"), "no shutdown path"
    assert seen["addr"] == ("127.0.0.1", 0)


def test_the_token_can_come_from_the_environment_instead_of_argv(monkeypatch, capsys):
    """`--token` is visible in `ps` to anyone on the box."""
    class _FakeServer:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setitem(srv.ENGINES, "qwen", lambda model_dir: _FakeEngine())
    monkeypatch.setenv("ARKIV_ASR_SERVE_TOKEN", "from-the-env")

    srv.main(["--model-dir", "/nowhere", "--token", "from-argv"])

    assert "token=from-the-env" in capsys.readouterr().out


def test_a_client_that_stalls_mid_body_still_gets_its_refusal(server):
    """The first version of the drain reintroduced the bug it was written to fix.

    Draining before answering looks tidier, but a client that declares 1000 bytes,
    sends 10, and stops makes the read block until the socket timeout — and the
    401 is never sent. From the client's side that is indistinguishable from the
    server being down, which is exactly the symptom of the `Content-Length` crash
    fixed in the same change. So the answer goes out first.
    """
    import socket
    url = server(_FakeEngine())
    s = socket.create_connection(("127.0.0.1", _port(url)), timeout=5)
    try:
        s.sendall(b"POST /audio/transcriptions HTTP/1.1\r\nHost: x\r\n"
                  b"Content-Type: multipart/form-data; boundary=B\r\n"
                  b"Content-Length: 1000\r\n\r\n0123456789")
        assert _statuses(s.recv(4096).decode("utf-8", "replace")) == ["401"]
    finally:
        s.close()
