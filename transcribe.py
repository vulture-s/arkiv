from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import platform
from pathlib import Path

import numpy as np
import soundfile as sf
from silero_vad import load_silero_vad, get_speech_timestamps
import torch

from config import WHISPER_MODEL, OLLAMA_URL, CUSTOM_VOCABULARY, FILTER_WORDS, WHISPER_GUARD_DEFAULT_MODE, WHISPER_GUARD_LAYERS
NO_SPEECH_THRESHOLD = 0.6
DEFAULT_LANGUAGE = "zh"
WHISPER_LANGUAGE_HINT = None
WHISPER_LLM_MODEL = "qwen2.5:14b"
VAD_ENABLED = True
LLM_POLISH = True

# ── Platform Detection ───────────────────────────────────────────────────────
_USE_MLX = platform.system() == "Darwin" and platform.machine() == "arm64"

# ── Model Singletons ────────────────────────────────────────────────────────
WHISPER_GUARD_ACTIVE_MODE = WHISPER_GUARD_DEFAULT_MODE
WHISPER_GUARD_ACTIVE_LAYER = WHISPER_GUARD_LAYERS[WHISPER_GUARD_ACTIVE_MODE]
WHISPER_MODEL = WHISPER_GUARD_ACTIVE_LAYER["model"]
WHISPER_LANGUAGE_HINT = WHISPER_GUARD_ACTIVE_LAYER["language_hint"]
WHISPER_LLM_MODEL = WHISPER_GUARD_ACTIVE_LAYER["llm_model"] or WHISPER_LLM_MODEL
VAD_ENABLED = WHISPER_GUARD_ACTIVE_LAYER["vad_enabled"]
LLM_POLISH = WHISPER_GUARD_ACTIVE_LAYER["llm_polish"]

def _coerce_whisper_guard_mode(raw_mode):
    if raw_mode is None:
        return None
    if isinstance(raw_mode, int):
        return raw_mode if raw_mode in WHISPER_GUARD_LAYERS else None
    raw_mode = str(raw_mode).strip()
    if not raw_mode:
        return None
    try:
        value = int(raw_mode)
    except ValueError:
        path = Path(raw_mode)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if isinstance(payload, dict):
                for key in ("baseline_mode", "mode", "layer"):
                    if key in payload:
                        try:
                            value = int(payload[key])
                            break
                        except (TypeError, ValueError):
                            continue
                else:
                    return None
            else:
                return None
        else:
            return None
    return value if value in WHISPER_GUARD_LAYERS else None

def _resolve_whisper_guard_mode(cli_mode=None):
    env_mode = _coerce_whisper_guard_mode(os.getenv("ARKIV_WHISPER_GUARD_LAYERS"))
    if env_mode is not None:
        return env_mode
    cli_mode = _coerce_whisper_guard_mode(cli_mode)
    if cli_mode is not None:
        return cli_mode
    return WHISPER_GUARD_DEFAULT_MODE

def _apply_whisper_guard_mode(mode):
    global WHISPER_GUARD_ACTIVE_MODE, WHISPER_GUARD_ACTIVE_LAYER
    global WHISPER_MODEL, WHISPER_LANGUAGE_HINT, WHISPER_LLM_MODEL
    global VAD_ENABLED, LLM_POLISH
    WHISPER_GUARD_ACTIVE_MODE = mode
    WHISPER_GUARD_ACTIVE_LAYER = WHISPER_GUARD_LAYERS.get(mode, WHISPER_GUARD_LAYERS[WHISPER_GUARD_DEFAULT_MODE])
    WHISPER_MODEL = WHISPER_GUARD_ACTIVE_LAYER["model"]
    WHISPER_LANGUAGE_HINT = WHISPER_GUARD_ACTIVE_LAYER["language_hint"]
    WHISPER_LLM_MODEL = WHISPER_GUARD_ACTIVE_LAYER["llm_model"] or "qwen2.5:14b"
    VAD_ENABLED = WHISPER_GUARD_ACTIVE_LAYER["vad_enabled"]
    LLM_POLISH = WHISPER_GUARD_ACTIVE_LAYER["llm_polish"]
    return WHISPER_GUARD_ACTIVE_LAYER

def _current_whisper_guard_backend():
    return "mlx_whisper" if _USE_MLX else "whisperx"

def _current_whisper_guard_settings():
    return WHISPER_GUARD_ACTIVE_LAYER[_current_whisper_guard_backend()]

