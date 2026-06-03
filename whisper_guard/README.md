# whisper-guard

Pure, dependency-free **text-level hallucination filters** for Whisper transcripts.

Whisper — and its `faster-whisper` / MLX variants — hallucinate on silence and
music: looping phrases (`字幕由字幕由字幕由…`), repeated n-grams, and degenerate
character cycles. `whisper-guard` is the small set of string checks that catch
those, extracted from a production media pipeline ([arkiv](https://github.com/vulture-s/arkiv))
so any transcription project can drop them in.

> Scope: this is the **text** layer (post-decode). Audio-side defenses (VAD,
> decode params, LLM polish) live in the host pipeline — these are the portable,
> pure-function pieces.

## Install

```bash
pip install whisper-guard   # stdlib only, no heavy deps
```

## Use

```python
from whisper_guard import HallucinationGuard, is_repetitive, has_char_loops, remove_char_loops

g = HallucinationGuard()
g.is_hallucination("好好好好好好好好好好")   # True  — looped, drop the segment
g.clean("字幕由字幕由字幕由")                 # "字幕由" — collapse char-loops

# or the functional API
is_repetitive("字幕由字幕由字幕由字幕由")     # True
has_char_loops("哈哈哈哈哈哈")               # True
remove_char_loops("哈哈哈哈哈哈")            # "哈"
```

## API

| Function | Returns | Notes |
|---|---|---|
| `is_repetitive(text, window=6, threshold=0.35)` | `bool` | text dominated by repeated fixed-width chunks; short text never flagged |
| `has_char_loops(text, min_pattern=2, min_repeats=3)` | `bool` | a 2–4 char pattern repeated 3+ times |
| `remove_char_loops(text)` | `str` | collapse each loop to one occurrence |
| `HallucinationGuard().is_hallucination(text)` | `bool` | repetition OR char-loop |
| `HallucinationGuard().clean(text)` | `str` | collapse char-loops (repetition = drop the segment) |

The detectors are intentionally **conservative**: they target the obvious
degenerate output Whisper emits on non-speech, not normal repetition in real
language.

## License

MIT
