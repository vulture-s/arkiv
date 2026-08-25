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
    assert "args.token or secrets.token_urlsafe" in src
    assert "compare_digest" in src, "token compared with ==, not constant-time"


def test_uploads_are_capped():
    src = (_ROOT / "scripts" / "asr_serve.py").read_text(encoding="utf-8")
    assert "MAX_UPLOAD_BYTES" in src
    assert "length > MAX_UPLOAD_BYTES" in src
