"""mediaprobe.py — can this environment actually read and decode THIS library's media?

`health.py` answers "is ffmpeg installed". That question has been green on every
machine where this went wrong. What it does not answer is "can the ffmpeg you have
decode the files you have", and that is where the silence comes from: extraction
returns nothing, whisper is handed nothing, the row is stored empty, and the
library looks like footage with nobody talking in it.

Three real cases, all measured on 2026-08-27/28, all of which pass a presence check:

* **The ffmpeg is codec-crippled.** Synology's bundled ffmpeg 4.1.9 lists `pcm_s16be`
  in `-decoders` and not `aac`. It demuxes an iPhone `.mov` happily, reports
  `Audio: aac`, and then says `Decoder (codec aac) not found`. Every iPhone clip in
  that library would transcribe to nothing, and nothing would say why.
* **The files cannot be read at all.** A macOS process without Full Disk Access gets
  `Operation not permitted` on an SMB-mounted NAS. The mount exists, the file
  `stat()`s, `ls` shows it — and ffmpeg cannot open it. `health.py`'s mount check
  passes, because it only asks whether `/Volumes/<name>` exists.
* **The tool being used to check is not the tool doing the work.** Probing on a
  different machine, or with a different binary, answers a different question. That
  is not hypothetical: three separate checks of the same nineteen clips gave three
  different answers before this module existed.

So the rule this module is built on: **the probe runs in the process that will do
the work, and exercises the same call the pipeline makes.** It shells out to
`config.FFMPEG_PATH` with `-map a:0 -af volumedetect`, exactly as
`transcribe._mean_volume_db` does, differing only by `-t` to bound the cost.

**What this cannot tell you**: it catches "cannot", not "wrong". A decoder that
works and a transcript that is a hallucination both come back OK here — every gate
is green when whisper invents `謝謝大家` over room tone. That needs a different
instrument. And it samples: it catches "this whole codec is unusable", not "clip
847 is corrupt".
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import config

OK = "ok"
MISSING = "missing"
UNREADABLE = "unreadable"
NO_AUDIO = "no_audio"
NO_DECODER = "no_decoder"
NOT_MEDIA = "not_media"
PROBE_FAILED = "probe_failed"

# Verdicts that mean "this file will silently produce an empty transcript".
# `NO_AUDIO` is deliberately absent: a clip with no voice on it is a fact about
# the footage, and a library can legitimately be full of them.
BLOCKING = (MISSING, UNREADABLE, NO_DECODER, NOT_MEDIA, PROBE_FAILED)

_AUDIO_STREAM = re.compile(r"Audio:\s*([A-Za-z0-9_]+)")
_ANY_STREAM = re.compile(r"Stream #\d+:\d+")
_NO_DECODER = re.compile(r"Decoder \(codec ([^)]+)\) not found")
_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")

DEFAULT_SECONDS = 5
DEFAULT_PER_BUCKET = 2
DEFAULT_MAX_FILES = 12


def bucket_key(path: str):
    """What makes two files "the same kind" for sampling purposes.

    Extension plus immediate parent directory, because that is how a shoot is
    actually laid out: `A7V/` is PCM off a Sony body and `iPhone Clip/reels/` is
    AAC off a phone, in the same library, and a sample that ignored the directory
    would have taken three files from one camera and declared the library fine.

    Chosen without touching the disk — the audio codec is what we actually want to
    group by, and learning it costs an ffmpeg run per file, which is the thing
    being budgeted.
    """
    p = Path(path)
    return (p.suffix.lower(), p.parent.name)


def choose_sample(paths: Sequence[str], per_bucket: int = DEFAULT_PER_BUCKET,
                  max_files: int = DEFAULT_MAX_FILES) -> List[str]:
    """A bounded, spread-out sample: up to `per_bucket` per kind, `max_files` total.

    Deterministic (first-seen order), so two runs on an unchanged library probe the
    same files and a difference in the report is a difference in the environment.
    """
    buckets: Dict[tuple, List[str]] = {}
    for p in paths:
        buckets.setdefault(bucket_key(p), []).append(p)
    out: List[str] = []
    # Round-robin across buckets rather than draining each in turn, so a cap that
    # bites still leaves every kind represented — draining would spend the whole
    # budget on whichever camera happens to sort first.
    for depth in range(per_bucket):
        for key in buckets:
            if len(out) >= max_files:
                return out
            if depth < len(buckets[key]):
                out.append(buckets[key][depth])
    return out


def probe_one(path: str, seconds: int = DEFAULT_SECONDS,
              ffmpeg: Optional[str] = None, timeout: int = 60) -> Dict:
    """Four gates, in the order they actually fail. Returns {verdict, codec, detail}."""
    if not os.path.exists(path):
        return {"path": path, "verdict": MISSING, "codec": None,
                "detail": "not found on disk"}
    try:
        # The read is the point: a file can exist, stat, and list while the process
        # is still forbidden to open it (macOS TCC on a network volume). ffmpeg
        # would report that as an input error and the caller would read it as bad
        # media.
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        return {"path": path, "verdict": UNREADABLE, "codec": None,
                "detail": "{0}: {1}".format(type(exc).__name__, exc)}

    cmd = [ffmpeg or config.FFMPEG_PATH, "-i", path, "-map", "a:0",
           "-t", str(seconds), "-af", "volumedetect", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"path": path, "verdict": PROBE_FAILED, "codec": None,
                "detail": "{0}: {1}".format(type(exc).__name__, exc)}
    err = (proc.stderr or b"").decode("utf-8", "replace")

    missing_decoder = _NO_DECODER.search(err)
    codec_match = _AUDIO_STREAM.search(err)
    codec = missing_decoder.group(1) if missing_decoder else (
        codec_match.group(1) if codec_match else None)

    if missing_decoder:
        return {"path": path, "verdict": NO_DECODER, "codec": codec,
                "detail": "this ffmpeg build has no decoder for it"}
    if codec_match is None:
        # A file with no audio and a file ffmpeg cannot open at all look the same
        # from "there was no Audio: line", and they must not be treated the same:
        # the first is a title card, the second is a zero-byte or truncated file
        # that will transcribe to nothing. ffmpeg separates them — it lists the
        # streams it found for real media, and lists nothing for a file it could
        # not parse.
        if _ANY_STREAM.search(err) is None:
            return {"path": path, "verdict": NOT_MEDIA, "codec": None,
                    "detail": "ffmpeg could not read this as media at all"}
        return {"path": path, "verdict": NO_AUDIO, "codec": None,
                "detail": "no audio stream"}
    if _MEAN_VOLUME.search(err) is None:
        # Demuxed but produced no samples. Not the same as "no decoder" — the
        # decoder may exist and still fail on this file — but the consequence for
        # the pipeline is identical, so it is reported as blocking either way.
        return {"path": path, "verdict": NO_DECODER, "codec": codec,
                "detail": "decoded no samples"}
    return {"path": path, "verdict": OK, "codec": codec, "detail": ""}


def ffmpeg_runnable(ffmpeg: Optional[str] = None) -> Optional[str]:
    """None if the binary runs, else why it doesn't.

    Checked once, up front, because "no ffmpeg" is one fact about the machine and
    reporting it as twelve unreadable files sends the reader to look at their
    footage. It matters more than it sounds: without ffmpeg `transcribe._to_wav`
    returns None, `transcribe()` returns the empty contract, the H1 guard declines
    to blank the existing row — and the batch reports success having done nothing.
    The same silence this module exists to break.
    """
    binary = ffmpeg or config.FFMPEG_PATH
    try:
        subprocess.run([binary, "-version"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "{0}: {1}".format(binary, exc)
    return None


def probe(paths: Sequence[str], resolve: Optional[Callable[[str], str]] = None,
          per_bucket: int = DEFAULT_PER_BUCKET, max_files: int = DEFAULT_MAX_FILES,
          seconds: int = DEFAULT_SECONDS, ffmpeg: Optional[str] = None) -> Dict:
    """Sample the library, run the gates, group the answer by the codec found.

    Grouped by codec because that is the shape the failure has: not "these three
    files are broken" but "nothing aac in this library can be read". A per-file
    list would bury that under whichever files the sample happened to draw.
    """
    sample = choose_sample(list(paths), per_bucket=per_bucket, max_files=max_files)
    broken_ffmpeg = ffmpeg_runnable(ffmpeg)
    if broken_ffmpeg:
        return {"sampled": 0, "of": len(paths), "files": [], "by_codec": {},
                "ffmpeg_error": broken_ffmpeg}
    results = [probe_one(resolve(p) if resolve else p, seconds=seconds, ffmpeg=ffmpeg)
               for p in sample]
    by_codec: Dict[str, Dict] = {}
    for r in results:
        key = r["codec"] or r["verdict"]
        slot = by_codec.setdefault(key, {"total": 0, "ok": 0, "verdicts": {}})
        slot["total"] += 1
        slot["ok"] += 1 if r["verdict"] == OK else 0
        slot["verdicts"][r["verdict"]] = slot["verdicts"].get(r["verdict"], 0) + 1
    return {"sampled": len(sample), "of": len(paths),
            "files": results, "by_codec": by_codec}


def blocking(result: Dict) -> List[str]:
    """Codec groups where NOTHING decoded — the "silently empty" condition.

    A group is only reported when every probed member failed. One bad file among
    good ones is a bad file; a whole kind failing is an environment that cannot do
    the job, and only the second should stop a four-hour batch.

    **`no_audio` never blocks**, which is why `BLOCKING` exists rather than "any
    non-OK verdict". A clip with no voice on it is a fact about the footage — a
    title card, a wild shot — and a library can be all of them. The first version
    of this function ignored `BLOCKING` and let a silent clip halt the run; a test
    caught it, which is the only reason the constant is load-bearing now.
    """
    if result.get("ffmpeg_error"):
        return ["ffmpeg will not run — {0}".format(result["ffmpeg_error"])]
    out = []
    for codec, slot in sorted(result.get("by_codec", {}).items()):
        blocks = any(v in BLOCKING for v in slot["verdicts"])
        if slot["ok"] == 0 and slot["total"] > 0 and blocks:
            kinds = ", ".join(sorted(slot["verdicts"]))
            out.append("{0}: 0/{1} usable ({2})".format(codec, slot["total"], kinds))
    return out


def format_report(result: Dict) -> str:
    if result.get("ffmpeg_error"):
        return ("media probe — ffmpeg will not run\n  {0}\n"
                "Nothing can be extracted without it; a batch would report success "
                "and produce nothing.".format(result["ffmpeg_error"]))
    lines = ["media probe — sampled {0} of {1} clip(s)".format(
        result.get("sampled", 0), result.get("of", 0))]
    for codec, slot in sorted(result.get("by_codec", {}).items()):
        mark = "ok  " if slot["ok"] == slot["total"] else ("FAIL" if slot["ok"] == 0 else "part")
        lines.append("  [{0}] {1:<14} {2}/{3} decodable".format(
            mark, codec, slot["ok"], slot["total"]))
    for r in result.get("files", []):
        if r["verdict"] != OK:
            lines.append("     {0}: {1} — {2}".format(
                Path(r["path"]).name, r["verdict"], r["detail"]))
    problems = blocking(result)
    if problems:
        lines.append("")
        lines.append("BLOCKING — these would transcribe to nothing, silently:")
        lines.extend("  " + p for p in problems)
    return "\n".join(lines)
