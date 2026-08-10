"""`--propose-collections` / `--apply-collections`, driven through the real handlers.

The Codex audit of #292 returned eight blockers; four of them lived on these two
CLI paths and were only ever reproduced by hand against a copy of the real
library. That is a gap, not a verification — a hand check does not survive the
next refactor. These drive `ingest._run_propose_collections` /
`ingest._run_apply_collections` directly so the assertions land on the shipped
decision logic rather than on a paraphrase of it (the trap
`test_ingest_llm_shape.py:107` fell into, copying a guard loop into the test).

ISOLATION: both config paths are rebound to tmp and the loader cache is busted,
or these would read and OVERWRITE the developer's real
`~/.arkiv/collections.json`. `fastapi_client` does not isolate PROJECT_ROOT.
"""
from __future__ import annotations

import argparse
import json

import pytest

import collection_defs as cd
import config
import ingest


@pytest.fixture(autouse=True)
def _isolated_collection_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COLLECTIONS_PATH", tmp_path / "collections.json")
    monkeypatch.setattr(config, "COLLECTIONS_PROPOSED_PATH", tmp_path / "collections.proposed.json")
    cd._CACHE["mtime"] = None
    yield
    cd._CACHE["mtime"] = None


def _args(**kw):
    return argparse.Namespace(**{
        "collection_min_tag_count": 3, "collection_min_members": 4,
        "collection_max_share": 0.35, "collection_dry_run": True, **kw,
    })


def _valid_entry(key="topic_deadbeef", title="黑膠與唱盤"):
    return {
        "key": key, "title": title, "category": "topic",
        "tags": ["黑膠唱片", "唱針", "轉盤", "唱片架"], "origin": "derived",
    }


def _clip(mid, tags):
    """A row in the shape `smart_collections.media_signal` actually consumes.

    `frame_tags` is a JSON list of per-FRAME objects, each carrying its own
    `tags` — not a flat list of tag names. A fixture that gets this wrong yields
    an empty signal and every assertion below passes for the wrong reason.
    """
    return {"id": mid, "filename": "c%d.mp4" % mid,
            "frame_tags": json.dumps([{"tags": list(tags)}], ensure_ascii=False)}


def _write_proposal(payload):
    config.COLLECTIONS_PROPOSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PROPOSED_PATH.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── apply: every failure path exits NON-ZERO ─────────────────────────────────
# --apply-aliases prints and returns on all of these, so a script reads a missing
# or malformed proposal as "applied". That silent-success shape is what
# --migrate-storage was fixed for in #290; apply must not reintroduce it.

def test_apply_with_no_proposal_exits_nonzero():
    with pytest.raises(SystemExit) as e:
        ingest._run_apply_collections(_args())
    assert e.value.code == 1
    assert not config.COLLECTIONS_PATH.exists()


@pytest.mark.parametrize("payload, why", [
    ([], "top level is a list, not an object"),
    ({"version": 2, "collections": [_valid_entry()]}, "unknown format version"),
    ({"version": 1, "collections": 42}, "`collections` is not a list"),
    ({"version": 1, "collections": []}, "nothing to apply"),
])
def test_apply_rejects_malformed_proposals(payload, why):
    _write_proposal(payload)
    with pytest.raises(SystemExit) as e:
        ingest._run_apply_collections(_args())
    assert e.value.code == 1, why
    assert not config.COLLECTIONS_PATH.exists(), "a rejected proposal must write nothing"


def test_apply_is_all_or_nothing(capsys):
    # BLOCKER: apply used to drop the invalid entries, write the survivors and exit
    # 0. A reviewer approves a set, not whatever subset clears validation.
    _write_proposal({"version": 1, "collections": [
        _valid_entry(), {"key": "bad key!", "title": "x", "tags": []}, "naked string",
    ]})
    with pytest.raises(SystemExit) as e:
        ingest._run_apply_collections(_args())
    assert e.value.code == 1
    assert not config.COLLECTIONS_PATH.exists(), "nothing may be written"
    out = capsys.readouterr().out
    assert "#1" in out and "#2" in out, "the offending entries must be named, not just counted"


