"""R5-24: mediatypes.py is the single source of truth for the media extension
sets. These tests fail if any module re-introduces a hand-copied literal (the
identity checks break) or if db.py's SQL video filter drifts away from .insv/.360
again (the SQL check breaks).
"""
import mediatypes


def test_video_set_includes_360():
    # The whole point of the fix: 360 rigs are video.
    assert ".insv" in mediatypes.VIDEO_EXT
    assert ".360" in mediatypes.VIDEO_EXT
    assert mediatypes.VIDEO_360_EXT == {".insv", ".360"}
    assert mediatypes.VIDEO_360_EXT <= mediatypes.VIDEO_EXT


def test_media_partitions_into_video_and_audio():
    assert mediatypes.VIDEO_EXT | mediatypes.AUDIO_EXT == mediatypes.MEDIA_EXT
    assert mediatypes.VIDEO_EXT.isdisjoint(mediatypes.AUDIO_EXT)


def test_every_module_references_the_shared_set():
    """Each module binds its extension name to the SAME shared object — no copies."""
    import ingest
    import watch
    import query_builder
    import frames
    import routers.media as rm  # R5-25 #51: the media-search ext buckets moved here
    import routers.ingest as ri  # R5-25 #51: the scan-manifest ext buckets moved here

    assert ingest.VIDEO_EXT is mediatypes.VIDEO_EXT
    assert ingest.SUPPORTED is mediatypes.MEDIA_EXT

    # R5-25 #51: the ingest-scan ext buckets (VIDEO_EXTS/AUDIO_EXTS/MEDIA_EXTS)
    # moved to routers/ingest.py with the /api/ingest family; still the shared object.
    assert ri.VIDEO_EXTS is mediatypes.VIDEO_EXT
    assert ri.AUDIO_EXTS is mediatypes.AUDIO_EXT
    assert ri.MEDIA_EXTS is mediatypes.MEDIA_EXT
    # R5-25 #51: _VIDEO_EXTS/_AUDIO_EXTS (list_media's search-branch filter buckets)
    # moved to routers/media.py with the media route group; still the shared object.
    assert rm._VIDEO_EXTS is mediatypes.VIDEO_EXT
    assert rm._AUDIO_EXTS is mediatypes.AUDIO_EXT

    assert watch.MEDIA_EXTS is mediatypes.MEDIA_EXT

    assert query_builder._VIDEO_EXTS is mediatypes.VIDEO_EXT
    assert query_builder._AUDIO_EXTS is mediatypes.AUDIO_EXT

    assert frames._FISHEYE_360_EXT is mediatypes.VIDEO_360_EXT


def test_db_video_filter_now_includes_360():
    """db.py's SQL video filter is built from the shared set — the drift bug fix."""
    import db

    clause, _params = db._build_filter_clause(media_type="video")
    assert ".insv" in clause
    assert ".360" in clause
    # every video ext appears in the generated predicate
    for ext in mediatypes.VIDEO_EXT:
        assert "'{0}'".format(ext) in clause

    audio_clause, _ = db._build_filter_clause(media_type="audio")
    assert ".insv" not in audio_clause
    assert ".mp3" in audio_clause


def test_sql_in_literal_shape():
    lit = mediatypes.sql_in_literal(frozenset({".mp4", ".mov"}))
    assert lit == "('.mov', '.mp4')"  # sorted, quoted, parenthesized
