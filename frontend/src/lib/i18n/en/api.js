// Backend API text (#634): HTTPException details and JSON `message`/`error`
// fields. Keys are the exact zh-TW strings (or templates) the backend emits —
// the full list is ../backend-keys.json, generated from the Python code by
// scripts/i18n_backend_keys.py; tests/i18nBackend.test.mjs requires every one
// of them to have an entry here (or in another fragment).
export default {
  // generic
  '找不到': 'Not found',
  '找不到媒體': 'Media not found',
  '找不到該媒體': 'Media not found',
  '找不到檔案': 'File not found',
  '找不到這支素材': 'Clip not found',
  '找不到素材：{ids}': 'Clips not found: {ids}',
  '找不到媒體檔案：{path}': 'Media file not found: {path}',
  '不是媒體檔案': 'Not a media file',
  '只能開啟已索引的媒體檔案': 'Only indexed media files can be opened',
  '原始檔不在原位（NAS 沒有連上，或檔案被移走了）': 'The original file is not at its recorded location (the NAS is not connected, or the file was moved)',
  'ids 不可為空': 'ids must not be empty',
  'ids 必須是整數 list': 'ids must be a list of integers',
  'ids 必須是逗號分隔的整數，例如 ids=3,1,7': 'ids must be comma-separated integers, e.g. ids=3,1,7',
  '無效的 media id: {raw}': 'Invalid media id: {raw}',
  '一次最多 500 支': 'At most 500 clips per request',
  'apply 需要 ingest_write scope': 'apply requires the ingest_write scope',

  // path access (health.path_access_message) — {fm} and {action} are words below
  '無法存取這個路徑（{reason}）。如果是網路磁碟，請先在{fm}開啟這個位置並登入，再回來{action}。':
    'Cannot access this path ({reason}). If it is a network drive, open this location in {fm} and sign in first, then come back and retry ({action}).',
  '檔案總管': 'File Explorer',
  '檔案管理員': 'the file manager',
  '匯出': 'Export',
  '轉存': 'Offload',
  '加入': 'Add',

  // ingest
  '路徑不是有效的目錄': 'The path is not a valid folder',
  '路徑不是有效的目錄：{path}': 'The path is not a valid folder: {path}',
  '已有匯入任務進行中，請稍候': 'Another ingest is already running. Please wait',
  '已有匯入進行中，請等待完成後再試': 'An ingest is already running. Try again after it finishes',
  '已有匯入或轉錄任務進行中，請稍候': 'An ingest or transcription job is already running. Please wait',
  '已有匯入任務進行中，已複製檔案但略過索引；請稍後手動 ingest': 'Another ingest is running: files were copied but indexing was skipped. Run ingest manually later',
  '已開始匯入 — 連線 /ws/ingest 取得進度': 'Ingest started — connect to /ws/ingest for progress',
  '匯入逾時': 'Ingest timed out',
  '匯入停滯：已 {idle} 分鐘沒有任何進度輸出（門檻 {limit} 分鐘），研判卡住，已停止整個處理程序。已完成的素材都已寫進素材庫，重跑會從中斷處接續。':
    'Ingest stalled: no progress output for {idle} min (limit {limit} min), so it was judged stuck and the whole process was stopped. Clips already processed are in the library; re-running resumes where it stopped.',
  '匯入超過預估時間的上限（{limit} 分鐘）並被停止 —— 它當時仍在輸出進度，所以多半只是估值偏低而非卡住。已完成的素材都已寫進素材庫，重跑會從中斷處接續。':
    'Ingest exceeded its estimated time limit ({limit} min) and was stopped. It was still reporting progress, so the estimate was most likely too low rather than the job being stuck. Clips already processed are in the library; re-running resumes where it stopped.',
  '重新處理逾時（>10 分鐘）': 'Reprocessing timed out (>10 min)',
  '上傳並發已達上限，請稍後再試': 'Too many uploads in progress. Try again later',
  '沒有收到任何檔案': 'No files were received',
  '檔案超過上傳上限（{mb} MB）：{name}': 'File exceeds the upload limit ({mb} MB): {name}',
  '非法檔名：{name}': 'Invalid file name: {name}',
  '不支援的檔案類型：{ext}（僅允許媒體格式）': 'Unsupported file type: {ext} (media formats only)',
  '非法路徑：{path}': 'Invalid path: {path}',
  '這個資料夾不在 arkiv 允許匯入的位置：{path}\n如果是 NAS／網路資料夾：在 Windows 上先把它對應成網路磁碟機（例如 Z:），或到 設定 → 匯入 → 額外允許的資料夾 加入這個路徑（Mac 上先在 Finder 連上伺服器，掛在 /Volumes 底下就能直接匯入）。\n預設允許：這個庫的資料夾、Desktop、Documents、Movies／Videos、Pictures、已連上的網路磁碟。進階：ARKIV_INGEST_ROOTS 環境變數可整份取代預設清單。':
    'This folder is not in a location arkiv is allowed to ingest from: {path}\nFor a NAS / network folder: on Windows, map it as a network drive first (e.g. Z:), or add this path under Settings → Ingest → Additional allowed folders (on a Mac, connect to the server in Finder first; anything mounted under /Volumes can be ingested directly).\nAllowed by default: this library’s folder, Desktop, Documents, Movies / Videos, Pictures, and connected network drives. Advanced: the ARKIV_INGEST_ROOTS environment variable replaces the default list entirely.',

  // transcription / vision / waveform
  '這支素材的轉錄正在進行中': 'Transcription for this clip is already in progress',
  '這支素材的視覺重試正在進行中': 'A vision retry for this clip is already in progress',
  'transcribe 回空，拒絕覆寫既有逐字稿（可能是音訊擷取失敗）': 'Transcription returned nothing; the existing transcript was not overwritten (audio extraction may have failed)',
  'retranscribe 失敗：{error}': 'Re-transcription failed: {error}',
  '該語言尚無轉錄': 'No transcript in this language yet',
  '所有幀都已有描述': 'All frames already have descriptions',
  '波形計算失敗': 'Waveform computation failed',
  '沒有含音訊的素材可重轉錄': 'No clips with audio to re-transcribe',
  '批次重轉錄已在進行中，請稍候': 'Batch re-transcription is already running. Please wait',
  '這台機器讀不了這個庫的素材，批次不會有結果：\n{report}': 'This machine cannot read this library’s media, so the batch would produce no results:\n{report}',

  // export
  '不支援的格式：{fmt}。請使用 srt/vtt/txt/edl/edl-markers/fcpxml': 'Unsupported format: {fmt}. Use srt/vtt/txt/edl/edl-markers/fcpxml',
  'markers 必須是 {allowed}': 'markers must be one of {allowed}',
  'markers={markers} 只適用於 {fmts}': 'markers={markers} only applies to {fmts}',
  '批次匯出僅支援 edl / srt / fcpxml，收到：{fmt}': 'Batch export supports only edl / srt / fcpxml; received: {fmt}',
  '匯出路徑必須在批准的目錄下（Desktop / Documents / Downloads / Movies / Videos / temp，或 ARKIV_EXPORT_ROOTS 指定的目錄）':
    'The export path must be inside an approved folder (Desktop / Documents / Downloads / Movies / Videos / temp, or a folder listed in ARKIV_EXPORT_ROOTS)',
  '不允許的匯出副檔名：{ext}': 'Export file extension not allowed: {ext}',
  '拒絕寫入系統敏感目錄（LaunchAgents / .ssh / cron / systemd 等）': 'Refusing to write to a sensitive system folder (LaunchAgents / .ssh / cron / systemd, etc.)',
  '拒絕寫入系統目錄': 'Refusing to write to a system folder',
  '子片段切不出來：{error}': 'Could not cut the subclip: {error}',

  // offload
  '來源路徑不存在': 'The source path does not exist',
  '來源路徑不存在（卡片要插著才能補拷）': 'The source path does not exist (the card must be inserted to re-copy)',
  '至少需要一個目的地 (dst)': 'At least one destination (dst) is required',
  '此來源的轉存正在進行中，請稍候': 'An offload from this source is already in progress. Please wait',
  '找不到這張卡的轉存紀錄 — 請先跑一次 offload': 'No offload record for this card — run an offload first',
  '無法讀取來源：{error}': 'Cannot read the source: {error}',

  // bins / projects / sample
  '目的地已是既有專案（含 .arkiv/project.db）；請改選現有專案或換新路徑': 'The destination is already a project (contains .arkiv/project.db). Choose it as an existing project, or use a different path',
  '找不到目的專案：{dest}': 'Destination project not found: {dest}',
  'sample 載入已在進行中': 'Sample loading is already in progress',
  '專案已有內容，請改用重新索引的範例載入': 'The project already has content. Use the re-indexing sample load instead',
  '找不到預建範例（尚未打包）': 'Prebuilt sample not found (not packaged yet)',

  // color / LUT
  '找不到這個 LUT': 'LUT not found',
  '未知的色彩設定檔：{profile}': 'Unknown color profile: {profile}',

  // proxies / index / cache
  '全部 proxy 已存在': 'All proxies already exist',
  'proxy 已存在': 'Proxy already exists',
  'proxy 生成已在進行中，請稍候': 'Proxy generation is already running. Please wait',
  '剪輯用 proxy 生成已在進行中，請稍候': 'Editing proxy generation is already running. Please wait',
  '開始生成 proxy（背景執行）': 'Generating proxy (in background)',
  '開始生成 {n} 個 proxy（背景執行）': 'Generating {n} proxies (in background)',
  '開始生成 {n} 個剪輯用 proxy（背景執行）': 'Generating {n} editing proxies (in background)',
  '這個庫關閉了 proxy（ingest.proxies = never），所以這支沒有可在瀏覽器播的版本。請用能直接播放原檔的播放器（macOS App／Safari），或只為這一支建立 proxy，或執行 `ingest.py --regenerate-proxies` 補齊整庫。':
    'Proxies are disabled for this library (ingest.proxies = never), so this clip has no browser-playable version. Use a player that can play the original file (macOS app / Safari), create a proxy for this clip only, or run `ingest.py --regenerate-proxies` to fill in the whole library.',
  '尚無素材可建立索引': 'No clips to index yet',
  '向量索引重建已在進行中，請稍候': 'Vector index rebuild is already running. Please wait',
  '開始重建向量索引（{n} 筆素材，背景執行）': 'Rebuilding vector index ({n} clips, in background)',
  '語意索引重建進行中，請待完成後再清除': 'Semantic index rebuild in progress. Clear it after the rebuild finishes',
  '清除 chromadb 失敗：{error}': 'Failed to clear chromadb: {error}',

  // model setup
  '模型下載已在進行中': 'A model download is already in progress',
  'Ollama 沒有在執行，無法下載它的模型——先安裝並開啟 Ollama': 'Ollama is not running, so its models cannot be downloaded — install and start Ollama first',
  '有 {n} 個模型下載失敗': '{n} model downloads failed',
  '找不到這個模型名稱——檢查設定裡的模型名稱': 'Model name not found — check the model name in Settings',
  '連不到下載伺服器——確認網路後再試一次': 'Cannot reach the download server — check the network and try again',
  '磁碟空間不足': 'Not enough disk space',
}
