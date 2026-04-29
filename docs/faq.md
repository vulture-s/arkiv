# arkiv — FAQ

## Installation

**Q: Which Whisper backend should I use?**

- **macOS Apple Silicon:** `pip install mlx-whisper` (fastest, Metal GPU)
- **NVIDIA GPU (Linux/Windows):** `pip install faster-whisper torch` (CUDA)
- **CPU only:** `pip install faster-whisper` (works everywhere, slower)

## Multi-language / CJK

**Q: Does arkiv support languages other than English?**

Yes. arkiv is CJK-first — transcription and search are tested on Mandarin Chinese, Japanese, and English. Use the default `large-v3-turbo` Whisper model with the built-in 4-layer anti-hallucination guard for best CJK accuracy.

## Cross-project search

**Q: Can I search across multiple projects at once?**

Cross-project query is on the roadmap (W2). Current release uses per-project `media.db` files. Track progress in the [arkiv roadmap](https://github.com/vulture-s/arkiv).

## GPU requirements

**Q: Do I need a GPU?**

No. arkiv runs CPU-only with `faster-whisper` (no torch). A GPU speeds up transcription 3-5x. Vision descriptions (`qwen3-vl:8b`) can be skipped with `--skip-vision` if GPU is unavailable.

## DaVinci Resolve plugin

**Q: Does the Resolve plugin work on Windows and Linux?**

The plugin is developed and tested on macOS with DaVinci Resolve 18/19/21. Windows support is in progress. Linux (Resolve Studio) is untested — open a GitHub Discussion if you get it working.
