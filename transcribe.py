import subprocess
import tempfile
from pathlib import Path

from config import WHISPER_MODEL, OLLAMA_URL
NO_SPEECH_THRESHOLD = 0.6
DEFAULT_LANGUAGE = "zh"  # 強制繁體中文，避免簡體/日文亂跳
LLM_POLISH = True  # 用 Ollama LLM 後處理校正逐字稿

# ── Model Singleton ──────────────────────────────────────────────────────────
_whisper_loaded = False


def warm_up():
    """Pre-load Whisper model into memory. Call once before batch processing."""
    global _whisper_loaded
    if _whisper_loaded:
        return
    import mlx_whisper
    # Transcribe 1 second of silence to force model load + compile
    import numpy as np
    silence = tempfile.mktemp(suffix=".wav")
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
    _whisper_loaded = True
    print("  [whisper model loaded]", flush=True)


def transcribe(media_path: str, language: str = DEFAULT_LANGUAGE) -> tuple[str, str]:
    """
    Transcribe audio from a media file using mlx-whisper.
    Returns (transcript_text, language). Returns ("", "") if no speech detected.
    """
    import mlx_whisper
    global _whisper_loaded
    _whisper_loaded = True  # mark loaded after first real call

    wav = _to_wav(media_path)
    if not wav:
        return "", ""

    try:
        result = mlx_whisper.transcribe(
            wav,
            path_or_hf_repo=WHISPER_MODEL,
            language=language,
            word_timestamps=True,
            condition_on_previous_text=True,
            no_speech_threshold=NO_SPEECH_THRESHOLD,
            compression_ratio_threshold=2.4,
            logprob_threshold=-1.0,
        )
        text = result.get("text", "").strip()
        lang = result.get("language", language)
        segments = result.get("segments", [])

        if not segments:
            return text, lang

        # ── Anti-Hallucination Guards ──

        # Guard 1: ALL segments are silence → no speech
        avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
        if avg_no_speech > NO_SPEECH_THRESHOLD:
            return "", lang

        # Guard 2: Per-segment filtering (keep good segments, drop bad ones)
        good_segments = []
        for s in segments:
            seg_text = s.get("text", "").strip()
            if not seg_text:
                continue
            # Skip segments with very high no_speech
            if s.get("no_speech_prob", 0) > 0.8:
                continue
            # Skip segments with extremely low confidence
            if s.get("avg_logprob", 0) < -1.5:
                continue
            # Skip segments with extreme compression (hallucination loops)
            if s.get("compression_ratio", 1) > 3.0:
                continue
            good_segments.append(seg_text)

        if not good_segments:
            return "", lang

        filtered_text = " ".join(good_segments).strip()

        # Guard 3: Text-level repetition (e.g. "輪輪輪輪" or "你只會你只會")
        if _is_repetitive(filtered_text):
            return "", lang

        # Guard 4: Character-level repetition (e.g. "蕭希蕭希蕭希")
        if _has_char_loops(filtered_text):
            filtered_text = _remove_char_loops(filtered_text)

        # Step 5: LLM polish — fix typos, names, punctuation
        if LLM_POLISH and len(filtered_text) > 10:
            filtered_text = _llm_polish(filtered_text, language)

        return filtered_text, lang
    finally:
        Path(wav).unlink(missing_ok=True)


def _is_repetitive(text: str, window: int = 6, threshold: float = 0.35) -> bool:
    """Detect looping/repetitive hallucination by checking n-gram repetition ratio."""
    if len(text) < window * 3:
        return False
    chunks = [text[i:i+window] for i in range(0, len(text) - window, window)]
    unique = len(set(chunks))
    return unique / len(chunks) < threshold


def _has_char_loops(text: str, min_pattern: int = 2, min_repeats: int = 3) -> bool:
    """Detect character-level loops like 蕭希蕭希蕭希."""
    import re
    # Match any 2-4 char pattern repeated 3+ times
    return bool(re.search(r'(.{2,4})\1{2,}', text))


def _remove_char_loops(text: str) -> str:
    """Remove character-level loops, keep one instance."""
    import re
    return re.sub(r'(.{2,4})\1{2,}', r'\1', text)


_ollama_warm = False


def warm_up_ollama():
    """Pre-load Ollama model to avoid cold start."""
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
    """Use Ollama LLM to fix Whisper transcription errors."""
    import requests as _req
    MODEL = "qwen2.5:14b"

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
            # Sanity check: polished should be similar length (not hallucinated)
            if polished and 0.5 < len(polished) / max(len(text), 1) < 2.0:
                return polished
    except Exception:
        pass
    return text  # fallback to original if LLM fails


def _to_wav(media_path: str) -> str | None:
    out = tempfile.mktemp(suffix=".wav")
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
