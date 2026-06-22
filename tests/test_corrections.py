"""Phase 9.6b — per-project correction dictionary.

Covers the unit surface (load/save/validate, scope-aware apply, segments sync,
backup/revert, hotword pre-path) and the API surface (GET/PUT corrections,
POST /api/recorrect dry-run → apply → revert). The red line guarded here is the
word-scope boundary (a 松→鬆 rule must never touch 馬拉松) and the gotcha that
recorrect MUST sync segments_json alongside the transcript blob.
"""
import importlib
import json

import pytest


@pytest.fixture
def corr_env(tmp_db, tmp_path, monkeypatch):
    """Active project rooted at tmp_path with an initialized tmp DB.
    corrections.py reads config.PROJECT_ROOT for the dict + backups."""
    config = importlib.import_module("config")
    corrections = importlib.import_module("corrections")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return corrections


def _seed(transcript, segments=None, words=None):
    """Insert one media row, return its id."""
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO media (path, filename, transcript, segments_json, words_json) "
            "VALUES (?,?,?,?,?)",
            ("/tmp/clip.mp4", "clip.mp4", transcript,
             json.dumps(segments, ensure_ascii=False) if segments is not None else None,
             json.dumps(words, ensure_ascii=False) if words is not None else None),
        )
        return cur.lastrowid


def _fetch(mid):
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT transcript, segments_json, words_json FROM media WHERE id=?", (mid,)
        ).fetchone()
    return dict(row)


# ── rule load / save / validate ──────────────────────────────────────────────

def test_save_load_roundtrip_and_validation(corr_env):
    corrections = corr_env
    saved = corrections.save_rules([
        {"from": "富田", "to": "Furutech"},                 # defaults: global, post on
        {"from": "", "to": "x"},                            # invalid: empty from → dropped
        {"to": "no-from"},                                  # invalid: no from → dropped
        {"from": "嗯", "to": "", "scope": "bogus"},          # bad scope → global
        {"from": "名", "to": "Name", "pre": True, "post": False},
    ])
    assert len(saved) == 3
    assert saved[0] == {"from": "富田", "to": "Furutech", "scope": "global",
                        "pre": False, "post": True}
    assert saved[1]["scope"] == "global"  # bogus normalized
    loaded = corrections.load_rules()
    assert loaded == saved


def test_load_missing_is_empty(corr_env):
    assert corr_env.load_rules() == []


def test_hotword_terms_only_pre(corr_env):
    corrections = corr_env
    corrections.save_rules([
        {"from": "富田", "to": "Furutech", "pre": True},
        {"from": "保礦力", "to": "寶礦力", "pre": False},
        {"from": "名", "to": "Furutech", "pre": True},  # dup `to` deduped
    ])
    assert corrections.hotword_terms() == ["Furutech"]


# ── scope semantics (the red line) ───────────────────────────────────────────

def test_global_scope_replaces_everywhere(corr_env):
    c = corr_env
    new, n = c._apply_rule("我用富田電源也用富田線", {"from": "富田", "to": "Furutech", "scope": "global"})
    assert new == "我用Furutech電源也用Furutech線"
    assert n == 2


def test_word_scope_guards_against_longer_token(corr_env):
    """A 松→鬆 word-scope rule must NOT bleed into 馬拉松 (the tag-dedup red line)."""
    c = corr_env
    rule = {"from": "松", "to": "鬆", "scope": "word"}
    # flanked by CJK on the left → no match
    new, n = c._apply_rule("馬拉松比賽", rule)
    assert new == "馬拉松比賽"
    assert n == 0
    # standalone → matches
    new2, n2 = c._apply_rule("放 松 一下", rule)
    assert new2 == "放 鬆 一下"
    assert n2 == 1


def test_line_scope_only_line_initial(corr_env):
    c = corr_env
    rule = {"from": "嗯", "to": "", "scope": "line"}
    new, n = c._apply_rule("嗯我覺得\n他說嗯\n嗯好", rule)
    assert new == "我覺得\n他說嗯\n好"   # only the two line-initial 嗯 removed
    assert n == 2


def test_noop_when_from_equals_to(corr_env):
    new, n = corr_env._apply_rule("富田", {"from": "富田", "to": "富田", "scope": "global"})
    assert (new, n) == ("富田", 0)


# ── segments sync (gotcha #1) ────────────────────────────────────────────────

def test_correct_segments_syncs_text_preserves_timing(corr_env):
    c = corr_env
    segs = json.dumps([
        {"start": 0.0, "end": 1.0, "text": "我用富田"},
        {"start": 1.0, "end": 2.0, "text": "電源"},
    ], ensure_ascii=False)
    new_json, hits = c._correct_segments(segs, [{"from": "富田", "to": "Furutech", "scope": "global"}])
    out = json.loads(new_json)
    assert out[0]["text"] == "我用Furutech"
    assert out[0]["start"] == 0.0 and out[0]["end"] == 1.0  # timing untouched
    assert hits == 1


