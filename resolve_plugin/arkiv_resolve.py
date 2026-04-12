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
    # Set up Resolve scripting module paths
    if sys.platform == "darwin":
        modules_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if modules_path not in sys.path:
            sys.path.append(modules_path)
    elif sys.platform == "win32":
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
            print("[arkiv] DaVinciResolveScript 已載入但 Resolve 未回應")
        return resolve
    except ImportError as e:
        print(f"[arkiv] 無法匯入 DaVinciResolveScript：{e}")
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
        print(f"[arkiv] 搜尋錯誤：{e}")
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
        print(f"[arkiv] 列表錯誤：{e}")
        return []


RATING_COLORS = {
    "good": "Green",
    "ng": "Orange",
    "review": "Yellow",
}


def _get_camera_folder(path):
    """Extract camera/source folder name from file path."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    folder = parts[-2] if len(parts) >= 2 else ""
    if folder.lower() in ("reels", "clips", "raw", "media", "footage"):
        folder = parts[-3] if len(parts) >= 3 else folder
    return folder


def _get_or_create_bin(media_pool, root_folder, bin_name):
    """Get existing Bin or create new one under root_folder."""
    # Check existing sub-folders
    for sub in root_folder.GetSubFolderList():
        if sub.GetName() == bin_name:
            return sub
    # Create new bin
    media_pool.SetCurrentFolder(root_folder)
    new_bin = media_pool.AddSubFolder(root_folder, bin_name)
    if new_bin:
        print(f"[arkiv] 建立 Bin：{bin_name}")
    return new_bin


def import_to_resolve(resolve, file_paths, ratings=None, tags=None):
    """Import files into the current Resolve project's Media Pool.
    Auto-creates Bin folders by camera/source under Master.
    ratings: dict mapping file_path -> rating string (good/ng/review)
    tags: dict mapping file_path -> list of tag name strings
    """
    if not resolve:
        print("[arkiv] Resolve 未連線")
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

    root_folder = media_pool.GetRootFolder()

    # Group files by camera folder
    groups = {}
    for p in file_paths:
        folder = _get_camera_folder(p)
        if folder not in groups:
            groups[folder] = []
        groups[folder].append(p)

    total_imported = 0
    all_imported_clips = []

    for folder_name, paths in groups.items():
        # Create or get Bin for this camera
        if folder_name:
            target_bin = _get_or_create_bin(media_pool, root_folder, folder_name)
            if target_bin:
                media_pool.SetCurrentFolder(target_bin)
            else:
                media_pool.SetCurrentFolder(root_folder)
        else:
            media_pool.SetCurrentFolder(root_folder)

        result = media_pool.ImportMedia(paths)
        if result:
            all_imported_clips.extend(result)
            total_imported += len(result)
            print(f"[arkiv] {folder_name or 'Master'}: 匯入 {len(result)} 個片段")

    # Reset to root
    media_pool.SetCurrentFolder(root_folder)

    if all_imported_clips:
        # Apply clip colors based on rating
        if ratings:
            for mpi in all_imported_clips:
                clip_name = mpi.GetName()
                for path, rating in ratings.items():
                    if clip_name and (clip_name in path or path.endswith(clip_name)):
                        color = RATING_COLORS.get(rating)
                        if color:
                            mpi.SetClipColor(color)
                            print(f"[arkiv]   {clip_name} → {color} ({rating})")
                        break
        # Set tags as metadata (Keywords + Comments for Smart Bin filtering)
        if tags:
            for mpi in all_imported_clips:
                clip_name = mpi.GetName()
                for path, tag_list in tags.items():
                    if clip_name and (clip_name in path or path.endswith(clip_name)):
                        if tag_list:
                            tag_str = ", ".join(tag_list)
                            mpi.SetMetadata("Keywords", tag_str)
                            mpi.SetMetadata("Comments", f"[arkiv] {tag_str}")
                            print(f"[arkiv]   {clip_name} → Tags: {tag_str}")
                        break
        print(f"[arkiv] 完成：共匯入 {total_imported} 個片段到 {len(groups)} 個 Bin")
        return True
    else:
        print("[arkiv] 匯入失敗")
        return False


def get_media_detail(media_id):
    """Fetch single media detail with frames from arkiv API."""
    url = f"{ARKIV_API}/api/media/{media_id}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[arkiv] 詳情取得錯誤：{e}")
        return None


def add_markers_to_timeline(resolve, media_items):
    """Add frame analysis markers as clip markers on matching timeline items."""
    if not resolve:
        print("[arkiv] Resolve 未連線")
        return 0

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        print("[arkiv] 未開啟時間線 — 請先開啟時間線")
        return 0

    # Get timeline FPS
    fps_str = timeline.GetSetting("timelineFrameRate")
    try:
        fps = float(fps_str)
    except (TypeError, ValueError):
        fps = 30.0
    print(f"[arkiv] 時間線 FPS：{fps}")

    # Build a map of filename -> timeline_item for all video tracks
    clip_map = {}
    track_count = timeline.GetTrackCount("video")
    for t in range(1, track_count + 1):
        for ti in timeline.GetItemListInTrack("video", t):
            name = ti.GetName()
            if name:
                clip_map[name] = ti
    print(f"[arkiv] 在時間線上找到 {len(clip_map)} 個片段")

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
            print(f"[arkiv] 片段「{filename}」未在時間線上找到 — 請先匯入並放置")
            continue

        detail = get_media_detail(media_id)
        if not detail:
            continue

        frames = detail.get("frames") or []
        if not frames:
            print(f"[arkiv] 無 {filename} 的幀資料")
            continue

        print(f"[arkiv] 正在為 {filename} 新增 {len(frames)} 個片段標記")
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
                print(f"[arkiv]   ！在幀 {frame_offset} 處標記失敗")

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
        print("[arkiv] 無法存取 Fusion UIManager")
        print("[arkiv] 提示：確保 DaVinci Resolve 正在執行且從工作區 → 指令碼啟動")
        return None

    ui = fusion.UIManager
    disp = bmd.UIDispatcher(ui)  # noqa: F821 — bmd is injected by Resolve

    # ── Build Window ──
    win = disp.AddWindow(
        {
            "ID": "ArkivWin",
            "WindowTitle": "arkiv — 媒體搜尋",
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
                                    "PlaceholderText": "搜尋媒體...（語義搜尋）",
                                    "Weight": 3,
                                }
                            ),
                            ui.Button({"ID": "SearchBtn", "Text": "搜尋", "Weight": 0.5}),
                            ui.Button({"ID": "ResetBtn", "Text": "全部", "Weight": 0.3}),
                            ui.Button({"ID": "GoodBtn", "Text": "僅 GOOD", "Weight": 0.5}),
                            ui.Button({"ID": "ExcludeNGBtn", "Text": "排除 NG", "Weight": 0.5}),
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
                            ui.Label({"ID": "StatusLabel", "Text": "準備就緒", "Weight": 3}),
                            ui.Button(
                                {
                                    "ID": "ImportBtn",
                                    "Text": "匯入到媒體庫",
                                    "Weight": 1,
                                }
                            ),
                            ui.Button(
                                {
                                    "ID": "MarkerBtn",
                                    "Text": "新增標記",
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
    hdr.Text[0] = "檔名"
    hdr.Text[1] = "長度"
    hdr.Text[2] = "評級"
    hdr.Text[3] = "語言"
    hdr.Text[4] = "得分"
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
    exclude_ng_on = [False]

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
        win.Find("StatusLabel").Text = f"找到 {len(items)} 個結果"

    def on_search(ev):
        query = win.Find("SearchField").Text.strip()
        if not query:
            on_reset(ev)
            return
        win.Find("StatusLabel").Text = "搜尋中..."
        try:
            items = search_media(query)
            populate_tree(items)
        except Exception as e:
            win.Find("StatusLabel").Text = f"搜尋失敗：{e}"

    def on_reset(ev):
        win.Find("SearchField").Text = ""
        win.Find("StatusLabel").Text = "載入所有媒體..."
        good_filter_on[0] = False
        exclude_ng_on[0] = False
        win.Find("GoodBtn").Text = "僅 GOOD"
        win.Find("ExcludeNGBtn").Text = "排除 NG"
        try:
            items = list_media(limit=500)
            populate_tree(items)
        except Exception as e:
            win.Find("StatusLabel").Text = f"載入失敗：{e}"

    def on_good(ev):
        good_filter_on[0] = not good_filter_on[0]
        exclude_ng_on[0] = False
        win.Find("ExcludeNGBtn").Text = "排除 NG"
        try:
            if good_filter_on[0]:
                win.Find("GoodBtn").Text = "顯示全部"
                win.Find("StatusLabel").Text = "載入 GOOD 素材..."
                items = list_media(rating="good")
            else:
                win.Find("GoodBtn").Text = "僅 GOOD"
                win.Find("StatusLabel").Text = "載入所有媒體..."
                items = list_media(limit=500)
            populate_tree(items)
        except Exception as e:
            win.Find("StatusLabel").Text = f"載入失敗：{e}"

    def on_exclude_ng(ev):
        exclude_ng_on[0] = not exclude_ng_on[0]
        good_filter_on[0] = False
        win.Find("GoodBtn").Text = "僅 GOOD"
        try:
            if exclude_ng_on[0]:
                win.Find("ExcludeNGBtn").Text = "顯示全部"
                win.Find("StatusLabel").Text = "排除 NG 素材..."
                all_items = list_media(limit=500)
                items = [i for i in all_items if i.get("rating") != "ng"]
            else:
                win.Find("ExcludeNGBtn").Text = "排除 NG"
                win.Find("StatusLabel").Text = "載入所有媒體..."
                items = list_media(limit=500)
            populate_tree(items)
        except Exception as e:
            win.Find("StatusLabel").Text = f"載入失敗：{e}"

    def on_import(ev):
        selected = tree.SelectedItems()
        if not selected:
            win.Find("StatusLabel").Text = "未選擇任何項目"
            return
        try:
            paths = []
            ratings = {}
            tags = {}
            for sel_id in selected:
                row = selected[sel_id]
                fname = row.Text[0]
                item_data = results_map.get(fname)
                if item_data and item_data.get("path"):
                    p = item_data["path"]
                    paths.append(p)
                    if item_data.get("rating"):
                        ratings[p] = item_data["rating"]
                    item_tags = item_data.get("tags", [])
                    if item_tags:
                        tags[p] = [t["name"] for t in item_tags]
            if paths:
                success = import_to_resolve(resolve, paths, ratings, tags)
                if success:
                    win.Find("StatusLabel").Text = f"已匯入 {len(paths)} 個片段"
                else:
                    win.Find("StatusLabel").Text = "匯入失敗 — 請檢查媒體庫"
            else:
                win.Find("StatusLabel").Text = "找不到有效的檔案路徑"
        except Exception as e:
            win.Find("StatusLabel").Text = f"匯入錯誤：{e}"

    def on_markers(ev):
        selected = tree.SelectedItems()
        if not selected:
            win.Find("StatusLabel").Text = "未選擇任何項目"
            return
        try:
            items = []
            for sel_id in selected:
                row = selected[sel_id]
                fname = row.Text[0]
                item_data = results_map.get(fname)
                if item_data:
                    items.append(item_data)
            if not items:
                win.Find("StatusLabel").Text = "找不到有效的項目"
                return
            win.Find("StatusLabel").Text = "新增標記中..."
            count = add_markers_to_timeline(resolve, items)
            win.Find("StatusLabel").Text = f"已在時間線上新增 {count} 個標記"
        except Exception as e:
            win.Find("StatusLabel").Text = f"標記錯誤：{e}"

    def on_close(ev):
        disp.ExitLoop()

    win.On.SearchBtn.Clicked = on_search
    win.On.ResetBtn.Clicked = on_reset
    win.On.GoodBtn.Clicked = on_good
    win.On.ExcludeNGBtn.Clicked = on_exclude_ng
    win.On.ImportBtn.Clicked = on_import
    win.On.MarkerBtn.Clicked = on_markers
    win.On.ArkivWin.Close = on_close
    win.On.SearchField.ReturnPressed = on_search

    # Load initial list
    items = list_media(limit=500)
    populate_tree(items)

    win.Show()
    disp.RunLoop()
    win.Hide()


def run_cli_mode(resolve):
    """Fallback CLI mode when UIManager is not available."""
    print("\n=== arkiv 媒體搜尋（CLI 模式）===\n")
    while True:
        query = input("搜尋（輸入 'q' 離開，'good' 顯示 GOOD 素材）：").strip()
        if query.lower() == "q":
            break
        if query.lower() == "good":
            items = list_media(rating="good")
        else:
            items = search_media(query)

        if not items:
            print("找不到結果。\n")
            continue

        for i, item in enumerate(items):
            rating = (item.get("rating") or "—").upper()
            dur = format_duration(item.get("duration_s"))
            print(f"  [{i}] {item['filename']}  ({dur})  [{rating}]  {item.get('lang', '?')}")

        sel = input("\n輸入編號匯入（逗號分隔，或 'skip' 跳過）：").strip()
        if sel.lower() == "skip":
            continue
        try:
            indices = [int(x.strip()) for x in sel.split(",")]
            paths = [items[i]["path"] for i in indices if 0 <= i < len(items)]
            if paths and resolve:
                import_to_resolve(resolve, paths)
        except (ValueError, IndexError) as e:
            print(f"無效選擇：{e}")
        print()


if __name__ == "__main__":
    resolve = get_resolve()
    if resolve:
        print("[arkiv] 已連線到 DaVinci Resolve")
    else:
        print("[arkiv] DaVinci Resolve 未執行 — 僅限 CLI 模式")
    create_ui(resolve)
