"""arkiv geo — EXIF GPS → location label generator (Tier 1).

Pure Python, no new deps, no external geocoding service (stays offline / fast /
non-blocking). Turns a clip's `gps_lat` / `gps_lon` (numeric EXIF, as captured by
ingest.py) into a stable, human-readable *location label* that Smart Collections
and the UI can group on.

Two-tier resolution:
  1. Named place — if the point falls within a configured `Place`'s radius, use
     its name (e.g. "明燒肉"). Gazetteer is operator-supplied (real coords live
     outside the repo); `DEFAULT_PLACES` ships empty on purpose — see below.
  2. Coarse cell — otherwise, a rounded-coordinate bucket like "24.15N,120.67E".
     Two clips shot near each other land in the same cell, so location grouping
     works even with no gazetteer at all.

Defensive: missing coords and the GPS "null island" (0.0, 0.0 — what many cameras
write when they have no fix) both resolve to None, never to a phantom location.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

# Operator gazetteer. Real shooting-location coordinates are personal data and
# live outside the repo — load them via `config`/env and pass to the functions
# below, or append to this list at runtime. Empty by design: the coarse-cell
# fallback means location grouping still works with zero named places.
DEFAULT_PLACES: List["Place"] = []

_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Place:
    """A named location with a match radius (km)."""

    name: str
    lat: float
    lon: float
    radius_km: float = 0.5


def _valid_coord(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) as floats if usable, else None.

    Rejects None, non-numeric, out-of-range, and the (0, 0) null-island that
    cameras emit when they have no GPS fix.
    """
    if lat is None or lon is None:
        return None
    try:
        flat = float(lat)
        flon = float(lon)
    except (TypeError, ValueError):
        return None
    if math.isnan(flat) or math.isnan(flon):
        return None
    if not (-90.0 <= flat <= 90.0) or not (-180.0 <= flon <= 180.0):
        return None
    if flat == 0.0 and flon == 0.0:
        return None  # null island = "no fix"
    return flat, flon


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points, in kilometres."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_place(lat: Any, lon: Any, places: Sequence[Place]) -> Optional[Place]:
    """Closest gazetteer place whose radius contains the point, or None."""
    coord = _valid_coord(lat, lon)
    if coord is None:
        return None
    flat, flon = coord
    best: Optional[Place] = None
    best_d = float("inf")
    for p in places:
        d = haversine(flat, flon, p.lat, p.lon)
        if d <= p.radius_km and d < best_d:
            best, best_d = p, d
    return best


def coarse_cell(lat: Any, lon: Any, precision: int = 2) -> Optional[str]:
    """Rounded-coordinate bucket, e.g. "24.15N,120.67E". None if coords unusable.

    `precision` is decimal places: 2 ≈ 1.1 km cells, 1 ≈ 11 km. Hemisphere
    suffixes keep labels stable and human-readable across the equator/meridian.
    """
    coord = _valid_coord(lat, lon)
    if coord is None:
        return None
    flat, flon = coord
    ns = "N" if flat >= 0 else "S"
    ew = "E" if flon >= 0 else "W"
    return "{0:.{p}f}{1},{2:.{p}f}{3}".format(abs(flat), ns, abs(flon), ew, p=precision)


def location_label(
    lat: Any,
    lon: Any,
    places: Optional[Sequence[Place]] = None,
    precision: int = 2,
) -> Optional[str]:
    """Best location label for a point: named place if matched, else coarse cell.

    Returns None when coords are missing/invalid/null-island. This is the single
    entry point ingest and Smart Collections call.
    """
    if places is None:
        places = DEFAULT_PLACES
    p = nearest_place(lat, lon, places)
    if p is not None:
        return p.name
    return coarse_cell(lat, lon, precision=precision)
