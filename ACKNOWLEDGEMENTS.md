# Acknowledgements

## pen — the VAD timestamp defect, and the fix

In August 2026 `pen` reported that clicking a transcript timecode in the Inspector
seeked to the wrong place, or jumped back to 00:00, and sent a 20-patch series.

It was not a bug in their setup. `_vad_filter()` ran Silero VAD, concatenated the
speech it kept into a gapless WAV, and returned only the path — the `stamps` that
said where that speech came from went out of scope, and the trimmed file was
unlinked moments later. Every timestamp Whisper reported was therefore in
gapless-speech time while every other clock in arkiv (frames, waveform,
`<video>.currentTime`, EDL source TC) is media time. The error equals the silence
removed before that line, so it grows along the clip.

Measured on a real library after the fix: a caption labelled 0.00 s was actually
at 4.62 s on an 8-second clip; on a 2m48s clip the last line was labelled 60.7 s
and belonged at 167.0 s.

**pen had already fixed it.** Patch 0019 in that series arrived on 2026-08-22 with
the same design arkiv shipped two days later — an offset map returned from
`_vad_filter`, remapping applied to segments *and* words before anything
downstream sees them, `ARKIV_VAD_THRESHOLD` exposed, and oversized segments
re-wrapped through `subtitle.wrap()`'s 14-CJK-unit budget with each line's span
sized **proportionally to its display width rather than an equal 1/n split**,
because an equal split puts a click's seek target at the segment's geometric
midpoint. arkiv reached the same conclusions independently and rebuilt them
test-first; the convergence is the strongest evidence the diagnosis was right.

Work in this repository that implements what pen had already written and sent:

| pen's patch | shipped as |
|---|---|
| 0019 VAD timestamp drift, oversized-segment re-wrap | #350, #353 |
| 0011 chunked LLM punctuation polish | #352 |
| 0004 waveform prefers the H.264 proxy | #363 |
| 0016 prefer an editor-made proxy beside the source | #364, #365 |
| 0005 auto-start Ollama on app launch | #367 |
| 0008–0010, 0013, 0014 transcript / punctuation output | #354, #356, #357 |

### Correction

PRs #349, #350, #351, #356 and #357 carry a
`Co-authored-by: Penny <penny@users.noreply.github.com>` trailer. **That address
was invented and is wrong.** `penny@users.noreply.github.com` resolves to
[github.com/Penny](https://github.com/Penny), an unrelated account with no
connection to this project or to the work described above. The trailers cannot be
removed from merged history without rewriting `main`, so the correction lives
here.

pen's own Git identity is on the patches they sent. It is deliberately not
reproduced here: per this project's practice, a contributor is asked before being
named, and that question is outstanding. If pen tells us how they would like to be
credited — a GitHub account, a different name, or not at all — this page and the
project's public credits will follow it.

---

*Attribution rule that came out of this: never construct a
`<something>@users.noreply.github.com` address. Those resolve to whoever holds
that account. Use the contributor's own Git author line, or credit them by name
here.*
