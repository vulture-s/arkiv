"""Unit tests for geo.py — EXIF GPS → location label generator.

Synthetic coordinates only; no real GPS data exists in the test corpus (all 5
明燒肉 clips have null GPS), but the generator is pure and fully exercisable.
"""
from __future__ import annotations

import geo


# Reference points (Taichung area, where Furutech / 明燒肉 footage is shot).
TAICHUNG = (24.1477, 120.6736)
TAIPEI = (25.0330, 121.5654)


def test_haversine_one_degree_latitude_is_about_111km():
    d = geo.haversine(24.0, 120.0, 25.0, 120.0)
    assert 110.0 < d < 112.0


def test_haversine_zero_distance():
    assert geo.haversine(*TAICHUNG, *TAICHUNG) == 0.0


def test_haversine_symmetric():
    a = geo.haversine(*TAICHUNG, *TAIPEI)
    b = geo.haversine(*TAIPEI, *TAICHUNG)
    assert abs(a - b) < 1e-9


# ── nearest_place ────────────────────────────────────────────────────────────
PLACES = [
    geo.Place("明燒肉", 24.1477, 120.6736, radius_km=0.3),
    geo.Place("Furutech", 24.1600, 120.6500, radius_km=0.3),
]


def test_nearest_place_inside_radius():
    p = geo.nearest_place(24.1478, 120.6737, PLACES)  # ~15 m away
    assert p is not None and p.name == "明燒肉"


def test_nearest_place_outside_all_radii_returns_none():
    assert geo.nearest_place(*TAIPEI, PLACES) is None  # ~140 km away


def test_nearest_place_picks_closest_when_multiple_match():
    # A point ~10 m from 明燒肉 but with both radii widened to overlap it.
    wide = [
        geo.Place("明燒肉", 24.1477, 120.6736, radius_km=50),
        geo.Place("Furutech", 24.1600, 120.6500, radius_km=50),
    ]
    p = geo.nearest_place(24.1477, 120.6736, wide)
    assert p.name == "明燒肉"


def test_nearest_place_no_gazetteer():
    assert geo.nearest_place(*TAICHUNG, []) is None


# ── coarse_cell ──────────────────────────────────────────────────────────────
def test_coarse_cell_format_and_hemispheres():
    assert geo.coarse_cell(24.1477, 120.6736) == "24.15N,120.67E"
    assert geo.coarse_cell(-33.8688, 151.2093) == "33.87S,151.21E"
    assert geo.coarse_cell(40.7128, -74.0060) == "40.71N,74.01W"


def test_coarse_cell_precision_buckets_nearby_points_together():
    # precision=1 ≈ 11 km cells; two points within the same cell collapse to one
    # label (rounding has boundary effects, so both are kept clear of 24.15).
    a = geo.coarse_cell(24.1100, 120.6900, precision=1)
    b = geo.coarse_cell(24.1400, 120.6600, precision=1)  # ~4 km away, same cell
    assert a == b == "24.1N,120.7E"


def test_coarse_cell_invalid_returns_none():
    assert geo.coarse_cell(None, None) is None


# ── location_label (the single entry point) ──────────────────────────────────
def test_location_label_prefers_named_place():
    assert geo.location_label(24.1478, 120.6737, PLACES) == "明燒肉"


def test_location_label_falls_back_to_coarse_cell():
    assert geo.location_label(*TAIPEI, PLACES) == "25.03N,121.57E"


def test_location_label_no_places_uses_cell():
    assert geo.location_label(*TAICHUNG) == "24.15N,120.67E"


def test_location_label_missing_coords_is_none():
    assert geo.location_label(None, None) is None
    assert geo.location_label(24.1, None) is None


def test_location_label_null_island_is_none():
    # The (0,0) a camera writes when it has no fix must NOT become a location.
    assert geo.location_label(0.0, 0.0) is None


def test_location_label_out_of_range_is_none():
    assert geo.location_label(95.0, 200.0) is None


def test_location_label_non_numeric_is_none():
    assert geo.location_label("n/a", "n/a") is None
