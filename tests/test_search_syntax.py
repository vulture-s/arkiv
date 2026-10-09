"""The search box's `key:value` filters are parsed and applied (search_syntax).

They were advertised (placeholder, website, docs) but never parsed: the whole
string was searched as text, so `格爾木氧氣 camera:a7s3` returned NOTHING on a
library holding exactly that clip. The REST and MCP tests below reproduce that
library and pin the answer.
"""
import pytest

import search_syntax
from search_syntax import QueryError, parse


# ── parser ───────────────────────────────────────────────────────────────────
def test_free_text_only_is_untouched():
    assert parse("海邊 日落") == ("海邊 日落", [])


def test_quoted_phrase_is_text_without_quotes():
    assert parse('"格爾木氧氣"') == ("格爾木氧氣", [])


def test_filters_are_split_from_text():
    text, conds = parse('"格爾木氧氣" camera:a7s3 tag:cycling lang:ZH')
    assert text == "格爾木氧氣"
    assert conds == [
        {"field": "camera", "op": "contains", "value": "a7s3"},
        {"field": "tag", "op": "contains", "value": "cycling"},
        {"field": "lang", "op": "eq", "value": "zh"},
    ]


def test_quoted_filter_value_and_repeated_keys():
    _, conds = parse('tag:"黃昏 海邊" tag:逆光')
    assert [c["value"] for c in conds] == ["黃昏 海邊", "逆光"]


@pytest.mark.parametrize("q", ["12:30 會議", "http://example.com", "foo:bar 海邊"])
def test_colons_that_are_not_filters_stay_text(q):
    assert parse(q) == (q, [])


def test_keys_are_case_insensitive():
    assert parse("Camera:fx30")[1] == [{"field": "camera", "op": "contains", "value": "fx30"}]


@pytest.mark.parametrize("value,expected", [
    ("good", "good"), ("REV", "review"), ("N·G", "ng"), ("unrated", "unrated"),
])
def test_rating_accepts_the_labels_the_ui_shows(value, expected):
    assert parse("rating:" + value)[1][0]["value"] == expected


@pytest.mark.parametrize("q", ["rating:maybe", "type:pdf", "shot:last-week", "shot:2025-13", 'tag:""'])
def test_a_known_key_with_a_bad_value_is_an_error(q):
    with pytest.raises(QueryError):
        parse(q)


@pytest.mark.parametrize("value,window", [
    ("2025", ["2025-01-01", "2025-12-31"]),
    ("2024-02", ["2024-02-01", "2024-02-29"]),
    ("2025-10-03", ["2025-10-03", "2025-10-03"]),
    ("2025-10-01..2025-10-15", ["2025-10-01", "2025-10-15"]),
    ("..2025-10-15", [None, "2025-10-15"]),
])
def test_shot_windows(value, window):
    assert parse("shot:" + value)[1] == [{"field": "shot", "op": "range", "value": window}]


# ── against a library ────────────────────────────────────────────────────────
@pytest.fixture
def library(tmp_db, sample_record):
    """The probe library: same transcript, different cameras, shoot days, tags."""
    import db
    db.upsert(sample_record(path="/tmp/a.mp4", filename="a.mp4", transcript="格爾木氧氣",
                            camera_model="ILME-A7S3", lang="zh"))
    db.upsert(sample_record(path="/tmp/b.mp4", filename="b.mp4", transcript="格爾木氧氣",
                            camera_model="ILME-FX30", lang="zh"))
    with db.get_conn() as conn:
        conn.execute("UPDATE media SET shot_date='2025-10-03', rating='good' WHERE filename='a.mp4'")
        conn.execute("UPDATE media SET shot_date='2024-05-20' WHERE filename='b.mp4'")
    db.add_tag(2, "cycling", "manual")
    return {"a": 1, "b": 2}


def test_matching_ids(library):
    import db
    with db.get_conn() as conn:
        ids = search_syntax.matching_ids(conn, parse("camera:a7s3 shot:2025")[1])
    assert ids == {library["a"]}