def _optional_option(opts, key, value):
    if value is not None:
        opts[key] = value

_whisper_loaded = False
_fw_model = None  # faster-whisper model instance
_whisperx_model = None  # WhisperX model instance
_vad_model = None  # Silero VAD model instance


_apply_whisper_guard_mode(_resolve_whisper_guard_mode(None))

def _get_vad_model():
    """Lazy-load Silero VAD model."""
    global _vad_model
    if _vad_model is None:
        _vad_model = load_silero_vad()
    return _vad_model


def _vad_filter(wav_path: str, sample_rate: int = 16000):
    """Run Silero VAD on WAV, return new WAV with only speech segments.
    Returns None if no speech detected. Returns original path if VAD disabled."""
    if not VAD_ENABLED:
        return wav_path

    audio, sr = sf.read(wav_path, dtype="float32")
    if sr != sample_rate:
        return wav_path  # safety: skip VAD if sample rate mismatch

    tensor = torch.from_numpy(audio)
    stamps = get_speech_timestamps(tensor, _get_vad_model(),
                                   sampling_rate=sample_rate,
                                   min_silence_duration_ms=300,
                                   speech_pad_ms=150)
    if not stamps:
        return None  # no speech at all

    # Concatenate speech segments
    chunks = [audio[s["start"]:s["end"]] for s in stamps]
    speech = np.concatenate(chunks)

    _fd, out = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
    sf.write(out, speech, sample_rate)
    kept = len(speech) / max(len(audio), 1)
    print(f"  [VAD] kept {kept:.0%} of audio ({len(stamps)} segments)", flush=True)
    return out


def warm_up():
    """Pre-load Whisper model into memory. Call once before batch processing."""
    global _whisper_loaded, _fw_model, _whisperx_model
    if _whisper_loaded:
        return

    if _USE_MLX:
        import mlx_whisper
        import numpy as np
        _fd, silence = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
        try:
            subprocess.run([
                "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-t", "1", "-loglevel", "error", silence, "-y"
            ], capture_output=True)
            mlx_whisper.transcribe(silence, path_or_hf_repo=WHISPER_MODEL, language="zh")
        except Exception:
            pass
        finally:
            Path(silence).unlink(missing_ok=True)
    else:
        import whisperx
        _whisperx_model = whisperx.load_model(
            WHISPER_MODEL,
            "cuda",
            compute_type="float16",
        )
        print("  [whisperx on cuda]", flush=True)

    _whisper_loaded = True
    print("  [whisper model loaded]", flush=True)


def transcribe(media_path: str, language=None) -> tuple:
    """
    Transcribe audio from a media file.
    Returns (transcript_text, language, segments_list, words_list).
    segments_list: [{"start": float, "end": float, "text": str}, ...]
    words_list: [{"word": str, "start": float, "end": float, "score": float}, ...]
    Returns ("", "", [], []) if no speech detected.
    """
    if language is None:
        language = WHISPER_LANGUAGE_HINT or DEFAULT_LANGUAGE
    wav = _to_wav(media_path)
    if not wav:
        return "", "", [], []

    try:
        if _USE_MLX:
            vad_wav = _vad_filter(wav)
            if vad_wav is None:
                return "", "", [], []
            try:
                return _transcribe_mlx(vad_wav, language)
            finally:
                if vad_wav != wav:
                    Path(vad_wav).unlink(missing_ok=True)
        else:
            return _transcribe_whisperx(wav, language)
    finally:
        Path(wav).unlink(missing_ok=True)

def _build_initial_prompt() -> str:
    """Build initial_prompt from custom vocabulary config."""
    if not CUSTOM_VOCABULARY:
        return ""
    terms = [t.strip() for t in CUSTOM_VOCABULARY.split(",") if t.strip()]
    return "、".join(terms) if terms else ""


