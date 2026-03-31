from __future__ import annotations
"""
Media Asset Manager — Styles & Theme (DaVinci Resolve Dark)
Colors from mockup_ui.html v2, format utilities, CSS injection.
"""
from __future__ import annotations

import streamlit as st

# ── Color Constants (DaVinci Resolve dark theme) ─────────────────────────────
C_SURFACE = "#1a1a1e"
C_SURFACE_50 = "#222226"
C_SURFACE_100 = "#2a2a2e"
C_PANEL = "#1e1e22"
C_PANEL_BORDER = "#2e2e34"
C_ACCENT = "#3b82f6"
C_ACCENT_HOVER = "#2563eb"
C_DANGER = "#ef4444"
C_SUCCESS = "#22c55e"
C_WARNING = "#f59e0b"
C_TXT_PRIMARY = "#e4e4e7"
C_TXT_SECONDARY = "#a1a1aa"
C_TXT_TERTIARY = "#71717a"

# ── Rating Colors ────────────────────────────────────────────────────────────
RATING_COLORS = {
    "good": {"bg": "#22c55e20", "text": "#4ade80", "badge_bg": "#22c55e", "badge_text": "#000"},
    "ng": {"bg": "#ef444420", "text": "#f87171", "badge_bg": "#ef4444", "badge_text": "#fff"},
    "review": {"bg": "#f59e0b20", "text": "#fbbf24", "badge_bg": "#f59e0b", "badge_text": "#000"},
}

# ── Type Icons ───────────────────────────────────────────────────────────────
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac"}


def get_type_icon(ext: str) -> str:
    ext = ext.lower()
    if ext in VIDEO_EXT:
        return "film_frames"
    if ext in AUDIO_EXT:
        return "music_note"
    return "insert_drive_file"


def get_media_type(ext: str) -> str:
    ext = ext.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"


# ── Format Helpers ───────────────────────────────────────────────────────────

def format_duration(seconds: float) -> str:
    if seconds is None or seconds <= 0:
        return "0s"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def format_duration_hms(seconds: float) -> str:
    """Format as HH:MM:SS for inspector."""
    if seconds is None or seconds <= 0:
        return "00:00:00"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(mb: float) -> str:
    if mb is None or mb <= 0:
        return "0 MB"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def rating_badge_html(rating: str | None) -> str:
    """Return HTML badge for rating."""
    if not rating or rating not in RATING_COLORS:
        return ""
    c = RATING_COLORS[rating]
    label = rating.upper() if rating != "review" else "REV"
    return (
        f'<span style="background:{c["badge_bg"]};color:{c["badge_text"]};'
        f'font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;">'
        f'{label}</span>'
    )


# ── CSS Injection ────────────────────────────────────────────────────────────

def inject_custom_css():
    st.markdown("""<style>
    /* ── Global dark overrides ── */
    .stApp {
        background-color: #1a1a1e;
    }

    /* Search bar accent */
    .stTextInput > div > div > input {
        background: #222226 !important;
        border: 1px solid #2e2e34 !important;
        color: #e4e4e7 !important;
        border-radius: 6px;
    }
    .stTextInput > div > div > input:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 1px rgba(59,130,246,0.3);
    }
    .stTextInput > div > div > input::placeholder {
        color: #71717a !important;
    }

    /* Card styling */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 8px;
        border-color: #2e2e34 !important;
        background: #222226;
    }

    /* Sidebar dark */
    section[data-testid="stSidebar"] {
        background-color: #1e1e22;
    }
    section[data-testid="stSidebar"] div[data-testid="stMetric"] {
        padding: 4px 0;
    }
    section[data-testid="stSidebar"] div[data-testid="stMetric"] label {
        font-size: 11px;
        color: #71717a;
    }

    /* Filter chips (pills) */
    .filter-chip {
        display: inline-block;
        background: #2a2a2e;
        border: 1px solid #3a3a40;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 11px;
        color: #a1a1aa;
        cursor: pointer;
        margin: 2px;
        text-decoration: none;
    }
    .filter-chip:hover { border-color: #3b82f6; color: #e4e4e7; }
    .filter-chip.active { background: rgba(59,130,246,0.13); border-color: #3b82f6; color: #3b82f6; }

    /* Rating button styles */
    .rating-good { background: #22c55e20; color: #4ade80; border: 1px solid #22c55e40; }
    .rating-ng { background: #ef444420; color: #f87171; border: 1px solid #ef444440; }
    .rating-review { background: #f59e0b20; color: #fbbf24; border: 1px solid #f59e0b40; }

    /* Tag styles */
    .tag-auto {
        background: rgba(59,130,246,0.06);
        color: rgba(96,165,250,0.5);
        border: 1px dashed rgba(59,130,246,0.19);
        border-radius: 3px;
        padding: 1px 6px;
        font-size: 10px;
        display: inline-flex;
        margin: 1px;
    }
    .tag-manual {
        background: rgba(34,197,94,0.13);
        color: #4ade80;
        border-radius: 3px;
        padding: 1px 6px;
        font-size: 10px;
        display: inline-flex;
        margin: 1px;
    }

    /* Media card hover */
    .media-card {
        transition: all 0.15s ease;
        border-radius: 8px;
        overflow: hidden;
    }
    .media-card:hover {
        transform: translateY(-1px);
        border-color: #3b82f6 !important;
    }

    /* Analytics bar */
    .analytics-bar {
        background: #1e1e22;
        border-top: 1px solid #2e2e34;
        padding: 8px 16px;
    }

    /* Pagination */
    .pagination-info { text-align: center; color: #71717a; font-size: 13px; }

    /* Section headers */
    .section-header {
        font-size: 10px;
        font-weight: 600;
        color: #71717a;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 6px;
    }

    /* NG items: semi-transparent */
    .ng-item { opacity: 0.6; }
    .ng-item .filename { text-decoration: line-through; }

    /* Pool item compact */
    .pool-item {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 3px 8px;
        font-size: 12px;
        color: #a1a1aa;
        cursor: pointer;
        border-left: 2px solid transparent;
    }
    .pool-item:hover { background: rgba(255,255,255,0.03); }
    .pool-item.active { background: rgba(59,130,246,0.13); border-left-color: #3b82f6; }
    </style>""", unsafe_allow_html=True)