def test_correct_segments_malformed_untouched(corr_env):
    c = corr_env
    assert c._correct_segments("not json", [{"from": "a", "to": "b", "scope": "global"}]) == ("not json", 0)
    assert c._correct_segments(None, []) == (None, 0)


# ── scan / apply / revert against a real DB ──────────────────────────────────

def test_scan_does_not_write(corr_env):
    corrections = corr_env
    mid = _seed("我用富田電源", [{"start": 0, "end": 1, "text": "我用富田電源"}])
    corrections.save_rules([{"from": "富田", "to": "Furutech"}])
    scan = corrections.scan()
    assert scan["media_affected"] == 1
    assert scan["total_hits"] == 1
    assert scan["rules"][0]["hits"] == 1
    # DB untouched
    assert _fetch(mid)["transcript"] == "我用富田電源"


def test_apply_syncs_transcript_and_segments_then_revert(corr_env):
    corrections = corr_env
    mid = _seed(
        "我用富田電源，富田很好",
        [{"start": 0, "end": 1, "text": "我用富田電源"},
         {"start": 1, "end": 2, "text": "富田很好"}],
        [{"word": "富田", "start": 0.1, "end": 0.4, "score": 0.9}],
    )
    corrections.save_rules([{"from": "富田", "to": "Furutech"}])

    result = corrections.apply()
    assert result["media_updated"] == 1
    assert result["total_hits"] == 2
    assert result["backup"]

    after = _fetch(mid)
    assert after["transcript"] == "我用Furutech電源，Furutech很好"
    segs = json.loads(after["segments_json"])
    assert segs[0]["text"] == "我用Furutech電源"
    assert segs[1]["text"] == "Furutech很好"
    # whole-token word rename applied too
    assert json.loads(after["words_json"])[0]["word"] == "Furutech"

    # revert restores the exact pre-correction state
    rev = corrections.revert()
    assert rev["restored"] == 1
    back = _fetch(mid)
    assert back["transcript"] == "我用富田電源，富田很好"
    assert json.loads(back["segments_json"])[0]["text"] == "我用富田電源"
    assert json.loads(back["words_json"])[0]["word"] == "富田"


def test_apply_empty_dictionary_is_noop(corr_env):
    corrections = corr_env
    _seed("我用富田電源")
    result = corrections.apply()
    assert result["media_updated"] == 0
    assert result["backup"] is None


def test_apply_only_post_rules(corr_env):
    """A pre-only rule (post=False) feeds the hotword but never rewrites text."""
    corrections = corr_env
    mid = _seed("我用富田電源")
    corrections.save_rules([{"from": "富田", "to": "Furutech", "pre": True, "post": False}])
    result = corrections.apply()
    assert result["media_updated"] == 0
    assert _fetch(mid)["transcript"] == "我用富田電源"


# ── API surface ──────────────────────────────────────────────────────────────

@pytest.fixture
def client_env(fastapi_client, tmp_path, monkeypatch):
    """fastapi_client whose server's config.PROJECT_ROOT points at the tmp DB dir."""
    import config
    import corrections
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return fastapi_client


def test_api_get_put_corrections(client_env):
    client = client_env
    assert client.get("/api/corrections").json() == {"rules": []}
    r = client.put("/api/corrections", json={"rules": [{"from": "富田", "to": "Furutech"}]})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert client.get("/api/corrections").json()["rules"][0]["from"] == "富田"


def test_api_recorrect_dry_run_then_apply_then_revert(client_env):
    client = client_env
    _seed("我用富田電源", [{"start": 0, "end": 1, "text": "我用富田電源"}])
    client.put("/api/corrections", json={"rules": [{"from": "富田", "to": "Furutech"}]})

    # default POST = dry-run, writes nothing
    dry = client.post("/api/recorrect").json()
    assert dry["dry_run"] is True
    assert dry["media_affected"] == 1
    assert _fetch(1)["transcript"] == "我用富田電源"

    # apply
    applied = client.post("/api/recorrect?dry_run=0").json()
    assert applied["dry_run"] is False
    assert applied["media_updated"] == 1
    assert _fetch(1)["transcript"] == "我用Furutech電源"

    # revert via API
    rev = client.post("/api/recorrect/revert", json={}).json()
    assert rev["restored"] == 1
    assert _fetch(1)["transcript"] == "我用富田電源"


def test_api_recorrect_apply_requires_same_site(client_env):
    """Mutating apply is same-origin only; a cross-site Origin is refused."""
    client = client_env
    _seed("我用富田電源")
    client.put("/api/corrections", json={"rules": [{"from": "富田", "to": "Furutech"}]})
    r = client.post("/api/recorrect?dry_run=0", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert _fetch(1)["transcript"] == "我用富田電源"  # untouched
