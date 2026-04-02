#!/usr/bin/env python3
"""
arkiv — DaVinci Resolve Plugin
Search arkiv media library and import selected files into the current Resolve project.

Install (macOS):
    cp arkiv_resolve.py ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
Install (Windows):
    copy arkiv_resolve.py "%APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\"

Usage:
    In DaVinci Resolve → Workspace → Scripts → arkiv_resolve
"""
import json
import urllib.request
import urllib.parse

ARKIV_API = "http://localhost:8501"


def get_resolve():
    """Get the DaVinci Resolve scripting object."""
    import sys
    import os
    # Windows: set up Resolve scripting paths
    if sys.platform == "win32":
        script_api = os.environ.get(
            "RESOLVE_SCRIPT_API",
            r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        )
        modules_path = os.path.join(script_api, "Modules")
        if modules_path not in sys.path:
            sys.path.append(modules_path)
    try:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if resolve is None:
            print("[arkiv] DaVinciResolveScript loaded but Resolve not responding")
        return resolve
    except ImportError as e:
        print(f"[arkiv] Cannot import DaVinciResolveScript: {e}")
        return None


def search_media(query, limit=50):
    """Search arkiv API for media matching query."""
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    url = f"{ARKIV_API}/api/media?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("items", [])
    except Exception as e:
        print(f"[arkiv] Search error: {e}")
        return []


def list_media(limit=50, rating=None):
    """List media from arkiv API."""
    params = {"limit": limit, "sort": "date"}
    if rating:
        params["rating"] = rating
    url = f"{ARKIV_API}/api/media?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("items", [])
    except Exception as e:
        print(f"[arkiv] List error: {e}")
        return []


RATING_COLORS = {
    "good": "Green",
    "ng": "Orange",
    "review": "Yellow",
}


def import_to_resolve(resolve, file_paths, ratings=None):
    """Import files into the current Resolve project's Media Pool.
    ratings: dict mapping file_path -> rating string (good/ng/review)
    """
    if not resolve:
        print("[arkiv] Resolve not connected")
        return False
    pm = resolve.GetProjectManager()
    if not pm:
        return False
    project = pm.GetCurrentProject()
    if not project:
        return False
    media_pool = project.GetMediaPool()
    if not media_pool:
        return False

    result = media_pool.ImportMedia(file_paths)
    if result:
        # Apply clip colors based on rating
        if ratings:
            for mpi in result:
                clip_name = mpi.GetName()
                for path, rating in ratings.items():
                    if clip_name and clip_name in path or path.endswith(clip_name):
                        color = RATING_COLORS.get(rating)
                        if color:
                            mpi.SetClipColor(color)
                            print(f"[arkiv]   {clip_name} → {color} ({rating})")
                        break
        print(f"[arkiv] Imported {len(result)} clips into Media Pool")
        return True
    else:
        print("[arkiv] Import failed")
        return False


def get_media_detail(media_id):
    """Fetch single media detail with frames from arkiv API."""
    url = f"{ARKIV_API}/api/media/{media_id}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[arkiv] Detail fetch error: {e}")
        return None


def add_markers_to_timeline(resolve, media_items):
    """Add frame analysis markers as clip markers on matching timeline items."""
    if not resolve:
        print("[arkiv] Resolve not connected")
        return 0

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        print("[arkiv] No timeline open — open a timeline first")
        return 0

    # Get timeline FPS
    fps_str = timeline.GetSetting("timelineFrameRate")
    try:
        fps = float(fps_str)
    except (TypeError, ValueError):
        fps = 30.0
    print(f"[arkiv] Timeline FPS: {fps}")

    # Build a map of filename -> timeline_item for all video tracks
    clip_map = {}
    track_count = timeline.GetTrackCount("video")
    for t in range(1, track_count + 1):
        for ti in timeline.GetItemListInTrack("video", t):
            name = ti.GetName()
            if name:
                clip_map[name] = ti
    print(f"[arkiv] Found {len(clip_map)} clips on timeline")

    colors = ["Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple"]
    total_added = 0

    for item in media_items:
        media_id = item.get("id")
        filename = item.get("filename", "")
        if not media_id:
            continue

        # Find matching clip on timeline
        ti = clip_map.get(filename)
        if not ti:
            # Try without extension
            stem = filename.rsplit(".", 1)[0] if "." in filename else filename
            for k, v in clip_map.items():
                if k.startswith(stem):
                    ti = v
                    break
        if not ti:
            print(f"[arkiv] Clip '{filename}' not found on timeline — import and place it first")
            continue

        detail = get_media_detail(media_id)
        if not detail:
            continue

        frames = detail.get("frames") or []
        if not frames:
            print(f"[arkiv] No frame data for {filename}")
            continue

        print(f"[arkiv] Adding {len(frames)} clip markers for {filename}")
        for i, fr in enumerate(frames):
            ts = fr.get("timestamp_s", 0)
            frame_offset = round(ts * fps)
            desc = fr.get("description") or f"Frame {fr.get('frame_index', i) + 1}"
            color = colors[i % len(colors)]

            if desc.startswith("```") or not desc.strip():
                continue

            # AddMarker on timeline_item = clip marker (frame offset from clip start)
            success = ti.AddMarker(
                frame_offset,
                color,
                desc[:50],
                desc,
                1,
            )
            if success:
                total_added += 1
            else:
                print(f"[arkiv]   ! Marker failed at frame {frame_offset}")

    return total_added


