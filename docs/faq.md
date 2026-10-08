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

Each project keeps its own library in `<PROJECT_ROOT>/.arkiv/` (`project.db`, `chroma_db/`, `thumbnails/`). The free core covers up to 3 projects. Search and collections that span projects are part of the optional Pro add-on. See [pro-addon-license.md](pro-addon-license.md).

## GPU requirements

**Q: Do I need a GPU?**

No. arkiv runs CPU-only with `faster-whisper` (no torch), but transcription is much slower without acceleration. Measured RTF (processing time ÷ audio length) is 0.158 on an M2 Max with MLX and 0.087 on an RTX 4070 with CUDA. Full numbers are in [BENCHMARK.md](../BENCHMARK.md). If you have no GPU, skip vision descriptions (`qwen2.5vl:7b`) with `--skip-vision`.

## DaVinci Resolve plugin

**Q: Does the Resolve plugin work on Windows and Linux?**

The plugin is developed and tested on macOS with DaVinci Resolve 18/19/21. `resolve_plugin/arkiv_resolve.py` includes Windows install paths; macOS is the only tested platform so far. Linux (Resolve Studio) is untested — open a GitHub Discussion if you get it working.