def test_apply_leaves_a_working_config_intact_when_it_refuses():
    # The failure mode that matters: an operator with a live collections.json runs
    # apply on a broken proposal. Refusing is only half of it — the file that was
    # already working has to still be there afterwards.
    good = {"version": 1, "collections": [_valid_entry(key="topic_11111111")]}
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    _write_proposal({"version": 1, "collections": [{"key": "bad key!", "title": "x"}]})

    with pytest.raises(SystemExit):
        ingest._run_apply_collections(_args())

    assert json.loads(config.COLLECTIONS_PATH.read_text(encoding="utf-8")) == good


def test_apply_carries_disable_through_and_drops_unknown_keys():
    # BLOCKER: a `disable` the project had set was dropped on the propose→apply
    # round trip, silently re-enabling a built-in the project had turned off.
    _write_proposal({
        "version": 1, "collections": [_valid_entry()],
        "disable": ["b_roll", "not_a_builtin"],
    })
    ingest._run_apply_collections(_args())
    written = json.loads(config.COLLECTIONS_PATH.read_text(encoding="utf-8"))
    assert written["disable"] == ["b_roll"]
    assert written["collections"][0]["key"] == "topic_deadbeef"


def test_apply_strips_the_stale_marker():
    _write_proposal({"version": 1, "collections": [dict(_valid_entry(), stale=True)]})
    ingest._run_apply_collections(_args())
    entry = json.loads(config.COLLECTIONS_PATH.read_text(encoding="utf-8"))["collections"][0]
    assert "stale" not in entry, "an applied collection is by definition not stale"


def test_apply_takes_effect_without_a_restart():
    _write_proposal({"version": 1, "collections": [_valid_entry()]})
    ingest._run_apply_collections(_args())
    assert any(c.key == "topic_deadbeef" for c in cd.load_collections())


# ── propose ──────────────────────────────────────────────────────────────────

def test_propose_survives_a_live_file_the_loader_accepts(monkeypatch, capsys):
    # BLOCKER: the loader is fail-soft about `collections` being a non-list, but
    # propose reached a list comprehension over it and died with TypeError — a
    # crash on a file the rest of the system reads without complaint.
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text('{"version": 1, "collections": 42}', encoding="utf-8")
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())  # must not raise

    assert config.COLLECTIONS_PROPOSED_PATH.exists()
    assert "empty proposal" in capsys.readouterr().out


def test_an_empty_derivation_supersedes_an_earlier_proposal(monkeypatch):
    # BLOCKER: an early return left the previous proposal on disk, so a later
    # apply could activate definitions derived from a library that no longer
    # exists. "This run found nothing" is a result, and it has to win.
    _write_proposal({"version": 1, "collections": [_valid_entry()]})
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())

    written = json.loads(config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))
    assert written["collections"] == []


