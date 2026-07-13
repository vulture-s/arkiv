import csv
import importlib
import shutil
import sqlite3
import tempfile
from pathlib import Path
from io import StringIO


def _parse_csv_text(csv_text):
    return list(csv.reader(StringIO(csv_text)))


def _make_temp_root():
    base = Path(__file__).resolve().parents[1] / ".tmp-camera-report-tests"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=str(base)))


def test_camera_report_writes_three_section_csv_and_summary_matches_sql(monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    temp_root_path = _make_temp_root()
    try:
        db_path = temp_root_path / "test.db"
        monkeypatch.setattr(config, "PROJECT_ROOT", temp_root_path)
        monkeypatch.setattr(config, "DB_PATH", db_path)  # R5-23: SSOT accessor follows config.DB_PATH
        db.init_db()

        camera_report = importlib.import_module("camera_report")
        camera_report = importlib.reload(camera_report)

        db.upsert({
            "path": "/tmp/明燒肉_C3742.MP4",
            "filename": "明燒肉_C3742.MP4",
            "ext": ".MP4",
            "duration_s": 10.0,
            "width": 1920,
            "height": 1080,
            "fps": 29.97,
            "has_audio": 1,
            "transcript": "",
            "lang": "zh",
            "frame_tags": "[{\"description\": \"火烤聲與油脂聲。\", \"tags\": [\"CJK\", \"detail\"], \"content_type\": \"A-Roll\", \"focus_score\": 4, \"exposure\": \"normal\", \"stability\": \"穩定\", \"audio_quality\": \"清晰\", \"atmosphere\": \"熱鬧\", \"energy\": \"高\", \"edit_position\": \"前段\", \"edit_reason\": \"fixture\"}]",
            "reel_name": "A001",
            "thumbnail_path": "/tmp/thumb-1.jpg",
            "processed_at": "2026-05-26T09:00:00",
            "rating": "good",
            "rating_note": "notes for good",
            "camera_make": "Sony",
            "camera_model": "FX30",
            "lens_model": "FE 24-70mm",
            "iso": 800,
            "shutter_speed": "1/50",
            "aperture": 2.8,
            "focal_length": 35,
            "focus_score": 4,
            "start_tc": "01:00:00:00",
            "content_type": "A-Roll",
        })
        db.upsert({
            "path": "/tmp/scene-b.mp4",
            "filename": "B_CAM_A002_SC02_T05.MP4",
            "ext": ".mov",
            "duration_s": 20.0,
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "has_audio": 1,
            "transcript": "",
            "lang": "zh",
            "frame_tags": "",
            "reel_name": "A002",
            "thumbnail_path": "/tmp/thumb-2.jpg",
            "processed_at": "2026-05-26T10:00:00",
            "rating": "ng",
            "camera_make": "Canon",
            "camera_model": "R5",
            "lens_model": "RF 50mm",
            "iso": 1600,
            "shutter_speed": "1/100",
            "aperture": 4.0,
            "focal_length": 50,
            "focus_score": 2,
            "start_tc": "01:10:00:00",
        })
        db.upsert({
            "path": "/tmp/scene-c.mp4",
            "filename": "C003_take7.mov",
            "ext": ".mxf",
            "duration_s": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "has_audio": 1,
            "transcript": "",
            "lang": "zh",
            "frame_tags": "",
            "reel_name": "A001",
            "thumbnail_path": "/tmp/thumb-3.jpg",
            "processed_at": "2026-05-26T11:00:00",
            "rating": "review",
            "camera_make": "Sony",
            "camera_model": "FX30",
            "lens_model": "FE 24-105mm",
            "iso": 400,
            "shutter_speed": "1/48",
            "aperture": 5.6,
            "focal_length": 85,
            "focus_score": 3,
            "start_tc": "01:20:00:00",
        })
        db.upsert({
            "path": "/tmp/other-day.mp4",
            "filename": "other-day.mp4",
            "duration_s": 5.0,
            "processed_at": "2026-05-25T11:00:00",
            "rating": "good",
        })

        dest = temp_root_path / "report.csv"
        written = camera_report.write_camera_report(
            date_text="2026-05-26",
            output=dest,
            project="明燒肉",
            dp="Hevin Yeh",
            scene_pattern=r"(SC\d{2})",
            take_pattern=r"(T\d{2})",
        )
        assert written == dest
        assert dest.exists()

        csv_text = dest.read_text(encoding="utf-8")
        rows = _parse_csv_text(csv_text)

        assert rows[0] == ["Project", "明燒肉"]
        assert rows[1] == ["Date", "2026-05-26"]
        assert rows[2] == ["DP", "Hevin Yeh"]
        assert rows[4] == ["Total Reels", "2"]
        assert rows[6] == [
            "Filename",
            "Reel",
            "Scene",
            "Take",
            "TC-in",
            "TC-out",
            "Duration",
            "Camera",
            "Lens",
            "Codec",
            "ISO",
            "WB",
            "ND",
            "Shutter",
            "Aperture",
            "Focal",
            "Focus",
            "Notes",
            "Rating",
            "FPS",
        ]

        cjk_row = next(row for row in rows if row and row[0] == "明燒肉_C3742.MP4")
        assert cjk_row[1] == "A001"
        assert cjk_row[7] == "Sony FX30"
        assert cjk_row[8] == "FE 24-70mm"
        assert cjk_row[12] == ""
        assert cjk_row[17].startswith("notes for good")
        assert cjk_row[18] == "GOOD"

        assert rows[-6:] == [
            ["Total Clips", "3"],
            ["Total Duration", "00:01:00"],
            ["GOOD", "1"],
            ["NG", "1"],
            ["REVIEW", "1"],
            ["Reel List", "A001 A002"],
        ]

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            summary = conn.execute(
                """
                SELECT COUNT(*) AS total_clips,
                       SUM(duration_s) AS total_duration_s,
                       COUNT(CASE WHEN rating = 'good' THEN 1 END) AS good_count,
                       COUNT(CASE WHEN rating = 'ng' THEN 1 END) AS ng_count,
                       COUNT(CASE WHEN rating = 'review' THEN 1 END) AS review_count
                  FROM media
                 WHERE DATE(processed_at) = ?
                """,
                ("2026-05-26",),
            ).fetchone()

        assert rows[-6][1] == str(summary["total_clips"])
        assert rows[-5][1] == "00:01:00"
        assert rows[-4][1] == str(summary["good_count"])
        assert rows[-3][1] == str(summary["ng_count"])
        assert rows[-2][1] == str(summary["review_count"])
    finally:
        shutil.rmtree(temp_root_path, ignore_errors=True)


def test_camera_report_main_returns_1_when_no_data(monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    temp_root_path = _make_temp_root()
    try:
        db_path = temp_root_path / "test.db"
        monkeypatch.setattr(config, "PROJECT_ROOT", temp_root_path)
        monkeypatch.setattr(config, "DB_PATH", db_path)  # R5-23: SSOT accessor follows config.DB_PATH
        db.init_db()

        camera_report = importlib.import_module("camera_report")
        camera_report = importlib.reload(camera_report)

        exit_code = camera_report.main([
            "--date",
            "2026-05-26",
            "--output",
            str(temp_root_path / "empty.csv"),
        ])
        assert exit_code == 1
    finally:
        shutil.rmtree(temp_root_path, ignore_errors=True)