def format_duration(seconds):
    """Format seconds to MM:SS."""
    if not seconds:
        return "00:00"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def create_ui(resolve):
    """Create the arkiv search UI using Fusion's UIManager."""
    fusion = resolve.Fusion() if resolve else None
    if not fusion:
        print("[arkiv] Cannot access Fusion UIManager")
        print("[arkiv] Tip: Make sure DaVinci Resolve is running and script is launched from Workspace → Scripts")
        return None

    ui = fusion.UIManager
    disp = bmd.UIDispatcher(ui)  # noqa: F821 — bmd is injected by Resolve

    # ── Build Window ──
    win = disp.AddWindow(
        {
            "ID": "ArkivWin",
            "WindowTitle": "arkiv — Media Search",
            "Geometry": [200, 200, 700, 500],
        },
        [
            ui.VGroup(
                {"Spacing": 5},
                [
                    # Search bar
                    ui.HGroup(
                        {"Spacing": 5},
                        [
                            ui.LineEdit(
                                {
                                    "ID": "SearchField",
                                    "PlaceholderText": "Search media... (semantic search)",
                                    "Weight": 3,
                                }
                            ),
                            ui.Button({"ID": "SearchBtn", "Text": "Search", "Weight": 0.5}),
                            ui.Button({"ID": "GoodBtn", "Text": "GOOD Only", "Weight": 0.5}),
                        ],
                    ),
                    # Results tree
                    ui.Tree(
                        {
                            "ID": "ResultTree",
                            "SortingEnabled": True,
                            "SelectionMode": "ExtendedSelection",
                            "HeaderHidden": False,
                            "Weight": 5,
                        }
                    ),
                    # Status + Actions
                    ui.HGroup(
                        {"Spacing": 5},
                        [
                            ui.Label({"ID": "StatusLabel", "Text": "Ready", "Weight": 3}),
                            ui.Button(
                                {
                                    "ID": "ImportBtn",
                                    "Text": "Import to Media Pool",
                                    "Weight": 1,
                                }
                            ),
                            ui.Button(
                                {
                                    "ID": "MarkerBtn",
                                    "Text": "Add Markers",
                                    "Weight": 0.7,
                                }
                            ),
                        ],
                    ),
                ],
            )
        ],
    )

    # Setup tree columns
    tree = win.Find("ResultTree")
    hdr = tree.NewItem()
    hdr.Text[0] = "Filename"
    hdr.Text[1] = "Duration"
    hdr.Text[2] = "Rating"
    hdr.Text[3] = "Language"
    hdr.Text[4] = "Score"
    tree.SetHeaderItem(hdr)
    tree.ColumnCount = 5
    tree.ColumnWidth[0] = 280
    tree.ColumnWidth[1] = 70
    tree.ColumnWidth[2] = 60
    tree.ColumnWidth[3] = 60
    tree.ColumnWidth[4] = 60

    # Store results for import
    results_map = {}
    good_filter_on = [False]

    def populate_tree(items):
        tree.Clear()
        results_map.clear()
        for item in items:
            row = tree.NewItem()
            fname = item.get("filename", "")
            row.Text[0] = fname
            row.Text[1] = format_duration(item.get("duration_s"))
            row.Text[2] = (item.get("rating") or "—").upper()
            row.Text[3] = item.get("lang") or "?"
            row.Text[4] = f"{round(item.get('score', 0) * 100)}%" if item.get("score") else ""
            tree.AddTopLevelItem(row)
            results_map[fname] = item
        win.Find("StatusLabel").Text = f"Found {len(items)} results"

    def on_search(ev):
        query = win.Find("SearchField").Text.strip()
        if not query:
            return
        win.Find("StatusLabel").Text = "Searching..."
        items = search_media(query)
        populate_tree(items)

    def on_good(ev):
        good_filter_on[0] = not good_filter_on[0]
        if good_filter_on[0]:
            win.Find("GoodBtn").Text = "Show All"
            win.Find("StatusLabel").Text = "Loading GOOD takes..."
            items = list_media(rating="good")
        else:
            win.Find("GoodBtn").Text = "GOOD Only"
            win.Find("StatusLabel").Text = "Loading all media..."
            items = list_media(limit=30)
        populate_tree(items)

    def on_import(ev):
        selected = tree.SelectedItems()
        if not selected:
            win.Find("StatusLabel").Text = "No items selected"
            return
        paths = []
        ratings = {}
        for sel_id in selected:
            row = selected[sel_id]
            fname = row.Text[0]
            item_data = results_map.get(fname)
            if item_data and item_data.get("path"):
                p = item_data["path"]
                paths.append(p)
                if item_data.get("rating"):
                    ratings[p] = item_data["rating"]
        if paths:
            success = import_to_resolve(resolve, paths, ratings)
            if success:
                win.Find("StatusLabel").Text = f"Imported {len(paths)} clips"
            else:
                win.Find("StatusLabel").Text = "Import failed — check Media Pool"
        else:
            win.Find("StatusLabel").Text = "No valid file paths found"

    def on_markers(ev):
        selected = tree.SelectedItems()
        if not selected:
            win.Find("StatusLabel").Text = "No items selected"
            return
        items = []
        for sel_id in selected:
            row = selected[sel_id]
            fname = row.Text[0]
            item_data = results_map.get(fname)
            if item_data:
                items.append(item_data)
        if not items:
            win.Find("StatusLabel").Text = "No valid items found"
            return
        win.Find("StatusLabel").Text = "Adding markers..."
        count = add_markers_to_timeline(resolve, items)
        win.Find("StatusLabel").Text = f"Added {count} markers to timeline"

    def on_close(ev):
        disp.ExitLoop()

    win.On.SearchBtn.Clicked = on_search
    win.On.GoodBtn.Clicked = on_good
    win.On.ImportBtn.Clicked = on_import
    win.On.MarkerBtn.Clicked = on_markers
    win.On.ArkivWin.Close = on_close
    win.On.SearchField.ReturnPressed = on_search

    # Load initial list
    items = list_media(limit=30)
    populate_tree(items)

    win.Show()
    disp.RunLoop()
    win.Hide()


