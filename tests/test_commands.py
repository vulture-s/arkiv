"""commands.py is the one write path for a clip's editorial fields.

Pins the split (leaf module; the router no longer writes these fields itself) and
the contract the HTTP tests in test_server.py only see from outside: omitted vs
explicit-null, and that a rejected or unknown-id write leaves the row untouched.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def clip(tmp_db, sample_record):
    import db
    db.upsert(sample_record(path="/tmp/clip.mp4", filename="clip.mp4"))
    return db.get_record_by_id(1)["id"]


def test_commands_is_a_leaf_module():
    src = (_ROOT / "commands.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(server|fastapi|routers)\b", src, re.M)


def test_router_no_longer_writes_editorial_fields_itself():
    src = (_ROOT / "routers" / "media.py").read_text(encoding="utf-8")
    # Each SQL statement is one string literal; check what each one sets.
    statements = re.findall(r"\"(UPDATE media SET[^\"\n]*)\"", src)
    assert statements, "pattern no longer finds the router's SQL — fix the test"
    for column in ("rating", "rating_note", "in_point", "out_point", "camera_id", "angle"):
        hits = [s for s in statements if re.search(r"\b{0}\s*=".format(column), s)]
        assert not hits, (column, hits)
    # The old generic form built its SET list at runtime.
    assert "UPDATE media SET {0}" not in src
    # The manual-tag routes delegate. (retry-vision still calls db.add_tag for
    # source="auto" tags inside its own transaction — a pipeline write, not an
    # editorial one, so it is out of scope here.)
    import inspect
    import routers.media as rm
    for handler in (rm.add_tag, rm.remove_tag):
        body = inspect.getsource(handler)
        assert "commands." in body and "db.add_tag(" not in body and "db.remove_tag(" not in body


def test_omitted_field_is_untouched_and_null_clears(clip):
    import commands
    import db
    assert commands.set_rating(clip, {"rating": "good", "note": "n"}) == {"rating": "good", "note": "n"}
    assert commands.set_rating(clip, {"rating": "ng"}) == {"rating": "ng", "note": "n"}
    assert commands.set_rating(clip, {"note": None}) == {"rating": "ng", "note": None}
    rec = db.get_record_by_id(clip)
    assert (rec["rating"], rec["rating_note"]) == ("ng", None)


def test_empty_changes_write_nothing_and_return_current_values(clip):
    import commands
    commands.set_camera(clip, {"camera_id": "A", "angle": "wide"})
    assert commands.set_camera(clip, {}) == {"camera_id": "A", "angle": "wide"}


def test_inverted_inout_is_rejected_and_nothing_is_written(clip):
    import commands
    import db
    commands.set_inout(clip, {"in_point": 1.0, "out_point": 4.0})
    # Checked against the MERGED window: only out_point is sent, and it lands
    # before the stored in_point.
    with pytest.raises(commands.Invalid):
        commands.set_inout(clip, {"out_point": 0.5})
    rec = db.get_record_by_id(clip)
    assert (rec["in_point"], rec["out_point"]) == (1.0, 4.0)


@pytest.mark.parametrize("call", [
    lambda c: c.set_rating(999, {"rating": "good"}),
    lambda c: c.set_inout(999, {"in_point": 1.0}),
    lambda c: c.set_camera(999, {"camera_id": "A"}),
    lambda c: c.add_tag(999, "x", "manual"),
])
def test_unknown_media_id_is_not_found(tmp_db, call):
    import commands
    with pytest.raises(commands.NotFound):
        call(commands)


def test_remove_tag_on_unknown_id_is_a_no_op(tmp_db, monkeypatch):
    import commands
    import embed
    monkeypatch.setattr(embed, "reindex_media", lambda media_id: None)
    assert commands.remove_tag(999, "x") == []


def test_failed_reindex_keeps_the_tag(clip, monkeypatch):
    import commands
    import embed

    def boom(media_id):
        raise RuntimeError("index down")

    monkeypatch.setattr(embed, "reindex_media", boom)
    tags = commands.add_tag(clip, "海邊", "manual")
    assert "海邊" in {t["name"] for t in tags}


# ── edit audit (.arkiv/audit/edits.jsonl) ────────────────────────────────────
@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    path = tmp_path / ".arkiv" / "audit" / "edits.jsonl"

    def lines():
        import json
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    return lines


def test_a_change_is_audited_with_before_after_and_actor(clip, audit_log):
    import commands
    commands.set_rating(clip, {"rating": "good"}, {"name": "editor-1"})
    commands.set_rating(clip, {"note": "再聽一次"}, {"name": "editor-1"})
    first, second = audit_log()
    assert first["action"] == "set_rating" and first["media_id"] == clip
    assert first["before"] == {"rating": None, "note": None}
    assert first["after"] == {"rating": "good", "note": None}
    assert first["actor"] == "editor-1"
    assert second["before"] == {"rating": "good", "note": None}
    assert second["after"] == {"rating": "good", "note": "再聽一次"}


def test_no_op_and_rejected_writes_are_not_audited(clip, audit_log):
    import commands
    commands.set_camera(clip, {})                                  # nothing sent
    commands.set_camera(clip, {"angle": None})                     # already None
    commands.set_inout(clip, {"in_point": 1.0, "out_point": 4.0})
    with pytest.raises(commands.Invalid):
        commands.set_inout(clip, {"out_point": 0.5})
    assert [l["action"] for l in audit_log()] == ["set_inout"]


def test_tag_edits_are_audited_as_name_sets(clip, audit_log, monkeypatch):
    import commands
    import embed
    monkeypatch.setattr(embed, "reindex_media", lambda media_id: None)
    commands.add_tag(clip, "海邊", "manual")
    commands.remove_tag(clip, "海邊")
    commands.remove_tag(clip, "海邊")                              # already gone
    added, removed = audit_log()
    assert "海邊" not in added["before"] and "海邊" in added["after"]
    assert removed["before"] == added["after"] and removed["after"] == added["before"]
    assert added["actor"] == "unknown"                            # no token passed


def test_an_unwritable_audit_dir_does_not_fail_the_edit(clip, tmp_path, monkeypatch):
    import commands
    import config
    import db
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setattr(config, "PROJECT_ROOT", blocker)          # mkdir under a file fails
    commands.set_rating(clip, {"rating": "ng"})
    assert db.get_record_by_id(clip)["rating"] == "ng"


def test_routes_pass_the_caller_token_through(fastapi_client, sample_record, audit_log):
    import db
    db.upsert(sample_record(path="/tmp/clip.mp4", filename="clip.mp4"))
    assert fastapi_client.patch("/api/media/1/rating", json={"rating": "good"}).status_code == 200
    (line,) = audit_log()
    assert line["actor"] == "pytest-admin"
