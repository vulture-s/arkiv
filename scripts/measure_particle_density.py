#!/usr/bin/env python3
"""Re-measure the spoken-texture numbers `transcript_compare` quotes.

Those numbers — 541 transcripts, 22,799 characters, 134 marker hits, and the
0.90 vs 1.46 engine ordering — decided the marker set and the length below which
a percentage is not reported. They were measured once, on libraries that live on a
NAS and two other machines, and written into a docstring. A reader with the repo in
front of them had no way to check any of it, and I had no way to re-check it after
changing the set. This is that way.

    python scripts/measure_particle_density.py <library.db> [more.db ...]

Reads `media.transcript` and `transcripts.transcript` from each database, opened
read-only — it never writes, so it is safe to point at a live library. Prints the
corpus totals, the length distribution (the reason the per-clip percentage is
withheld below a threshold), and how much of the corpus clears it.

To compare two ENGINES rather than describe one corpus, point it at two databases
holding the same clips and read the two density lines against each other. The
absolute value depends on the marker set; only the ordering is robust.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcript_compare as tc  # noqa: E402

_BUCKETS = [(0, 20), (20, 40), (40, 80), (80, 200), (200, 1000), (1000, None)]


def _texts(db_path: str):
    """Every DISTINCT non-empty transcript in one library, active and archived.

    `media.transcript` mirrors whichever language is active, and the endpoint
    lazily writes that same text into `transcripts` — so a naive union over both
    tables counts every active transcript twice. Keyed by (media_id, lang), with
    `media` read first because it is the authoritative copy of the active one.
    """
    con = sqlite3.connect("file:{0}?mode=ro".format(db_path), uri=True)
    con.row_factory = sqlite3.Row
    seen = set()
    try:
        rows = con.execute(
            "SELECT id AS mid, lang, transcript AS t FROM media "
            "WHERE transcript IS NOT NULL AND TRIM(transcript) <> ''")
        for r in rows:
            seen.add((r["mid"], r["lang"]))
            yield r["lang"], r["t"]
        try:
            rows = con.execute(
                "SELECT media_id AS mid, lang, transcript AS t FROM transcripts "
                "WHERE transcript IS NOT NULL AND TRIM(transcript) <> ''")
        except sqlite3.Error:
            return  # a database predating the per-language archive
        for r in rows:
            if (r["mid"], r["lang"]) not in seen:
                yield r["lang"], r["t"]
    finally:
        con.close()


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[2].strip() or "usage: see module docstring")
        print("usage: measure_particle_density.py <library.db> [more.db ...]")
        return 2

    corpus = []
    for db_path in argv:
        if not Path(db_path).exists():
            print("{0}: not found".format(db_path))
            continue
        got = [(lang, t) for lang, t in _texts(db_path)]
        corpus.extend(got)
        chars = sum(len(t) for _l, t in got)
        hits = sum(tc.particle_count(t) for _l, t in got)
        print("{0}: {1} transcripts, {2} chars, {3} hits ({4:.2f}%)".format(
            db_path, len(got), chars, hits, _pct(hits, chars)))

    if not corpus:
        print("nothing to measure")
        return 1

    chars = sum(len(t) for _l, t in corpus)
    hits = sum(tc.particle_count(t) for _l, t in corpus)
    print("\nCORPUS  {0} transcripts · {1} chars · {2} hits · density {3:.2f}%".format(
        len(corpus), chars, hits, _pct(hits, chars)))
    print("markers  particles={0!r}  taigi={1!r}  words={2}".format(
        tc.PARTICLE_MARKERS, tc.TAIGI_MARKERS, "".join(tc.TAIGI_WORDS)))

    print("\nlength distribution — why a per-clip percentage is withheld below "
          "{0} chars:".format(tc.DENSITY_MIN_CHARS))
    print("{0:<12}{1:>7}{2:>9}{3:>12}".format("chars", "clips", "share", "1 marker ="))
    for lo, hi in _BUCKETS:
        sel = [t for _l, t in corpus if lo <= len(t) < (hi if hi is not None else 10 ** 12)]
        if not sel:
            continue
        mid = (lo + (hi if hi is not None else lo * 2)) / 2 or 1
        print("{0:<12}{1:>7}{2:>8.0f}%{3:>11.2f}%".format(
            "{0}-{1}".format(lo, hi if hi is not None else "∞"),
            len(sel), _pct(len(sel), len(corpus)), 100.0 / mid))

    measurable = [t for _l, t in corpus if len(t) >= tc.DENSITY_MIN_CHARS]
    print("\n{0} of {1} transcripts ({2:.0f}%) are long enough for a percentage; "
          "the rest report a count only.".format(
              len(measurable), len(corpus), _pct(len(measurable), len(corpus))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
