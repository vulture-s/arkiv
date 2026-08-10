"""Per-project collection definitions loaded from `.arkiv/collections.json`.

The file is hand-editable, so the loader is treated as untrusted input: it must
degrade to the shipped defaults for anything it can't parse, and it must never
raise into /api/collections.

ISOLATION: every test rebinds `config.COLLECTIONS_PATH` to a tmp file and busts
the mtime cache. Without that these would read the developer's real
`~/.arkiv/collections.json` — the same trap `test_tag_aliases.py` sidesteps.
"""
from __future__ import annotations

import json

import pytest

import collection_defs as cd
import config
import smart_collections as sc


@pytest.fixture(autouse=True)
def _isolated_collections_file(tmp_path, monkeypatch):
    """Point the loader at a tmp path and clear the cache before AND after, so a
    test can neither read the developer's real file nor leak into the next test."""
    monkeypatch.setattr(config, "COLLECTIONS_PATH", tmp_path / "collections.json")
    cd._CACHE["mtime"] = None
    yield tmp_path / "collections.json"
    cd._CACHE["mtime"] = None


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cd._CACHE["mtime"] = None


VALID = {
    "version": 1,
    "collections": [{
        "key": "topic_9f2a1c3d",
        "title": "黑膠與唱盤",
        "category": "topic",
        "tags": ["黑膠唱片", "唱針", "轉盤", "唱片架"],
    }],
}


# ----------------------------------------------------------------- fail-soft

def test_no_file_yields_exactly_the_shipped_defaults(_isolated_collections_file):
    assert cd.file_collections() == []
    assert cd.load_collections() == list(sc.DEFAULT_COLLECTIONS)


def test_unparseable_file_degrades_to_defaults(_isolated_collections_file):
    _isolated_collections_file.write_text("{ not json at all", encoding="utf-8")
    cd._CACHE["mtime"] = None
    assert cd.file_collections() == []
    assert cd.load_collections() == list(sc.DEFAULT_COLLECTIONS)


def test_wrong_version_is_treated_as_absent(_isolated_collections_file):
    """Half-reading a future format is worse than ignoring it."""
    _write(_isolated_collections_file, dict(VALID, version=2))
    assert cd.file_collections() == []


def test_one_bad_entry_costs_only_that_entry(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1, "collections": [
        {"key": "topic_ok", "title": "好的", "tags": ["黑膠唱片"]},
        {"key": "topic_notags", "title": "沒有標籤"},          # no tags
        {"key": "bad key!", "title": "壞 key", "tags": ["x"]},   # key regex
        {"key": "topic_notitle", "tags": ["x"]},                # no title
        "not even a dict",
    ]})
    assert [c.key for c in cd.file_collections()] == ["topic_ok"]


def test_a_top_level_list_does_not_raise(_isolated_collections_file):
    _write(_isolated_collections_file, ["not", "a", "mapping"])
    assert cd.file_collections() == []


# ------------------------------------------------------------------ loading

def test_valid_entry_becomes_a_real_collection(_isolated_collections_file):
    _write(_isolated_collections_file, VALID)
    cols = cd.file_collections()
    assert len(cols) == 1
    col = cols[0]
    assert isinstance(col, sc.Collection)
    assert col.key == "topic_9f2a1c3d"
    assert col.title == "黑膠與唱盤"
    assert col.tags == ("黑膠唱片", "唱針", "轉盤", "唱片架")


def test_file_collection_never_carries_a_predicate(_isolated_collections_file):
    """A config file must not be able to inject a callable into the classifier."""
    _write(_isolated_collections_file, {"version": 1, "collections": [
        dict(VALID["collections"][0], predicate="__import__('os').system('boom')"),
    ]})
    assert cd.file_collections()[0].predicate is None


def test_a_file_entry_actually_classifies(_isolated_collections_file):
    """End-to-end through the real scorer: 4 tags means 2 hits are required."""
    _write(_isolated_collections_file, VALID)
    col = cd.file_collections()[0]
    one_hit = {"duration_s": 3, "has_audio": 1, "tags": ["黑膠唱片"]}
    two_hits = {"duration_s": 3, "has_audio": 1, "tags": ["黑膠唱片", "唱針"]}
    assert sc.score_collection(one_hit, col) < sc.MIN_CONFIDENCE
    assert sc.score_collection(two_hits, col) >= sc.MIN_CONFIDENCE


def test_defaults_come_first_then_file_entries(_isolated_collections_file):
    _write(_isolated_collections_file, VALID)
    keys = [c.key for c in cd.load_collections()]
    assert keys[:len(sc.DEFAULT_COLLECTIONS)] == [c.key for c in sc.DEFAULT_COLLECTIONS]
    assert keys[-1] == "topic_9f2a1c3d"