def _names(resp):
    return sorted(i["filename"] for i in resp["items"])


@pytest.fixture
def no_vectors(monkeypatch):
    """Vector index unavailable → lexical leg only (what the probe exercised)."""
    import vectordb

    def down(*a, **k):
        raise RuntimeError("no vector backend in tests")

    monkeypatch.setattr(vectordb, "search", down)


def test_rest_text_plus_filter_returns_the_clip(fastapi_client, library, no_vectors):
    assert _names(fastapi_client.get("/api/media", params={"q": "格爾木氧氣"}).json()) == ["a.mp4", "b.mp4"]
    r = fastapi_client.get("/api/media", params={"q": '"格爾木氧氣" camera:a7s3'}).json()
    assert _names(r) == ["a.mp4"]
    assert r["parsed"] == {"text": "格爾木氧氣", "filters": [{"field": "camera", "value": "a7s3"}]}


@pytest.mark.parametrize("q,expected", [
    ("camera:fx30", ["b.mp4"]),
    ("tag:cycling", ["b.mp4"]),
    ("shot:2025-10", ["a.mp4"]),
    ("rating:good", ["a.mp4"]),
    ("rating:unrated lang:zh", ["b.mp4"]),
    ("camera:a7s3 shot:2024", []),
])
def test_rest_filters_alone_list_the_matching_set(fastapi_client, library, no_vectors, q, expected):
    assert _names(fastapi_client.get("/api/media", params={"q": q}).json()) == expected


def test_rest_semantic_hits_are_filtered_too(fastapi_client, library, monkeypatch):
    # The live case: the vector index returns BOTH clips for the text; the filter
    # must still cut it to one, not let the nearest neighbours through.
    import vectordb
    monkeypatch.setattr(vectordb, "search", lambda q, n_results=10: [
        {"media_id": "1", "score": 0.9, "excerpt": ""},
        {"media_id": "2", "score": 0.8, "excerpt": ""},
    ])
    r = fastapi_client.get("/api/media", params={"q": "高原 camera:fx30"}).json()
    assert _names(r) == ["b.mp4"]


def test_rest_bad_filter_value_is_422(fastapi_client, library):
    r = fastapi_client.get("/api/media", params={"q": "rating:maybe"})
    assert r.status_code == 422 and "rating" in r.text


@pytest.fixture
def mcp(library):
    pytest.importorskip("mcp")
    import mcp_server
    mcp_server._DB_READY = True
    return mcp_server


def test_mcp_text_plus_filter_returns_the_clip(mcp, no_vectors):
    assert [i["filename"] for i in mcp.search_media_impl('"格爾木氧氣" camera:a7s3')] == ["a.mp4"]


def test_mcp_filters_alone(mcp, no_vectors):
    assert [i["filename"] for i in mcp.search_media_impl("shot:2024")] == ["b.mp4"]


def test_mcp_semantic_hits_are_filtered(mcp, monkeypatch):
    import vectordb
    monkeypatch.setattr(vectordb, "search", lambda q, n_results=10: [
        {"media_id": "1", "score": 0.9, "excerpt": ""},
        {"media_id": "2", "score": 0.8, "excerpt": ""},
    ])
    assert [i["filename"] for i in mcp.search_media_impl("高原 tag:cycling")] == ["b.mp4"]


def test_mcp_bad_filter_is_an_error(mcp):
    with pytest.raises(ValueError):
        mcp.search_media_impl("type:pdf")


def test_a_bare_key_with_nothing_after_it_is_just_text():
    # Someone mid-typing `tag:` should not get an error for it.
    assert parse("海邊 tag:") == ("海邊 tag:", [])