def _transcribe_mlx(wav: str, language: str) -> tuple:
    """Transcribe using mlx-whisper (Apple Silicon)."""
    import mlx_whisper
    initial_prompt = _build_initial_prompt()
    layer = _current_whisper_guard_settings()
    opts = dict(
        path_or_hf_repo=WHISPER_MODEL,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=layer["condition_on_previous_text"],
        no_speech_threshold=NO_SPEECH_THRESHOLD,
        beam_size=layer["beam_size"],
    )
    _optional_option(opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(opts, "logprob_threshold", layer["logprob_threshold"])
    if initial_prompt:
        opts["initial_prompt"] = initial_prompt
    result = mlx_whisper.transcribe(wav, **opts)
    text = result.get("text", "").strip()
    lang = result.get("language", language)
    raw_segments = result.get("segments", [])
    return _postprocess(text, lang, raw_segments, language, words=[])

def _transcribe_whisperx(wav: str, language: str) -> tuple:
    """Transcribe using WhisperX (CUDA) with forced alignment."""
    global _whisperx_model
    if _whisperx_model is None:
        warm_up()

    import whisperx

    initial_prompt = _build_initial_prompt()
    layer = _current_whisper_guard_settings()
    transcribe_opts = {
        "batch_size": layer["whisperx"]["batch_size"],
        "beam_size": layer["beam_size"],
        "language": language,
        "condition_on_previous_text": layer["condition_on_previous_text"],
    }
    _optional_option(transcribe_opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(transcribe_opts, "log_prob_threshold", layer["whisperx"]["log_prob_threshold"])
    if initial_prompt:
        transcribe_opts["initial_prompt"] = initial_prompt

    audio = whisperx.load_audio(wav)
    result = _whisperx_model.transcribe(audio, **transcribe_opts)
    lang = result.get("language", language) or language

    align_model, align_meta = whisperx.load_align_model(
        language_code=lang,
        device="cuda",
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        align_meta,
        audio,
        "cuda",
        return_char_alignments=False,
    )

    segments = []
    all_words = []
    for seg in result.get("segments", []):
        segments.append({
            "text": seg.get("text", "").strip(),
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "no_speech_prob": seg.get("no_speech_prob", 0),
            "avg_logprob": seg.get("avg_logprob", 0),
            "compression_ratio": seg.get("compression_ratio", 1),
        })
        for word in seg.get("words", []):
            if "start" in word and "end" in word:
                all_words.append({
                    "word": word.get("word", "").strip(),
                    "start": round(float(word["start"]), 3),
                    "end": round(float(word["end"]), 3),
                    "score": round(float(word.get("score", 0)), 3),
                })

    text = " ".join(seg["text"] for seg in segments).strip()
    return _postprocess(text, lang, segments, language, words=all_words)

def _postprocess(text: str, lang: str, segments: list, language: str, words: list = None) -> tuple:
    """Shared post-processing: anti-hallucination + LLM polish.
    Returns (text, lang, clean_segments, words) where clean_segments has start/end/text."""
    if not segments:
        return text, lang, [], words or []

    # Guard 1: ALL segments are silence → no speech
    avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
    if avg_no_speech > NO_SPEECH_THRESHOLD:
        return "", lang, [], []

    # Guard 2: Per-segment filtering
    good_segments = []
    timed_segments = []
    for s in segments:
        seg_text = s.get("text", "").strip()
        if not seg_text:
            continue
        if s.get("no_speech_prob", 0) > 0.8:
            continue
        # Dynamic logprob threshold: short segments (<1.6s) are more likely hallucinations
        duration = s.get("end", 0) - s.get("start", 0) if "start" in s and "end" in s else None
        logprob_thresh = -1.7 if duration is not None and duration < 1.6 else -1.5
        if s.get("avg_logprob", 0) < logprob_thresh:
            continue
        if s.get("compression_ratio", 1) > 3.0:
            continue
        good_segments.append(seg_text)
        # Preserve timing for SRT/VTT export
        if "start" in s and "end" in s:
            timed_segments.append({
                "start": round(float(s["start"]), 3),
                "end": round(float(s["end"]), 3),
                "text": seg_text,
            })

    if not good_segments:
        return "", lang, [], []

    filtered_text = " ".join(good_segments).strip()

    # Guard 3: Text-level repetition
    if _is_repetitive(filtered_text):
        return "", lang, [], []

    # Guard 4: Character-level repetition
    if _has_char_loops(filtered_text):
        filtered_text = _remove_char_loops(filtered_text)

    # Step 4.5: Filter dictionary — remove configured filler words
    if FILTER_WORDS:
        filter_list = [w.strip() for w in FILTER_WORDS.split(",") if w.strip()]
        for word in filter_list:
            filtered_text = filtered_text.replace(word, "")
        # Also clean timed segments
        for ts in timed_segments:
            for word in filter_list:
                ts["text"] = ts["text"].replace(word, "")
            ts["text"] = ts["text"].strip()
        timed_segments = [ts for ts in timed_segments if ts["text"]]
        # Clean up double spaces
        import re
        filtered_text = re.sub(r'\s{2,}', ' ', filtered_text).strip()

    # Step 5: LLM polish
    if LLM_POLISH and len(filtered_text) > 10:
        filtered_text = _llm_polish(filtered_text, language)

    return filtered_text, lang, timed_segments, words or []


def _is_repetitive(text: str, window: int = 6, threshold: float = 0.35) -> bool:
    if len(text) < window * 3:
        return False
    chunks = [text[i:i+window] for i in range(0, len(text) - window, window)]
    unique = len(set(chunks))
    return unique / len(chunks) < threshold


def _has_char_loops(text: str, min_pattern: int = 2, min_repeats: int = 3) -> bool:
    import re
    return bool(re.search(r'(.{2,4})\1{2,}', text))


def _remove_char_loops(text: str) -> str:
    import re
    return re.sub(r'(.{2,4})\1{2,}', r'\1', text)


_ollama_warm = False


def warm_up_ollama():
    global _ollama_warm
    if _ollama_warm:
        return
    import requests as _req
    try:
        _req.post(f"{OLLAMA_URL}/api/generate", json={
            "model": "qwen2.5:14b", "prompt": "hi", "stream": False,
            "options": {"num_predict": 1}
        }, timeout=30)
        _ollama_warm = True
    except Exception:
        pass


def _llm_polish(text: str, language: str = "zh") -> str:
    import requests as _req
    MODEL = WHISPER_LLM_MODEL

    lang_name = {"zh": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}.get(language, language)

    prompt = f"""你是一個逐字稿校正助手。以下是語音辨識（Whisper）的原始輸出，可能有錯字、同音字錯誤、人名地名錯誤、缺少標點。

請校正以下逐字稿，規則：
1. 修正明顯的同音字錯誤（例如「蕭希」→「小熙」）
2. 補上適當的標點符號（句號、逗號、問號）
3. 不要改變原意、不要增刪內容
4. 保持口語化，不要改成書面語
5. 語言：{lang_name}
6. 只輸出校正後的文字，不要加任何說明

原始逐字稿：
{text}

校正後："""

    try:
        r = _req.post(f"{OLLAMA_URL}/api/generate", json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": len(text) * 2}
        }, timeout=60)
        if r.ok:
            polished = r.json().get("response", "").strip()
            if polished and 0.5 < len(polished) / max(len(text), 1) < 2.0:
                return polished
    except Exception:
        pass
    return text


def _to_wav(media_path: str):
    _fd, out = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
    cmd = [
        "ffmpeg", "-i", media_path,
        "-ac", "1", "-ar", "16000",
        "-map", "a:0",
        "-loglevel", "error",
        out, "-y"
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not Path(out).exists():
        return None
    return out


def _whisper_guard_snapshot(mode=None):
    if mode is None:
        mode = WHISPER_GUARD_ACTIVE_MODE
    layer = WHISPER_GUARD_LAYERS.get(mode, WHISPER_GUARD_LAYERS[WHISPER_GUARD_DEFAULT_MODE])
    return {
        "mode": mode,
        "name": layer["name"],
        "model": layer["model"],
        "beam_size": layer["beam_size"],
        "language_hint": layer["language_hint"],
        "vad_enabled": layer["vad_enabled"],
        "condition_on_previous_text": layer["condition_on_previous_text"],
        "compression_ratio_threshold": layer["compression_ratio_threshold"],
        "logprob_threshold": layer["logprob_threshold"],
        "llm_polish": layer["llm_polish"],
        "llm_model": layer["llm_model"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="arkiv Whisper transcription")
    parser.add_argument("audio", nargs="?", help="Audio or video file to transcribe")
    parser.add_argument("--language", default=None, help="Override language hint")
    parser.add_argument("--baseline-mode", type=int, choices=range(5), help="Select Whisper Guard baseline layer (0-4)")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved Whisper Guard layer and exit")
    args = parser.parse_args(argv)

    mode = _resolve_whisper_guard_mode(args.baseline_mode)
    _apply_whisper_guard_mode(mode)

    if args.dry_run or not args.audio:
        print(json.dumps(_whisper_guard_snapshot(mode), indent=2, ensure_ascii=False))
        return 0

    text, lang, segments, words = transcribe(args.audio, language=args.language)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
