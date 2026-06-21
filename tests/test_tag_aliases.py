"""tag_aliases: library-level alias-map folding of the global tag cloud.

The map is reversible/non-destructive — these tests drive it through a temp
JSON file (config.TAG_ALIASES_PATH) and assert fold/expand behaviour, including
the no-map no-op and malformed-file fail-soft.
"""
import json

import config
import tag_aliases


def _write_map(tmp_path, groups):
    p = tmp_path / "tag_aliases.json"
    p.write_text(json.dumps({"version": 1, "groups": groups}, ensure_ascii=False), encoding="utf-8")
    config.TAG_ALIASES_PATH = p
    # bust the mtime cache so the new file is picked up within one test process
    tag_aliases._CACHE["mtime"] = None
    return p


def test_no_map_is_noop(tmp_path):
    config.TAG_ALIASES_PATH = tmp_path / "absent.json"
    tag_aliases._CACHE["mtime"] = None
    recs = [{"name": "賽事", "count": 5}, {"name": "運動會", "count": 3}]
    assert tag_aliases.fold_records(recs) == recs
    assert tag_aliases.is_active() is False


def test_fold_merges_alts_into_pref_and_sums(tmp_path):
    _write_map(tmp_path, [{"pref": "賽事", "alts": ["運動會", "比賽"]}])
    recs = [
        {"name": "賽事", "count": 16},
        {"name": "運動會", "count": 8},
        {"name": "比賽", "count": 4},
        {"name": "跑步", "count": 17},
    ]
    out = tag_aliases.fold_records(recs)
    by = {r["name"]: r for r in out}
    assert by["賽事"]["count"] == 28          # 16 + 8 + 4
    assert set(by["賽事"]["aliases"]) == {"運動會", "比賽"}
    assert "運動會" not in by and "比賽" not in by  # alts folded away
    assert by["跑步"]["count"] == 17           # untouched concept survives
    assert out[0]["name"] == "賽事"            # 28 > 17, sorted by merged count


def test_fold_pref_with_no_own_row(tmp_path):
    # The pref term itself may have zero direct tags — only alts exist.
    _write_map(tmp_path, [{"pref": "賽事", "alts": ["運動會"]}])
    out = tag_aliases.fold_records([{"name": "運動會", "count": 8}])
    assert out == [{"name": "賽事", "count": 8, "aliases": ["運動會"]}]


def test_expand_pref_to_all_spellings(tmp_path):
    _write_map(tmp_path, [{"pref": "賽事", "alts": ["運動會", "比賽"]}])
    assert set(tag_aliases.expand("賽事")) == {"賽事", "運動會", "比賽"}
    assert set(tag_aliases.expand("運動會")) == {"賽事", "運動會", "比賽"}  # alt → whole ring
    assert tag_aliases.expand("跑步") == ["跑步"]  # unmapped → identity


def test_malformed_map_fails_soft(tmp_path):
    p = tmp_path / "tag_aliases.json"
    p.write_text("{ not valid json", encoding="utf-8")
    config.TAG_ALIASES_PATH = p
    tag_aliases._CACHE["mtime"] = None
    recs = [{"name": "賽事", "count": 5}]
    assert tag_aliases.fold_records(recs) == recs  # behaves as no map
