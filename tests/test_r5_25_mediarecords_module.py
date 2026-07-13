"""R5-25 (round-5 #51): bulk media-record helpers extracted to mediarecords.py.

_get_tags_bulk (H15) and _get_light_records_by_ids (H16) are shared by the media
group (routers/media.py) AND the search group (structured_query / search_all,
which stay in server.py for a later peel). Extracting them into a leaf module
breaks the router→server→router import cycle #51 exists to fix.

These tests pin the same two invariants as pathres:
  1. mediarecords is a genuine leaf — it imports no server state.
  2. server.py re-exports the names BY IDENTITY, so the search handlers that stay
     in server keep calling the exact same object the media router calls.
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = ("_get_tags_bulk", "_get_light_records_by_ids")


def test_mediarecords_is_a_leaf_module():
    src = (_ROOT / "mediarecords.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_server_reexports_mediarecords_by_identity():
    import server
    import mediarecords
    for name in _NAMES:
        assert getattr(server, name) is getattr(mediarecords, name), (
            "server.{0} must BE mediarecords.{0} (a re-export), so structured_query"
            " / search_all keep calling the SAME object the media router calls".format(name)
        )


def test_mediarecords_functions_are_importable_standalone():
    import mediarecords
    for name in _NAMES:
        assert callable(getattr(mediarecords, name))
