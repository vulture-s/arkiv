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


def import_to_resolve(resolve, file_paths):
    """Import files into the current Resolve project's Media Pool."""
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
        print(f"[arkiv] Imported {len(result)} clips into Media Pool")
        return True
    else:
        print("[arkiv] Import failed")
        return False


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
                    # Status + Import
                    ui.HGroup(
                        {"Spacing": 5},
                        [
                            ui.Label({"ID": "StatusLabel", "Text": "Ready", "Weight": 3}),
                            ui.Button(
                                {
                                    "ID": "ImportBtn",
                                    "Text": "Import Selected to Media Pool",
                                    "Weight": 1,
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
        win.Find("StatusLabel").Text = "Loading GOOD takes..."
        items = list_media(rating="good")
        populate_tree(items)

    def on_import(ev):
        selected = tree.SelectedItems()
        if not selected:
            win.Find("StatusLabel").Text = "No items selected"
            return
        paths = []
        for sel_id in selected:
            row = selected[sel_id]
            fname = row.Text[0]
            item_data = results_map.get(fname)
            if item_data and item_data.get("path"):
                paths.append(item_data["path"])
        if paths:
            success = import_to_resolve(resolve, paths)
            if success:
                win.Find("StatusLabel").Text = f"Imported {len(paths)} clips"
            else:
                win.Find("StatusLabel").Text = "Import failed — check Media Pool"
        else:
            win.Find("StatusLabel").Text = "No valid file paths found"

    def on_close(ev):
        disp.ExitLoop()

    win.On.SearchBtn.Clicked = on_search
    win.On.GoodBtn.Clicked = on_good
    win.On.ImportBtn.Clicked = on_import
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