# ── cross-project search (/api/search/all — what the app's search box calls) ──
def _real_project(tmp_path, name, clips):
    """A project whose DB has the real schema (camera_model, shot_date, tags)."""
    import config
    import db
    root = tmp_path / name
    (root / ".arkiv" / "chroma_db").mkdir(parents=True)
    saved = config.DB_PATH
    config.DB_PATH = root / ".arkiv" / "project.db"
    try:
        db.init_db()
        for c in clips:
            db.upsert({"path": c["filename"], "filename": c["filename"], "ext": ".mp4",
                       "transcript": "格爾木氧氣", "camera_model": c["camera"]})
        with db.get_conn() as conn:
            for c in clips:
                conn.execute("UPDATE media SET shot_date=? WHERE filename=?",
                             (c["shot"], c["filename"]))
    finally:
        config.DB_PATH = saved  # federation opens each project's own DB by path
    import projects
    return projects.ProjectMeta(name=name, path=root)


def _chroma_returning_ids(*ids):
    class Coll(object):
        def query(self, query_embeddings, n_results, include):
            return {
                "documents": [["hit"] * len(ids)],
                "metadatas": [[{"media_id": str(i), "filename": "", "path": ""} for i in ids]],
                "distances": [[0.1] * len(ids)],
            }

    class Client(object):
        def __init__(self, path):
            pass

        def get_collection(self, name):
            return Coll()

    return Client


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    import types
    import federation
    alpha = _real_project(tmp_path, "alpha", [
        {"filename": "a1.mp4", "camera": "ILME-A7S3", "shot": "2025-10-03"},
        {"filename": "a2.mp4", "camera": "ILME-FX30", "shot": "2025-10-04"},
    ])
    beta = _real_project(tmp_path, "beta", [
        {"filename": "b1.mp4", "camera": "ILME-FX30", "shot": "2024-05-20"},
        {"filename": "b2.mp4", "camera": "ILME-A7S3", "shot": "2024-05-21"},
    ])
    monkeypatch.setattr(federation.config, "discover_projects", lambda: [alpha, beta])
    monkeypatch.setattr(federation, "embed_query", lambda q: [0.1])
    # Vector index returns BOTH clips of every project — the filter must cut them.
    monkeypatch.setattr(federation, "chromadb",
                        types.SimpleNamespace(PersistentClient=_chroma_returning_ids(1, 2)))
    return federation


def _fed_names(payload):
    return sorted(i["filename"] for i in payload["items"])


def test_cross_project_text_plus_filter(two_projects):
    p = two_projects.search_all_projects('"格爾木氧氣" camera:a7s3', timeout=5.0)
    assert _fed_names(p) == ["a1.mp4", "b2.mp4"]
    assert p["parsed"]["filters"] == [{"field": "camera", "value": "a7s3"}]


def test_cross_project_filters_alone(two_projects):
    assert _fed_names(two_projects.search_all_projects("shot:2024", timeout=5.0)) == ["b1.mp4", "b2.mp4"]
    assert _fed_names(two_projects.search_all_projects("camera:fx30 shot:2025", timeout=5.0)) == ["a2.mp4"]


def test_cross_project_filters_apply_on_the_sql_fallback_too(two_projects, monkeypatch):
    def down(q):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(two_projects, "embed_query", down)
    assert _fed_names(two_projects.search_all_projects("格爾木氧氣 camera:fx30", timeout=5.0)) == ["a2.mp4", "b1.mp4"]


def test_cross_project_bad_filter_is_an_error(two_projects):
    with pytest.raises(QueryError):
        two_projects.search_all_projects("rating:maybe", timeout=5.0)


def test_cross_project_route_applies_filters_and_rejects_bad_ones(fastapi_client, two_projects, monkeypatch):
    import types
    import entitlements
    monkeypatch.setattr(entitlements, "check_cross_project",
                        lambda **kw: types.SimpleNamespace(allowed=True))
    ok = fastapi_client.get("/api/search/all", params={"q": "格爾木氧氣 camera:a7s3"})
    assert ok.status_code == 200
    assert _fed_names(ok.json()) == ["a1.mp4", "b2.mp4"]
    bad = fastapi_client.get("/api/search/all", params={"q": "rating:maybe"})
    assert bad.status_code == 422 and "rating" in bad.text
