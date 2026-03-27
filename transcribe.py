import subprocess
import tempfile
from pathlib import Path

WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
NO_SPEECH_THRESHOLD = 0.6


def transcribe(media_path: str) -> tuple[str, str]:
    """
    Transcribe audio from a media file using mlx-whisper.
    Returns (transcript_text, language). Returns ("", "") if no speech detected.
    """
    import mlx_whisper

    wav = _to_wav(media_path)
    if not wav:
        return "", ""

    try:
        result = mlx_whisper.transcribe(
            wav,
            path_or_hf_repo=WHISPER_MODEL,
            no_speech_threshold=NO_SPEECH_THRESHOLD,
            condition_on_previous_text=False,
        )
        text = result.get("text", "").strip()
        lang = result.get("language", "")
        segments = result.get("segments", [])

        if segments:
            # Guard 1: high no_speech_prob → silence/noise
            avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
            if avg_no_speech > NO_SPEECH_THRESHOLD:
                return "", lang

            # Guard 2: low avg_logprob → uncertain transcription
            avg_logprob = sum(s.get("avg_logprob", 0) for s in segments) / len(segments)
            if avg_logprob < -1.0:
                return "", lang

            # Guard 3: high compression_ratio → repetitive hallucination
            avg_compression = sum(s.get("compression_ratio", 1) for s in segments) / len(segments)
            if avg_compression > 2.4:
                return "", lang

        # Guard 4: text-level repetition (e.g. "輪輪輪輪" or "你只會你只會")
        if text and _is_repetitive(text):
            return "", lang

        return text, lang
    finally:
        Path(wav).unlink(missing_ok=True)


def _is_repetitive(text: str, window: int = 6, threshold: float = 0.4) -> bool:
    """Detect looping/repetitive hallucination by checking n-gram repetition ratio."""
    if len(text) < window * 2:
        return False
    chunks = [text[i:i+window] for i in range(0, len(text) - window, window)]
    unique = len(set(chunks))
    return unique / len(chunks) < threshold


def _to_wav(media_path: str) -> str | None:
    out = tempfile.mktemp(suffix=".wav")
    cmd = [
        "ffmpeg", "-i", media_path,
        "-ac", "1", "-ar", "16000",
        "-map", "a:0",
        out, "-y"
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not Path(out).exists():
        return None
    return out
