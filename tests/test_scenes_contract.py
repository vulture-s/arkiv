"""Byte-identity contract test for GET /api/media/{media_id}/scenes.

The per-scene shape is a PUBLISHED contract. routers/media.py names its consumers
outright — smart-edit, OpenMontage arkiv_clip_search, Vyra, Palmier — and the
2026-06-29 per-scene reshape was itself flagged a breaking change. Nothing else
in the suite pins the *wire format*: test_phase8.py's four scenes tests assert
values through response.json(), which is blind to key order, to a key flipping
between `null` and absent, and to float rendering. All three are contract.

So this freezes the exact response body. It exists to be a characterization test
for the scenes.py leaf extraction (round-5 #51 follow-on): it is authored and
merged against the pre-extraction route, so when the extraction lands and this
still passes untouched, that is evidence the move was byte-for-byte — not a
description of the new code written after the fact.

Two deliberate fixture choices:

- **Exact binary fractions** (0/5/12/20, duration 30). duration_s = end_s -
  start_s renders through repr(), so non-binary timestamps emit FP artifacts
  (5.5 → 12.3 gives 6.800000000000001). Those are deterministic and *could* be
  frozen, but freezing one encodes an IEEE-754 artifact as contract and invites
  a later "cleanup" to break this test for entirely the wrong reason.
- **Frame 2 has no thumbnail.** keyframe_url is appended last and conditionally
  (routers/media.py), so a scene without a thumbnail omits the key entirely. A
  plausible "tidy-up" — hoisting it into the dict literal as
  `"keyframe_url": ... or None` — changes both its position and the key set, and
  is invisible to every json.loads-based assertion in the suite. Scene 2 is the
  canary: without a keyframe-less scene in the fixture, the golden cannot see it.
"""
import importlib


# Real CJK in the source, encoded at import — not \xNN escapes. Equivalent bytes,
# but readable in review, and it doubles as the ensure_ascii=False pin: if the
# response ever escaped CJK to \uXXXX this comparison fails loudly.
_GOLDEN = (
    '{"media_id":1,"media_duration_s":30.0,"scenes":['
    # scene 0 — every vision field populated, CJK values, has a thumbnail
    '{"scene_index":0,"start_s":0.0,"end_s":5.0,"duration_s":5.0,'
    '"description":"手持走入店內","content_type":"Establishing","focus_score":3,'
    '"atmosphere":"紀實","energy":"中","edit_position":"開場","edit_reason":"建立場景",'
    '"stability":"穩定","exposure":"normal","audio_quality":"清晰",'
    '"keyframe_url":"/thumbnails/contract_f0.jpg"},'
    # scene 1 — partial ingest: absent vision fields are null, NOT omitted
    '{"scene_index":1,"start_s":5.0,"end_s":12.0,"duration_s":7.0,"description":"seg 1",'
    '"content_type":"B-Roll","focus_score":null,"atmosphere":null,"energy":null,'
    '"edit_position":null,"edit_reason":null,"stability":null,"exposure":null,'
    '"audio_quality":null,"keyframe_url":"/thumbnails/contract_f1.jpg"},'
    # scene 2 — no thumbnail: keyframe_url is ABSENT, not null. The canary.
    '{"scene_index":2,"start_s":12.0,"end_s":20.0,"duration_s":8.0,"description":"seg 2",'
    '"content_type":"A-Roll","focus_score":5,"atmosphere":null,"energy":null,'
    '"edit_position":null,"edit_reason":null,"stability":null,"exposure":null,'
    '"audio_quality":null},'
    # scene 3 — last scene closes at media.duration_s, not at a next frame
    '{"scene_index":3,"start_s":20.0,"end_s":30.0,"duration_s":10.0,"description":"seg 3",'
    '"content_type":null,"focus_score":null,"atmosphere":null,"energy":null,'
    '"edit_position":null,"edit_reason":null,"stability":null,"exposure":null,'
    '"audio_quality":null,"keyframe_url":"/thumbnails/contract_f3.jpg"}],'
    '"total":4}'
).encode("utf-8")

_SCENE_KEYS = [
    "scene_index",
    "start_s",
    "end_s",
    "duration_s",
    "description",
    "content_type",
    "focus_score",
    "atmosphere",
    "energy",
    "edit_position",
    "edit_reason",
    "stability",
    "exposure",
    "audio_quality",
]

_ENVELOPE_KEYS = ["media_id", "media_duration_s", "scenes", "total"]


def _seed(db, sample_record):
    # duration_s is passed explicitly: sample_record is a factory holding a
    # mutable counter (conftest.py) and defaults to 30.0 + idx, so the default
    # silently depends on how many times it has been called in this test.
    db.upsert(sample_record(path="/tmp/contract.mp4", duration_s=30.0))
    db.upsert_frame(
        media_id=1, frame_index=0, timestamp_s=0.0,
        thumbnail_path="thumbnails/contract_f0.jpg",
        description="手持走入店內", tags="門市,走位",
        content_type="Establishing", focus_score=3,
        atmosphere="紀實", energy="中",
        edit_position="開場", edit_reason="建立場景",
        stability="穩定", exposure="normal", audio_quality="清晰",
    )
    db.upsert_frame(
        media_id=1, frame_index=1, timestamp_s=5.0,
        thumbnail_path="thumbnails/contract_f1.jpg",
        description="seg 1", content_type="B-Roll",
    )
    # no thumbnail_path — see the module docstring
    db.upsert_frame(
        media_id=1, frame_index=2, timestamp_s=12.0,
        description="seg 2", content_type="A-Roll", focus_score=5,
    )
    db.upsert_frame(
        media_id=1, frame_index=3, timestamp_s=20.0,
        thumbnail_path="thumbnails/contract_f3.jpg",
        description="seg 3",
    )


def test_scenes_response_bytes_are_golden(fastapi_client, sample_record):
    """The whole wire format, frozen. Key order, key set, float rendering, CJK
    encoding and separators all ride on this single assertion."""
    db = importlib.import_module("db")
    _seed(db, sample_record)

    response = fastapi_client.get("/api/media/1/scenes")

    assert response.status_code == 200
    assert response.content == _GOLDEN


def test_scenes_key_order_is_stable(fastapi_client, sample_record):
    """Key order is contract on its own terms, spelled out rather than left
    implicit in the golden — a byte diff on a 1.2KB literal is unreadable, so
    when key order is what broke, this is the test that says so."""
    db = importlib.import_module("db")
    _seed(db, sample_record)

    data = fastapi_client.get("/api/media/1/scenes").json()

    assert list(data.keys()) == _ENVELOPE_KEYS
    # scene 0 has a thumbnail: keyframe_url comes LAST, after audio_quality
    assert list(data["scenes"][0].keys()) == _SCENE_KEYS + ["keyframe_url"]
    # scene 2 has none: the key is absent entirely, not present-and-null
    assert list(data["scenes"][2].keys()) == _SCENE_KEYS
    assert "keyframe_url" not in data["scenes"][2]
