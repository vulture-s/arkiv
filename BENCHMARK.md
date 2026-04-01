# arkiv — Whisper Transcription Benchmark

## Test Environments

| Platform | GPU | Engine | Model | VRAM |
|----------|-----|--------|-------|------|
| Mac M2 Max | Apple Silicon (Metal) | mlx-whisper | large-v3-mlx | Unified 32GB |
| PC Windows 11 | NVIDIA RTX 4070 | faster-whisper (CTranslate2, CUDA) | large-v3 | 12.9 GB |

## PC Benchmark (RTX 4070)

6 test files across 3 tiers (short/medium/long), Chinese Mandarin audio.

| Tier | Duration | File Size | Process Time | Chars/sec | RTF |
|------|----------|-----------|-------------|-----------|-----|
| Short (23 min) | 1376s | 52.5 MB | 2m 06s | 62.4 | 0.092 |
| Short (22 min) | 1288s | 49.2 MB | 2m 06s | 46.9 | 0.098 |
| Medium (59 min) | 3520s | 134.3 MB | 4m 39s | 63.3 | 0.079 |
| Medium (56 min) | 3360s | 128.2 MB | 2m 58s | 67.2 | 0.053 |
| Long (74 min) | 4410s | 168.2 MB | 10m 19s | 40.7 | 0.140 |
| Long (90 min) | 5426s | 207.0 MB | 5m 16s | 68.2 | 0.058 |

**Average RTF: 0.087** (11.5x realtime — 1 hour of audio in ~5 minutes)

Model load time: 6 seconds (CUDA float16)

## Mac Benchmark (M2 Max)

65 podcast episodes (2 shows), Chinese Mandarin, mlx-whisper large-v3-mlx.

### Summary by Tier

| Tier | Count | Avg Duration | Avg Process Time | Avg RTF |
|------|-------|-------------|-----------------|---------|
| Short (<25 min) | 8 | 14.7 min | 2.5 min | 0.175 |
| Medium (25–60 min) | 20 | 51.0 min | 7.5 min | 0.152 |
| Long (>60 min) | 28 | 74.1 min | 11.3 min | 0.157 |

**Average RTF: 0.158** (6.3x realtime — 1 hour of audio in ~9.5 minutes)

### Overall Stats (65 episodes)

- Total audio: 135.6 hours
- Total processing: 7.5 hours
- Total output: 1.01 million characters
- Average throughput: 33.1 chars/sec

## RTF Comparison

> RTF (Real-Time Factor) = processing time / audio duration. Lower = faster.

| Platform | Avg RTF | Speed | 1hr audio |
|----------|---------|-------|-----------|
| PC RTX 4070 (CUDA) | 0.087 | 11.5x | ~5 min |
| Mac M2 Max (MLX) | 0.158 | 6.3x | ~9.5 min |

PC with CUDA is ~1.8x faster than Mac M2 Max with MLX for Whisper large-v3 transcription.

## Anti-Hallucination Guard

4-layer defense active during all benchmarks:

1. **Silence filter** — `no_speech_prob > 0.6` discarded
2. **Low-confidence filter** — `avg_logprob < -1.0` discarded
3. **Compression ratio** — `compression_ratio > 2.4` catches repetition hallucination
4. **n-gram dedup** — text-level dedup of looping output