def test_propose_carries_disable_through_from_the_live_file(monkeypatch):
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text(
        json.dumps({"version": 1, "collections": [], "disable": ["b_roll"]}), encoding="utf-8")
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())

    assert json.loads(
        config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["disable"] == ["b_roll"]


def test_propose_leaves_a_hand_written_collection_completely_alone(monkeypatch):
    # Re-running derivation is a diff against what is live, never a rebuild. A
    # hand-written collection is not derived, so it can never become "no longer
    # derivable" — it must come through untouched, not merely un-renamed.
    hand = _valid_entry(key="custom_mine", title="我自己寫的")
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text(
        json.dumps({"version": 1, "collections": [hand]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())

    merged = json.loads(config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"]
    assert merged == [hand], "a hand-written entry must survive derivation byte for byte"


def test_propose_flags_a_derived_collection_that_no_longer_derives(monkeypatch):
    # Hysteresis: an ingest that temporarily starves a topic must FLAG the
    # definition, never delete it — the next ingest may well restore it, and a
    # deletion would repoint every reference to a key that no longer exists.
    derived = _valid_entry(key="topic_abcd1234", title="黑膠與唱盤")
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text(
        json.dumps({"version": 1, "collections": [derived]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())

    merged = json.loads(config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"]
    assert len(merged) == 1
    assert merged[0]["key"] == "topic_abcd1234"
    assert merged[0]["title"] == "黑膠與唱盤", "the frozen title must not be regenerated"
    assert merged[0]["stale"] is True


def test_a_duplicated_key_resolves_the_way_the_loader_resolves_it(monkeypatch):
    # BLOCKER: merge kept the LAST duplicate while the loader keeps the first, so
    # merely re-running derivation over a file with a duplicated key swapped which
    # definition was effective — a silent rename, from the function whose whole
    # purpose is preventing renames.
    first = _valid_entry(key="topic_abcd1234", title="第一個")
    second = _valid_entry(key="topic_abcd1234", title="第二個")
    config.COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECTIONS_PATH.write_text(
        json.dumps({"version": 1, "collections": [first, second]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(cd, "classification_records", lambda: [])

    ingest._run_propose_collections(_args())

    merged = json.loads(config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"]
    assert [c["title"] for c in merged] == ["第一個"]


def test_a_single_topic_library_proposes_nothing(monkeypatch):
    # The reference vinyl library's correct output. Every frequent tag co-occurs
    # with every other, so any candidate holds most of the library — the honest
    # answer is zero, and a run that produces collections here is the bug.
    records = [_clip(i, ["黑膠唱片", "唱盤", "室內", "音響"]) for i in range(20)]
    monkeypatch.setattr(cd, "classification_records", lambda: records)

    ingest._run_propose_collections(_args())

    assert json.loads(
        config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"] == []


def test_a_multi_topic_library_proposes_its_topics(monkeypatch):
    # The positive control the real library cannot give: three disjoint topics, 6
    # clips each, no shared vocabulary. Without this, "proposes nothing" passes
    # for a pipeline that is simply broken.
    topics = [
        ["黑膠唱片", "唱針", "轉盤", "唱片架", "唱片封面"],
        ["登山背包", "帳篷", "登山杖", "睡袋", "營地"],
        ["烘豆機", "手沖壺", "濾杯", "咖啡豆", "磨豆機"],
    ]
    records = [_clip(t * 10 + i, tags) for t, tags in enumerate(topics) for i in range(6)]
    monkeypatch.setattr(cd, "classification_records", lambda: records)

    ingest._run_propose_collections(_args())

    proposed = json.loads(
        config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"]
    assert len(proposed) == 3, "one per topic"
    for entry in proposed:
        n = len(entry["tags"])
        assert cd.MIN_DERIVED_TAGS <= n <= cd.MAX_DERIVED_TAGS, (
            "%d tags is outside the band where two hits are required" % n)
        assert entry["derived"]["members_at_build"] == 6
    # Disjoint by construction, so no two may claim the same clip.
    assert len({e["key"] for e in proposed}) == 3


def test_propose_then_apply_round_trips(monkeypatch):
    # Three topics, not two: with two, each holds 50% of the library and the
    # max_share gate correctly rejects both. A collection that holds half the
    # library IS the library — that gate is the whole point of the design.
    topics = [
        ["黑膠唱片", "唱針", "轉盤", "唱片架", "唱片封面"],
        ["登山背包", "帳篷", "登山杖", "睡袋", "營地"],
        ["烘豆機", "手沖壺", "濾杯", "咖啡豆", "磨豆機"],
    ]
    records = [_clip(t * 10 + i, tags) for t, tags in enumerate(topics) for i in range(6)]
    monkeypatch.setattr(cd, "classification_records", lambda: records)

    ingest._run_propose_collections(_args())
    ingest._run_apply_collections(_args())

    keys = {c.key for c in cd.load_collections()}
    proposed = json.loads(
        config.COLLECTIONS_PROPOSED_PATH.read_text(encoding="utf-8"))["collections"]
    assert {e["key"] for e in proposed} <= keys
    # A file entry can never carry a predicate — the config file must not be able
    # to inject anything executable into the classifier.
    assert all(c.predicate is None for c in cd.load_collections() if c.key.startswith("topic_"))


# ── the atomic write ─────────────────────────────────────────────────────────

def test_a_failed_write_leaves_the_previous_file_untouched(tmp_path):
    # BLOCKER: Path.write_text opens O_TRUNC, so a disk-full or a kill partway
    # through left a half-written file where a valid configuration used to be.
    target = tmp_path / "collections.json"
    target.write_text('{"version": 1, "collections": []}', encoding="utf-8")

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        ingest._atomic_write_json(target, {"collections": [Unserialisable()]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1, "collections": []}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, "the temp file must be cleaned up: %r" % leftovers