def test_duplicate_keys_in_file_keep_only_the_first(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1, "collections": [
        {"key": "topic_dup", "title": "一", "tags": ["a"]},
        {"key": "topic_dup", "title": "二", "tags": ["b"]},
    ]})
    cols = cd.file_collections()
    assert len(cols) == 1 and cols[0].title == "一"


def test_file_cannot_override_a_builtin_key(_isolated_collections_file):
    """Overriding `recent` from JSON would drop its predicate and leave a
    collection that can never match anything."""
    _write(_isolated_collections_file, {"version": 1, "collections": [
        {"key": "recent", "title": "劫持", "tags": ["x"]},
    ]})
    assert cd.file_collections() == []
    recent = [c for c in cd.load_collections() if c.key == "recent"]
    assert len(recent) == 1 and recent[0].predicate is not None


# ------------------------------------------------------------------ disable

def test_disable_hides_exactly_one_builtin(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1, "disable": ["b_roll"],
                                        "collections": []})
    keys = [c.key for c in cd.load_collections()]
    assert "b_roll" not in keys
    assert "a_roll" in keys
    assert len(keys) == len(sc.DEFAULT_COLLECTIONS) - 1


def test_disable_of_an_unknown_key_is_a_noop(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1,
                                        "disable": ["topic_whatever", "nope"],
                                        "collections": []})
    assert len(cd.load_collections()) == len(sc.DEFAULT_COLLECTIONS)


# -------------------------------------------------------------------- caps

def test_file_collection_count_is_capped(_isolated_collections_file):
    """A runaway file must not make /api/collections classify the library against
    thousands of definitions on every uncached request."""
    _write(_isolated_collections_file, {"version": 1, "collections": [
        {"key": "topic_k{0}".format(i), "title": "t{0}".format(i), "tags": ["a"]}
        for i in range(cd.MAX_FILE_COLLECTIONS + 25)
    ]})
    assert len(cd.file_collections()) == cd.MAX_FILE_COLLECTIONS


def test_tags_and_title_are_bounded(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1, "collections": [{
        "key": "topic_big", "title": "标" * 200,
        "tags": ["t{0}".format(i) for i in range(200)],
    }]})
    col = cd.file_collections()[0]
    assert len(col.title) == cd.MAX_TITLE_LEN
    assert len(col.tags) == cd.MAX_TAGS_PER_COLLECTION


def test_wrongly_typed_optional_fields_are_dropped_not_fatal(_isolated_collections_file):
    _write(_isolated_collections_file, {"version": 1, "collections": [dict(
        VALID["collections"][0], min_duration="long", require_audio="yes",
        exclude_tags="not-a-list",
    )]})
    col = cd.file_collections()[0]
    assert col.min_duration is None
    assert col.require_audio is None
    assert col.exclude_tags == ()


# ------------------------------------------------------------------- cache

def test_rewriting_the_file_takes_effect_without_a_restart(_isolated_collections_file):
    _write(_isolated_collections_file, VALID)
    assert [c.title for c in cd.file_collections()] == ["黑膠與唱盤"]
    _write(_isolated_collections_file, {"version": 1, "collections": [
        {"key": "topic_9f2a1c3d", "title": "改過的名字", "tags": ["黑膠唱片"]},
    ]})
    assert [c.title for c in cd.file_collections()] == ["改過的名字"]


# ------------------------------------------------------- derived-tag band

def test_the_derived_tag_band_matches_the_scorer_arithmetic():
    """MIN/MAX_DERIVED_TAGS are not style choices — they are the range where one
    shared tag can never create membership and two always can. Asserted against
    the real scorer, so moving either bound breaks this.

    k<=3 admits on ONE hit, which is exactly the PR #92 accident (a cable-making
    clip joining a food collection through the single shared tag 切割).
    """
    def base(k, hits):
        col = sc.Collection(key="topic_x", title="t", category="topic",
                            tags=tuple("tag%d" % i for i in range(k)))
        clip = {"duration_s": 3, "has_audio": 1,
                "tags": ["tag%d" % i for i in range(hits)]}
        return sc.score_collection(clip, col)

    for k in range(cd.MIN_DERIVED_TAGS, cd.MAX_DERIVED_TAGS + 1):
        assert base(k, 1) < sc.MIN_CONFIDENCE, "k=%d admitted on one hit" % k
        assert base(k, 2) >= sc.MIN_CONFIDENCE, "k=%d rejected two hits" % k

    # the bounds are tight: just outside the band the guarantees break
    assert base(cd.MIN_DERIVED_TAGS - 1, 1) >= sc.MIN_CONFIDENCE
    assert base(cd.MAX_DERIVED_TAGS + 1, 2) < sc.MIN_CONFIDENCE
