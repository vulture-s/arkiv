"""R5-25 (round-5 #51): request-input parsers / option builders → reqopts.py.

Fourth (final) leaf-service module of the APIRouter split. Holds the helpers that
turn a raw request input into validated internal options: the `?ids=` query
parser (export routes) and the IngestRequest→ingest.py-CLI-flags translator plus
its whisper-language allowlist (ingest routes). Both raise HTTPException on bad
input; both are server-state-free.

`_ingest_cmd_opts` reads an IngestRequest by attribute (duck-typed via a string
annotation), so the pydantic model stays in server.py — reqopts imports no
server. These tests pin the leaf boundary, identity re-export, and the
parse/validation behaviour so the move is provably semantics-preserving. (Route +
IngestRequest-integration coverage already lives in test_ingest_options.py.)
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = (
    "_parse_ids_query",
    "_INGEST_LANGUAGES",
    "_INGEST_LANGUAGE_CODES",
    "_ingest_cmd_opts",
)


# ── module boundary ──────────────────────────────────────────────────────────
def test_reqopts_is_a_leaf_module():
    src = (_ROOT / "reqopts.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_server_reexports_reqopts_by_identity():
    import server
    import reqopts
    for name in _NAMES:
        assert getattr(server, name) is getattr(reqopts, name), (
            "server.{0} must BE reqopts.{0} (a re-export)".format(name)
        )


def test_ingest_request_model_stays_in_server():
    # The pydantic model is a server/API-schema concern; reqopts only duck-types on
    # it, so it must NOT have moved (would drag pydantic wiring into the leaf).
    import server
    assert hasattr(server, "IngestRequest")


# ── _parse_ids_query behaviour ───────────────────────────────────────────────
def test_parse_ids_query_decodes_and_validates():
    import reqopts
    assert reqopts._parse_ids_query("1,2,3") == [1, 2, 3]
    assert reqopts._parse_ids_query(None) is None       # no filter requested
    assert reqopts._parse_ids_query("") is None
    assert reqopts._parse_ids_query(" 1 , ,2 ") == [1, 2]  # blanks skipped, trimmed
    assert reqopts._parse_ids_query(",,") == []          # all-blank → empty filter
    with pytest.raises(HTTPException) as e:
        reqopts._parse_ids_query("1,abc")
    assert e.value.status_code == 400


# ── _ingest_cmd_opts behaviour (duck-typed body) ─────────────────────────────
class _Body:
    """Minimal stand-in with IngestRequest's attribute surface."""
    def __init__(self, **kw):
        defaults = dict(
            skip_vision=False, refresh=False, recursive=False, max_failures=0,
            skip_failed=False, no_embed=False, whisper_guard=None, language=None,
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


def test_ingest_cmd_opts_defaults_empty():
    import reqopts
    assert reqopts._ingest_cmd_opts(_Body()) == []


def test_ingest_cmd_opts_emits_flags():
    import reqopts
    opts = reqopts._ingest_cmd_opts(_Body(
        skip_vision=True, refresh=True, recursive=True, max_failures=3,
        skip_failed=True, no_embed=True,
    ))
    assert "--skip-vision" in opts and "--refresh" in opts and "--recursive" in opts
    assert opts[opts.index("--max-failures") + 1] == "3"
    assert "--skip-failed" in opts and "--no-embed" in opts


def test_ingest_cmd_opts_max_failures_zero_omitted():
    import reqopts
    assert "--max-failures" not in reqopts._ingest_cmd_opts(_Body(max_failures=0))


def test_ingest_cmd_opts_language_allowlist():
    import reqopts
    # a valid curated code emits; an unknown code is silently dropped (not injected)
    assert reqopts._ingest_cmd_opts(_Body(language="zh")) == ["--language", "zh"]
    assert "--language" not in reqopts._ingest_cmd_opts(_Body(language="xx"))
    assert reqopts._INGEST_LANGUAGE_CODES == {"zh", "en", "ja", "ko"}


def test_ingest_cmd_opts_whisper_guard_validated_against_config():
    import reqopts
    import config
    valid = next(iter(config.WHISPER_GUARD_LAYERS))
    assert reqopts._ingest_cmd_opts(_Body(whisper_guard=valid)) == [
        "--whisper-guard", str(int(valid))
    ]
    # a guard value not in config is dropped, not passed through
    bogus = 9999
    assert bogus not in config.WHISPER_GUARD_LAYERS
    assert "--whisper-guard" not in reqopts._ingest_cmd_opts(_Body(whisper_guard=bogus))
