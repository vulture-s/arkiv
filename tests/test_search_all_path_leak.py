"""Regression: /api/search/all must not leak absolute filesystem paths.

Completes the Phase 16.2 path-leak hardening. The federation layer hands the
endpoint rich records that include absolute paths in three still-leaking places:
  - item["relative_path"] — for an out-of-root row this is the *absolute* path
    (federation._resolve_paths falls back to str(stored) when relative_to fails),
  - item["absolute_path"] — already stripped before this fix,
  - errors[].project_path — the absolute project root, set on timeout/preflight.
The endpoint must basename/drop all of them before returning to a videos_read
client.
"""
import json

import federation

LEAK_ABS = "/Volumes/home/secret-project/footage/clip.mov"
LEAK_ROOT = "/Volumes/home/secret-project"


def _leaky_payload(*args, **kwargs):
    return {
        "query": "x",
        "total_results": 1,
        "projects_queried": 2,
        "projects_failed": 1,
        "items": [
            {
                "media_id": 1,
                "project_name": "p",
                "filename": "clip.mov",
                "relative_path": LEAK_ABS,   # out-of-root → absolute (the leak)
                "absolute_path": LEAK_ABS,
                "path": LEAK_ABS,
                "project_path": LEAK_ROOT,
                "score": 0.9,
            }
        ],
        "errors": [
            {
                "project_name": "slow",
                "project_path": LEAK_ROOT,   # absolute root on timeout (the leak)
                "error": "timeout after 10.0s",
                "stage": "timeout",
            }
        ],
    }


def test_search_all_strips_absolute_paths_from_items_and_errors(fastapi_client, monkeypatch):
    monkeypatch.setattr(federation, "search_all_projects", _leaky_payload)

    resp = fastapi_client.get("/api/search/all", params={"q": "x"})
    assert resp.status_code in (200, 207)
    data = resp.json()

    item = data["items"][0]
    # internal absolute/relative fields are gone; only the sanitized path remains
    assert "absolute_path" not in item
    assert "relative_path" not in item
    assert item["path"] == "clip.mov"               # basenamed
    assert item["project_path"] == "secret-project"  # basenamed

    err = data["errors"][0]
    assert err["project_path"] == "secret-project"   # basenamed

    # nothing absolute survives anywhere in the response body
    blob = json.dumps(data, ensure_ascii=False)
    assert LEAK_ABS not in blob
    assert LEAK_ROOT not in blob
    assert "/Volumes/" not in blob


def test_search_all_handles_none_project_path_in_errors(fastapi_client, monkeypatch):
    # the "no matching projects" error path sets project_path=None — must not crash
    def _payload(*a, **k):
        return {
            "query": "x", "total_results": 0, "projects_queried": 0,
            "projects_failed": 0, "items": [],
            "errors": [{"project_name": None, "project_path": None,
                        "error": "no matching projects", "stage": "preflight"}],
        }

    monkeypatch.setattr(federation, "search_all_projects", _payload)
    resp = fastapi_client.get("/api/search/all", params={"q": "x"})
    assert resp.status_code in (200, 207)
    assert resp.json()["errors"][0]["project_path"] is None


# Codex P2: a cross-platform federation peer can hand over Windows-style absolute
# paths; os.path.basename on a POSIX host doesn't split on "\" and would leak them.
WIN_ABS = "C:\\Users\\me\\secret-proj\\footage\\clip.mov"
WIN_ROOT = "C:\\Users\\me\\secret-proj"


def test_search_all_strips_windows_style_absolute_paths(fastapi_client, monkeypatch):
    def _payload(*a, **k):
        return {
            "query": "x", "total_results": 1, "projects_queried": 2, "projects_failed": 1,
            "items": [{
                "media_id": 1, "project_name": "p", "filename": "clip.mov",
                "relative_path": WIN_ABS, "absolute_path": WIN_ABS, "path": WIN_ABS,
                "project_path": WIN_ROOT, "score": 0.9,
            }],
            "errors": [{"project_name": "slow", "project_path": WIN_ROOT,
                        "error": "timeout after 10.0s", "stage": "timeout"}],
        }

    monkeypatch.setattr(federation, "search_all_projects", _payload)
    resp = fastapi_client.get("/api/search/all", params={"q": "x"})
    assert resp.status_code in (200, 207)
    data = resp.json()
    assert data["items"][0]["path"] == "clip.mov"              # basenamed across "\"
    assert data["items"][0]["project_path"] == "secret-proj"
    assert data["errors"][0]["project_path"] == "secret-proj"
    blob = json.dumps(data, ensure_ascii=False)
    assert WIN_ABS not in blob and WIN_ROOT not in blob
    assert "C:" not in blob and "\\" not in blob              # no Windows path fragment
