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