def run_cli_mode(resolve):
    """Fallback CLI mode when UIManager is not available."""
    print("\n=== arkiv Media Search (CLI Mode) ===\n")
    while True:
        query = input("Search query (or 'q' to quit, 'good' for GOOD takes): ").strip()
        if query.lower() == "q":
            break
        if query.lower() == "good":
            items = list_media(rating="good")
        else:
            items = search_media(query)

        if not items:
            print("No results found.\n")
            continue

        for i, item in enumerate(items):
            rating = (item.get("rating") or "—").upper()
            dur = format_duration(item.get("duration_s"))
            print(f"  [{i}] {item['filename']}  ({dur})  [{rating}]  {item.get('lang', '?')}")

        sel = input("\nEnter numbers to import (comma-separated, or 'skip'): ").strip()
        if sel.lower() == "skip":
            continue
        try:
            indices = [int(x.strip()) for x in sel.split(",")]
            paths = [items[i]["path"] for i in indices if 0 <= i < len(items)]
            if paths and resolve:
                import_to_resolve(resolve, paths)
        except (ValueError, IndexError) as e:
            print(f"Invalid selection: {e}")
        print()


if __name__ == "__main__":
    resolve = get_resolve()
    if resolve:
        print("[arkiv] Connected to DaVinci Resolve")
    else:
        print("[arkiv] DaVinci Resolve not running — CLI mode only")
    create_ui(resolve)
