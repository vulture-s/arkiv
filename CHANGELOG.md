# Changelog

## Unreleased

## v1.7.7 - 2026-10-29

**工具列與 List 檢視照 UI 設計稿重排。**

### 變更
- **工具列**（#614）：第一列照設計稿，標題＋統計在左、篩選＋Grid/List 在右；搜尋、排名、
  排序選單（Grid）、CSV、回收桶移到第二列。
- **List 檢視**（#614）：縮圖放大到 110×62、只有檔名用主色、其餘數值靠左用暗色；
  選取改成左側白條（原本整列反白）；N·G 列淡化加刪除線；勾選框平常隱藏，hover 或多選時出現。

### 修正
- **視窗 1400 寬時拍攝日期、匯入日期欄被切掉**（#614）：檔名欄改成自動截斷，所有欄位都看得到。

## v1.7.6 - 2026-10-29

**Media pool 標頭重排，list 檢視直接點欄位名排序；App 換上新 icon。**

### 變更
- **Media pool 標頭改兩列、控制項同高**（#611）：第一列專案名／搜尋／排名，右側 CSV・回收桶；
  第二列篩選與 Grid/List 切換。窄視窗時右側按鈕整組換行，不再出現孤立的回收桶。
- **List 檢視點欄位名排序**（#611）：檔名、評分、時長、容量、拍攝日期、匯入日期，再點一次反向，
  箭頭標示目前方向。Grid 沒有欄位列，保留排序選單（改成方角）。新增檔名 Z→A、時長 短→長、容量 小→大。
- **App icon 與網頁分頁圖示**換成設計稿的 arkiv 框線字標（#612），取代原本的預設佔位圖。

## v1.7.5 - 2026-10-28

**Media pool 可以照拍攝日期排序。**

### 新增
- **Media pool 排序選單**：匯入 新→舊／舊→新、拍攝 新→舊／舊→新、檔名、長度、大小、評分（#608）。
  排序在伺服器端做，整個素材庫一起排，不是只排已載入的那一頁；沒有拍攝日期的素材兩個方向都排最後。
  選擇會記住，下次打開沿用。文字搜尋的結果仍依相關度。

### 修正
- **Windows 升級腳本不再把瘦身後的版本判成「沒裝完整」**（#609）：`scripts/upgrade_desktop_windows.py`
  原本要求新版檔案數不少於舊版的九成，1.7.4 刻意從 26,691 個檔減到 7,890 個，每次升級都誤報 FAIL。
  現在改跟「這個安裝包本身帶了幾個檔」比（用 7-Zip 列出安裝包內容）；沒有 7-Zip 時退回 5,000 個檔的下限，
  並在輸出寫明。

## v1.7.4 - 2026-10-27

**安裝包瘦身：檔案數約剩三成。** macOS 27,779 → 8,899 個檔（1.4 GB → 863 MB）、Windows 25,764 → 7,895 個檔
（1.06 GB → 564 MB）。功能沒有變；轉錄的切段與 1.7.3 逐一相同。檔案少了，Windows 的安裝、解除安裝與升級
會快很多，防毒掃描也是按檔案數算時間。

### 改善
- **安裝包不再帶 torch**（#605）：torch 原本只為了跑 silero VAD（判斷哪幾段有人講話），
  Windows 安裝目錄 25,764 個檔裡它佔 11,956 個、macOS .app 裡 526 MB。VAD 改在 onnxruntime 上跑同一個
  silero 模型（`silero_onnx.py`，模型隨原始碼附在 `models/`，MIT），切段結果與 torch 版逐一相同：
  真實素材 50 支、54 分鐘全數一致，CI 另以 torch 版產生的參考值釘住。Linux／Docker 連帶不再裝 torch 與
  NVIDIA CUDA 套件。macOS 的 mlx-whisper 仍列 torch 為依賴，但轉錄路徑不用它，打包時排除；
  打包腳本最後會用打包內的 Python 實跑一次 VAD、並確認 torch 不在裡面。
- **安裝包再瘦一輪**：打包時不再放入各套件自帶的測試（numpy／scipy／numba 等約 1,700 個檔）、pip，以及
  Python 內建的 Tk／IDLE（arkiv 的介面是 Tauri，用不到）。打包最後會用精簡後的內容把 server、ingest、
  轉錄（macOS 另含 MCP）全部載入一次，排除過頭會直接讓打包失敗（#606）。

## v1.7.3 - 2026-10-27

**看片時順手的四件事：列表看得到拍攝日、套 LUT 也能拖時間軸、評分和匯出搬到預覽下方、一次評很多支。**

### 新增
- **多選後批次評分**：多選列加 GOOD／REVIEW／N·G／—（清除）。每支走跟 Inspector 同一條寫入，不會動到既有的
  評分備註；某幾支寫入失敗時只有那幾支退回原評分，並提示失敗幾支（#589）。
- **列表檢視加「拍攝日期」欄**，與「匯入日期」並列；讀不到拍攝日顯示「—」，不拿匯入日頂替（#586）。

### 改善
- **Inspector 欄寬 340→400px**：340 時匯出列最右的「字幕 MARKER」超出邊界點不到；匯出列也改成放不下就換行（#589）。
- **Rate 與匯出／在 Finder 顯示／刪除素材移到預覽正下方**，不用捲到面板最底（#588、#589）。

### 修正
- **套了色彩設定檔（如 S-Log3）的影片，預覽器時間軸拖不動**：LUT 畫面蓋住了原生控制列，只剩波形能移播放頭。
  套 LUT 時改顯示自己的時間軸；拖動時不會自動播放，原本在播的放開後繼續（#587）。

### 工具
- **`scripts/upgrade_desktop_windows.py`：Windows 桌面 App 原地升級**：解除安裝 → 等背景的 NSIS 解除安裝程式真的結束 →
  殘留的安裝目錄改名移到 `%USERPROFILE%\arkiv-upgrade-backup-<日期>\`（不刪）→ 靜默安裝 → 驗檔案數、
  `site-packages` 沒有同一套件兩個版本、`config.VERSION` 與登錄檔版本。arkiv 在跑就不動手。
  素材庫、資料庫與專案登錄不在安裝目錄裡，一律不碰。

### 測試
- **#572 回歸測試**：人名清單的「一頁只抓一次」抽成 `lib/identityNames.js`，node 測試釘住空清單不會重抓，
  另檢查 `MainLive.svelte` 驅動它的反應式那一行不讀它寫入的 `identityNames`（#572 就是這條縫）。

## v1.7.2 - 2026-10-23

**Inspector 用剪輯軟體的快捷鍵操作；匯入失敗時最後一行一定講清楚是哪一支、為什麼。**
J／K／L、逐格、跳點、只播 IN→OUT，照 Resolve／Premiere 的鍵位；ingest 非 0 結束時結論改印在最後一行，
`… | tail` 也看得到。

### 新增
- **Inspector 的剪輯快捷鍵，照 DaVinci Resolve／Premiere 的鍵位**：J／K／L 倒轉、停、快轉（連按加速到 8×）、
  ←／→ 逐格（Shift 一次一秒）、I／O 設點、Shift＋I／O 跳到 IN／OUT、Home／End、「/」只播 IN 到 OUT、⌥X 清除 IN／OUT。
  waveform 上方列出這些按鍵。鍵位用實體按鍵位置判斷，中文輸入法開著也能用；⌘／Ctrl 組合鍵一律不攔。
  `<video>` 不能倒著播，倒轉是以每秒 15 步往回跳模擬。

### 修正
- **ingest 非 0 結束時，最後一行一律是 `[exit N] …` 結論**（#393 根因）：失敗檔名＋原因（最多列 3 支）、
  vision 失敗的檔與錯誤、vision 停下的原因；`--vision-only` 中途停、vision 模型沒裝、儲存空間預檢失敗、
  `--dir` 不存在也一樣。原本失敗清單與對帳印在中段，後面接 embed 與 BENCHMARK，
  跑 `… | tail` 時看不到失敗、`$?` 也是 tail 的 0 —— 2026-08-28 那支「靜默漏檔」最可能就是這樣被蓋掉的。

## v1.7.1 - 2026-10-22

**Windows 第一次打開就看得到設定精靈；開 App 的頭幾秒不再出現別人的素材庫名字。**
v1.7.0 在 PC 實機驗時發現：精靈的檢查在 Windows 要等好幾秒，冷開機時使用者早已開始操作；
載入中側欄拿設計稿的示範資料墊底，每個使用者都會看到。

### 修正
- **Windows：首次啟動精靈要等好幾秒才出現，等不到的人直接錯過**：精靈的檢查一次要 3.7 秒（冷開機更久），
  其中 2.2 秒是連 Ollama——`localhost` 在 Windows 先解析成 IPv6 的 `::1`，Ollama 只聽 IPv4，每次都要等連線逾時才退回；
  1.3 秒是用 PowerShell 查 CPU 名稱。`ARKIV_OLLAMA_URL` 預設改成 `http://127.0.0.1:11434`（所有 Ollama 呼叫——畫面描述、
  語意搜尋、對話——在 Windows 上都省下這 2 秒）、CPU 名稱改讀登錄檔、硬體資訊每個程序只查一次。
- **打開 App 的頭幾秒，側欄會出現不是你的素材庫**（「Bicycle Diaries」「vulture.s reels」「Furutech RCA spot」…）：
  那是設計稿的示範資料，真資料還沒載入前拿來墊底，每個使用者都會看到。正式畫面載入中改為留空。
- **Windows：桌面 App 每次啟動都在 console 報一個 CSP 違規**：Tauri 對桌面殼的呼叫在 Windows 走 `http://ipc.localhost`，
  被 arkiv 自己的 CSP 擋下後才退回 postMessage。`connect-src` 加上 `ipc:` 與 `http://ipc.localhost`（兩者都不出本機）。

## v1.7.0 - 2026-10-22

**桌面 App 可以開第二個素材庫、第一次打開有設定精靈、片段可以直接拖進剪輯軟體。**
這三件事原本都卡在「只能用環境變數或指令列」：換庫要改 `ARKIV_PROJECT_ROOT` 重開、缺 ffmpeg 或模型要到第一次匯入才發現、
要把一段 IN/OUT 交給 Resolve 只能先匯出。另修一個沒有標過人物的庫會讓畫面卡住的迴圈。

### 新增
- **桌面 App 可以建立、切換多個獨立素材庫**（#561）：側欄 Projects 的每一列點了就切過去，
  另有「＋ 新增素材庫」（選一個資料夾 → 登記 → 切換，第一次啟動時在裡面建 `.arkiv/`）。
  切換＝App 重啟後端、改指向新庫，約 10–30 秒；上次開的庫會記住，下次啟動直接開它
  （資料夾不在了——例如 NAS 沒掛上——就回到內建素材庫）。匯入、卸載、重建索引、產生 proxy
  等工作進行中時拒絕切換並說明原因（新增 `GET /api/library/busy`）；新庫起不來時自動回到原本的庫。
  免費版的專案數上限照舊適用。**MCP 不跟著切**：它綁的是設定檔裡的庫。
- **首次啟動設定精靈**（roadmap 9.8a）：第一次打開、而且庫裡還沒有自己的素材時，自動進入三步精靈——
  ① 檢查這台電腦（CPU／記憶體／顯示卡、轉錄會用哪個裝置、有沒有 ffmpeg；沒有 NVIDIA 卻留著 CUDA 預設、
  或缺 ffmpeg 會在這裡就講清楚怎麼修）② AI 模型（Whisper 與三顆 Ollama 模型是否已下載，可「現在下載」看逐項進度，
  或「等匯入時再下載」）③ 加入素材（前往匯入，或先看範例片）。隨時可略過；設定 → System 可重新執行。
  已經有素材的既有使用者升級後不會被帶進精靈。新 API：`/api/setup/status`、`/api/setup/models/pull`、
  `/api/setup/complete`、`/api/setup/reset`。只下載模型，素材不離開本機。
- **把素材直接拖進剪輯軟體**（roadmap 12.6，桌面 App）：搜尋結果的卡片可以直接拖進 DaVinci Resolve、
  Premiere 或 Finder（拖的是原始檔本身，不是複本）；Inspector 有「拖出原始檔」，標了 IN/OUT 時改成
  「拖出 IN/OUT 片段」——arkiv 先切出那一段（存在 `.arkiv/dragout/`，7 天後自動清掉）再拖那個檔：
  2 分鐘內的片段重新編碼，剛好從 IN 那一格開始（Apple Silicon 用硬體編碼，幾秒內完成）；更長的用無損切，
  只能從關鍵格開始，開頭可能比 IN 多幾格，畫面上會註明。標 IN/OUT 時影片會定格，空白鍵播放／暫停。原始檔不在（NAS 沒連上）時會直接說。
  新 API：`POST /api/media/{id}/drag-source`；Tauri 加 `tauri-plugin-drag` 2.1.1。瀏覽器版不受影響。

### 修正
- **還沒標過任何人物的庫，選一支片就會讓前端每秒打數百次 `/api/identities/names`**（#556 起）：
  人名清單是空的就一直重抓。畫面因此卡頓（waveform 拖曳、快捷鍵、拖出都受影響）。改成每次開頁抓一次、新增／刪除人物後再刷新。
- **`no_speech_prob` 偏低的靜音幻覺不再混進逐字稿**：噪音底下 faster-whisper 會用「Thank you.」填滿 30 秒的視窗，
  `no_speech_prob` 只有 0.14–0.75，任何門檻都擋不到。現在同一解碼視窗 ≥10 秒、每秒 <1 字就整段丟掉，不看分數。
  規則在 whisper-guard 0.3.3（vulture-s/whisper-guard#6）。需要 `whisper-guard>=0.3.3`。
- **搬移並改名的檔案，匯出、從垃圾桶還原與對帳清單不再用舊檔名**（#566）：偵測到素材被搬走時只改了 `path`，
  `filename`／`ext` 還是舊的，於是匯出檔名、垃圾桶還原與對帳列表指向一個磁碟上已經不存在的名字。現在一起更新；
  檔名也算進語意索引的內容雜湊，那次匯入結束時會自動重建那支的索引。
- **匯入對帳摘要的「失敗」列會寫出原因**（#573）：原本只列檔名，看不出是解碼失敗、讀不到還是例外；
  現在印成「檔名 — 原因」（例外帶型別與訊息，單行、最多 200 字）。結束碼不變。

## v1.6.1 - 2026-10-20

**Windows 第一次使用就能從 NAS 匯入；只在 Mac 上看片可以不建 proxy。**
這一版的修正來自一次 Windows 首次使用模擬：連不到的網路路徑不再只丟「API 500」、
Windows 的網路磁碟機與「影片」資料夾可以匯入、Projects 頁不再被誤當「開新專案」。

- 依賴升級：fastapi／uvicorn 系列、pydantic、requests、chromadb、pytest-asyncio（3.9 維持可安裝）、
  vite／svelte／three 等前端套件、`actions/upload-artifact` v7、serde_json。

### 新增
- **ingest 可以不建 proxy**（#558）：`ingest.py --skip-proxies`（＝`--proxies never`）跳過 Phase 3；
  `ARKIV_PROXIES=never` 或庫設定 `ingest.proxies` 設成這個庫的預設（優先序：旗標 ＞ 庫設定 ＞ 環境變數 ＞ `auto`）。
  只在 Mac（App／Safari 能直接播 HEVC）看片的庫省下整段轉檔——871 支手機短片實測約 5 小時、9 GB。
  預設 `auto` 行為不變；關掉 proxy 不影響 embedding。這種庫的 `/api/stream` 會明講「這個庫關閉了 proxy」，
  播放面板顯示原因、按鈕改為「只為這一支建立 proxy」。
- **`--regenerate-proxies` 會補建還沒有的 proxy**，不再只重建已存在的——對從沒建過 proxy 的庫原本什麼都不做。

### 修正
- **加入還沒登入的網路磁碟不再只看到「API 500」**：在 Windows 填 `\\NAS\share\...` 這類尚未登入的 SMB 路徑，
  系統會回「帳號或密碼不正確／拒絕存取」，原本這個錯誤沒被接住，Projects 頁只顯示「arkiv API 500」。
  現在加入專案、匯入掃描、DIT 轉存、匯出 CSV 遇到這種路徑會回 400，並說明原因與下一步：
  「無法存取這個路徑（系統原因）。如果是網路磁碟，請先在檔案總管開啟這個位置並登入，再回來加入。」
  專案健康檢查／同步也不再因為其中一個專案連不到就整個失敗——那個專案會標成「無法存取」（`inaccessible`），其他照常。

- **Windows：NAS 資料夾、自己的「影片」資料夾都匯入不了**（403「ingest 路徑必須在批准的目錄底下」）。
  允許匯入的清單只寫了 Mac 的 `Movies`，Windows／Linux 的 `Videos` 不在裡面；Mac 會自動放行 `/Volumes/*`（NAS 掛載），
  Windows 沒有對應。現在：預設加入 `~/Videos`（匯出允許清單也是）；Windows 已對應的**網路磁碟機**（`Z:` 等，只限 DRIVE_REMOTE）
  自動放行；`\\伺服器\分享\…` 這種網路路徑可到 設定 → Ingest · 匯入 → 額外允許的資料夾 加入（庫設定 `ingest.extra_roots`）。
  本機整顆磁碟（`C:\`、`/`）仍然擋，`\\host\C$` 這種管理分享也擋。`ARKIV_INGEST_ROOTS` 仍是「整份取代」，設定是額外加入。
  403 訊息改成講得出下一步的白話，且不再列出伺服器上的實際路徑。

- **設定 → Projects 頁不再被誤當成「開新專案」**：2026-10-20 Windows 首次使用實測，新使用者把跨庫註冊表的
  「加入專案」當成開始使用 arkiv 的地方，登記了一個 NAS 素材資料夾。頁首改用一般語言說明這頁只登記**其他已建好的
  arkiv 素材庫**供跨庫搜尋、不會建庫也不會匯入；`ARKIV_PROJECT_ROOT`／`~/.arkiv-projects.json` 收進「技術細節」。
  目前開啟的庫沒有自己的素材時（扣掉自動載入的範例片），頁頂顯示「請按右上角 ＋ INGEST」引導並附按鈕直達匯入。
  登記後若該資料夾沒有 `.arkiv/`（health `db_missing`）會明講「不是 arkiv 素材庫，請改用 ＋ INGEST」；
  加入失敗訊息改成人話，健康狀態欄顯示中文標籤。邏輯在 `frontend/src/lib/projectsFirstRun.js`（附 node 測試）。
- **配樂／噪音底下的真講話不再被當靜音丟掉**：faster-whisper 在這種情況會對整段真講話回 `no_speech_prob` 0.9–1.0
  （信心 −0.07、每秒約 9 字），Guard 2 只看 no_speech > 0.8 就整段丟、不留紀錄；只有這種段落的短片會在 Guard 1 被整份清空。
  實測一支 177 秒的解說片少了 20 段。現在同一解碼視窗 ≥5 秒、每秒 ≥2 字、信心 > −0.5 就保留；規則沿用 whisper-guard 0.3.2（不另抄一份）。
  靜音幻覺（「Thank you.」「由 Amara.org 社群提供的字幕」）每秒 <1 字，照舊擋下。需要 `whisper-guard>=0.3.2`。

## v1.6.0 - 2026-10-19

**過卡更安全、誰在畫面裡標得出來、逐字稿直接進 Resolve。**
換卡不再沿用前一張卡的續傳狀態、目的地同名不同內容一律不覆蓋；可以手動標「誰在畫面裡」並用 `who:` 搜；
逐字稿可匯出成 Resolve 時間軸 marker；字典改一次就記住、下次轉錄就聽對。

### 修正（資料安全）
- **過卡：第二張卡不再被當成「已驗證」。** 卡 A 過完、退卡、卡 B 掛在同一個掛載點再按 offload，舊版會沿用卡 A 的續傳狀態，
  卡 B 的檔不被列舉、同名的被當成已驗證，回 `done`——DIT 看到 done 就格式化卡 B。續傳狀態現在綁卡片內容指紋，換卡就重來。
- **過卡：目的地已有同名但內容不同的檔，絕不覆蓋**（例如兩張卡都有 `C0001.MP4`）。改成標「需要改名」並跳出改名對話框；
  內容相同則視為已驗證、不重拷。MHL 會比對每個檔第一次被記錄的 hash，備份被改過就判失敗。
- **關掉 App 時，正在跑的 ingest／whisper／offload 會一起停**，不再在背景變成孤兒繼續寫庫（重開後也不會跟孤兒搶記憶體）。
- **刪除重複列、還原逐字稿、移除範例片，不再波及別支素材**（素材編號會被重用，舊版只認編號）。
- **切換逐字稿語言再切回來，字典校正不會不見。**
- **重建關鍵畫面後，畫面不再一直顯示舊圖**（原本要重開 App 才會更新）。

### 其他新增
- **運鏡欄位（A7，opt-in）**：每個 scene 多 `camera_motion`（左右搖、推拉、手持晃動），不靠 VLM。ingest 加 `--camera-motion`，
  既有庫用 `--refresh-motion` 補量；只量水平軸。
- **MCP `export_project`**：一次讀整個庫（或篩選後）的逐字稿段落＋場景描述＋時間碼，分頁，給 AI 規劃整場拍攝用。
- **匯出時看得到在跑**：匯出中按鈕顯示 `…`、其他匯出按鈕暫停，完成 toast 寫出檔名與大小。
- **素材陸續到位時 watch 會合批進庫**（同一輪最多 8 支一次轉錄），不再每支都重新載入模型。
- **vision 併發（opt-in）**：`ARKIV_VISION_CONCURRENCY=2` 可同時送兩張 frame；實測效益依機器而定，預設仍是 1。

### 安全
- **App 與瀏覽器介面現在真的有 CSP。** 舊的 Tauri CSP 設定其實碰不到 App 載入的頁面，改由後端在頁面回應帶上。


**依賴版本固化：每條安裝路徑裝到的是同一組版本。**
新增 `constraints.txt`（`scripts/lock.sh`，uv universal 解析，一份涵蓋 3.9–3.12 與 macOS／Windows／Linux），
CI、release、Docker、安裝文件一律 `pip install -r … -c constraints.txt`；CI 會重跑 lock，有差異就紅。
Python／Node／Rust 版本各自只有一個來源（`.python-version`／`.nvmrc`／`rust-toolchain.toml`），
`scripts/doctor.py` 對照本機、`--fix` 一鍵建 venv。

- **修：`requirements.txt` 在 Python 3.9 上裝不起來。** #234 起 fastapi／uvicorn／pillow 的下限都要 3.10，
  而 CI 的 3.9 測試腿裝的是另一份手抄清單，所以兩個月沒人發現。3.9 改用各自最後支援 3.9 的版本。
- **Windows 安裝包的 torch 從 2.9.1 對齊到跟 Mac 一樣的版本**：打包時多裝的 `torchaudio`（沒有任何程式用到）
  把 torch 鎖在 2.9.1。
- Docker：UI 建置從 Node 25 改為 22（與 CI 同版），Python 3.11 → 3.12（與正式環境同版）。

**校正一次就記住：`POST /api/corrections/learn`。** 把「李東煙」改成「李多慧」一次，
本專案字典就記下這條（同一個錯字再改會更新、不會重複），並把「李多慧」排進**下一次轉錄**的
Whisper initial_prompt；`apply: true` 會順便只用這一條改寫本專案已存的逐字稿（先備份、可還原）。
整句話不會被當 hotword 餵給 Whisper（只留作批次校正），避免 prompt 幻覺。

**hotword 清單有長度上限了。** Whisper 的 prompt 只留最後約 223 個 token，超過時會默默丟掉
**最前面**的詞——也就是 `ARKIV_CUSTOM_VOCABULARY` 與 `vocabulary.txt` 的詞。現在由 arkiv
自己按優先序裁到 200（估算值，偏保守）：環境變數 → vocabulary.txt → 字典（最近學到的先），
裝不下的從尾巴丟，轉錄 log 會印出丟了哪些。



### 新增
- **人物標註（A6，人工）**：Inspector 新增「人物」區塊——按「標人物」輸入名字（自動補完本專案用過的名字；Enter 送出、Esc 取消），有 IN/OUT 就標那一段，沒有就標整支片；點時間跳到該處、× 刪除。同一支片同一個名字的重疊／相接區段會自動合併成一段，整支片的標註會吸收該名字的所有區段。名字一律去頭尾空白、NFC 正規化。搜尋框（與 MCP `search_media`／`export_project`、`exposure_report --filters`）多一個 `who:小明`（子字串、英文不分大小寫）。`/api/media/{id}/scenes`、MCP `get_scenes`、`export_project` 的每個 scene 多 `people: [名字…]`（與 scene 時間有重疊的標註；**只有標過人物的片才出現這個 key**，沒標過的片回應一字不變）。API：`GET/POST /api/media/{id}/identities`、`DELETE /api/media/{id}/identities/{identity_id}`、`GET /api/identities/names`（權限比照標籤：`videos_read`／`videos_write`）。**不做任何自動辨識、不存任何臉部或生物特徵資料**；這批人工標註之後當自動化的驗證集。
- **品牌／物件曝光報告**：給一個或多個詞（品牌名、產品名、物件），統計素材裡命中幾支、總共幾秒、在哪些 scene。比對 keyframe 的 vision 描述與標籤，大小寫、全半形、繁簡都不分；可用搜尋框語法（`shot:` `camera:` `tag:`…）縮小範圍，可依拍攝日期或相機分組。曝光秒數＝命中 scene 的整段長度，scene 切法跟 `get_scenes` 同一套。三個入口：CLI `python exposure_report.py "古河" --filters "shot:2026-10"`（markdown／`--format json`）、REST `GET /api/report/exposure?term=…`、MCP 工具 `exposure_report`。⚠️ 這是依 vision 描述比對，不是逐格偵測：會漏判、會誤判，報告裡也這樣寫；沒做過 vision 分析的素材另外計數，不算成「沒出現」。

**逐字稿可以直接變成 Resolve 時間軸上的 marker。** Inspector 匯出列多一顆「字幕 MARKER」：
匯出的 FCPXML 每段話一個 marker，名稱是那句話（前 60 字），備註是全文，長度跟著那段話。
設了 ✂ IN/OUT 只輸出範圍內的段落。API：`/api/media/{id}/export/fcpxml?markers=transcript`
（`edl-markers` 也吃，但 EDL 的 marker 只能是 ASCII，中文會變成 `?`，中文片請用 FCPXML）。
原本的場景 marker 不變，仍是預設。

**Remotion props 吃 IN/OUT。** `GET /api/media/{id}/remotion-props` 接受跟單支匯出同一組
`in_s`／`out_s`（秒）：只輸出範圍內的字、時間改成從 IN 起算、`duration` 變成範圍長度，
並多帶 `trim: {in_s, out_s}` 讓渲染端知道要從原檔哪裡開始。跨邊界的字裁到邊界、只碰到邊界的字丟掉，
規則跟 SRT／EDL／FCPXML 的 trim 匯出共用同一個函式（`transcript_time.trim_window`／`trim_rebase`）。
Inspector 設了 IN/OUT 時，「Remotion」按鈕會帶上範圍（按鈕顯示 ✂）。不帶範圍時輸出與先前完全相同。

**高 fps 素材匯出 EDL 會先提醒。** CMX3600 EDL 只涵蓋 23.976／24／25／29.97／30，50／59.94／119.88p 這類片在 Resolve conform 時 TC 可能對不上。現在照樣匯出，但 EDL 檔頭多一行 `* WARNING` 註解（解析器會略過，不影響匯入），Inspector 的 EDL 按鈕也會標 ⚠ 並在提示裡建議改用 FCPXML（B13）。

## v1.5.1 - 2026-10-14

**過卡的 MHL 清單快了一倍以上：複製完之後不再把整張卡重讀 3 遍。**
寫清單時沿用複製當下已經讀回驗證過的 hash（檔案大小與修改時間都沒變才採用），
確認清單時只讀一遍（原本同一份資料算兩次）。每個檔在目的地上的讀取次數從 4 次降為 2 次。
實際一次 117 GB、SMB 走 Wi-Fi 的過卡：MHL 階段原本 6 小時 42 分，照新流程約為原本的三分之一（#535）。

**DIT 頁顯示每個檔的複製速度。** 「最近的檔」每列多一欄 MiB/s，`--progress json` 的
`file` 事件帶 `copy_mib_s`／`verify_mib_s`，每個目的地的 summary 帶整體 `copy_mib_s`。
過卡慢的時候當下就看得出來，不用事後查（#537）。

> 💡 **NAS 用 Bonjour 名稱掛載時，SMB 可能一直走 Wi-Fi**（實測 6 MiB/s，有線網卡閒著）。
> 改用 IP 掛（`smb://<NAS 的 IP>/<共享名稱>`）可以避開。arkiv 的複製本身跟 `cp` 一樣快。

## v1.5.0 - 2026-10-14

**Log 素材進庫就是正常顏色了。** S-Log3、D-Log、C-Log、N-Log、F-Log、Apple Log、V-Log 這類
log 模式拍的片，縮圖、vision 讀的關鍵畫面、播放器以前全是灰的；現在會換算成 Rec.709 給你看，
原始檔一個 byte 都沒動。

- **12 個內建色彩設定檔**：Sony S-Log3／S-Gamut3、S-Gamut3.Cine，DJI D-Log，Insta360 I-Log，
  Canon C-Log2／C-Log3，Nikon N-Log，Fujifilm F-Log／F-Log2／F-Log2 C，Apple Log，Panasonic V-Log。
  每一個都是 arkiv **依廠商公開發表的曲線與色域公式自己算出來的**，不內附任何廠商的 LUT 檔；
  曲線用白皮書上的 0%／18%／90% 參考點逐一驗過。這是技術轉換、不是廠商的調色風格，
  跟廠商 LUT 不會逐點相同（以 Sony s709 對照：18% 灰 0.426 vs 0.429，90% 白 0.791 vs 0.780）。
- **Sony 自動辨識**：A7 系列、FX 系列的 XAVC 檔會自己標上 S-Log3。其他廠牌在 Inspector 指定一次即可。
- **自訂 LUT**：Inspector 的 COLOR 選單「匯入 .cube…」，或把 `.cube` 放進 `~/.arkiv-luts/`
  （`ARKIV_LUT_DIR` 可改）。**DJI D-Log M 與 Apple Log 2 沒有公開數學**，請匯入廠商提供的 `.cube`。
- **播放器即時套用**：有設定檔的片子，Inspector 播放器右上「LUT ● / LOG ○」隨時切回原始 log
  （proxy 本身保持 log，所以切得回去）。套用中時點畫面或按空白鍵播放／暫停。
- **舊素材庫一次補完**：`python ingest.py --regenerate-thumbnails` 會偵測並重生封面；
  關鍵畫面要等下次 `--refresh` 才套用。
- API：`GET /api/color-profiles`、`PATCH /api/media/{id}/color-profile`、批次 `POST /api/media/color-profile`、
  `POST /api/color-profiles/import`。

**長片的 proxy 不再白等 10 分鐘然後失敗。** proxy 轉檔的時限原本寫死 600 秒，
4K60 HEVC 在 M2 Max 上大約 0.9 倍即時，超過約 11 分鐘的片全部燒滿 10 分鐘才放棄
（實際一次 ingest：25 支裡 4 支沒有 proxy、多等 40 分鐘）。現在時限依片長給（片長 × 3，最少 600 秒、最多 4 小時）。

**FFmpeg 7 以上：長片的關鍵畫面重新依場景切換挑選。** FFmpeg 拿掉了 `-vsync`，
場景偵測每次都失敗、靜默退回固定位置取格；改用 `-fps_mode`（FFmpeg 5.1 起支援，arkiv 最低需求 6.0）。

- **批次匯入結束多一行對帳。** `Done.` 之後印出 scanned／ingested／already-indexed／moved／failed，
  任何一支沒乾淨進庫的檔都會列出檔名，不再需要自己拿磁碟清單跟資料庫比對（#393）。
- **修復已在庫的音檔列：`python fix_audio_cover_metadata.py`。** #521 修好之前進庫的帶封面音檔，
  `codec`／`fps`／寬高存的是封面的值（`mjpeg`、`90000`、`300×300`）。這支指令預設只列出受影響
  列數與樣本、不寫入；加 `--apply` 才清成 NULL，寫入前自動把 DB 備份到同目錄的
  `<db>.pre-521-<時間>.bak` 並核對列數，備份失敗就不寫。只動音訊副檔名的列；
  看起來也帶封面的**影片**列只列出來，請用 `ingest.py --refresh` 重新探測，不用猜的。

- **ingest 對帳不平時 exit 7，不再 exit 0。** 批次結束的 `Reconcile:` 那行若發現有檔案掃到了
  卻沒落到任何分類、或計數說進庫了但 DB 沒有那一列，現在以 **exit 7** 結束（#393）。
  原本 0 只代表「沒當掉」，不代表「每支都進庫」。7 是新號碼，0–6 的意義都不變；
  同時符合時優先序 2 > 7 > 1。`watch.py` 遇到 7 跟其他失敗一樣：記錄、退避、重試，
  監看不會停，且 log 會印出 exit code 與對帳行（原本只印 stderr，而對帳寫在 stdout，
  所以只會看到一個沒有原因的「✗ 檔名:」）。

- **帶封面的音檔不再被記成「300×300 的 mjpeg 影片、90000 fps」。** MP3／M4A／FLAC 內嵌的
  專輯封面，ffprobe 會報成一條 video stream（只靠 `attached_pic` 標記區分），ingest 把它當成
  畫面軌，codec／fps／寬高全部抄自封面。現在 ingest 跳過封面、音訊副檔名一律不讀畫面資訊；
  `/api/stream` 對舊資料回填 codec 時也不會再把 `mjpeg` 寫進音檔列（#521）。
  **已經在庫裡的錯誤值不會自己變回來**，需要另跑修復指令。

- **健檢會發現「模型名稱沒變、算出來的向量變了」。** 升級 Ollama、重新 pull 同一個 tag、
  或換了量化版本，都可能讓 `bge-m3` 這個名字底下的向量悄悄改變；原本的檢查只比對模型名稱，
  抓不到。結果是新的查詢落在跟舊索引不同的空間裡，搜尋變差卻沒有任何錯誤。
  `python health.py` 現在會拿幾段已存的內容重算一次，跟存著的向量比對，
  差太多（cosine < 0.999）就 WARN 並提示 `python embed.py --rebuild`。
  索引是空的或連不上時明確標「not measured」，不會算成通過。

- **搜尋結果會告訴你是在第幾秒講到的，點進去直接停在那裡。** 命中逐字稿的結果，
  摘要前多一個「▶ 01:23」；點開素材後播放器跳到那一秒（不自動播放）。
  語意命中取「那段文字開始的地方」，關鍵字命中取「這個詞第一次出現的地方」。
  索引本身沒有時間資訊，時間是查詢當下從逐字稿分段（segments）對回去的，
  **不需要重建索引**，舊素材庫直接有。逐字稿經過標點修復、繁簡轉換或校正後，
  文字跟分段不會逐字相同，所以用去掉標點的短片段比對；對不上就不顯示時間，
  不會用猜的（標籤／畫面描述命中、沒有分段的素材也不顯示）。

## v1.4.0 - 2026-10-11

**你開著 arkiv 的時候，你瀏覽的任何一個網頁都可能替你按下刪除鍵。這一版把它關上。**

arkiv 對從本機（127.0.0.1）來的請求一律信任 —— 那是「自己的電腦上不用輸 token」的由來。
但一個惡意網頁可以把**它自己的網域**重新解析到 127.0.0.1（DNS rebinding）：
瀏覽器認為那是同源，請求的來源又確實是本機，於是那個網頁拿到的是全部 12 種權限，
包括批次刪除、清空回收桶、以及發一把 admin token 給自己。

現在本機信任多看一件事：請求的 `Host`。只有 IP、`localhost`／`*.localhost`、
以及你自己列在 `ARKIV_ALLOWED_HOSTS` 的名字才算本機；所有寫入路由（原本只有 13／52 支）
都套上同站檢查。順帶修掉三個同一輪審計找到的 MED：轉送標頭不能再拿來繞過 token 的 IP 白名單、
WebSocket 的 `?token=` 不再明文寫進 `backend.log`、`/api/logs/tail` 輸出前會再遮一次。

### 升級前要知道

- 🔴 **如果你用 `localhost` 或 IP 以外的名字開 arkiv**（例如 `mymac.local`、tailnet 的 MagicDNS 名稱、
  反向代理的網域），升級後會拿到 401，訊息會直接指向這個設定：
  `ARKIV_ALLOWED_HOSTS=mymac.local,arkiv.example.com`（逗號分隔，附加在內建清單之後）。
- **放在反向代理後面的**：uvicorn 現在一律以 `--no-proxy-headers` 啟動，帶轉送標頭的請求
  不能使用有 IP 白名單的 token。確定代理是你的，才設 `ARKIV_TRUST_PROXY_HEADERS=1`。
- **Docker 的對外埠預設只綁本機**、**Pro 改為簽章授權檔**：見下面兩段。

**Pro 授權改為簽章驗證 —— 手寫的授權檔不再解鎖。**
以前 `~/.arkiv/pro-license.json` 只要寫了 `licensee` 和 `key` 兩個欄位就算 Pro，任何人自己寫一個就有。
現在授權檔必須帶授權方的 Ed25519 簽章，由 arkiv 以內建公鑰**離線**驗證（`pro_licence.py`，純 Python，
不需要額外套件）：不連網、不會過期、沒有啟用伺服器。

- **1.1.0 前就在使用的安裝不受影響**：無限專案與跨專案聚合照舊，不需要任何授權檔（附加條款第 3 點）。
- 授權檔在、但不算數時，`/api/entitlements` 的 `licence_problem` 會說原因（未簽章／簽章不符／未知金鑰／已撤銷）。
- 名為 `arkiv_pro` 的模組不再「存在就算 Pro」，必須透過 `has_valid_license()` 回報。
- 2026-10-08 之前沒有售出任何 Pro；當時發出的授權已改發簽章版。

**授權：PolyForm Perimeter 1.0.1 加上「附加條款」—— 修改核心來繞過免費額度或開啟 Pro 功能，不被允許（自己用也一樣）。**
原本的條款只禁止「提供他人競爭產品」，而 PolyForm 允許為任何允許的目的修改程式；
免費額度又刻意不做技術鎖（`entitlements.py`：善意承諾，不是 DRM）。兩者疊起來，
自己改掉上限自用在條款上很可能是允許的 —— 這次補上。PolyForm 原文一字不改，
附加條款放在它前面；1.1.0 前就在使用的安裝永久保留無限專案與跨專案聚合（附加條款第 3 點）。法律本身允許的修改（如著作權法第 59 條）與 Fair Use 不受影響。只適用於含這段文字的版本。

順帶：README 的「尚未開賣」改成實況（2026-10-03 起販售）。

**新增：剪輯用 proxy —— 寫到原始檔旁邊的 `Proxy/<原檔名>.mov`，Resolve 可以直接連結。**
Inspector 的 proxy 給不了剪輯軟體用：它叫 `{id}_{hash}.mp4`（Resolve 靠檔名配對 proxy）、
沒有 timecode、只留一條雙聲道、720p H.264，而且只在瀏覽器播不了的編碼才會產生。
這次**另外加一份選用輸出**，Inspector 的 proxy 一個字都沒動：

- ProRes 422 Proxy、保留原始 timecode、**保留全部音軌**，最高 1080p 且不放大（`ARKIV_EDITOR_PROXY_HEIGHT`）
- **絕不覆蓋**：`Proxy/` 裡已經有同名 `.mov`／`.mp4`／`.mxf`（剪輯師自己做的）就跳過；編碼途中有人放進來也是對方贏
- 先寫隱藏暫存檔，成功才搬到定位；失敗會清掉暫存檔，以及這次才建立的空 `Proxy/` 資料夾
- 用法：`python editor_proxy.py <media_id>…` 或 `--all`；API `POST /api/proxy/editor`（`{"ids": […]}`，`ingest_write`）、
  `GET /api/proxy/editor/status`、`GET /api/proxy/editor/{media_id}`

- Inspector 的 Reprocess 區多一顆「剪輯用 proxy」：按下去會等到做完，直接顯示寫到哪裡、已經有同名檔所以沒覆蓋、或失敗的原因

⚠️ 它會**寫進你的素材資料夾**（這正是重點，剪輯軟體才找得到）。唯讀的素材碟會回 `failed`，不會改寫別處。

**Docker：對外埠改成預設只綁本機（127.0.0.1）。**
原本 `docker-compose.yml` 把 8501（API）、8502（MCP）、11434（Ollama）開在所有網卡上，
同一個區網的人拿到 token 就能讀素材庫，Ollama 則是誰都能用。

🔴 **如果你是從別台機器連 Docker 裡的 arkiv**（例如區網 MCP、另一台電腦開網頁），
升級後要加一個環境變數才連得到：`ARKIV_BIND_HOST=0.0.0.0 docker compose up -d`。
只在同一台電腦上用的人不受影響。

### Fixed

- **現場收音被 VAD 判成「沒人聲」，逐字稿是空的，而且沒有任何訊息。**
  一場 200 支的現場素材（PA ＋ 人群）有 85 支逐字稿為空，繞過 VAD 重跑後 54 支解出人聲。
  現在 VAD 說沒人聲時會改餵整段給 Whisper，再交給既有的幻覺防護判斷，並且印出
  `[vad] no speech detected — decoding the full file anyway`。
  ⚠️ **已經是空逐字稿的素材不會自己好**：在 Inspector 對它們按「重新轉錄」。
- **轉錄的整批靜音判斷被幾段短配樂拖垮。** 三段 1 秒的 BGM 尾巴可以讓 30 秒清楚的人聲整份清空；
  現在同時看段數平均與時長加權平均，取較不激進的那個。
- **匯出：相機 timecode 換算錯。** 23.976 的相機起始 TC 01:00:00:00 被匯成 00:59:56:10，
  EDL／FCPXML 的 source 落在素材第一格之前。DF／NDF 改看相機 TC 本身的分隔符，
  單支 FCPXML 錨在相機 TC，`src` 會做 URL 編碼（空白／`#`／`%` 讓剪輯軟體找錯檔），
  camera report 的 DF 標籤也修好了。**之前匯出的 EDL／FCPXML 請重新匯出。**
- **Windows 上讀 Mac 寫的路徑（`/Volumes/…`）被當成相對路徑**，接在專案根目錄後面然後報錯。
- **繁中 Windows 上，git／ffmpeg／ffprobe 的輸出被用 cp950 解碼**，失敗時 `returncode` 仍是 0、輸出卻是 `None`。
- **1 秒以下的素材永遠沒有縮圖**（取格時間被推過檔尾）。
- **相機的旋轉旗標錯了會毀掉整條下游**（寬高對調、縮圖直式、視覺模型看側躺畫面）。
  ingest 現在會印出偵測到的旋轉；確定旗標是錯的那一批，用 `ARKIV_IGNORE_ROTATION=1` 匯入。
- **播放失敗一律說成「編碼問題」。** 401、404、後端沒開也被叫去建 proxy；現在會說真正的原因。
- **`/api/stream` 叫你去建 proxy、proxy builder 卻不建**的那幾種編碼（dnxhd／qtrle／mjpeg／cinepak），現在建得出來。
- **字幕：gap pass 把原本合格的 cue 削短，merge pass 再把它併進下一句**，讓下一句的字最多提早 0.88 秒出現。
- **刪除、評分、精選集複製會假裝成功。** 只刪了 metadata 不再顯示「已移至回收桶」；
  評分不再順手清掉評分備註；複製失敗以錯誤提示呈現。
- **Inspector 標完 IN／OUT 後 350 毫秒內切到下一支**，會清掉下一支已存的範圍。
- **回收桶還原**在桌面 App 裡幾乎必定 500；「清空全部」改成要確認（列出件數與容量）；
  未掛載的儲存不再被當成「檔案不見了」清掉。
- **校正字典讀取失敗被當成空字典**，存檔時把整份覆蓋掉。現在會警告，存檔前留 `.bak`。
- **Resolve 外掛**：不同卡的同名 `C0001.MP4` 勾第一列卻匯入最後一支；評分／標籤改依來源路徑比對；
  LAN 版下載保留原檔名並串流寫入，不再整支讀進記憶體。
- **過卡（offload）**：拷完到真正完成之間的兩趟雜湊沒有任何進度（實測 35 分鐘）；
  一顆碟的 MHL 失敗會讓第二顆碟根本沒被拷，畫面還只顯示「exit 1」。
- **匯入**：長素材在健康轉錄時被判超時、Windows 上 watchdog 留下 ffmpeg／whisper 孤兒行程、
  跑完的匯入被回報成「已停止」、一個非 UTF-8 位元組讓匯入被誤殺、
  打錯一個調校用環境變數會讓整個 API 起不來。
- **Projects 頁沒說你現在開的是哪個庫**，註冊表看起來像切換器（它不是）。
- **CLI 的 `--all-projects` 沒有套用跟 API 一樣的跨專案授權檢查。**

### Added

- **搜尋框的 `tag:`／`camera:`／`lang:`／`rating:`／`type:`／`shot:` 真的會篩了**，「所有專案」搜尋也一樣。
  以前整串被當成文字丟進語意搜尋，`格爾木氧氣 camera:a7s3` 會回 0 支或回整個庫。
- **匯入的時間上限拆成「靜默多久」與「總共多久」**，停下來時會說是哪一道；
  原本寫死 30 分鐘（實測 200 支要 94 分鐘），而 UI 實際走的那條路根本沒有上限。
  掃描時就顯示預估時間。
- **瀏覽器版的匯入頁有常用來源捷徑**（瀏覽器沒有選資料夾的能力，以前每次都得手打路徑）。
- **索引過期看得見**：`GET /api/embed/status`，匯入頁的「重建索引」底下顯示幾支待更新／尚未索引。
- **剪輯欄位的修改紀錄**：評等、IN／OUT、camera、標籤每次真的改動，都會在
  `.arkiv/audit/edits.jsonl` 記一行修改前後的值。
- **健康檢查會抓「前端是舊的」**：`frontend/dist` 比最後一次前端原始碼的 commit 舊時回報失敗。

### Changed

- 官網改成多頁站（首頁、安裝、文件、FAQ、更新紀錄），首頁功能區改成會動的介面示意；購買 Pro 改成實心按鈕。
- Pro 條款定稿：數位內容交付後不退款、管轄法院為臺灣臺北地方法院。
- `PUBLIC-INTEREST.md`、README、FAQ 裡過期的說明更新為現況（專案上限 1.1.0 起已實施、環境變數預設路徑等）。

## v1.3.0 - 2026-09-05

**如果你的素材庫裡有 JPEG，它們一直是半盲的。**
每一張進庫的 JPEG 都沒有縮圖、沒有格、沒有視覺標籤 —— 而且沒有任何錯誤訊息。

ffprobe 把 `.jpg` 報成 `mjpeg` 且 duration `0.040000`（單格，假設 25fps），
所以「這是不是靜圖」的舊判準 `duration_s <= 0` 把每一張 JPEG 當成影片，
送進「跳到中間點再取一格」的路徑。而 `-ss` 放在 `-i` 之前對單格 mjpeg
**即使 `-ss 0` 也編不出東西**（ffmpeg 回 exit 0，訊息是 "Output file is empty"）。
`.png` 與 `.webp` 報 `duration=N/A` → 0.0，所以它們一直是好的 —— 只有 JPEG 壞。

看得見的損失不是那張缺席的縮圖。零格代表 `ingest.py` 的
`if not skip_vision and frame_data:` 從來沒有成立過，**視覺模型一張 JPEG 都沒看過**：
沒有場景描述、沒有自動標籤，而且用內容搜尋找不到它。

🔴 **既有素材庫要把那些 JPEG 重新 ingest 才會補上。** 缺的是衍生檔與標籤、不是原始檔，
所以重跑就會好，不需要重新轉錄。

缺陷由外部使用者 pixb 回報（issue #418）。他報的是圖片預覽，
實際去 ingest 一張圖驗證時才發現 PNG 沒事、**JPEG 全壞**。

### Fixed

- **每一張 JPEG 都沒有縮圖／格／視覺標籤**（上述）。
- **跨行程寫入之後，搜尋看不到新素材，而 `count()` 一直是對的。**
  ChromaDB 的 `count()` 讀 SQLite、`query()` 讀行程內的 HNSW 索引，
  所以「筆數對、但搜尋不到」不是矛盾，是兩個資料來源。
  典型情境：CLI ingest 完成，開著的 UI 搜尋不到那批新素材，直到重啟服務。
- **刪掉的素材卡在每一個精選集裡，而那個編號會被下一支素材重用。**
  `media.id` 是 `INTEGER PRIMARY KEY` 沒有 `AUTOINCREMENT`，SQLite 會把刪掉的編號
  發給下一次匯入。所以殘留的精選集條目不是永遠顯示「已從庫刪除」——
  它只停在那裡到下一次匯入為止，之後就乾乾淨淨地指向**你從沒放進去的素材**，
  而精選集正是跨專案複製的來源。
  刪除現在會同步清掉所有精選集裡的引用；**已經殘留的舊條目**則靠檔名比對抓出來，
  標成「已換成別支」而不是靜靜地指向別人。
- **瀏覽器播不動的編碼一律回 409，而不是只有 hevc 與 prores 兩種。**
  先前 mjpeg／qtrle／dnxhd／cinepak／rawvideo 都是把原始 bytes 直接交給播放器：
  一片黑、沒有錯誤訊息、也沒有辦法要求 proxy。改為 allow-list —— 瀏覽器**能**播的
  編碼是一份短而穩定的清單，播不動的則是無窮無盡的。
- **`docker compose` 掛了 arkiv 產生的東西，卻從來沒掛使用者的素材。**
  跨庫精選集與專案 registry 也會持久化了（先前 `down` 或重建映像會靜默清空）。
- 打包版離開時，backend 與我們啟動的 ollama 沒有被收掉。

### Added

- **字幕有了時間維度。** 先前只有行寬與斷點規則，沒有任何東西在管
  「一句字停多久」「兩句之間有沒有呼吸」。
  ⚠️ 規格是量出來的：拿一個 34 支、312 段的真實素材庫實測，
  **CPS（字/秒）中位數 4.7、只有 1.3% 超過 9** —— 原本假設的「字太快」不成立。
  真正的缺陷是 **28 個過短的 cue 與 256 個零間隙**（字接字，沒有喘息）。
  依實測加上最短／最長顯示時間、最小間隙、目標 CPS 三道 pass，
  違規率 **10.2% → 1.7%**。既有素材庫不需重新轉錄，排版是算繪時套用的。
- **MCP 走 HTTP/SSE，LAN 上的 client 連得到**（先前只有 stdio，等於只有本機）。
  唯讀工具，沿用 HTTP API 同一套 token store、IP 白名單與到期時間，
  另需 `videos_read` scope。**預設仍綁 `127.0.0.1`**，要開給 LAN 得自己改；
  真正的關卡是 Host 白名單而不是綁定位址。
- **media pool 與素材卡片顯示拍攝日**，不是入庫日。
  （62 支的測試庫裡，56 支有日期的素材中有 55 支兩者的年份不同。）

### Changed

- 落地頁的 hero 從「承諾」換成「機制」：講 arkiv 實際做什麼
  （有畫面的分析畫面、有聲音的轉錄），而不是講一句每個競品都說得出口的話。

## v1.2.0 - 2026-09-02

**每一份在 Mac 上產生的逐字稿，時間碼都是錯的 —— 而且錯得沿著片子越走越大。**
這一版修好它。如果你用 arkiv 做過訪談或字幕，這是你要的那一版。

缺陷由一位外部使用者回報：點逐字稿的時間碼跳轉，會對不上、或跑回 00:00。
那不是她的環境問題 —— 而且她連修法都一起寄來了，設計與本版採用的相同。
致謝見 `ACKNOWLEDGEMENTS.md`（具名方式待她本人回覆，本專案先問過再寫）。轉錄前 arkiv 會跑 VAD 把靜音切掉、把語音接成一段沒有空隙的
音檔再送進 whisper —— 而那個函式算完「每一段語音原本在哪裡」之後只回傳了檔案路徑，
對照表就此遺失，裁切檔幾行後被刪除。於是 whisper 報回來的每一個時間都是
「無空隙語音時間」，而 arkiv 裡其他每一個時鐘（畫格、波形、播放器、EDL 來源 TC）
都是媒體時間。誤差等於那句話之前被移除的靜音總和，**所以它沿著片子累積遞增**。

影響 `_transcribe_mlx`（**所有 Mac**）與 `_transcribe_faster_whisper`（非 Mac 預設）；
`_transcribe_whisperx` 讀原始 wav 不受影響 —— 三個後端對「3.5 秒」的定義並不一致。

🔴 **既有素材庫必須重新轉錄才會拿到正確時間碼。** 沒有「只補 offset」的捷徑：
stamps 被消費即丟，來源 wav 是當場刪除的暫存檔，只能整支重跑。重跑請帶 `backup=True`。

### Fixed

- **VAD 時間碼偏移**（上述）。`_vad_filter()` 現在回傳 `(path, offset_map)`，
  以 `bisect` 將 segment 與逐字時間碼在下游看到之前就映射回媒體時間。
- **使用者從 UI 下載的每一份 SRT/VTT，從來沒經過排版引擎。** `subtitle.py`（CJK 行寬、
  斷點規則、雙語 cue）全樹只有 CLI 與它自己的測試在 import；UI 那三條路各自走手寫的
  cue emitter。三條路現在共用同一個 seam。
- **mlx 後端向 whisper 要了逐字時間碼，然後把它丟掉** —— 每一支在 Mac 上 ingest 的素材，
  `words_json` 都是 NULL。
- **潤稿在長逐字稿上靜默逾時**，然後把未標點的原文當成結果回傳，零 log。現在切塊處理、
  每塊逾時、並有整體預算；到點就把剩下的原樣回傳並記錄。
- **單支重轉錄完全不拿 ingest 鎖** —— 批次跑到一半點單支重轉，等於兩個 whisper 同時解碼。
- 畫一條波形要去解 4K 原檔；剪接室既有的 proxy 現在會被優先採用。
- `.mts` 探測出來是 h264 但瀏覽器播不了；ProRes 原檔配 ProRes proxy 不能算「能播」。
- 舊 library 的「在 Finder 顯示」永遠回 403。
- 視覺標籤與結構化欄位仍會把簡體字漏進索引。
- ⌘Q 離開時，backend 與我們啟動的 ollama 都沒有被收掉。
- **側欄那份「Projects」清單其實從來不是專案清單**，是你當下開著的那個庫的資料夾名，
  而且從未呼叫 `/api/projects`。現在讀 registry，並顯示這個安裝的免費額度用量。

### Added

- **靜態圖片成為一等公民**（PNG／JPG／WebP／GIF／SVG）：與影片同庫、同樣可搜尋與篩選。
- **回收桶**：刪除改為移入 `.arkiv/trash` 而非 unlink，可還原；刪除時同步從跨庫精選集
  移除該筆，精選集計數不再指向已消失的素材。
- **第二條 ASR 引擎**，走 OpenAI 相容的 `/audio/transcriptions` 介面（QwenASR、Groq、
  Cloudflare、whisper.cpp 皆同協定，一個轉接層通到全部）。同一段聲音跑兩次、把兩份稿的
  差異變成產出：對齊按字元流而非 segment 邊界。
  **台語濃的素材上兩個引擎是系統性分歧**（一致率 49%），對系統性差異用 diff 是拿錯工具 ——
  改以句尾語氣詞密度判斷哪一份稿保留了較多口語質地，一份稿一個數字，不需對齊也不需人耳。
  這是**引擎選擇的輔助訊號，不是台語轉錄功能**。
- **媒體能力探針**：批次開始前先問「這台機器解得開這個庫嗎」（存在／讀得到／有音軌／
  解得開四道閘），而不是只問「ffmpeg 裝了嗎」。只有全部不可用才拒絕。
- 手動與正規化 tag、以及視覺 tag 全部進向量索引；改 tag 會被新鮮度檢查視為 stale 而自動重嵌。
- 匯出**不含時間碼的純文字稿**（server 早就支援，UI 從來沒提供入口）。
- 轉錄階段的進度回報（只有階段，沒有假百分比）。
- per-project 設定真的會被讀到 —— 解析鏈早就完整，但 production 八個消費點沒有一個帶 `project=`。
- `docker compose` 現在會持久化跨庫精選集與專案 registry；先前 `down` 或重建映像會靜默清空。

### Changed

- **低電平素材的正規化預設關閉。** 13 支實測：8 支結果相同、5 支變差、**0 支變好**。
  正規化會把底噪抬起來，VAD 因此多留三倍音訊，whisper 就在噪音上產生幻覺。
- 字幕只保留 `，！？`，停頓交給 cue 時間；標點政策只在算繪時套用，不動儲存 ——
  `.txt` 仍完整，既有素材庫不必重新轉錄。
- 字幕行寬收進 `export.subtitle_max_cjk`，先前硬編在四處。
- 打包的 app 現在會啟動 Ollama 並找得到 ffmpeg。

### 本版同時包含先前標為 1.1.2 但從未發佈的內容

`v1.1.2` 有 CHANGELOG 段落與版號，但**從未打過 tag、也沒有 release**。
其內容（出貨守衛改清單驅動、tauri-cli 快取、Pro add-on 介面合約測試）併入本版。

## v1.1.2 - 2026-08-24（已備妥，未發佈）

**Nothing in this release changes what arkiv does.** It exists so that three changes to the
build and packaging path get exercised by a real release instead of waiting for the next one
that happens to carry a feature — a release pipeline that has only ever been reasoned about is
not a verified one. If you are on 1.1.1 there is no reason to update.

### Fixed

- **A local build no longer ships a developer's benchmark logs.** Both assemble scripts already
  excluded them, and both already refused to ship a stray `.env` — but that refusal only looked
  for the one pattern it named, which is the "only what someone remembered" failure it exists to
  prevent, one level up. The check is now driven by a list, so covering a new file class is one
  line and the error says which pattern matched. `bench_*.json` is the second entry: it carries
  the machine's GPU model and the filenames of whatever was last ingested, which on a working
  editor is a list of client media. **Artifacts published from this repository were never
  affected** — releases build from a clean CI checkout.

### Changed

- **Release builds no longer recompile `tauri-cli` from source every time.** Measured on the
  1.1.1 tag run: 9m38s on macOS and 10m05s on Windows, producing a binary identical to the last
  one. The CLI version is now pinned and the built binary cached. Pinning also closes a hole
  that had not yet bitten: `--version "^2.0"` resolves at run time, so two releases could embed
  different bundlers with nothing in the repository changing.
- The Pro add-on interface (`arkiv_pro`) now has tests. The component ships separately and does
  not exist yet, which is exactly why the contract is worth writing down now: when it arrives, a
  mistake on this side either refuses a paying user or bypasses the tier, and neither shows up
  as a failing test unless the contract was pinned first.

## v1.1.1 - 2026-08-21

**1.1.0 shipped the free allowance. It also shipped three ways for a long-time user to lose
the exemption 1.1.0 had just promised them.** All three are fixed here. If you have been using
arkiv since before 1.1.0, this release is the one that actually keeps that promise.

They are the same bug wearing three faces: the guarantee is written down in one place, and the
code that honours it depends on an observation that can disappear.

### Fixed

- **Your exemption no longer depends on whether a drive is mounted today.** The check only ever
  looked at libraries reachable *at that moment*, so an install whose grandfathered corpus lives
  on a NAS looked like a brand-new install on any morning the NAS did not mount — refusing a
  fourth project and cross-project search, and pointing the user at a Pro add-on they should
  never have been asked to buy. The observation is now recorded the first time it is made and
  the record is the answer, the same move `first_seen_version` makes: write the fact down while
  it is still observable. The record can only ever grant, never revoke.
- **Upgrading straight from 0.12.x no longer costs you the exemption.** The provenance anchor
  assumed it would ship a release or two ahead of the cap; in fact the window was one day
  (anchor in 1.0.0, cap in 1.1.0). Anyone who upgraded to the newest release rather than to
  whichever one happened to carry the anchor had their years-old library stamped `1.1.0` on
  first open and silently reclassified as new. An existing library is now stamped `pre-anchor`
  — which is what is actually known about it — instead of a version number it never ran.
- **The library you have open now counts as evidence.** The grandfather probe collected
  registered projects and an environment variable and nothing else, so the ordinary install —
  one library, no second project ever registered — produced an empty set and reported itself as
  new no matter how old that library was. This is why the two fixes above did not help anyone on
  their own: they marked the library correctly and stood ready to remember it, while nothing put
  it in front of the check.
- **Local builds no longer bundle a developer's `.env`.** Both assemble scripts copied the repo
  into the bundle through a denylist that did not list `.env`. **Artifacts published from this
  repository are unaffected** — releases build from a clean CI checkout, where a gitignored
  `.env` does not exist — but anyone building an installer on their own machine was shipping
  their own secrets. The Windows script's exclusion list also claimed to drop `bench_*.json`
  while its `/XF` arguments did not.

### Changed

- **Frontend moved to Svelte 5** (with Vite 8, `vite-plugin-svelte` 7 and `svelte-spa-router` 5).
  No behaviour change is intended; it is here because the dependency set had been pinned by it
  for weeks. Verified by rendering the real UI on both platforms rather than by the build going
  green — a build that compiles proves nothing about a framework whose breaking change is at
  mount time. The initial bundle dropped from ~404 kB to ~219 kB as a side effect.
- Release builds cache the Tauri packaging toolchain instead of downloading WiX and NSIS during
  every build, which had already failed one release mid-flight.

## v1.1.0 - 2026-08-19

**The free allowance the product page has described for months now exists in the software.**

Every build up to and including 1.0.0 shipped with the tier table published and *nothing
enforcing it* — `project_limit`, `entitlement` and `is_pro` had zero hits across the whole
repository. v1.0.0's own notes said it plainly ("no shipped build caps anything"), so this is
not a regression being fixed; it is the other half of a design finally arriving. Until now
every user was running the Pro feature set.

**If you already use arkiv, nothing changes for you — permanently.** The allowance applies to
installations that begin with 1.1.0 or later. An installation already in use before this
release keeps *both* Pro features, unlimited projects and cross-project aggregation, forever.
That is not a grace period and not a setting: 1.0.0 recorded the version each library was first
seen under (`library_meta.first_seen_version`), specifically so this could be honoured from a
record instead of guessed at afterwards. A build older than 1.1.0 does not enforce the
allowance at all.

**The add-on is still not on sale.** The tier is now enforced, but the Pro component and a
payment path do not exist yet, so a new installation that reaches three projects cannot
currently buy its way past it. The refusal points at the published terms, which state their own
availability. If that limit is in your way today, say so — the grandfathering rule is generous
by design and the terms have a public-interest route.

### Added
- **Free tier: 3 projects, no cross-project aggregation — enforced.** A single decision point
  (`entitlements.py`) answers every "is this allowed?" question, rather than three independent
  `if`s that would drift apart the way the extension sets did before `mediatypes.py`. Three
  enforcement points, each with a deliberate choice about *where* to stop:
  - `projects.add_project()` — only the path that actually grows the registry. Re-registering
    an existing name (a rename, or a library that moved) leaves the count unchanged and is not
    refused; gating it would leave someone at the limit unable to repair a project they already
    own, which the terms never claimed.
  - `GET /api/search/all` — refused *before* the fan-out, not by filtering results afterwards.
    A federated search that quietly returned only the current project would be indistinguishable
    on screen from "your other projects had no matches".
  - `bins.add_items()` — refuses only the operation that widens a collection to a *new* project.
    Adding to a project the collection already contains stays available, so existing
    cross-project collections are never retroactively frozen.
- **`GET /api/entitlements`** — the tier, in one probe, from the code that enforces it. Reports
  `armed` explicitly, because "allowed" and "entitled" are otherwise indistinguishable through
  the API, and that is exactly how a shipped-but-inert gate goes unnoticed.
- **Pro entitlement detection**, two independent routes, either sufficient: the closed-source
  `arkiv_pro` component being importable, or `~/.arkiv/pro-license.json` naming a licensee and a
  key. Unsigned and unobfuscated on purpose — the licence is enforced by its terms, not by the
  database, and the anchor row has always been trivially editable.

### Fixed
- **The cap would have taken effect one release early for a specific user.** The grandfathering
  machinery answers "has this installation been in use for a while?", but nothing answered "is
  the rule in force yet?". An installation that had registered several project roots without
  ingesting into them owns no `project.db`, therefore no version anchor, therefore reads as new
  — and a 1.0.0 build would have refused its fourth project even though 1.0.0 promised no limit
  at all. Builds older than the allowance now decline to enforce it regardless of what the
  registry looks like.
- **Refusals reached the UI as raw JSON.** The new 403s carry a structured `{code, message}`
  detail — the first endpoint to do so — and the client folded any non-string detail through
  `JSON.stringify`, putting braces and quotes in a toast where a sentence belonged. The client
  now shows the message and keeps the code on the error for callers to branch on.

### Changed
- Product page and `docs/pro-addon-license.md` state 1.1.0 as the boundary instead of "a future
  release", and the English terms now spell out that a grandfathered installation keeps **both**
  features. The Chinese page had always promised that ("新安裝不含"); the English had only
  spelled it out for the project count, and the code follows the stronger reading.

## v1.0.0 - 2026-08-18

**1.0 because the product now has a front door, and the licence finally says one thing.**

Not a feature milestone — v0.12.1 already indexed a 1,506-clip production library and shipped
on three platforms. What was missing was everything around it: the only way to download arkiv
was a GitHub Releases page, the repository's homepage field pointed at an Instagram account,
and the licence required a paragraph of explanation before a working editor could tell whether
they were allowed to use it on a paying job. All three are now answered, so the version number
stops pretending this is still a preview.

The change that made the rest possible is the licence. Under PolyForm-NC, a freelance editor
organising a client's footage was outside the terms from their first project, while the README
told them they were free up to three — a promise the licence did not authorise. PolyForm
Perimeter replaces that with one sentence, and a product page can now lead with it.

Nothing is on sale. The paid tier is defined and its terms are published, but no shipped build
caps anything, and there is no payment path yet.

### Changed
- **License: PolyForm Noncommercial 1.0.0 → PolyForm Perimeter 1.0.1. Using arkiv commercially is now free.** The NC license restricted *use of the software itself* to noncommercial purposes, and PolyForm-NC defines the permitted personal case as use "without any anticipated commercial application". A freelance editor organising a paying client's footage has an anticipated commercial application from the first project — so under the old terms, essentially every professional in arkiv's target audience needed a separate commercial license, and the README told them the opposite. The public wording promised free use with payment only past three projects; the license did not authorise that promise. Perimeter's Noncompete section replaces the whole problem with one sentence: "Any purpose is a permitted purpose, except for providing to others any product that competes with the software."

  This relaxes the terms for commercial users and, in one narrow way, tightens them: NC permitted a *noncommercial* competing fork, and Perimeter's Competition section does not — a substitute product competes "even if it is provided free of charge", and explicitly whether delivered as a service, library, or plug-in. That was always the actual concern behind leaving MIT in #225 ("so it cannot be forked into a competing commercial product"); NC was an imprecise instrument for it.

  The **Commercial Output Exception is gone as an exception.** Under Perimeter there is nothing to except from — any purpose is permitted, so what you make with arkiv was never in question. What replaces it is a clearly-marked non-binding summary at the top of `LICENSE` that says the same thing plainly and draws the one line users actually need: if your customer is buying a deliverable you produced with arkiv, that is ordinary tool use; if they are buying arkiv's functionality itself, that competes. The PolyForm Perimeter 1.0.1 body is reproduced verbatim from the canonical source and verified byte-identical against it.

  `PUBLIC-INTEREST.md` **existed only because of the NC restriction** — it granted a free commercial license to documentary, nonprofit, and archival work that grants or distribution would otherwise have made "commercial". That premise dissolved, so the program now grants the paid Pro add-on instead, free and perpetual, on the same case-by-case human review. Same ethos, a promise that now costs something to keep.

  Contributions made this switchable without tracking anyone down: `CONTRIBUTING.md` contributor term 3 already grants the maintainer a perpetual right to relicense contributed work, which is exactly the situation it was written for. Versions already released stay available under the terms they shipped with; this applies going forward. reel-scout stays MIT as the open funnel.

  ⚠️ **Legal review is still outstanding** — the same note #225 left behind, now narrower and more specific. Governing law and the contracting entity are marked `[LEGAL REVIEW]` in the add-on terms and must be settled before anything is sold. The third item, refunds, was checked rather than guessed: Consumer Protection Act art. 19 para. 1 gives a distance buyer 7 days, and non-tangible digital content supplied with prior consent is an enumerated exception to even that (Standards for Reasonable Exceptions art. 2 subparas. 4–5), so the add-on could lawfully carry no withdrawal right at all. **The terms give the statutory 7 days in full and decline the exception** — there is no goodwill window on top because the core is already free for commercial use, so nothing gates access to the product; what is being sold is extra capacity on software the buyer can already run and earn with, and can evaluate across three real projects first. Sitting exactly on the floor rather than above it does make two mechanics load-bearing for the eventual checkout, both recorded in the terms: art. 19 para. 3 (the 7 days do not start until the withdrawal information is supplied at delivery — omit it and the right stays open four months) and para. 5 (any conflicting term is void).

### Added
- **Pro add-on terms published ahead of the add-on (`docs/pro-addon-license.md`).** The single paid line is now defined: **NT$3,000, one time, perpetual**, unlocking unlimited projects and cross-project aggregation above the free core's three. One tier rather than two, because a two-tier ladder priced at any honest distance would have collapsed — and because adding a tier later costs nothing while removing one breaks a promise to whoever already bought. No subscription, no seat counter, no activation server: the add-on inherits arkiv's no-phone-home property, so scope is bounded by the terms rather than by code. Not on sale yet; the terms are published so they can be read before the purchase path exists.
- **Browse footage by the day it was shot (#316).** v0.12.0 landed the facet endpoint and `?shot_year=`; the sidebar half was deliberately left out while we waited on a patch from the documentary user who reported the gap. `Shoot date · 拍攝日` now sits between Smart Pools and Cameras: pick a year to filter to it and open its shoot days, pick a day to narrow further, with undated clips in a selectable `未知日期` bucket so they never fall out of reach. Days cap at 20 with 更多/收合, the same answer the tag cloud already gave to the same problem.

  Both filters run server-side (`?shot_year=` / the new `?shot_date=`), unlike the camera pool beside them. The counts in that column describe the whole library, so filtering only the loaded page would put "2025 · 54" next to a grid holding whichever of those happened to fall inside the first 500 rows.

  The year alone was not enough to ship: footage shot across one season collapses into a single bucket, which is the same "one bucket answers nothing" failure that kept `processed_at` out of this facet to begin with. Days cost nothing extra — establishing the years already required normalising every `creation_date`. They cannot be compared naively the way years can, though: both writers are YYYY-prefixed so the year works by luck, while exiftool's `2025:10:03 13:56:20` and the XAVC sidecar's `2025-10-03T13:56:20+08:00` differ at characters 5 and 8, so the comparison folds `':'` to `'-'` first.

### Fixed
- **The shoot-year filter disagreed with its own sidebar count, and no amount of SQL was going to fix it (#316).** The facet decides what a bucket contains by calling `normalise_shot_date`, a Python parser. The filters ran in SQL. Two parsers for one question is a bug generator, and it produced real ones: `0000:00:00 00:00:00` — what exiftool writes for an *unset* date field, so a common value rather than an exotic one — was counted as unreadable by the facet and filed under year `"0000"` by the filter. Tightening the SQL only moved the boundary; a Codex audit of the tightened version found seven more values that disagreed, in both directions: `2025:10-03` and `2025-10-03Tgarbage` admitted where the parser refused, ` 2025-10-03` and `2025-10-03+08:00` refused where the parser accepted. SQLite cannot reproduce `datetime.strptime`, and the set of near-misses has no edge to chase to.

  So there is one parser now. `media.shot_date` holds the normalised ISO day, derived by `normalise_shot_date` at the single write path and backfilled for existing libraries on upgrade; the facet groups on that column and all three filter branches compare against it. A year, a day and `unknown` are a partition by construction rather than by three predicates agreeing. Nothing was failing loudly before this — the sidebar simply advertised numbers it could not deliver.

  Two things follow that are worth knowing: the raw `creation_date` is now display-only (it was never sortable anyway — `':'` sorts above `'-'`), and the previously documented "calendar-impossible dates" residual is gone rather than reduced.
- **The header contradicted the grid whenever a filter was on (#316).** Picking "2025 · 54" left the toolbar reading `62 items`, and the `顯示 N / total` line is hidden once everything has loaded, so nothing on screen corrected it — the sidebar and the grid agreed with each other and the only number in the chrome disagreed with both. It now reads `54 / 62 items · 篩選中` whenever the view is narrowed, counting the cards actually rendered: stacking a rating filter on a shoot year previously left the header on the server-side total while the grid showed a smaller number, and counting what is drawn cannot disagree with what is drawn.
- **`?shot_year=` was never actually exercised on the semantic-search branch.** `/api/media` filters on three paths, and both existing `?q=` tests fell through to the SQL fallback, because tests run without embeddings — so the third implementation of the rule was assumed correct rather than verified. Faking the vector store reaches it, and a new test asserts the three branches return *the same rows* rather than each passing its own case. The two SQL sites also stopped hand-writing the predicate separately, that duplication being the mechanism behind audits H8 and H14.

## v0.12.1 - 2026-08-13

**Native dialogs worked for the first time.** Not "worked again" — the ACL had never let a single Tauri command through, on either platform, and nothing had noticed because no frontend code had ever called one.

### Fixed
- **Every native call was denied by the ACL, on both platforms (#313).** A folder browse button was added to Offload, rendered, and then did nothing: `Command plugin:dialog|open not allowed by ACL`. The permission was not missing — `capabilities/default.json` already listed `dialog:default` — but the capability applied to a context arkiv never runs in. arkiv does not load bundled `app://` content: `main.rs` points the webview at the Python backend on `http://127.0.0.1:<negotiated port>`, which Tauri's ACL classifies as a **remote** origin, and a capability with no `remote` block covers local app URLs only (tauri-utils `acl::capability`: `remote` "defaults to not being set, as our default use case is that the content is served from our local application"). `remote.urls` now brings the running webview inside the capability; the port is chosen at runtime, so the pattern accepts any.

  What made this read as a dead button rather than a permissions problem: `withGlobalTauri` injects the plugin API objects, and **injection is not ACL-gated**. Feature detection answered yes, the button appeared, and only the invoke was refused. The webview URL has no platform branch, so macOS was equally affected — this shipped broken in v0.12.0 and in every build before it.

  Checked rather than assumed, in order: the capability reaches the build (`remote.urls` present in the generated `capabilities.json`); the pattern actually matches (a scratch binary replicating `RemoteUrlPattern::from_str` against `urlpattern` 0.3 returns true for `http://127.0.0.1:59142/` and for the hash-routed URL); `resolve_command` does extend contexts from `remote`; and the pattern strings are embedded in the rebuilt binary. One round appeared to fail after the fix — that was a stale installed binary, not a bad fix.
- **Dropdowns fell back to the browser's light list on the dark theme (#313).** `app.css` styled `input.ak-input` — element-qualified — so `<select class="ak-input">` matched none of it, and no route styled `select` either. Every dropdown in the app was painted by the browser default: a grey-white list over near-black chrome. Both a `select` and a `select option` rule are needed, because the open popup is a separate paint surface from the closed control and Chromium/WebView2 paints it from the select's own computed `background-color` — leaving that transparent sends the popup back to the light list however the control looks. Values are tokens, so light mode stays coherent. On macOS the popup is an AppKit menu and follows the system appearance instead; the rule is a no-op there rather than a regression.
- **`gen/schemas/capabilities.json` was left stale.** It is generated and tracked, so it regenerated dirty for anyone who built after the capability change.

- **Docker ran a different embedding model from every other install (#312).** `docker-compose.yml` set `ARKIV_EMBED_MODEL=nomic-embed-text` and `ARKIV_VISION_MODEL=qwen3-vl:8b`. Those do not *document* the defaults — they **override** them, so a Docker user got an English-centric embedder in a project whose headline feature is CJK semantic search, plus the vision model `config.py` documents at length as ~10x slower (~60s/frame against ~8s/frame; ~30h against ~3.5h over 2000 frames, at comparable tag quality). Both now track `config.py` (`bge-m3` / `qwen2.5vl:7b`), with a comment saying they must keep tracking it, because the divergence was invisible: nothing compared the compose file to the defaults it silently shadowed.
  **Migration for existing Docker libraries.** The vision change needs nothing — frame descriptions are regenerated per frame and nothing is keyed on the model. The embedding change does: `bge-m3` is 1024-dimensional against `nomic-embed-text`'s 768, so an index built under the old value cannot accept new vectors. Rebuild it once after pulling:
  ```bash
  docker exec arkiv-arkiv-1 python embed.py --rebuild
  ```
  This drops and rebuilds the ChromaDB index from the SQLite rows. Media, transcripts, frame descriptions and tags are untouched — only the vectors are recomputed. To stay on the old model instead, set `ARKIV_EMBED_MODEL=nomic-embed-text` in your own compose override.
- **Six files still named the pre-#202 model pair (#312).** #202 corrected the README, `.env.example` and `docs/install.md` when the defaults moved to `bge-m3` / `qwen2.5vl:7b`, and recorded why it mattered: a README-follower pulled a 10x slower vision model that `health.py` then reported MISSING. That sweep missed `CONTRIBUTING.md`, `docs/index.md`, `docs/pipeline.md` and its `.zh-TW` twin, `docs/faq.md` and `AGENTS.md`, so the same trap stayed live for anyone starting from any of them. CONTRIBUTING's pull block also listed only two of the three models `health.py` checks. Historical records (`CHANGELOG.md`, the handover and acceptance docs) are left as written — they describe what the defaults were at the time.

### Added
- **Browse buttons on the folder fields that hold a real path (#313).** Ingest setup's Source, plus Offload's Source and every Destination, open the OS chooser (Explorer / Finder) instead of being type-only. These are the easiest fields in the app to typo without noticing — offload then reads a directory that is not the one you meant. Wired through `window.__TAURI__.dialog` rather than an `@tauri-apps` npm import: `withGlobalTauri` is already on and the permission already granted, so this costs no dependency and puts nothing desktop-only in the web bundle. The same SPA is served in a plain browser by `server.py`, where the global does not exist, so the buttons are gated on that and simply do not render there — the fields still accept a typed path. Cancel deliberately leaves the field untouched rather than clearing it, and failures now toast instead of dying as an unhandled rejection.

## v0.12.0 - 2026-08-12

**arkiv runs on Windows without a Python setup.** v0.11.0 shipped the first installer but only
for macOS Apple Silicon; the README told everyone else to build from source. This release adds
the second platform end to end — a Windows backend assembler, a `windows-latest` release leg,
and the desktop shell fixes that a Windows bundle turns out to need — so a tag now publishes an
`.exe` and an `.msi` alongside the `.dmg`. Both platforms remain unsigned. Also lands a month of
Smart Collections, shoot-year faceting and storage-migration work.

### Added
- **arkiv ships a Windows installer (#308, #309, #310).** v0.11.0 was macOS-arm-only: `assemble-backend.sh` bundled an `aarch64-apple-darwin` Python and `release.yml` had one leg. Windows now has the same path — `assemble-backend-win.ps1` builds `backend/{python,site-packages,src}` from a python-build-standalone `x86_64-pc-windows-msvc` interpreter, and a `release-windows` job produces an unsigned `arkiv_<version>_x64-setup.exe` (226 MB) plus an `.msi` (341 MB) for admin/GPO deployment. Unsigned means SmartScreen shows "Windows protected your PC" once, the same shape as the macOS right-click → Open step; `docs/quickstart-windows.md` walks through it. The 2026-07-28 plan scoped this as "fork the shell script"; three things it did not anticipate mattered more than the fork.

  **The desktop shell was never portable.** `main.rs` hardcoded `backend/python/bin/python3` and joined PYTHONPATH with `:`. A Windows bundle would have built cleanly and then failed to spawn anything on first launch — the exact failure a CI "did it build?" dry-run cannot see, which is why this was built on the target machine and installed before the release leg landed. The interpreter path now branches on `cfg!(windows)` (python-build-standalone has no `bin/` there) and PYTHONPATH goes through `std::env::join_paths`. Same class, third fix: `windows_subsystem = "windows"` silences our own console but not a child's, so a GUI launch parked a black console window next to the app for the life of the backend; spawn now sets `CREATE_NO_WINDOW`.

  **torch is 4.2 GB on a machine with an NVIDIA card, not the ~200 MB the plan budgeted.** `pip install -r requirements.txt` resolves silero-vad's torch to the `+cuXXX` build, so packaging from the dev venv would have shipped a ~5 GB installer. Both the script and the CI job therefore build a dedicated `.venv-pack` against the PyTorch CPU index (524 MB measured). Nothing a user would feel changes: torch only backs silero VAD (`transcribe.py:14-15`), while transcription runs on ctranslate2. Final staging 1014 MB — python 142 MB, site-packages 850 MB, src 22 MB — comparable to the macOS side, and WiX linked all 25,547 files without falling over, so both bundle targets stay on.

  **Four Windows traps, three of which only fire outside CI.** `robocopy` reports success as exit 0-7, so treating it like a normal command fails the script on a successful copy and leaves `$LASTEXITCODE` dirty for whatever reads it next. `curl`/`tar` must be resolved from System32 by absolute path — launched from a Git Bash shell, `tar` otherwise hits Git's MSYS twin, which reads the `C:\...` destination as a remote `host:path` and dies with "Cannot connect to C: resolve failed". The src copy excludes the gitignored runtime-scratch dirs, because the offload tests leave `temp\arkiv-offload-*` behind with ACLs the user can no longer enumerate — 20 of them, which robocopy counts as failed directories. And the `.ps1` is pinned to **pure ASCII**: Windows PowerShell 5.1 reads a BOM-less file as the machine's ANSI codepage rather than UTF-8, so a multi-byte character is re-split under cp950/cp1252, and when that re-split swallows a quote the script dies at *parse* time with errors pointing at unrelated lines — only on machines with that codepage. A sibling script written the same way did exactly this here. A CI runner checks out clean and runs a Latin codepage, so all but the robocopy one bite only a real workstation: the machine that also does a local release build.

  Verified by installing the built `.exe` on a real Win11 workstation rather than by inspecting the build log: window titled `arkiv`, backend up on a negotiated port, SPA and every asset 200, `/api/collections` `/api/media` `/api/stats` `/api/bins` `/api/media/4/transcripts` all 200 with the pre-indexed sample library rendering, zero tracebacks in `backend.log`, zero python processes holding a window handle, and no orphan uvicorn after close.
- **Per-project Smart Collections — `.arkiv/collections.json` + `ingest --propose-collections` / `--apply-collections`.** PR #92 deleted the shipped topical collections (食材特寫 / 店內空景) because a hardcoded food vocabulary mis-filed a cable-making shoot through the shared `切割` tag; the source comment has said "topical/auto-derived and per-project collections are deferred (B + C)" ever since. The lesson was not that topical collections are bad — it was that a *shipped default* carries one shoot's vocabulary into every other project, which a per-project file structurally cannot do. So a project can now define its own, by hand or by derivation, and the file is the UI: plain JSON in the project data dir, the same contract as `tag_aliases.json` and `corrections.json`, reversible by deleting it. Treated as untrusted input throughout — missing, unparseable or wrong-version degrades to the shipped defaults, one malformed entry costs that entry and not the file, and a file entry's `predicate` is always `None`, which is a security property rather than a limitation since a config file must never be able to inject a callable into the classifier. Built-ins can be hidden via a `disable` list but never redefined by key: they carry predicates JSON cannot express, so an override would leave a collection that can never match anything. **Derivation** (`--propose-collections`) prunes the tag cloud, clusters what was shot together by co-occurrence Jaccard, has the LLM name each theme, then measures real membership with the production scorer before proposing anything — member counts are measured, never predicted. Review the proposal, then `--apply-collections`; `--collection-dry-run` runs the whole pipeline without Ollama. **An empty proposal is a correct answer**: on the reference vinyl library every frequent tag co-occurs with every other, and 252 raw tags reduce to 36 derivable, 7 clusters, and exactly 1 proposal — the 18-tag mega-cluster covering 66% of the library is refused, because a collection holding most of the library is a synonym for the library. What stops #92 recurring is pruning: shot types and generic scene furniture (`特寫`/`操作`/`人物`/`室內`) co-occur with everything, so in a co-occurrence graph they are bridge edges that glue unrelated subjects — the exact role `切割` played. A test proves the counterfactual: put `特寫` back and three clearly separate topics collapse into one cluster. Derived keys hash the *definition* rather than the name or rank, and a re-run diffs against what was already accepted — dropping 3 of 62 clips changed the derived set in five of six trials, so anything keyed on rank would rename or vanish on the next ingest. Unlike `--apply-aliases`, every failure path here exits **non-zero**: that command prints and returns, so a missing or malformed proposal still exits 0 and a script reads it as applied.
- **Browse footage by the year it was shot — `GET /api/media/facets/shoot-date` + `?shot_year=` (backend half).** The sidebar offers tags and smart collections but no time entry point, so a documentary user rebuilt one herself in her own checkout to find clips by year. Which column to group on was settled by measurement rather than preference: on a real 62-clip library, `creation_date` (EXIF CreateDate / XAVC sidecar) covers 90.3% and spreads across 2022/2025/2026, while `processed_at` covers 100% and puts **every single row in one bucket** — 55 of the 56 dated clips were shot in a different year than they were ingested, so the two are not interchangeable approximations of each other. The facet reports an explicit `unknown` count (6 of those 62) and its buckets reconcile with the library total, because a facet that quietly omits rows reads as "there is nothing there". Grouping had to survive two stored shapes: `ingest.exiftool_extract` writes `str(cdate)` unnormalised, and exiftool yields `2025:10:03 13:56:20` while the XAVC NRT sidecar yields ISO `2025-10-03T13:56:20+08:00`. Both are YYYY-prefixed so year grouping works naively, but ordering does not — `:` sorts above `-` — so everything past "what year" goes through a shared `db.normalise_shot_date` (timezone dropped, not converted: re-basing to UTC would move footage shot near midnight into the neighbouring shoot day). `?shot_year=` is wired into **all three** `/api/media` code paths — SQL list, semantic search, and the degraded SQL text search — since a filter honoured only on the first silently stops applying the moment the user types a query or Ollama goes down, which is exactly the shape of audits H8 and H14; `creation_date` joins `LIGHT_COLS` so the search branch can see it at all. Verified end-to-end against the real library: every bucket's filter count matches its facet count exactly, and none of its 56 distinct date values is rejected by the normaliser. 31 tests, mutation-checked — all 6 HTTP guards fail against the unwired router, both `?q=` variants included. **Frontend deliberately not built yet**: the user who needs this already has a working version, and it is worth seeing hers before shipping a second one.

### Fixed
- **Collection membership ignored the reviewed tag alias map.** `smart_collections` imported neither `tag_quality` nor `tag_aliases`, and scoring is an exact string comparison — so a clip tagged 黑膠唱盤 scored 0.0 against a collection listing 黑膠唱片, the same object with no overlap. Worse than a missed match: running `ingest --apply-aliases` folded the tag *cloud* in `/api/tags` and did nothing whatsoever to membership, which is the surface where folding actually changes what a user sees, so the reviewed map was only half-connected. Both sides of the comparison now fold through `canonicalize` → `to_pref`; folding one side only would have traded the old mismatch for a new one. Folding happens on the **clip** side and never by expanding a collection's tag list to include every alt spelling: expansion would inflate `len(col.tags)` — on the reference library's vinyl ring, ~4 to ~22 — and `0.5*(2/22) + 0.5*(2/3) = 0.379` falls below `MIN_CONFIDENCE`, so a clip matching two spellings would stop being a member. Clip-side folding also makes "two hits" mean two genuinely different concepts rather than two spellings of one. No shipped collection changes membership (verified: none of the five defaults carries a tag `canonicalize()` alters); one existing test did, since `media_signal` now returns 吧台 for 吧檯, which is the intended normalisation. `classify()` also computes the signal once per clip instead of once per collection — `media_signal` re-parses the `frame_tags` JSON blob on every call and `/api/collections` classifies the whole library uncached on every request.
- **Opening a Smart Collection made every rated clip look unrated.** The grid builds collection cards straight from the `/api/collections` items with no `/api/media` re-fetch — by design — and hardcoded `rating: 'none'` because the endpoint never sent one. The column was already in the narrow SELECT for the 待審查 predicate; it just never reached the client. Items now carry `rating` and the card goes through the same `ratingToUi()` the normal grid uses. Two adjacent gaps closed with it, both of which stop being cosmetic now that derived collections make this a real browse path: `activeCollection` was set on click but never passed to `PoolSidebar`, so nothing showed which collection was open and only a camera click ever cleared it — the row now highlights and clicking it again toggles back to the full library, with clearing centralised in `load()`/`runSearch()` so the highlight cannot outlive the view it describes; and the collections list was uncapped while tags were capped at 24, so a project deriving a dozen topics would push the Storage footer off the sidebar. Render-verified at 1440 and 900px.
- **`--migrate-storage` refused to run, reported success, and told you to delete your API tokens.** Its idempotency check was `if (BASE_DIR/.arkiv/project.db).exists()`, which answers "does this file exist", not "did the migration already run". An empty `project.db` also appears the moment anything opens the new layout — a stray `server.py` start, an ingest without `--dir`, a health check — and from then on the migration refused forever while the real library sat in `BASE_DIR/media.db`. It stranded 62 media rows on one machine for weeks, and nothing surfaced it: the branch printed `[SKIP]` and returned, so the process **exited 0**, and both a human skimming output and any script checking the return code read that as done. The printed remedy made it worse — `rm -rf .arkiv` deletes a `project.db` that holds the `access_tokens` rows, and the DB stores only hashes, so following the advice revokes every API token on the machine with no way to recover them, only reissue. The guard now decides on `media` row counts rather than on the inode, and separates the three real states: already migrated (skip, exit 0), an *empty shell blocking real legacy data* (**exit 6**, naming what is stranded), and both sides populated (refuse — the migration does not merge). An unreadable DB is reported as unknown (`?`) instead of being collapsed into "empty", since acting on an unverified assumption is exactly what the original guard did. The remedy is now to `mv` the directory aside, printed alongside how many `access_tokens` rows would have gone with it; the rollback instruction and both `docs/pipeline*.md` copies lost their `rm -rf` for the same reason. 8 tests, mutation-checked: 6 fail against the previous code, three of them with `DID NOT RAISE SystemExit` — the silent-success bug itself.
- **Federated search silently stopped being search under the pg backend.** With
  `ARKIV_VECTOR_BACKEND=pg` the vectors live in the shared pgvector store keyed by
  `project_name`, and no project has a `.arkiv/chroma_db` directory. Federation
  opened that directory anyway. Two things then went wrong in sequence, neither of
  which produced an error: `health.project_health` treated the missing directory as
  `CHROMA_MISSING`, so **every** project failed preflight and was reported as a
  project error; and where a project did get past that, `_query_chroma` found
  nothing and the code fell through to `_sql_like_search` — so a semantic query
  came back as a keyword `LIKE`, ranked by nothing, with a plausible-looking result
  set and no marker saying the search had changed kind. The pg path now queries the
  shared store scoped to the project (`PgCollection.query` already accepted a
  `project_name` filter, and `vectordb._query_collection` already built it — the
  wiring was the only thing missing), and the chroma preflight applies only under
  the chroma backend. As a side effect the pg path opens no external project
  directory at all, so the untrusted-collection concern documented on
  `_query_chroma` does not arise there.

### Added
- **Sony `.mxf` footage now indexes (DIT wrapper ④).** FX6 / FX9 / Venice cards are `.mxf`, and pointing a `--dir` scan at one produced zero indexed clips — the extension sat in `ingest._PRO_UNSUPPORTED_EXT` next to `.braw` / `.r3d` / `.ari`, and the skip notice told the user pro formats "aren't indexable (ffmpeg has no decoder without vendor SDKs)". That grouping was over-broad: those three are proprietary RAW **codecs** with genuinely no ffmpeg decoder, whereas MXF is a **container**, and ffmpeg decodes what Sony wraps inside it. `.mxf` moves to `mediatypes.VIDEO_EXT`, which propagates to `ingest.SUPPORTED`, db.py's SQL video filter, the watcher, and the query builder through the shared set rather than by hand. Verified before landing across the whole chain ingest depends on — `probe()`, thumbnail, frame extraction, and the 16 kHz mono audio decode whisper consumes — on two samples: `samples.ffmpeg.org/MXF/C0023S01.mxf` (Sony 2006, MPEG-4 part 2, 352×288, `start_tc 01:43:48:21` read from format-level tags) and a synthesised XAVC-Intra file (H.264 High 4:2:2 all-I, 1920×1080, `start_tc 01:00:00:00`); every stage green on both. An MXF whose inner codec ffmpeg *cannot* decode degrades exactly as any unreadable file does — `probe()` returns `None`, the clip is skipped with `[ffprobe failed]` — so admitting the container adds no new failure mode. `.m2ts` deliberately stays in the pro-unsupported set: it is also a container, but frame extraction failed on the sample tested, so it needs its own measurement instead of being swept along. Three documents had recorded this feature as shipped since June (ADR 0001's status line, its Decision ④ entry claiming "Shipped (v0.8.1)", and the roadmap); the ADR now carries a correction noting it was never true.

### Changed
- **The Windows CI leg now runs the MCP stdio suite (#273).** `test-windows` already installed `mcp` and `numpy` but only ran the offload deny-policy file, so the prewarm ordering that *is* the #271 fix was covered on macOS 3.12 only — the one platform where the bug never reproduced. Adding `tests/test_mcp_e2e.py` gives the stdio surface a Windows leg (subprocess spawn, JSON-RPC framing, teardown) for ~4.5s and zero new dependencies: 50 passed in 7.42s under a venv built from the job's exact install list. chromadb is deliberately **not** installed there, both to exercise the real degrade path and because it would cost ~120s (82 packages, ~360 MB) to guard nothing — measured on hosted runners, the stdin-pipe + `import numpy` probe completes in 0.6s on windows-latest, ubuntu-latest and macos-latest alike, while deadlocking on the Win11 dev box under both numpy 2.4.3 and 2.5.1. The job comment records that measurement so the question isn't re-opened from first principles.
- **The README leads with the downloadable app (#272).** v0.11.0 is the first release with an installer, but Quick Start still documented clone+pip only. A "Download the app (macOS, Apple Silicon)" section now comes first — grab the `.dmg` from Releases, drag to Applications, right-click → Open once (unsigned) — while staying honest that the bundle still needs FFmpeg + Ollama (external services, not pip packages) and is Apple-Silicon-only. The existing pip sections are reframed as run-from-source.

### Fixed
- **`mcp` 2.0.0 turned every PR red (#276).** `mcp` 2.0.0 shipped on 2026-07-28 and restructured the SDK, removing the `mcp.server.fastmcp` import path `mcp_server.py` uses. `requirements.txt` carried an unpinned `mcp>=1.2`, so from that afternoon every fresh install resolved to 2.0.0 → `ModuleNotFoundError` at `test_mcp_server` collection → pytest exit 2 → `test(3.12)`, `test-windows` and `coverage` red on **all** PRs (3.9 is unaffected; mcp is gated out there). main last went green at 09:48, before 2.0.0 landed at 13:45. Capped to the 1.x line until `mcp_server.py` is ported to the 2.x API. The cap had to go in **twice**: CI installs a curated inline dependency set rather than `-r requirements.txt`, so the requirements cap alone didn't take — all three `pip install` blocks (test matrix, test-windows MCP-stdio, coverage) are pinned to match.
- **Vision-only re-describes went silently unindexed (#275).** The G-corpus vector index froze on 07-22 while vision descriptions kept being written through 07-29 — semantic search quietly missed roughly a third of the library's visual content, with no error anywhere. Two defects combined: `--vision-only` returned *before* ingest's end-of-run embed, and the overnight scheduler runs exactly `--vision-only`, so the sole description writer never rebuilt the index; and `embed.py`'s freshness check only asked "is this media_id present in Chroma, or was `--force` passed?" — never what the row now *says* — so a row re-described after indexing stayed stale forever. Fixed defence-in-depth: vision-only now returns the ids it wrote and the caller re-embeds them via `run_embed(force_ids=…)` (skipped by `--no-embed`, and run even on halt so committed work is searchable); freshness became **content-based** via a new `media.embed_hash` (sha256 of `build_doc_text` at last embed) compared against the current hash, which catches changed descriptions regardless of what the caller passed, with legacy hash-less rows treated as "unverified" → re-embedded rather than assumed fresh; and `run_embed` now reports new/stale/unverified/forced/unchanged so a `0` meaning "checked, nothing stale" is distinguishable from a `0` meaning "never looked". Verified on a copy of the real G corpus: old logic reported "To process: 0", new logic detects 1506 stale.
- **MCP stdio deadlocked on Windows the moment a search touched numpy (#271).** `search_media` imports `vectordb` lazily, inside the tool body — so inside the stdio server that import runs while the transport is already up, and on Windows the process then wedged permanently: the server never answered again and only the client's timeout ended the session. Reproduced with **no mcp and no arkiv code**, isolating the trigger to a thread parked in a blocking read on a stdin pipe (exactly `mcp.server.stdio`'s steady state, since anyio's `AsyncFile` iteration keeps a worker thread in `readline()`): plain `import numpy` 0.11s, two idle threads + `import numpy` 0.09s, thread blocked on a stdin pipe + `import numpy` **never returns**. An import hook in the wedged server shows it reaching `numpy._core._multiarray_umath` (the OpenBLAS-linked extension) and stopping. It is numpy-specific rather than "any native import" — lzma, sqlite3 and `PIL.Image` all load in under 20 ms in the same running server — and not the event loop, since `to_thread` deadlocks identically while `sleep(5)` and a 222 KB file read inside a sync tool both return. Not version-driven either: mcp 1.2.1 / 1.27.0 / 1.28.1 all fail the same way on the same machine, so `requirements.txt` keeps its range. The fix is to prewarm `vectordb` **before** `mcp.run()` starts the transport, which also avoids paying the import mid-session. A stage-label bug had hidden this for two rounds: `_run_bounded_mcp` set `stage = "cleanup"` in a `finally`, which also runs while the `fail_after` cancellation unwinds through that frame, so a hang *during* the call always reported as "cleanup". Windows evidence: 1 failed at 93.93s → **6 passed in 10.24s** (the end-to-end case 90.03s → 5.82s).
- **The release pipeline could ship unsigned when signing was intended (#274).** A Codex audit found the signed/unsigned branch gated on a **single** secret (`APPLE_CERTIFICATE != ''`) while signing actually consumes six. A partial configuration would then either codesign with an empty identity — a hard failure on the first signed release — or, if `APPLE_CERTIFICATE` happened to be the missing one, fall through silently to an ad-hoc/unsigned release. Replaced with an all-or-nothing preflight step that reads all six and outputs `full` or `none`, exiting non-zero on a partial config with a message naming what's missing; the import/signed/unsigned steps now read that output. Simulated none/full/partial locally. Also drops a dead `bundle/macos/*.app.tar.gz` upload glob — that's an updater artifact and there's no updater config, so it only ever matched nothing.
- **A POSIX path literal in a test seed escaped PROJECT_ROOT on Windows (#269).** `_seed` hardcoded `Path("/tmp/arkiv-g2-clip.mp4")`, which is absolute only on POSIX: on Windows `str(Path("/tmp/x"))` is `\tmp\x` — rooted but drive-less — so `is_absolute()` is False, `db.resolve_path` takes the *relative* branch instead of the legacy-absolute pass-through the seed intends, and the join drive-anchors to `C:\tmp\x`, at which point the J2 containment guard correctly raises. Two of the six G2 tests failed there (the ones reaching retranscribe/activate, which actually resolve the media path). Same class as the Wave A offload finding — a POSIX literal on Windows is not the absolute path it looks like. The guard is right; the seed was not portable. `gettempdir()` is absolute on both platforms. No product code changed, no skip or xfail: 2 failed / 4 passed → **6 passed** on Windows.

## v0.11.0 - 2026-07-28

First release built and published by the automated `release.yml` pipeline — arkiv now
ships a downloadable macOS (Apple Silicon) `.dmg` instead of build-it-yourself. Bundles a
month of Phase 9.x editor / timeline / MCP work (below) plus the repo health-hardening
waves A–E (Windows correctness, CI truthfulness, dependency reproducibility, frontend
quality gates, and version/release hygiene). The `.dmg` is unsigned — right-click → Open
on first launch — until Apple signing secrets are configured.

### Security
- **MCP `_safe_path` leaked `C:/…` absolute paths whole (#182).** `mcp_server` carried its own copy of the leak guard because the original lived in `server.py`, which it must not import (that pulls in the whole FastAPI app) — there was nowhere to share from. Four hours after that copy landed on 2026-06-08, Codex round-2 (`fc35b8f`) overruled round-1 and made `C:/` count as a Windows absolute alongside `C:\`; the fix touched `server.py` only, so the MCP copy kept passing `C:/Users/me/secret.mov` through unchanged for 38 days — on the one surface whose stated red line is "no absolute-path leak", and the only one facing untrusted downstream agents. R5-25 extracted the guard into the `pathres` leaf, so the copy is now deleted rather than patched: `_safe_path` delegates to `pathres._display_path`, one guard, nothing left to drift. Paths beginning `C:/` now basename like every other absolute; every other input is byte-identical.
- **The offload destination guard silently opened on Windows (Wave A, #223).** `webguard._assert_offload_dst_safe` normalised with `Path(dst).expanduser().resolve()` and then string-matched against `/`-anchored POSIX literals (`/etc`, `/system`, …). On Windows `resolve()` drive-anchors a rootless path — `/etc` becomes `C:\etc` — which no longer matches `/etc`, so the 403 gate quietly stopped denying (four hardening tests failed on windows-latest, on a gate nobody had ever run there). The sibling export/ingest guards were unaffected because they use `Path.relative_to`, which is drive-aware; offload has no allowlist by design, so its denylist itself had to become host-independent. It is now evaluated over the **raw pre-resolve literal**, the resolved canonical path, and a drive-stripped form, with drive-agnostic Windows system and persistence roots added (Windows, Program Files, Program Files (x86), ProgramData, the per-user Startup folder), and the decision extracted into a pure `_offload_deny_reason` helper that can be fed Windows-shaped strings from a Mac. Two independent audits (Codex and a harness reviewer) then found residual false-negatives through Windows namespace, device and admin-share paths — `\\?\C:\Windows`, `\\.\C:\`, `\\localhost\C$\`, `\\?\UNC\…` all still reached a system root — which are closed by a normalisation pass; a drive-letter check that only accepts ASCII letters (so `1:/etc` isn't mistaken for a drive); and an `OSError` wrapper so a malformed destination no longer 500s. 8.3 short names (`C:\PROGRA~1`) are recorded as an accepted residual, resolved by the caller's own Windows `resolve()`.
- **The "explicit embeddings only" invariant on external Chroma is now locked (Wave B, #229).** `federation._query_chroma` opens **external, semi-trusted** project Chroma directories. chromadb builds and runs a collection's *persisted* embedding function when — and only when — a caller passes `query_texts=` or documents without embeddings; that is the ChromaToast / chroma#6717 client-SDK-RCE class. Federation queries with explicit `query_embeddings=` only, so the path is unreachable today, and this change is about keeping it that way: a SECURITY comment documenting the untrusted-persisted-EF rule, plus a regression guard that spies the external collection's `query()` and asserts federation passes `query_embeddings=` and never `query_texts=` or `add(documents=)`. The pgvector migration does not retire this path — federation hardcodes `chromadb.PersistentClient` regardless of `ARKIV_VECTOR_BACKEND` — so the guard belongs here rather than in the backend abstraction.

### Added
- **arkiv is a self-starting desktop app now, not a terminal dance (B3, #199, #200).** The Tauri shell spawns the bundled Python backend on launch: `free_port()` negotiates an OS-assigned port (no more hardcoded 8501 clash), the child runs `python -m uvicorn server:app` with `ARKIV_PROJECT_ROOT` pointed at `app_local_data_dir/arkiv` — writable, and deliberately **outside** the read-only `.app` bundle — plus `ARKIV_TRUST_LOOPBACK=1` so the loopback WebView skips the token dance; `wait_ready()` TCP-polls the port on a 45s budget before the window opens, and `RunEvent::ExitRequested` kills the child so no orphan uvicorn survives. The backend resolves from an env override in dev or `<resources>/backend/{python,site-packages,src}` when packaged. `assemble-backend.sh` rebuilds the ~1.3 GB staging bundle from python-build-standalone plus a trimmed site-packages — **not** PyInstaller; the spike showed this native-heavy tree boots cleanly under a stock portable interpreter. Validated end-to-end rather than assumed: Rust `setup()` spawned the backend, WKWebView loaded the SPA, and every startup call (`/api/collections`, `/api/tags`, `/api/media`, `/api/stats`, `/api/bins`) returned 200. One packaging gotcha is recorded with it — the dmg step fails with AppleEvent timeout -1712 in any non-GUI build because `bundle_dmg.sh` runs a Finder-prettifying AppleScript, so `CI=true cargo tauri build` (which passes `--skip-jenkins`) is the recipe; the dmg still contains the `.app` and the Applications drag-target, just without the custom background layout.
- **The backend's output goes to a log file the user can actually retrieve (#201).** A Finder-launched `.app` has no terminal, so the sidecar previously threw every uvicorn access line, `print()` and traceback into the void — a broken tester box was un-debuggable remotely. stdout and stderr are now redirected to `<project_root>/logs/backend.log` (writable, outside the read-only bundle) with a one-file rotation to `backend.log.prev` so the previous run survives; both streams share one duped fd so lines interleave cleanly, and it falls back to inherit if the log can't be opened.
- **First-run readiness gate, Load-sample CTA and Report-a-problem (W0, #204, #207, #208).** `MainLive` used to boot to an empty grid with silent runtime failures when Ollama, the models or FFmpeg were missing — the binding constraint the launch-readiness audit named. The landing surface now consumes a new **unauthenticated `GET /api/health`** (a JSON readiness report mirroring `health.py`: ffmpeg/ffprobe required, exiftool optional, Ollama reachable and the three configured models pulled — booleans and model names only, **no absolute paths**, 200 when ready and 503 otherwise) alongside `GET /api/version`, and drives a four-state machine: unreachable → Retry; not-ready-and-empty → the failing checks with their fixes plus Recheck/Settings; ready-and-empty → "Load sample library" or "ingest your footage". Backing it are `POST /api/sample/seed` + `GET /api/sample/seed/status` (SingleFlight guard then the shared ingest slot — the seed spawns whisper, so it must not run alongside a real `/api/ingest`; idempotency short-circuits *before* any lock so an already-seeded no-op can't 409 a concurrent ingest) and `GET /api/logs/tail` (bounded 256 KB tail, per-line sanitisation, **authed** since a log can carry paths and tracebacks). Settings → System gains "Report a problem", assembling a diagnostic from version + health + log tail, each degrading independently. The bug-report template now asks desktop users for that JSON instead of the terminal-only `python health.py`.
- **A pre-built sample library that makes a fresh install searchable instantly (A1, #205, #209).** First-open search-awe without the user owning footage: four tiny (~1 MB total) downscaled CC-BY Blender open-movie clips — Caminandes, Coffee Run, Glass Half, WING IT! — with licences and attribution in `sample/LICENSES.md`. v1 (#205) shipped `scripts/seed_sample.py`, which ingests them into the current project root; the demo queries it advertises were deliberately chosen to be robust, because a 4-clip index is ranking-noisy under bge-m3 anisotropy. v2 (#209) removes the ingest wait entirely: the corpus is packaged **pre-indexed** into a tar and ATTACH-merged into a fresh project's default store on startup, so the main grid is searchable with zero re-ingest (verified with a Chinese query — `駝羊` → llama clip). Only a non-install, fresh, never-dismissed root is ever seeded; a 範例素材 chip marks the demo as demo, and one click removes it. The merge takes the column intersection and reads ids **from the pre-built database, never the live table**, so remove can never reach a user's own footage — an adversarial review round caught exactly that: an earlier `_merge_content` returned every id in the table.
- **Taiwan Traditional zh transcripts, written at ingest time (Phase 9.8b, #210).** Whisper large-v3 emits Simplified for `zh` and the audience is Taiwan. `zh_convert.convert_result` now applies OpenCC `s2twp` (Taiwan idioms — 内存→記憶體) to transcript, segments **and** words on the write path, so the search index, the UI and every export are Traditional. Converting on display would not have worked: it can't fix search recall. Timing-safe by construction — `start`/`end`/`score` are copied verbatim, so idiom substitutions that change phrase length never shift a timestamp. Degrades to identity when opencc is absent (an optional dependency must never break a transcribe) and is gated to Python ≥3.10, where the arm64 wheel exists; production is 3.12.
- **`ingest.py --retraditionalize` backfills libraries indexed before 9.8b (#211).** The write-path fix only reaches new transcriptions, so a 5-year archive still answers 記憶體 queries with nothing while holding 内存. This batch-converts `media.transcript` / `segments_json` / `words_json` plus the per-language archive with no whisper re-run (follow with `embed.py --rebuild` for the semantic half). The naive version of this corrupts data, and building it surfaced why: opencc's **phrase layer re-segments valid Traditional** (系統→係統, 音樂類型→型別, 設備→裝置), and even neutral s2t does it. So rows are classified first — genuinely Simplified (a Simplified character, no Traditional-only character) gets full `s2twp` idioms matching the write path; **mixed** gets char-level `s2tw`, touching only genuinely-Simplified characters with no phrase layer, which is length-preserving so word timings survive; already-Traditional or empty is skipped. Idempotent (converted rows re-classify as Traditional), timing-safe, identity without opencc. Ten tests including the load-bearing already-Traditional-never-corrupted case, and verified on a copy of the real 62-media library: 34 Traditional rows byte-identical, 3 mixed rows char-fixed with zero corruption.
- **`GET /api/media/{id}/segments` — sentence timecodes without the whole record (three-piece W1a, #217).** The clip-detail route ships `segments_json` as a raw string and drops `words_json` for transport size, so a downstream edit agent that only needs sentence-level timing to place an IN/OUT on a quote had to re-parse the entire record. This returns the projection alone — `[{start, end, text}]`, no words, no frames or tags — reusing the same defensive JSON projection as `mcp_server`, so a corrupt column degrades instead of 500ing.
- **Speaker labels on transcript segments (A4, #221).** arkiv had no diarization. After transcription, the audio is now diarized via the `speaker-align` package and each segment tagged with a `speaker_id` (contract `{start, end, text, speaker_id}`). Optional and gated (`ARKIV_DIARIZATION_ENABLED`, off by default, plus `ARKIV_PYANNOTE_TOKEN`) and soft-failing at every step: no token, package absent, or a diarizer error each return the segments unchanged — an optional label must never break a transcription. The load-bearing detail is *which* audio gets diarized: it must be the **VAD-filtered** wav the segments were timed against, not the original media, because VAD concatenates speech chunks and collapses the timeline — labels derived from the original would not line up with the timecodes. Read projections (`/api/media/{id}/segments`, MCP `get_transcript`) surface `speaker_id` **only when present**, so non-diarized clips keep the exact prior shape and the existing contract tests are untouched. `speaker-align` is not yet on PyPI; when it's absent the feature and its tests self-skip.
- **Per-clip camera identity and angle for multicam (A-cam, #224).** arkiv knew a clip's camera make, model and reel, but not which **angle** it was in a multicam shoot — the missing premise for multicam edit decisions. Adds `camera_id` and `angle` as free-form editorial media fields with `PATCH /api/media/{id}/camera`, mirroring the `in_point`/`out_point` human-annotation pattern exactly: PATCH semantics (an omitted field is untouched, null clears), labels trimmed and length-capped, and — the reason that pattern exists — **kept out of `_ALLOWED_COLS`** so a re-ingest or refresh can never clobber the marks, with a test pinning it. Carried by the detail route and the REST list via `LIGHT_COLS`, so a downstream planner picks them up automatically.
- **Smart Collections honor hand-added tags (#222).** The classifier only saw vision-derived `frame_tags` — `list_collections`' SELECT never joined the `tags` table — so a tag added by hand through `POST /api/media/{id}/tags` could never form a collection, and a user labelling clips a-roll/b-roll got no clickable sidebar entry, only a Query Builder workaround. One bulk tag load now attaches each clip's manual tags to the scored signal, and two edit-role collections key on them: **A-roll · 主軸** and **B-roll · 輔助**, hidden at zero members on libraries that don't use them. A Codex audit caught the follow-on: the `tags` table also holds `source='auto'` vision copies, so an automatic tag that happened to be named `a-roll` would have joined an editorial collection with no user action — the load is now scoped to `source='manual'`. Verified against a real 20-clip library (A-roll → 5, B-roll → 14, structural collections unchanged).
- **A tag-triggered release pipeline that signs and notarizes (Wave E, #245, #246).** `.github/workflows/release.yml` builds the self-contained macOS-arm `.app`/`.dmg` on a `v*` tag (or a `workflow_dispatch` dry run) and attaches it to the GitHub Release, **stamping the version from the tag** so bundles can't drift from the tag again. Signing and notarization are conditional — they run only when the Apple secrets are configured, otherwise it builds unsigned. Ships `entitlements.plist` with the hardened-runtime exceptions the bundled Python and torch/mlx native libraries need (allow-jit, unsigned-exec-mem, disable-library-validation). Two bugs were found by actually running it rather than reading it: a step-level `if: ${{ secrets.X != '' }}` silently never fires, because the secrets context isn't available in step conditions — the check had to be hoisted to job-level env; and an unset secret expands to an empty string, so `APPLE_SIGNING_IDENTITY: ""` made Tauri attempt "sign with the identity `""`" and fail bundling outright. Since an arm64 binary must be at least ad-hoc signed to execute at all, the build is now split: a real Developer ID identity when the certificate is present, ad-hoc `-` otherwise.
- **Editor keyboard shortcuts for IN/OUT in the inspector.** The frame-exact spike (#190) shipped the `◂格/格▸` buttons and rVFC frame marking, but every mark was a mouse click — editors reach for keys. Now, whenever a clip with a player is open: **`,`/`.`** step one frame (only when fps is known, so the frame stepper stays inert for audio / unknown-fps), **`i`/`o`** mark IN/OUT at the current frame (or playhead for audio). A single guarded `svelte:window` `keydown` handler in `Inspector.svelte` ignores events whose target is an `input`/`textarea`/`select`/contenteditable and any event carrying a modifier (⌘/Ctrl/Alt) — so typing a tag never marks a clip and ⌘K (focus search) is never hijacked — and does nothing at all on the design-mock inspector (no `videoSrc`). Closes the last P1 item in `docs/frame-exact-inout-roadmap.md`.
- **IN/OUT trim marks now persist per clip (D1).** The inspector's IN/OUT points were Svelte-local state — set a range, switch clips, and it was gone; the backend never knew, and the timeline export couldn't see them. Now `media.in_point`/`out_point` (seconds) are stored via a new **`PATCH /api/media/{id}/inout`** (PATCH semantics like rating: an omitted field is untouched, explicit null clears; an inverted `in ≥ out` window is rejected 422; NaN/negative rejected by the model), surfaced by the detail route, and the inspector hydrates its marks from them on clip-open (once, without clobbering an edit already in progress) and persists changes debounced. This is the foundation for the multi-clip timeline export assembling the *marked sub-clips* instead of laying full clips end-to-end. Marks are written only by this endpoint — kept out of `_ALLOWED_COLS` so a re-ingest/refresh can never overwrite a user's range.
- **Proxy generation uses hardware decode on Apple Silicon (D3).** Building a 720p playback proxy for a 125-clip 4K shoot was pure software: `ffmpeg -c:v libx264` with a software decode of the (often 100+ Mbps) 4K source. Measured on an M2 Max off a real 140 Mbps 4K clip, the **decode** is the bottleneck, not the small encode — so proxy gen now prepends `-hwaccel videotoolbox` (VideoToolbox hardware decode), cutting **26% wall-clock and 59% CPU** per clip (a big deal for a sequential batch, where less sustained CPU means less thermal throttling). The encoder stays libx264 on purpose: the videotoolbox *encoder* was measured slower and produced a ~20× larger file at the same target. A source whose codec/pix_fmt VideoToolbox can't decode falls back to software automatically (retry without `-hwaccel`), so nothing that built a proxy before stops building one. Defaults on for arm64 macOS (`ARKIV_PROXY_HWDECODE=auto`), off elsewhere; proxy height is now `ARKIV_PROXY_HEIGHT` (default 720) so the resolution can be lifted without a code change.
- **The timeline export lays the MARKED sub-clip, not the whole file (D2).** `GET /api/export/timeline/{fmt}` sequenced full clips end-to-end by `duration_s`, ignoring the IN/OUT marks entirely — so "mark IN/OUT across N clips → one EDL/FCPXML" never produced a cut list. Now each clip contributes only its persisted `[in_point, out_point]` window (clamped to the real duration; an unmarked clip still contributes its whole duration, so old behaviour is byte-identical until a range is set): the **EDL** source TC is cut from the in-point and record TC advances by the window; **SRT** captions are clipped to the window and re-based onto the timeline (a caption fully outside the trim is dropped); the **FCPXML** asset-clip duration and source start reflect the window. A defensive fallback treats an empty/inverted stored window as unmarked. Together with D1 this closes the "mark across 125 clips → drop one timeline into Resolve" loop.
- **MCP timecode tools — "which clip" → "which seconds of it" (#184, #185, #186).** The MCP server was media-level only: an agent could find a clip but not learn what happens inside it, so arkiv's understanding of footage stopped at the HTTP API. Now:
  - **`get_scenes(media_id)`** — one entry per scene-detect boundary: `start_s`/`end_s`/`duration_s` plus that keyframe's nine vision fields and a `keyframe_path`. Unknown id → `null`; a clip with no vision analysis → `total: 0` (distinct answers, deliberately).
  - **`get_transcript`** now carries `duration_s`, `segments` (`[{start, end, text}]`, on by default) and `has_words`; **`include_words=true`** adds `[{word, start, end, score}]`, capped at 5000. The four original keys are unchanged. Word timing stays opt-in for the same reason the HTTP detail route drops `words_json` (round-5 #26) — except over MCP the failure mode is a blown context window, not a slow transfer.
  - Segments are **projected onto `{start, end, text}`, not passed through**: the three transcribe backends disagree on shape (mlx-whisper — every Mac ingest — stores its native dict including `tokens`, `seek` and logprobs; faster-whisper writes six keys; whisperx a third), so a verbatim payload would depend on *which machine ingested the clip*. Measured on 8s of real footage: 1128 bytes verbatim → 213 projected. Clips ingested before Phase 9.4 have no segment timing, so `segments` is `[]` and callers fall back to `transcript`.
  - Backed by a new **`scenes.py` leaf** that the HTTP route and the MCP tool both import, so the shapes cannot fork — the failure mode #182 had just demonstrated. They differ in exactly one key, pinned by a test: HTTP `keyframe_url` (a URL for the authed thumbnail route) vs MCP `keyframe_path` (PROJECT_ROOT-relative — a stdio client is on the same machine, so a path is actionable where a URL is not). The HTTP body is frozen byte-for-byte by `tests/test_scenes_contract.py`, authored against the pre-extraction route.
- **A real end-to-end MCP stdio test (`tests/test_mcp_e2e.py`).** The docs and this changelog had claimed an "end-to-end stdio smoke" since Phase 14, but none was ever checked in — it was an ad-hoc manual run. Now it exists: real subprocess, real `mcp` client, seeded temp project. It is the only thing covering FastMCP registration, stdio framing, dispatch, argument coercion and serialisation — every unit test calls the `*_impl` functions directly. Includes a path-leak assertion made at the far end of the wire.

### Changed
- **License: MIT → PolyForm Noncommercial 1.0.0, with a Commercial Output Exception (#225).** arkiv moves to source-available so it cannot be forked into a competing commercial product, while remaining free for any noncommercial use. The **Commercial Output Exception** is the part that matters to a working editor: videos, timelines and exports produced *with* arkiv stay fully usable commercially — the restriction is on the software, not on your work. `LICENSE` carries the canonical PolyForm-NC 1.0.0 text plus the exception and the Required Notice; both READMEs get the badge and section, and the wording moves from "open-source" to "source-available", because PolyForm-NC is not an OSI open source licence and saying otherwise would be false. Already-released versions remain MIT-forkable from their commits; this applies going forward. reel-scout stays MIT as the open funnel. Legal review is advisable before a commercial launch (the Output Exception wording, and the holding entity).
- **Ingest says what it skipped, caps a runaway scene detect, and warns before it garbles your audio (#203).** Three silent failures a first-wave user shouldn't have to diagnose. A `--dir` scan silently dropped pro/cinema footage (`.mxf`/`.braw`/`.r3d`/`.m2ts`/…), so someone emptying a C300, RED or BRAW card saw "Found 0 media files" and concluded arkiv was broken — it now prints "Skipped N unsupported file(s)" with a pro-codec callout explaining that ffmpeg has no decoder for these without vendor SDKs. The scene-detect timeout was `max(120, duration_s * 2)`, so a corrupt file lying about a huge duration could wedge one ffmpeg for hours; capped at 30 minutes, mirroring the audio path. And with no `--language`, Whisper defaults to `zh` and silently garbles English or Japanese audio — there's now a one-time upfront notice to pass `--language en/ja/ko`.
- **Phase 1 aborts fast when the drive goes away (#206).** Phase 2 (vision) halted after N consecutive whole-file failures, but Phase 1 (probe + whisper + frames) had no such guard: an external or network drive unplugged mid-batch would grind through every remaining file at up to ~240s each (`probe()` = 2×120s plus a 2s retry), i.e. hours on a large queue. The Phase-2 design is now mirrored into Phase 1 with `ARKIV_INGEST_HALT_STREAK` (default 5, `0` disables) and a pure, unit-testable predicate. The counter resets on any ok/skip/move — proof the drive is readable, so sporadically corrupt clips never trip it — and increments on **both** failure branches, including the empty-record path that is the dominant drive-unplug shape. The partial batch still flows on to Phase 2 and embed.
- **Vision failures name the right cause (#218, #219).** Surfaced by the first large real-corpus stress test (1506 files). A fresh project with empty settings resolves the vision model to the hardcoded default; if that model was never pulled, ingest used to 404 on every frame and then halt with a message that pointed at the wrong thing. `vision.is_model_installed()` (strict and tag-exact, returning `None` when Ollama is unreachable so a missing signal never becomes a blocker) now backs `ingest._preflight_vision_model()`, called before both vision entry points, failing loudly and up-front with the installed vision models and how to set one. Separately, the halt message stopped claiming Ollama was broken unconditionally: a halt from the consecutive-failure guard does implicate Ollama, but exceeding `--max-failures` just means isolated bad frames with Ollama perfectly healthy — that case now points at `--skip-failed` instead.
- **One source of truth for the version label (#197).** The header showed a stale `v0.9.2` while the app shipped v0.10.0, because the string was hardcoded independently in about a dozen files. `lib/version.js` is now the single source, imported by the six live routes that display it; future bumps are one line. The `/_design/*` mock artboards keep their frozen label on purpose — they are design reference snapshots, not the product.
- **Neutral placeholders in examples and fixtures (#215).** Docstrings, a code comment and several test fixtures named a specific real-world client library (and one machine-specific media path). Swapped for neutral stand-ins that keep each example's teaching value — the registry-name-vs-directory-basename example still contrasts a CJK name with a latin directory, and the vocabulary fixture still exercises the CJK wordlist path. Verified as more than a find-and-replace: disabling `_basename_only` makes the path-leak test fail, so the edited fixture still bites rather than passing vacuously.
- **The MCP server boots without a vector backend; the e2e now runs on CI.** `mcp_server` imported `vectordb` → `chromadb` at module scope, so a box without chromadb couldn't start the server at all — even though six of the seven tools never touch a vector index — and the e2e stdio test self-skipped on CI, which installs no heavy backends. The import is now lazy: only `search_media` pulls in `vectordb`, inside a try that binds it to `None` and degrades to a SQL filename/transcript match when it (or chromadb) is unavailable. The dim-mismatch branch stays correct because `vdb` is bound before `except vdb.EmbeddingDimensionMismatch` is evaluated. A new test blocks chromadb in a fresh interpreter and asserts the server still imports, serves the six non-vector tools, and degrades search — so the three e2e tests now execute on CI 3.12 instead of skipping.
- **Clear error when pointed at an uninitialised project root.** A tool call against an `ARKIV_PROJECT_ROOT` with no ingested data returned a raw `no such table: media` to the agent. Each tool now checks readiness first and fails with an actionable message (point `ARKIV_PROJECT_ROOT` at an existing library, or ingest there first) — the check is cached once the table exists, and never calls `init_db()` (which prints to stdout and would corrupt the stdio channel). The server still starts and lists its tools regardless; only calls fail, legibly. `search_media`'s SQL fallback is also wrapped so a live DB fault surfaces as a clear error rather than a raw driver string, kept distinct from the "vector index unavailable" degrade.
- **chromadb telemetry off.** `vectordb` now sets `ANONYMIZED_TELEMETRY=False` before importing chromadb, so a local, offline-capable media tool stops phoning home to PostHog on every client init. Set via env (not a `Settings` object) so it survives the test suite's fake chromadb and holds however the process is started; covers the MCP and HTTP surfaces alike. `setdefault`, so an explicit override still wins.

### Fixed
- **Federated search returned nothing for libraries indexed before the 8.0c rename (#212).** Federation search and registry sync hardcoded `.arkiv/project.db`. Libraries indexed before the Phase 8.0c `media.db` → `project.db` rename keep their corpus in the legacy `.arkiv/media.db` and may carry only an empty `project.db` stub — so cross-project search returned **empty** for exactly those projects, with no error to suggest anything was wrong. A small resolver now decides in three steps: an explicit override (`ARKIV_DB_PATH` / `--db`) wins, else the 8.0c default, else the legacy file.
- **The tag-merge LLM's answer was silently thrown away (#214).** `json_mode` guarantees only that the reply parses as JSON, never that it has the shape the prompt asked for — `chat.py` had already learned this from the M1/H9 audit findings and validates every field, but `ingest.py`'s two tag-canonicalization call sites never got the same treatment. Measured against the real provider (qwen2.5:14b / ollama 0.30.7), a prompt that literally says to return `{"groups":[...]}` answered `{"慢跑":"路跑"}` — no `groups` key at all, so `.get("groups", [])` yielded `[]`, the loop never ran, and the cluster's judgment was **discarded without a word**. The model was right (慢跑 and 路跑 *are* synonyms); arkiv simply dropped it. Silent loss is worse than a crash because nothing surfaces it. Two harder shapes on the same path aborted the entire run and discarded every cluster judged so far, because the failing accessor sat *outside* the try. Fixed in two layers — `llm.chat()` takes an optional `schema` passed as ollama's `format` to constrain structure at the source, and the callers validate anyway, because schema adherence is model-dependent and a non-conforming provider must never crash a run. `guard_canonical()`'s semantic protections were already sound and are untouched; the gap was structural, not semantic. Verified end-to-end: the input that previously yielded nothing now returns `{"pref":"慢跑","alts":["路跑"]}`.
- **Ten Taiwan-standard characters made ordinary Traditional text look Simplified (#213).** `_char_is_simplified` — and therefore `classify_zh` — asked neutral `s2t` whether a character changes. `s2t` "corrects" exactly ten Taiwan-**standard** characters to archaic variants (吃→喫, 唇→脣, 峰→峯, 床→牀, 灶→竈, 痴→癡, 皂→皁, 秘→祕, 粽→糉, 群→羣), and those are common: any transcript containing 吃 or 群 classified as "mixed", and one that happened to carry no Traditional-only character could reach the "simplified" bucket and be handed to phrase-level `s2twp` — precisely the input that re-segments and corrupts valid Traditional. Probing `s2tw` instead keeps all ten while still rewriting every genuinely Simplified character (软→軟), so the gate and the char-wise converter agree exactly. No output change on already-correct data; what goes away is a wasted pass and the residual mis-bucketing risk. Verified on a real 122-row library (the 3 rows previously mis-read as "mixed" now classify Traditional) and on a second library byte-identical old-vs-new with every genuine Simplified character still caught.
- **Every built DMG embedded version 0.2.0 (Wave E, #244).** `tauri.conf.json` and `Cargo.toml` sat at `0.2.0` across the **entire** v0.2 → v0.10 tag history, so the version a user saw in a downloaded bundle bore no relation to the release. Both (and the `Cargo.lock` root entry) are corrected to 0.10.0, matching the release tag at the time. `CONTRIBUTING.md` gains a Releases section documenting the changelog-cut and annotated-tag process plus a release-artifact matrix (macOS arm64 supported; Intel and Windows not built). The structural fix is #245, which stamps the version from the tag so this cannot silently drift again.
- **The toolbar clipped instead of wrapping on a narrow window (C1, #196).** Rendered at the 900px Tauri minimum width, the main toolbar genuinely clipped: the artboard is `overflow:hidden` and `.toolrow` was a non-wrapping flex, so below ~1000px the VIDEO/AUDIO/GOOD/REVIEW/N·G filters and the GRID/LIST toggle ran off the right edge **with no scroll** — simply unreachable. The stats line also shattered one token per line. Fixed with `flex-wrap` on the tool row and filter row, a `min-width` on the live search so it shrinks before it pushes buttons off-screen, and `white-space:nowrap` on the stats line. Verified by re-rendering at both 900px and 1440px.
- **A fresh install was told to pull a model it had correctly not pulled (B1, #198, #202).** `health.py` hardcoded a check for `nomic-embed` while `install.sh` and config had switched the default embedding model to `bge-m3` months earlier — so a correct fresh install reported the embed model MISSING and prompted a pointless `ollama pull nomic-embed-text`. It now reads `config.OLLAMA_EMBED_MODEL` like the vision, chat and intent checks already did. The same drift ran through the docs: the README pull commands and tables, `.env.example`, and `docs/install.md` all named `qwen3-vl:8b` / `nomic-embed-text` while the actual defaults are `qwen2.5vl:7b` / `bge-m3`, so a README-follower pulled a 10× slower model that `health.py` then flagged as missing. Everything is aligned to the defaults, with one note preserved that `qwen3-vl:8b` remains a higher-quality/slower override. Adds `docs/quickstart-mac.md`, a hand-holding Apple Silicon guide covering the Gatekeeper right-click → Open step, running `health.py` to READY before the first ingest, passing `--language` for non-Chinese footage, and where to find `backend.log`.

### Internal
- **Wave B — CI stopped taking the build's word for it (#226, #227, #228).** The Tauri (Rust) sidecar had **no gate at all**, so a `Cargo.toml`/`Cargo.lock` drift or a Rust regression could merge silently; a new `tauri-check` job on macos-latest (the only ship target — WKWebView is a system framework, so no apt webkit deps) builds the frontend first, because `generate_context!` embeds `frontend/dist` at compile time and that directory is git-ignored, then runs `cargo check --locked`. Docker was only `docker compose config`, a YAML parse — a broken Dockerfile merged clean; a new `docker-build` job now builds the real image and boots it, curling `/api/stats` over loopback **inside** the container (arkiv trusts loopback and 401s external callers, matching the Dockerfile HEALTHCHECK) with no models or GPU involved. A full Linux build is green-able because `requirements.txt` gates mlx behind a platform marker. Compose also gains an ollama `healthcheck` with `depends_on: condition: service_healthy`, so it waits for readiness rather than container-start — it does **not** wait for model pulls, which stay a runtime step. Buildx was dropped after its Docker Hub builder-image pull flaked the gate; the runner's built-in builder is sufficient. Separately, the coverage job's flake was diagnosed rather than retried: `tests/test_mcp_e2e.py` spawns a real subprocess over async stdio, and under `--cov` the parent-side tracer slows the stdio pump into the `fail_after` bound while the un-instrumented child adds ~0 coverage — it's now excluded from the `--cov` leg by marker (and still runs unfiltered on the test legs), with CONTRIBUTING documenting why coverage stays a non-blocking ratchet and what would flip it to blocking.
- **Wave C — a clean rebuild reproduces (#230, #231).** 19 of 21 Python dependencies were bare lower bounds and the Docker bases plus ollama were floating tags, so a rebuild months later could drift arbitrarily. The two build-path bases (`node:20-slim`, `python:3.11-slim`) and the runtime `ollama/ollama` image — the worst offender — are pinned by `@sha256`, with the human-readable tag kept alongside for legibility and the digest authoritative. Dependabot then keeps those digests from rotting and opens one weekly PR per bump across pip, npm, cargo, github-actions and docker, each linking upstream release notes so a rollback is just a revert. CONTRIBUTING gains a Dependency updates section explaining why there is no Python lockfile (a 3.9 × 3.12 matrix with platform markers). First batch merged: xxhash, pillow, fastapi, silero-vad, faster-whisper (#232, #233, #234, #236, #237) and, on the frontend, `@fontsource/jetbrains-mono` and three (#239, #241).
- **Wave D — the frontend has quality gates, not just a build (#242, #243).** The frontend CI job was build-only, its own comment conceding that lint and svelte-check were "a follow-up". `svelte-check --fail-on-warnings` now runs inside the already-required job (so no new required check), and the five pre-existing warnings were fixed to make zero the baseline: unused `export let` → `export const` on ArkivLogo and Thumb (no caller sets them, API parity kept), an `svelte-ignore` with a stated reason for the deliberate rename-field autofocus in Bins, and two dead CSS rules deleted. A bundle budget was added on the **eager** initial-load chunks (index js ≤512 KiB, css ≤560 KiB, currently ~404/481) — the lazy Pano360/three chunk at 501 kB, which loads only when inspecting 360 media, and the route chunks are intentionally exempt, because the budget exists to catch new growth in what every user downloads, not to re-litigate an already-split viewer.

## v0.10.0 - 2026-07-14

A large release: a month of Phase 9.x transcription/UI work, cross-library
"精選集" bins, live cross-project federation search, an optional pgvector backend,
two security-hardening rounds (round-4 + round-5), a performance batch, and the
internal `server.py` → `routers/` split. ~130 commits since v0.9.2.

### Breaking
- **`GET /api/media/{id}/scenes` returns a per-scene shape (#111).** Each scene is
  now an object (`{start, end, thumbnail, …}`) instead of the previous flat/parallel-array
  form, so per-scene metadata (deep-links, thumbnails) can attach to the right scene.
  Clients that indexed the old array layout must read the object fields.

### Added
- **Batch export to zip — `POST /api/export/batch` (Phase 12.4).** Bundle the per-clip export (`srt`/`vtt`/`txt`/`edl`/`edl-markers`/`fcpxml`) of many clips into one `.zip`, one file per clip. Reuses the single-clip builder verbatim so formats + content are identical; missing ids are skipped, duplicate filename stems de-collide (`clip.srt`, `clip_1.srt`) so nothing is silently overwritten. (For one stitched timeline use `/api/export/timeline`.) 6 tests in `tests/test_export_batch.py`. — *The rest of Phase 12 (corpus/jsonl/SRT-layout/bilingual via `export.py`) was already shipped; this closes the last item.*
- **ADR 0001 — DIT offload wrapper.** `docs/adr/0001-dit-offload-wrapper.md` records the Gate.exe teardown decision (build the wrapper UX, not the engine — arkiv's `offload.py` already beats Gate) so candidate ③ of the DIT-wrapper plan is a findable design record.
- **Event-driven folder watcher (Phase 11.2).** `watch.py` is rewritten from a 30s polling loop into an event-driven watcher: `watchdog` (FSEvents on macOS / inotify on Linux) when installed, with a polling fallback so a box without it (e.g. the NAS) still works — no hard new dependency. New safety logic over the old version: a **stability/debounce gate** (a file is ingested only once its size+mtime has held steady for `--debounce` seconds, so a slow network copy is never ingested mid-write — the old "mtime < 5s" guess missed those), **dedup** against the DB by resolved path, and **unmount-abort** (a watched root disappearing — card pulled — stops that root cleanly instead of ingesting a vanishing file). Watches multiple roots at once (`python watch.py /Volumes/SDCARD ~/.arkiv/inbox`). The `StabilityTracker` + `Watcher.tick` core is pure + clock-injected; 11 tests in `tests/test_watch.py`. (Still subprocess-per-file for crash isolation; in-process dispatch to avoid model reheat — microtask B8 — and the launchd autostart plist + drag-upload / onboarding UI are tracked follow-ups.)
- **Per-language transcript tabs (Phase 9.7 G2).** A clip used to hold a single transcript — retranscribing in another language destroyed the previous one. Now every transcribed language is kept in a `transcripts` archive table; the inspector shows language tabs (the active one marked ●), you can view any language and "設為主要" to make it the active/indexed/exported transcript. `GET /api/media/{id}/transcripts` (lazy-backfills the active language) + `POST /api/media/{id}/transcript/activate`. Retranscribe, batch-retranscribe **and re-ingest now archive the outgoing transcript first**, so a fresh ingest no longer silently clobbers a hand-corrected transcript — it's preserved and restorable (this folds in the real concern behind the retracted "conflict merge" gap, the RP-4 way).
- **UI scale control in Settings → Appearance (Phase 9.7 G4).** A real, persisted scale slider (80–140%) — long deferred because the layout is px-based (a root font-size does nothing). Implemented via CSS `zoom` on the document root, which scales the whole tree regardless of px/rem and is supported in WebKit (Tauri/WKWebView) + Chromium. Persists to localStorage and re-applies on load; a 重設 button returns to 100%. Type density stays honestly disabled (px spacing won't reflow from a density class).
- **List view in the main browser (Phase 9.7 G1).** The Grid/List toggle is now wired: List renders a real table (thumbnail · filename · rating · resolution · duration · size · ingest date) with row-select, selection highlight, and multi-select checkboxes — same selection/pick state as the grid. Columns are drawn from the existing light-list payload; camera/lens columns from the design are deferred (not carried by the list query). Grid remains the default.
- **VTT subtitle export in the inspector (Phase 9.7 G3).** The export row now offers VTT alongside EDL / FCPXML / SRT (the backend already produced WebVTT — the button was just missing). Honours the IN/OUT trim window like the other subtitle exports.
- **Tag-source breakdown in the inspector (Phase 9.7 G8).** The Tags section header shows `N AUTO · N MANUAL`, surfacing how many tags came from vision vs were hand-added — matching the design SSOT. Reads the existing `tags.source` field; no schema change.
- **Correction-dictionary editor in Settings (Phase 9.6c).** Settings → **Vocabulary**: edit the per-project dictionary (rule table `from → to · scope · pre · post`), then batch-recorrect from the same panel — `預覽命中` (dry-run) → `套用校正` (gated on a preview with hits) with an optional vector-rebuild, plus `還原最近一次`. The dictionary is the unified vocab UI — its `pre` toggle is the hotword function, so no separate `vocabulary.txt` editor is needed.
- **Project-wide batch retranscribe (Phase 9.6d, the 2a upgrade path).** `POST /api/retranscribe-all` re-runs Whisper across every audio clip so newly-added hotwords / correction-dictionary pre-terms take effect — for the rare case a term was mis-heard so badly that find→replace can't recover it. Single-flight + `GET /api/retranscribe-all/status` progress poll (mirrors the embed-rebuild pattern); snapshots all transcripts to the shared correction-backups first, so the existing revert restores them. Preserves the single-clip guard (never blanks a good transcript on an empty/failed decode). Wired into Settings → Vocabulary as a clearly-marked heavy "升級" action with live progress. 6 tests in `tests/test_retranscribe_all.py`.
- **Per-project correction dictionary + batch recorrect (Phase 9.6b).** A `.arkiv/corrections.json` dictionary of `{from, to, scope, pre, post}` rules drives two paths from one source: `pre` terms feed the Whisper hotword list (`initial_prompt`, hot-read like `vocabulary.txt`), and `post` rules batch-rewrite **already-stored** transcripts — fixing the whole backlog's search recall in seconds without re-running audio. Endpoints: `GET/PUT /api/corrections`, `POST /api/recorrect` (defaults to dry-run preview — writes nothing; `dry_run=0` applies, `rebuild=1` chains the embedding rebuild), `GET /api/recorrect/backups`, `POST /api/recorrect/revert`. CLI: `python recorrect.py --dry-run | --apply [--rebuild] | --revert [NAME]`. **RP-4 safe**: preview-first, and every apply writes a timestamped backup restorable via revert. The recorrect **syncs `segments_json` alongside the transcript blob** (else search/SRT drift), and `words_json` gets whole-token renames. Scope `word` guards against bleeding into a longer token (a `松→鬆` rule never touches `馬拉松`). Operates on the active project. 16 tests in `tests/test_corrections.py`; full suite 608 passed / 5 skipped.
- **Cross-library "精選集" bins (multi-project Phase 2, PR A/B/C).** Curate clips into named bins that span projects: persistence + CRUD + reference-integrity status (PR A), copy a bin *into* a project in either reference or verified-copy mode with project bootstrap (PR B), and add-to-bin straight from the main grid plus bin rename and a 精選集 section in the pool sidebar (PR C). Bin-detail status probes are batched per project so a large multi-project bin stays responsive.
- **Live cross-project federation search.** The search box now queries across projects through the federation layer (`GET /api/search/all`), with the API-boundary path sanitisation that keeps a `videos_read` client from mapping the operator's cross-project directory layout (Phase 16.2).
- **Optional pgvector backend for embeddings (#180).** A shared-NAS Postgres/pgvector store is selectable as the vector backend; Chroma stays the default, so existing single-box installs are unaffected.
- **Live vision-model picker at ingest (brick 4b).** IngestSetup lists the vision models actually installed in Ollama and lets you pick one per run, instead of being pinned to the config default.
- **機型 camera browser in the pool sidebar.** Browse the library by camera model, surfacing the camera/lens/timecode/codec metadata already captured at ingest.
- **Chat scene deep-links.** A chat answer's scene references filter the main grid to the relevant subset and wire back-navigation into the app; Esc aborts an in-flight request.
- **Basic drag-to-look 360 viewer in the inspector** for `.insv` / `.360` footage.
- **Settings tabs — projects CRUD + real transcription engines (Phase 9.7 G5 ①).**
- **Optional LLM tag canonicalization (E).** A semantic synonym-merge pass collapses near-duplicate tags, on top of the prompt-level canonicalization and variant-collapse fixes.
- **Draggable IN/OUT waveform markers + click-to-play** in the inspector.
- **Small quality-of-life bricks:** export success/failure toasts (B11), a mixed-frame-rate warning on timeline EDL export (B13), waveform cache dir + thumbnail-coverage reporting in health (B5), and a per-clip original→proxy size delta in the ingest log (B6).

### Changed
- **Legacy Tailwind UI retired (Svelte cutover Phase 3, #78).** The old single-file `index.html` + the standalone `dit-offload.html` island are deleted; `/` serves only the built Svelte SPA (a missing build now shows a clear "run `npm run build`" message instead of falling back). The `/legacy` route and the `ARKIV_UI=legacy` escape hatch are removed (the SPA has baked since v0.9.2), the Tailwind CDN proxy + `/tailwind-static.css` go with them, `/dit` now 308-redirects to `/#/offload`, and the Tauri `frontendDist` points at the built SPA. `install.sh` no longer copies the deleted `index.html`.
- **Default vision model is now `qwen2.5vl:7b` (was `qwen3-vl:8b`).** Qwen3-VL's vision path (DeepStack / interleaved-MRoPE) runs ~10x slower than Qwen2.5-VL under Ollama and often offloads the vision encoder to CPU — measured **~60s/frame vs ~8s/frame on an M2 Max** for identical frames (Ollama issues #12854 / #12882 / #14548). At ~2000 frames that's ~30h vs ~3.5h, at comparable tag quality (spot-checked). Override with `ARKIV_OLLAMA_VISION_MODEL=qwen3-vl:8b` for the higher ceiling once the Ollama regression is fixed.
- **arkiv is now Windows-portable.** Cross-platform path handling throughout and a green Windows test suite; the frontend/inspector was rebuilt to a full-window layout with a real video/audio player, waveform, and scrolling side panels.

### Fixed
- **Vision fallback no longer 404s on every fresh install.** The Phase-2 fallback model was hardcoded — and inconsistently (`ingest.py` used `minicpm-v`, `server.py` used `moondream2`) — while `install.sh` pulled neither, so the issue-#48 resilience path silently 404'd per failed frame and left those frames empty. Now: a single configurable `ARKIV_OLLAMA_VISION_FALLBACK_MODEL` (default `minicpm-v:latest`) used by both paths, pulled by `install.sh`, and **skipped gracefully** (logged once, frame left for a later `--vision-only` retry) when the model isn't installed instead of erroring per frame.
- **`install.sh` pulls the right models** for the new defaults: `qwen2.5vl:7b` (vision) + `minicpm-v` (fallback), alongside `bge-m3` + `qwen2.5:14b`.
- **CJK-named clips export instead of 500ing.** Content-Disposition now uses an RFC 6266 `filename*` so a clip with a Chinese/Japanese/Korean name downloads correctly instead of erroring.
- **Sony XAVC camera identity recovered from embedded XML** (closes #115); the ingest bench summary now reports the *effective* vision model rather than the config default.
- **Tag-cloud noise cut down** across the pipeline: Simplified→Traditional normalisation, artifact/English filtering, per-clip dedup, a canonical prompt + variant collapse, a UI cap, and a library alias map.
- **Domain-agnostic Tier-1 smart collections** — the hardcoded food vocabulary is gone, so smart pools work for any library.
- **Frontend robustness:** API errors are surfaced (`ApiError` detail, no more silently-swallowed mutations), stale-response guards on the bin + search loaders, and a grid truncation notice with offset-based load-more.
- **Content-hash move-detection dedup at ingest (round-5 #8)** and migration of legacy backslash-relative paths (round-5 #9), so a moved/re-ingested file is recognised instead of duplicated.

### Security
- **Hardening round-4 (fable-audit 2026-07-12).** Offload-path denylist, cache-clear CSRF protection, absolute-path leak fixes, and a DDL guard; absolute project paths are now scoped to admin callers only (#22).
- **Hardening round-5 (#51, ~30 findings).** Highlights: `/api/ingest` + reingest run through a tree-killing process group so an aborted ingest leaves no orphans (#2/#12); `/api/cache/clear` protects thumbnails and invalidates the chromadb cache (#15/#16); ingest single-flight now also covers `copy_bin` + retranscribe-all, and the ingest slot is released on a copy_bin index-phase failure (#58); atomic proxy writes with a non-empty gate at every consumer (#1/#60); resumable, abortable, single-flight offload state (#19/#45); transcript-merge re-parents transcripts before the media row is deleted (#7); retranscribe integrity (language backup, revert, archive-outgoing) (#14/#17); a permanently-failing watched file is backed off and quarantined (#5); the dead `VISION_MODEL` global is removed and the fallback model threaded through `_call_vision` (#50); single-source extension sets fix `.insv`/`.360` SQL drift (#57).

### Performance (round-5)
- Indexed the hot media columns, chunked large `id IN (…)` queries, and bounded the federation SQL fallback (#20/#21/#24); trimmed the media-detail payload, made stats a single pass, and reconciled embeddings ids-only (#26/#27/#31); dropped the heavy transcript JSON from the Phase-1→2 retained record (#28); batched bin-detail status probes per project (#23); code-split the `/_design` mock routes via async import.

### Internal
- **`server.py` split into a `routers/` package (#51, R5-25).** The ~4.5k-line FastAPI monolith was peeled — verbatim, behaviour-preserving — into focused `APIRouter` modules (`routers/*.py`) behind leaf service modules (pathres/webguard/export_builders/reqopts/mediarecords/state) that broke the import cycle. `server.py` is now a ~490-line app shell (static routes + lifespan + middleware + `include_router` wiring). No route paths, methods, or auth changed.

## v0.9.2 - 2026-06-17

### Added
- **New Svelte UI is now the default at `/`; the old Tailwind UI moves to `/legacy` (cutover Phase 1+2, #78).** `server.py` serves the built SPA from `frontend/dist` with a `/assets` mount; it auto-falls back to the legacy page when no build is present, and `ARKIV_UI=legacy` forces the old page. `install.sh` and the `Dockerfile` (new `node:20` build stage) now run `npm run build`, so a fresh clone / image ships the new UI. The old UI stays reachable at `/legacy` as an escape hatch during the cutover bake. Hash-routed SPA → no server-side fallback needed.
- **Tier 1 backend-parity features wired into the new UI (#78).** Inline tag editing (add/remove); per-clip re-processing (retranscribe / retry-vision / reingest); editing-proxy build + status (whole-library and per-clip); DaVinci Resolve metadata CSV export (whole-library and current selection); and a chat-history sidebar that lists past conversations and restores their threads.

### Notes
- Tier 2 backend capabilities are not yet wired into the new UI (in/out position save, chapters, export-to-path, remotion-props, open-file, project federation, pool view, admin tokens, cache ops, analytics breakdowns). They remain reachable via the API and the `/legacy` UI; tracked in #78.

### Performance
- **DIT `--organize` / preview now probe metadata in one ExifTool spawn, not one per file.** `_probe_camera_meta` spawned ExifTool per file, so a 400-clip card preview/offload paid ~400 process spawns (tens of seconds of pure spawn overhead). Extracted `_exiftool_batch()` (a single `exiftool -json …file1 …file2 …fileN` call keyed by `SourceFile`) + `_probe_camera_meta_batch()`; `preview_layout()` and `_ensure_file_records()` batch-probe up the whole file list once. The Sony XAVC sidecar / mtime fallbacks still run per file (cheap, no subprocess). The single-file `_probe_camera_meta` (card-watch) is unchanged in behaviour.

### Added
- **DIT Offload UI (`/dit`).** A working control panel for the DIT engine: type a source card, destination(s), and an `--organize` template, hit **Preview** to see the exact source→destination layout (read-only, no copy), then **Run** to offload (copy + hash-verify + ascMHL, never deletes the source). New endpoints `POST /api/offload/preview` (pure layout via `offload.preview_layout()`) and `POST /api/offload` (subprocess, mirrors `/api/ingest`; state file scoped to the project's `.arkiv`, not the install dir); page served at `GET /dit`. The run **streams live per-file progress** — `offload.py --progress json` emits single-line `dst_start`/`file`/`done` events and `/api/offload` relays them as ndjson via a `StreamingResponse`, so the UI shows a progress bar + current-file count instead of blocking on one giant request. Verified end-to-end (preview layout + streamed copy + MHL manifest). Tests in `tests/test_dit_offload_ui.py` + `tests/test_offload_organize.py`.
- **DIT card-watcher (`offload.py --watch`).** Waits for a camera card to mount, then auto-offloads it — copy + hash-verify + MHL, **never deletes the source** — with the `--organize` naming policy. A card is a newly-mounted volume with a `DCIM/` folder or media files; only NEW inserts trigger (already-plugged disks at startup are the baseline and ignored), and a removed→reinserted card re-triggers. One failed offload is logged and the watch loop survives. The "insert → it just copies" half of the Gate replacement, paired with the `--organize` folder policy. Requires at least one `--dst`. Tests in `tests/test_offload_card_watch.py`.

### Fixed
- **`--refresh` now actually re-extracts thumbnails + frames (issue #53).** It used to re-run vision/embed but reuse cached frame thumbnails (the `already_ok` check skipped extraction whenever a file existed), so a change to the extraction logic itself — e.g. the Phase 8.3b 360 reproject — never re-applied to already-ingested clips; they kept their stale raw-fisheye frames. `--refresh` now threads `force=True` down to frame/thumbnail extraction. Re-extraction is atomic (ffmpeg writes a temp sibling, `os.replace` onto the canonical file only on success), so a forced re-extract that times out / fails can't destroy the prior good thumbnail while the DB still points at it. Non-refresh ingest still reuses existing thumbnails (cheap re-ingest, unchanged).

### Added
- **360 footage is reprojected to equirectangular before vision (Phase 8.3b).** Dual-fisheye `.insv` / `.360` (Insta360 / GoPro Max) now get both lenses stitched into a full equirectangular panorama (`hstack` → `v360=dfisheye:equirect`) before frame extraction, so the vision model sees an undistorted scene. The raw fisheye buries the wearer and on-screen text (event name, bib numbers) in the distorted edge — vision reads nothing useful; the equirect stitch surfaces them (verified end-to-end: the same clip went from "a group in blue shirts" to reading "POCARI SWEAT RUN" + bib number off the reprojected frame). Detection is ext-gated + confirmed by a two-video-stream ffprobe (a single-lens file mislabeled `.360` won't try to stitch a missing stream); 360 frames extract at 1024px (vs the normal 320px) so reprojected text survives for the VLM. Audio/transcript is projection-agnostic and unchanged. Tests in `tests/test_frames_360.py`; design in `references/plans/arkiv/2026-06-13-arkiv-8.3a-360-indexing-poc.md`.
- **DIT naming / folder policy (`offload.py --organize TEMPLATE`).** Lay card files into camera-metadata folders instead of mirroring the card — the thing Gate's folder logic got wrong. Folder template with `{date}/{camera}/{reel}/{stem}/{ext}` tokens (original filename always appended), e.g. `--organize "{date}/{camera}/{reel}"`. Metadata via ExifTool + a Sony XAVC NRT sidecar fallback (so FX30/FX-series get make/model even when the `.mp4` carries none); missing values resolve to `UNKNOWN`, the date falls back to file mtime. Token values are sanitized to one filesystem-safe segment (a `Sony/FX30` can't spawn a nested directory; no path traversal), and two source files mapping to the same destination are **refused** (a DIT tool must never silently overwrite). Pair with `--dry-run` to preview the layout. The default (no `--organize`) still mirrors the source exactly. Tests in `tests/test_offload_organize.py`. (Follow-up: ExifTool currently spawns per file — batchable for large cards.)
- **Vision frame-failure tolerance (`--max-failures` / `--skip-failed`, issue #48).** The vision phase used to halt the entire run on the first frame that failed both the primary and fallback model — fine interactively, but a single transient Ollama hiccup killed a 481-frame overnight run. `--max-failures N` tolerates N cumulative failed frames before halting (N=0, the default, keeps the historical zero-tolerance behaviour). `--skip-failed` never halts on individual frame failures — it leaves them with an empty description (so a later `--vision-only` retries exactly those frames) and prints a report at the end. A consecutive-failure guard (whole files producing nothing) still halts fast under either flag, so a real Ollama outage can't spin all night. Successful frames are now committed even on a file that later triggers a halt (no lost work). `--vision-only` and the main Phase-2 loop share one describe→fallback→tolerance path. Tests in `tests/test_vision_tolerance.py`.

## v0.8.1 - 2026-06-12

### Added
- **360-camera formats (`.insv`, `.360`) are now first-class video.** Insta360 `.insv` and GoPro Max `.360` are HEVC-in-MOV/MP4 — ffmpeg probes them and extracts frames fine, but they were absent from the ingest whitelist, so `is_video` was False and they silently skipped thumbnail/frames/vision. Added to `VIDEO_EXT`/`SUPPORTED` (ingest), `_VIDEO_EXTS`/`MEDIA_EXTS` (server), `watch.py`, and the UI format lists. Verified on a real `.insv` (dual 2880×2880 HEVC fisheye + AAC; thumbnail decodes). `.insp` (Insta360 stills) not included — no image pipeline. Tests in `tests/test_ingest_microtasks.py`.

### Tests
- **Pinned 3 Codex-flagged edges as resolved** (H5 abs/rel row merge, scene_ids dangling reference, M24 codec backfill freshness). All were already fixed in the v0.8.0 sprint; regression tests in `tests/test_v081_edges.py` lock the behaviour — frames+tags merge to the survivor with no orphans + FK integrity (incl. the frame_index collision branch); a dangling `scene_ids_json` id drops gracefully (`WHERE id IN` + `find_similar` return `[]`); stored codec skips re-probe, NULL probes once + backfills, probe failure falls through without crashing playback.

### Docs
- README (EN + zh-TW): documented 360 format support and that camera identity is read from embedded EXIF **and** the Sony XAVC NRT sidecar XML (so FX30 / FX-series footage keeps make/model/lens/timecode that consumer DAMs drop).

## v0.8.0 - 2026-06-12

### Security
- **Stored/reflected XSS in the web UI closed.** `esc()` only escaped `&<>` (the textContent trick), leaving `'`/`"` raw — so LLM-generated tag names / filenames interpolated into HTML attributes and inline `onclick` strings were injectable, and the vision-LLM frame description was written into `innerHTML` unescaped (stored XSS via on-screen text in footage). `esc()` is now quote-safe, a dedicated `escJsAttr()` guards values placed in inline-handler JS strings, and the frame description, tag handlers, autocomplete row, and search-query title are all escaped.
- **`/api/media` no longer turns every authenticated read into a DB write.** Token resolution updated `last_used_at` on every request, so a `GET` competed for the SQLite write lock during concurrent ingest ("database is locked"). The write is now throttled to once per 60s per token.
- **`uninstall.sh` refuses to `rm -rf` a non-arkiv directory.** `ARKIV_DIR` came straight from the environment; a stray value (`$HOME`, a footage volume) would wipe an unrelated tree. It now requires the install markers (`server.py` + `config.py`) before removing anything.

### Added
- **Custom-vocabulary file for transcription hotwords.** Beyond the comma-separated `ARKIV_CUSTOM_VOCABULARY` env, you can now keep a newline-delimited wordlist (one term per line, `#` comments) — `ARKIV_VOCABULARY_FILE`, defaulting to `.arkiv/vocabulary.txt` when present. Terms (names, places, product jargon) merge with the env list, dedup, and feed Whisper's `initial_prompt` — the persistent-wordlist workflow FatSub validated with editors. Tests in `tests/test_transcribe_faster_whisper.py`.
- **Chapter-marker export (`export.py chapters <id>`).** Turns a clip's sampled
  scene frames (scene-change timestamps for long clips) into chapter markers, with
  titles auto-generated from each frame's vision description (first sentence,
  truncated). Two formats: `--format youtube` emits `MM:SS Title` lines (first
  marker forced to `0:00` per YouTube's rule) for video descriptions; `--format
  ffmetadata` emits an ffmpeg metadata file to embed real chapters (`ffmpeg -i in
  -i chapters.txt -map_metadata 1 -codec copy out`). Borrowed from the FatSub
  ProChapter benchmark; reuses existing scene segmentation, no new deps. Also
  exposed over HTTP: `GET /api/media/{id}/chapters?format=youtube|ffmetadata`
  (scope `media_read`) returns `{media_id, format, chapters, count}`.

### Fixed
- **`probe()` no longer swallows ffprobe failures, hangs, or lets one bad clip poison a batch.** It used `-v quiet` with no timeout and returned `None` on any non-zero exit — so a mid-ingest failure printed only `[ffprobe failed]` with no cause, and a transient subprocess-spawn failure (e.g. Windows handle exhaustion after a heavy clip) could fail every subsequent clip. Now: `-v error` surfaces the real stderr, a 120s timeout bounds hangs, the actual rc/stderr/exception is printed, and one retry (2s back-off) recovers transient spawn/resource pressure. Tests in `tests/test_ingest.py`.
- **Headless / SSH ingest on Windows no longer dies at the first `ffprobe` ([WinError 448]).** arkiv invoked `ffmpeg`/`ffprobe` by bare name, so on a non-interactive Windows session (headless service, SSH) they resolved to the WinGet `Links` App Execution Alias — a reparse-point shim that raises `[WinError 448]` outside an interactive token, crashing `probe()` before any clip is processed. ffmpeg/ffprobe are now resolved once via `config.FFMPEG_PATH` / `config.FFPROBE_PATH`: `ARKIV_FFMPEG_PATH` / `ARKIV_FFPROBE_PATH` env > `shutil.which()` (skipping alias shims) > common install paths (Gyan winget full build, choco, scoop, Homebrew, `/usr/bin`) > literal name. macOS/Linux behavior is unchanged (`which` returns a real path). New tests in `tests/test_config.py`.
- **`UnicodeDecodeError` on Windows zh-TW (cp950) during proxy generation / codec probe.** `subprocess.run(..., text=True)` without an explicit encoding decoded ffmpeg/ffprobe output with the cp950 locale codec and crashed on utf-8 bytes. Proxy gen (`ingest.py`) and codec probe (`codec.py`) now pin `encoding="utf-8", errors="replace"`.

#### Audit batch 2026-06-10 (5-agent review + Codex verification)
- **Thumbnails no longer cross-contaminate between same-named clips.** Camera cards reuse filenames (`C0001.MP4`, `GX010001.MP4`); with `--recursive` over several cards, the second clip found the first's thumbnail already on disk and reused it — so its vision tags / editability score were computed from the wrong frames and written to its DB row. Thumbnails are now keyed by `{stem}_{sha1(abs_path)[:10]}`, the same path-hash guard proxies already had.
- **`Phase 3` no longer re-runs ffprobe over the entire library every ingest** (the mid-run ffprobe storm behind the stalled large-batch runs). The detected codec is persisted in `media.codec` at ingest time and reused; only legacy NULL-codec rows probe once, then persist. `watch.py`'s per-file process spawn no longer triggers a full-library probe per new clip.
- **A mid-run crash no longer leaves "processed but unsearchable" records.** The media row, its auto-tags, and its frame rows are written in one transaction (so a crash can't leave a frame-less row that `is_processed` skips forever); embedding now runs even when `ok == 0` to reconcile rows a prior crashed run left un-embedded, force re-embeds `--refresh`'d rows, and prunes ChromaDB entries whose SQLite row is gone; `upsert_record` deletes a media's old chunks before re-inserting (no orphan chunks from a shrunk transcript).
- **`--refresh` no longer wipes `frame_tags` / `thumbnail_path` when vision or thumbnail extraction fails.** Those fields are omitted from the refresh upsert instead of being set to `None`, so the prior searchable values survive a failed re-vision.
- **Vision halt now exits non-zero.** A halt on the first file left `vision_fail = 0` → implicit exit 0, hiding a fully-unindexed library from cron/watch; the halt now counts as a failure and `--vision-only` propagates the halt as exit 1.
- **`/api/media?q=…` honors `rating` / `lang` with semantic search.** The filter ran against the raw vector hit (which carries no `rating`), dropping every semantic result under `rating=good` and silently falling back to an unfiltered SQL `LIKE`. It now filters the enriched record, and the SQL fallback applies the same filter.
- **Chat is hardened against malformed/injected model output.** Refinement intersects model-returned scene ids with the prior set (rejecting hallucinated / transcript-injected ids before they're persisted); analytics validates `time_range` as `YYYY-MM` and `dispatch` has a catch-all so a bad model response returns a clean error turn instead of a 500; the intent limit is clamped `>= 1`; intent is classified once per request instead of twice.
- **WebSocket ingest no longer deadlocks or runs unbounded.** `stderr` is merged into the read stream (an unread `stderr=PIPE` deadlocked once its 64 KB buffer filled), the background task is held with a crash-logging callback, a 409 guard serializes concurrent ingests, the progress regex handles filenames with spaces, and the completion message reports the real failed-count and exit code.
- **"Export GOOD as EDL" exports all good clips**, fetched across the whole library and sequenced via the batch timeline endpoint, instead of only the first good clip on the current grid page.
- **`federation` search timeout actually bounds wall-clock.** `search_all_projects` waited on `shutdown(wait=True)` even after a per-future timeout, so a worker hung on a stale mount hung the whole request; it now uses a shared deadline and abandons stragglers.
- **Installer fixes:** the venv is built from the freshly-installed `python@3.12` binary (not the rejected `python3`), and Ollama pulls match `config.py` defaults (`bge-m3` / `qwen3-vl:8b` / `qwen2.5:14b`) — the old `nomic-embed-text` pull left a fresh install's semantic search silently degraded.

### Changed
- **`media.codec` column added** (additive, backward-compatible migration) to persist the ffprobe codec for proxy decisions.
- **Dropped the unused `pandas` dependency** (~70 MB, imported nowhere) and removed the orphaned, broken `vision_refresh.py` (superseded by `ingest.py --vision-only`). `chromadb` and `pydantic` now carry upper version bounds.

## v0.7.0 - 2026-06-08

### Security
- **Optional HMAC token hashing (Phase 16.1).** Set `ARKIV_TOKEN_HMAC_KEY` to store access tokens as HMAC-SHA256 instead of bare SHA-256. Non-breaking: existing tokens keep working (dual-read) and upgrade to HMAC in place on next use. Losing the key invalidates HMAC tokens (re-mint), the same failure mode as losing the DB.
- **API responses no longer leak absolute filesystem paths (Phase 16.2).** Read-scope endpoints (`/api/media`, `/api/media/{id}`, `/api/media?q=`, `/api/search/all`, `/api/cache/info`) previously returned absolute paths that exposed the operator's directory tree. They now return PROJECT_ROOT-relative paths (basename for legacy absolute rows), and `/api/cache/info` no longer returns cache directory paths. open-file round-trips the relative path. (FCPXML `file://` URIs and user-supplied export destinations intentionally remain absolute — see `docs/phase-16.2-path-leak-triage.md`.)
- **`/api/search/all` path-leak fully closed.** The Phase 16.2 boundary strip handled `items[].path` and each item's `project_path`, but left two absolute paths reachable by a `videos_read` client: `items[].relative_path` (federation sets this to the *absolute* path for out-of-root rows — `_resolve_paths` falls back to `str(stored)` when `relative_to` fails) and `errors[].project_path` (the absolute project root, returned on a project timeout / preflight failure). The endpoint now drops the internal `absolute_path` / `relative_path` fields and basenames `project_path` across both items and errors. Regression: `tests/test_search_all_path_leak.py`.
- **WebSocket ingest auth + secrets hardening.** `/ws/ingest` was open to any LAN/tailnet peer or malicious browser page (the HTTP scope-gate doesn't reach `@app.websocket` routes). The handshake now enforces a same-origin / allowlisted-Origin check (CSWSH guard), the same loopback-trust rule as HTTP plus a `?token=` carrying `ingest_write`, and a 32-connection cap (close 1013 over the limit). Loopback trust additionally requires **no forwarding header** (`X-Forwarded-For` / `X-Real-IP` / `Forwarded` / `X-Forwarded-Host`) — closing the hole where a reverse proxy / `tailscale serve` (which connects *from* 127.0.0.1) handed every proxied remote request full admin. `ARKIV_ADMIN_BOOTSTRAP_TOKEN` now refuses tokens < 24 chars (a weak one was a remotely brute-forceable admin credential). `?token=` is redacted from uvicorn access logs; the token DB is created `0600` (data dir `0700`) on POSIX. `.env.example` now documents `ARKIV_TRUST_LOOPBACK` and `ARKIV_ADMIN_BOOTSTRAP_TOKEN`.

### Changed
- **Default embedding model is now `bge-m3` (1024-dim), up from `nomic-embed-text` (768-dim).** bge-m3 is multilingual (100+ languages, 8192-token context) and substantially stronger on Chinese retrieval while staying on par with nomic for English — a better default for mixed-language media libraries. **Breaking for existing indexes:** the dimension change means stored vectors are incompatible; run a full re-index (`python embed.py --rebuild` or 進階設定 → 重建向量索引) after upgrading. Override with `ARKIV_EMBED_MODEL` to keep the old model.

### Added
- **MCP server (Phase 14).** `mcp_server.py` exposes arkiv's local media knowledge layer to MCP clients (Claude Desktop, Claude Code, OpenClaw) over stdio, so an agent can search and read your footage library without going through the HTTP API. Six **read-only** tools: `search_media` (semantic search over transcripts + vision tags, SQL text fallback), `get_media`, `get_transcript`, `list_recent`, `library_stats`, `list_tags`. Reuses `db` + `vectordb` (does not import `server`, so no FastAPI startup cost) and returns JSON with `ensure_ascii=False`. **No absolute-path leak:** every path is PROJECT_ROOT-relative with a basename fallback for out-of-root rows — the same red line as the Phase 16.2 HTTP hardening, enforced in `_safe_path`. New optional dep `mcp`; 18 unit tests. See `docs/phase-14-mcp-handover.md`. — *Since superseded: `get_scenes` makes it seven tools and `get_transcript` gained timecodes (see Unreleased). The "end-to-end stdio smoke" this entry originally claimed was an ad-hoc run and was never checked in; `tests/test_mcp_e2e.py` makes it real.*
- **Smart Collections — rule-driven curation with a GPS location signal (Tier 1).** `smart_collections.py` scores each clip's existing vision metadata (tags + both per-frame *and* media-level `content_type`/`atmosphere`/`energy`) against predefined collection definitions; a clip non-exclusively joins every collection it scores ≥ 0.40 for. Surfaced at `GET /api/collections` and in the UI sidebar (rule-driven, **not** ML clustering). New `geo.py` turns EXIF `gps_lat`/`gps_lon` into a stable *location label* — a named place from an operator gazetteer (haversine within radius) or a coarse rounded-coordinate cell fallback — exposed in the collection signal and usable as a booster condition. Pure-Python, no new deps, no external geocoding service; missing coords and the `(0,0)` GPS "null island" cameras emit with no fix never resolve to a phantom location. (Domain-specific definitions beyond the food/interior archetypes await real footage from those shoots so their tag vocabularies can be verified against live `qwen3-vl` output rather than guessed.)
- **`whisper_guard` package (Phase 10).** The pure text-level hallucination filters (`is_repetitive` / `has_char_loops` / `remove_char_loops` + a `HallucinationGuard` class) are now a standalone, stdlib-only package extracted from `transcribe.py`, staged for a future `whisper-guard` PyPI release. `transcribe.py` imports them with unchanged behavior. `install.sh` now also ships first-party package directories (via a `*/__init__.py` glob), so a copy-install includes the new package.
- **`export.py` corpus/JSONL CLI (Phase 12, core).** Turns a library's transcripts + vision metadata into LoRA/RAG-ready formats. `export.py corpus [--lang] [--out]` emits a merged plain-text corpus (transcripts only, no JSON residue); `export.py jsonl [--lang] [--out]` emits one JSON object per media (`{id, text, metadata}` with tags + frame descriptions); `export.py txt <id>` dumps a single transcript. Reads the DB directly (no server import); `ensure_ascii=False` keeps Chinese readable. (Batch-zip API remains deferred.)
- **Subtitle layout engine (Phase 12.5).** `subtitle.py` re-wraps raw Whisper text into broadcast-style captions, surfaced as `export.py srt <id> [--max-cjk N]`. Lines are capped in CJK units (default 14, Netflix zh-Hant; Latin counts 1/3) as a hard invariant, broken at natural points (CJK punctuation / spaces) without splitting a Latin word or separating a number from its measure word (`14字`). Long segments are time-split into multiple proportional cues; optional bilingual cues (original above translation). Uses segment-aligned timestamps when present, else a single transcript cue.
- **Resource-aware ingest pipeline (Phase 11.5).** Ingest now probes machine state before a vision batch and backs off when memory is saturated — directly addressing the 427-clip stress test that lost 20 frames to a cold-start timeout and hit a 28 GB unified-memory crunch.
  - `resource_probe.py` reports loaded Ollama models (`/api/ps`), GPU VRAM (nvidia-smi on PC), unified memory (psutil on Apple Silicon), and active-job count. It is a **sensor, not a gate**: any source failing degrades that field to `None` and never raises — a broken probe can't block ingest. `ARKIV_PROBE_DISABLE=true` makes it a full no-op.
  - **Backpressure**: before each vision batch, ingest waits (bounded exponential backoff, `ARKIV_BACKPRESSURE_MAX_WAIT`) while memory pressure exceeds `ARKIV_GPU_MEM_THRESHOLD` (default 0.8), then warms the vision model only if it isn't already resident. Systematizes the previously ad-hoc warm-up.
  - **`python ingest.py --queue status|cancel|retry [--job-id N]`** — a SQLite-backed job queue (no Redis/Celery) with type-derived priority.
  - **`python ingest.py --status [--json]`** — one-shot resource + queue snapshot and the decision the next vision phase would take.
  - New env vars: `ARKIV_GPU_MEM_THRESHOLD`, `ARKIV_BACKPRESSURE_MAX_WAIT`, `ARKIV_PROBE_DISABLE`, `OLLAMA_NUM_PARALLEL`. `psutil` added as a soft dependency.

  > ⚠️ Mock-tested + verified live on Apple Silicon (probe reads real Ollama/memory). The **11.5d throughput A/B** (`OLLAMA_NUM_PARALLEL=1` vs `2`) and the **427-clip cold-start-elimination** acceptance still need a real GPU run — see `docs/phase-11.5-acceptance.md`.
- **`POST /api/embed/rebuild`** (scope: `ingest_write`) — drops and rebuilds the ChromaDB semantic index from all media in a background subprocess. Backs the existing 進階設定 → 搜尋引擎 「重建向量索引」 button, which previously called a non-existent route (404).
- **More ingest container formats + cache visibility (ingest microtasks B3–B6).** `.mkv` / `.avi` / `.webm` now get thumbnails, frames, and vision — they were ingested into the DB but missing from `VIDEO_EXT`, so `is_video` was False and they silently skipped all visual processing. New `ingest.py --regenerate-thumbnails` rebuilds every video's poster (force-bypasses the reuse cache). `health.py` "Cache dirs" now reports file count + size for thumbnails / proxies / chroma_db, and the regen commands print the net size delta on completion.

### Fixed
- **WebSocket token auth was broken on `main`.** `server.py`'s `_ws_authorized` called `auth.resolve_raw_token`, but the refactor that introduced it was never staged (a `git add` missed `auth.py`), so on `main` every token-based `/ws/ingest` handshake hit `AttributeError` (caught by a bare `except` → rejected) and the ingest-progress WebSocket only worked on loopback. `resolve_raw_token` (hash / expiry / IP-allowlist / scope) is now extracted and `verify_token` delegates to it; the WS Origin check accepts same-origin on any deployment host/port plus the Vite dev origin (`:5173`), and the frontend WS URL carries the token.
- **Embedding-dimension mismatch now fails loud instead of silently degrading.** After the bge-m3 (1024-dim) default, querying a stale 768-dim index threw a raw ChromaDB error → 500s on chat/federation and a *silent* SQL-only degradation on `/api/media?q=` (a bare `except: pass` swallowed it). `vectordb` now stamps the embedding model + dim into the collection on create/rebuild, raises a clear `EmbeddingDimensionMismatch` ("run `python embed.py --rebuild`") when the active model differs, and defensively wraps query/upsert (catching legacy/unstamped indexes by message). `/api/media?q=` surfaces a `search_degraded` warning instead of hiding it, chat returns a clean error (not a 500), federation reports it per-project in `errors[]`, and an incremental `embed.py` run aborts loudly. `config.EMBED_DIM` is no longer purely informational.
- **`install.sh` no longer hangs on headless installs.** Step 8's desktop-shortcut `cp` into `~/Desktop` blocked indefinitely on a headless / piped (`curl | bash`) / nohup install — macOS Desktop (TCC) prompts for access with no GUI to answer. It is now guarded on an interactive **stdin** (`[ -t 0 ]`, not stdout — for `curl | bash` fd 0 is the pipe) and skipped silently otherwise; when skipped, the success output prints the `cd … uvicorn` launch line instead of advertising the absent shortcut.
- **Windows zh-TW console no longer reports false errors during embedding.** `embed.py`'s per-file success marker used `✓` (U+2713), which raised `UnicodeEncodeError` on cp950 consoles and made every successfully-embedded file print as `[ERROR: ...]` — masking real failures. Replaced with an ASCII marker.

## v0.6.1 - 2026-05-28

### Fixed
- **Non-Mac (CUDA) ingest no longer breaks at transcription.** whisperx 3.8.5 moved the per-call ASR options (`beam_size` / `condition_on_previous_text` / `compression_ratio_threshold` / `log_prob_threshold` / `initial_prompt`) into `load_model(asr_options=…)` and now pulls torchcodec, whose DLLs fail to load on the Windows/CUDA box. The non-Mac backend now routes to **faster-whisper** (already a declared non-Mac dependency, no torchcodec), which still accepts those options per call — a clean drop-in for the whisper-guard layer config. Set `ARKIV_TRANSCRIBE_BACKEND=whisperx` to force the legacy path. The Apple-Silicon MLX path is unchanged.

> ⚠️ Verified on Mac with mocked `WhisperModel` unit tests only. **Live CUDA ingest→embed→chat on PC is still pending** before this is tagged/released — see `OVERNIGHT_RESUME.md`.

## v0.6.0 - 2026-05-28

### Added
- **Chat in the web UI.** A new "對話" button in the header opens an interim chat panel that calls `/api/chat`: conversation thread, intent badge, latency/token meta, and a scene-thumbnail strip whose clips open in the inspector. The canonical chat screen is being designed separately — this is a functional interim so the feature is usable today.

### Fixed
- **Web UI works in a plain browser again.** Since v0.4.1 every route required a Bearer token, but the browser frontend never sent one — so the web UI returned 401 on all data calls (and a fresh install was unusable in a browser). `auth.py` now trusts loopback (`127.0.0.1` / `::1`) as fully-scoped, so the local UI works with no token. Remote / fleet access still requires a token; set `ARKIV_TRUST_LOOPBACK=false` for reverse-proxied or network-exposed deployments.

### Docs
- README chat section now states the prerequisite: ingest **and** build the index (`python embed.py`) before chatting. `compilation` / `refinement` / `similarity` need the vector index; `analytics` needs ingested media; only `general` works on an empty library.

## v0.5.2 - 2026-05-28

### Changed
- **Chat needs only one model now.** `ARKIV_INTENT_MODEL` defaults to `ARKIV_CHAT_MODEL` (`qwen2.5:14b`), so a single `ollama pull` covers both intent classification and answers. Previously it defaulted to `qwen2.5:7b-instruct` — undocumented and frequently not installed, which silently broke chat. Override only if a smaller intent model is actually present.

### Added
- **`health.py` now checks the chat model** and warns with an `ollama pull …` hint when the configured chat (or a distinct intent) model is missing — previously health was blind to chat models.
- **README chat documentation (EN + zh-TW)** — model requirement, all five intents, `project_scope`, response shape, chat hardware floor, and an "embedding model is locked to your index" warning.

### Fixed
- **Missing chat model returns a clear message instead of HTTP 500.** `/api/chat` now catches the Ollama `HTTPError` and tells you to `ollama pull` the model.

## v0.5.1 - 2026-05-28

### Fixed
- **Chat `similarity` intent crashed with HTTP 500** on real ChromaDB data. `vectordb.find_similar()` used `ref.get("embeddings") or []`, which raises `ValueError: truth value of an array ... is ambiguous` because ChromaDB returns embeddings as NumPy arrays. Replaced with explicit `None` / `len()` checks. Mocked tests did not cover this path; caught by live verification against a real index.

## v0.5.0 - 2026-05-28

### Added — Chat: RAG over your video library
- **5-intent classifier** routes natural-language prompts to specialized handlers: `compilation`, `refinement`, `similarity`, `analytics`, and `general`.
- **Conversation memory** persists chat messages and scene IDs, then feeds recent history into follow-up prompts.
- **Project-scoped vector search** adds `project_scope` filtering to `vectordb.search()` and `vectordb.find_similar()`.
- **Chat API docs** in README EN / zh-TW describe intent examples and authenticated curl quickstarts.

### Hardening
- Chat dispatch now handles Ollama `Timeout` / `ConnectionError` without returning HTTP 500.
- Oversize prompts are trimmed before classification and handler execution.
- Invalid or empty classifier intents fall back to `general`, and classifier limits are capped at 100.

### Tests
- Expanded `tests/test_chat.py` to 17 cases covering timeout handling, prompt trimming, classifier fallback, project scope propagation, and compilation-to-refinement flow.

### Changed — LLM router abstraction
- **`llm.py` router** — centralized `chat` / `embed` / `vision` helpers with consistent request payloads, token counting, and provider metadata. `vision.py`, `vectordb.py`, and `transcribe.py` route Ollama calls through it while keeping existing module names and fallback hooks intact.
- **Model config** — `config.py` exposes `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`, and `OLLAMA_VISION_MODEL` (legacy `EMBED_MODEL` / `VISION_MODEL` aliases preserved).
- **Router coverage** — `tests/test_llm_router.py` adds schema, json-mode, token-clamping, and default-model checks.

## v0.4.1 (2026-05-27) — API Scope Token Auth

> **Security release.** All `/api/*` endpoints now require a Bearer token with the proper scope. See [API Authentication](README.md#api-authentication) for the bootstrap SOP.

### New Features
- **`arkiv_token.py` CLI** — `create` / `list` / `show` / `revoke` subcommands manage access tokens directly in local SQLite, with scope validation, CIDR allowlists, and optional expiry.
- **`tests/conftest.py` auth fixture** — `fastapi_client` now injects an admin bearer token so existing server tests continue to hit the real authenticated API without per-test boilerplate.
- **Auth coverage** — `tests/test_auth.py` adds CLI create/list/revoke, CIDR allowlist, and multi-scope token cases.

### Notes
- Scope-based tokens are designed for multi-machine fleets: read-only review stations can use `videos_read` or `media_read`, ingest boxes can use `ingest_write`, and admin boxes can manage tokens.
- Bootstrap remains first-run only: set `ARKIV_ADMIN_BOOTSTRAP_TOKEN`, start the server once, create per-machine tokens, then unset the env var and revoke the bootstrap token.

## v0.4.0 (2026-05-27) — W3 DIT three-pack complete

> **DIT companion ready.** Three new CLI tools bring arkiv from "AI metadata layer" to "DIT-grade companion": hash manifests interoperable with Silverstack / Hedge / MediaVerify, multi-destination resumable offload, and Resolve-friendly camera reports.

### New Features
- **ASC MHL v2 generation + verify** — `mhl.py create` / `mhl.py verify` CLI emits `urn:ASC:MHL:v2.0` manifests with `xxh3` / `md5` / `sha1` / `sha256` / `c4` hashing, directory + structure root hashes, and a chained `ascmhl_chain.xml`. Output lands at `<PROJECT_ROOT>/ascmhl/NNNN_<dir>_<timestamp>Z.mhl`. Round-trips through ASC reference impl 1.2 (`ascmhl info` reads emitted manifests, exit 0).
- **Multi-destination offload** — `offload.py --src <SD> --dst <dst1> --dst <dst2>` does chunked-read parallel copy with per-file hash verify, retry up to N times on hash mismatch, atomic `.partial → final` rename, sidecar-aware (Sony XAVC / ARRI / RED / iPhone Live Photo / generic). JSON state file at `offload-state.json` lets you kill mid-copy and resume — pending files pick up exactly where they stopped. Emits a per-dst MHL v2 manifest verifying every copied file (via `mhl.create_manifest`).
- **Camera report CSV + day summary** — `camera_report.py` writes a 20-column DIT-spec CSV (Reel / TC / Date / Camera / Lens / ISO / Shutter / Aperture / WB / FPS / Codec / Resolution / FileSize / Notes / ...) ready for Resolve's `File → Import Metadata from CSV`. Day-summary footer aggregates clip count / total runtime / by-camera / by-card.

### Internals
- `mhl.py` 723 lines, real ASC MHL v2 schema with `creatorinfo` (incl. `hostname`), `processinfo.process` enum (ingest/offload/verify), per-file `<hash>` + per-directory `<directoryhash>` with `<content>` + `<structure>` separation, `<roothash>` aggregate. `xxhash>=2.0.0` required for `xxh3` / `xxh64`.
- `offload.py` 416 lines + 5 pytest scenarios (2-dst copy, hash-mismatch retry, mid-copy resume, src-unmount cleanup, sidecar families).
- `camera_report.py` 501 lines + 2 pytest scenarios.
- `health.py` gains `_check_mount(path)` helper extracted from `project_health()` — single mount-precondition check reused by offload + ingest preflight.

### Driving incident
W3 milestone (per `references/plans/arkiv/2026-04-29-positioning-and-moat.md` §6): pre-W3, arkiv could only claim "AI metadata layer for DIT workflows" (soft). W3 ship locks in "DIT companion" (hard) — minimum bar to not get fact-checked on r/DaVinciResolve / r/cinematography.

---

## v0.3.1 (2026-05-25) — Per-Project Storage + Startup Preflight

> **⚠️ Breaking change** — default storage layout moves from `BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/}` to `BASE_DIR/.arkiv/{project.db, ...}`. One-shot migration provided. See [Upgrade SOP](docs/pipeline.md#upgrading-from-v030) before running.

### New Features
- **Phase 8.0c per-project storage** — `config.PROJECT_ROOT` is now the single source of truth. All 4 storage paths (DB, thumbnails, chroma, proxies) default to `PROJECT_ROOT/.arkiv/<xxx>`. Explicit `ARKIV_<X>_PATH` env vars still override per-path. Setting `ARKIV_PROJECT_ROOT=/path/to/your/footage/` produces a self-contained, portable archive next to the media.
- **Phase 8.0e startup health check** — new `health.preflight_paths()` runs before any pipeline work. Catches: dangling symlinks, unwritable storage, NAS mount precondition (`/Volumes/` paths), stale `PROJECT_ROOT` (sample DB resolve fails). Returns `(ok, errors)` for embedding in other entry points. `ingest.py main()` calls it pre-warm-up; fail → `sys.exit(4)`.
- **Phase 8.0f NAS-unavailable degradation** — preflight short-circuits with explicit "NAS not mounted" message + expected path when `PROJECT_ROOT` lives on `/Volumes/` but mount root is gone. Avoids the "process N files with the same root error" failure mode.
- **`--migrate-storage`** — one-shot migration from legacy `BASE_DIR/xxx` to new `BASE_DIR/.arkiv/xxx` layout. Backup-first (`.legacy-backup-{ts}.tar.gz`), idempotent, cross-checks `sqlite COUNT` + thumbnails file count post-move, cleans dangling symlinks left over from pre-8.0c workarounds.
- **`docs/pipeline.md`** + **`docs/pipeline.zh-TW.md`** — complete pipeline reference (4 stages, paths, exit codes, maintenance modes, upgrade SOP) in EN + zh-TW.

### Bug Fixes — Silent failure suite
- **Exit code reflects fail state** — `ingest.py main()` now exits non-zero on any failure: `2` if all files failed, `1` on partial fail, `3` on dangling symlink, `4` on preflight fail. Before this, `main()` always exited 0 even with 222/222 file failures, hiding regressions from launchd/cron runners.
- **`frames.py` dangling symlink defense** — new `_ensure_thumbnails_dir()` helper fails fast (exit 3) instead of letting `Path.mkdir(exist_ok=True)` raise `FileExistsError` on every file. Last line of defense behind preflight.
- **`frames.py` strict ffmpeg success check** — new `_run_ffmpeg()` enforces both `returncode == 0` AND non-zero-size output file. Catches "ffmpeg exits 0 but writes 0-byte file" edge case that would otherwise register empty frames as valid.
- **Phase 2 honest skip messages** — when vision phase starts with empty queue, message now distinguishes "phase 1 had X/Y failures" vs "all already indexed" vs "genuinely no new files". Replaces single misleading "No new files to run vision on".
- **Phase 2 halt-on-3-consecutive-fail** — symmetric with existing `still_failed` halt for both-models-fail. Avoids burning through every remaining file writing the same Ollama-disconnect / model-crash error.

### Bug Fixes — Other
- **`--dir` argparse fix** — maintenance modes (`--migrate-storage` / `--migrate-relative` / `--regenerate-proxies` / `--vision-only`) no longer require `--dir`. Long-standing usability bug (running `ingest.py --migrate-relative` previously failed with argparse error).

### Internals
- `health.preflight_paths()` returns `(bool, list[str])` for programmatic embedding (server startup, smoke tests, CI gates).
- Migration backup format = single `.tar.gz` per timestamp; rollback is `rm -rf .arkiv/ && tar xzf <backup>`.

### Documentation
- `docs/pipeline.md` + `docs/pipeline.zh-TW.md` ship with full 4-stage flow, storage layout diagram, exit code table, maintenance mode reference, upgrade SOP with rollback, and per-project layout example.
- README (EN + zh-TW) Architecture section links to the new pipeline doc + adds "Upgrading from v0.3.0 → v0.3.1" mini section.

### Driving incident
Codified after 2026-05-25 overnight ingest failure: 222 video files, 222/222 phase-1 fail on dangling thumbnails symlink (5/15 NAS workaround for `server.py:70` hardcode, since fixed but symlink never cleaned), exit code 0 hid the regression. Root-cause-of-root-cause was Phase 8.0c never wiring config defaults despite roadmap BEFORE/AFTER table specifying it.

---

## v0.3.0 (2026-05-22) — DIT Companion

### New Features
- **DaVinci Resolve metadata CSV export** — `/api/export/metadata-csv` endpoint + toolbar button + plugin auto-prompt. Drop-in for Resolve's `File → Import Metadata from CSV` workflow (Phase 7.6b/c/d/e)
- **ExifTool full integration** — Sony XAVC sidecar `.XML` parsing, iPhone Keys group fallback, Blackmagic Cam app per-vendor lens tags (`Blackmagic-designCameraLensType`). LensModel chain: ExifTool LensModel → BMD vendor tag
- **ExifTool auto-detect** — `config._detect_exiftool()` fallback chain (env var → `shutil.which` → Windows winget LOCALAPPDATA / Program Files / chocolatey / scoop / macOS homebrew / Linux apt + `~/.local/bin` + Strawberry Perl). Solves silent skip on fresh Windows clone
- **HEVC/ProRes browser proxy** — `/api/proxy/build/{id}` POST endpoint + 409 surface in frontend + "build proxy" button (Phase 7.7g)
- **Tauri panic hook** — surfaces Rust crashes to stderr (Windows dialog crash diagnosis)

### Bug Fixes
- **EDL reel name (B10)** — was `stem[:8]` unconditionally. Now uses ExifTool ReelName when present, falls back to filename stem; sanitizes control chars; pads/truncates to 8-char CMX3600 spec
- **EDL reel injection hotfix (B10-hotfix)** — control char (`\r\n` etc.) stripped before encode; whitespace-only reel falls back to stem (Codex audit)
- **Tauri dialog crash (Windows)** — `rfd` folder picker crash workaround: drop `title` arg
- **Vision Ollama timeout** — bump 120s → 300s for large prompts
- **mlx-whisper backend** — drop unsupported `beam_size` kwarg

### Security
- Codex Round 1+2 audit: 5 SSRF / path-bound hardenings (Batch J), export-to dest allowlist (blocks `~/.ssh` / LaunchAgents RCE), CSV formula injection prevention, XSS hardening, vectordb `build_doc_text` production schema alignment

### Internals
- Cross-platform `detect_gpu()` for `bench_ingest.json`
- `arkiv_resolve.py` honors `ARKIV_API` / `ARKIV_HOST` / `ARKIV_PORT` env
- 6+ new tests (ExifTool fallback chain + reel name regressions + CSV scope + BMD lens + ExifTool sidecar)

### Known Issues
- Resolve conform pathname pattern `*/%R/%D` mis-parses paths like `iphone 16pro` as reel — workaround: set conform pattern to filename-based, or use FCPXML import (B10c-resolve, open)

---

## v0.2.1 (2026-04-12)

### Performance
- **Vision O2+O6 代表幀策略** — 每支影片只對代表幀做完整 12 欄分析，其餘用 LIGHT_PROMPT 11 欄 + 只繼承 edit_reason。未來 ingest 預估省 50% vision 時間
- **廢幀過濾（O6）** — PIL+numpy 偵測全黑/全白/嚴重模糊幀，跳過 LLM 推理
- **SQLite WAL mode** — 啟用 Write-Ahead Logging，允許讀寫併發

### Bug Fixes
- **DB self-deadlock** — `_run_vision_only` 內巢狀 `get_conn()` 導致自鎖。`add_tag`/`delete_frames`/`upsert_frame` 新增 `_conn` 參數，所有 ingest 呼叫改用同一 connection
- **sqlite3.Row immutable** — `--vision-only` 模式傳入 Row 物件給需要可寫 dict 的函式，改為 `dict()` 轉換
- **Vision 冷啟動失敗** — 新增 `_warm_up_vision_model()` 發送 dummy request 確認模型已載入 VRAM

### Tests
- 新增 4 測試：`_conn` 參數 ×3 + vision-only 整合流程 ×1（39 → 43 tests）

### Data-Driven Insight
- 用 427 支 / 1,844 幀實測驗證 O2 繼承假設：content_type 一致率僅 23%、atmosphere 11%、edit_reason 3%。原始 5 欄繼承方案推翻，改為只繼承 edit_reason（D+ 方案）

---

## v0.2.0 (2026-04-09)

### New Features
- **WhisperX 整合** — word-level timestamps + `words_json` / `remotion-props` 支援
- **Phase 7.6 Tag→Keyword 映射** — 標籤寫入 DaVinci Keywords + Comments，支援 Smart Bin 篩選
- **Phase 7.7 Browser Proxy** — HEVC/ProRes .mov 透過 FFmpeg 產生 H.264 代理，瀏覽器直接播放
- **Vision 錯誤處理強化** — 兩階段 fallback（主模型 → 備援模型）+ 前端 Retry UI
- **Silero VAD 前處理** — 語音活動偵測，提升轉錄品質
- **FCPXML 匯出** — Final Cut Pro / DaVinci Resolve 時間線匯出
- **Clip Color 分級** — 匯入時依評級自動上色（GOOD=Green, NG=Orange, Review=Yellow）
- **Ingest 兩階段 Pipeline** — Phase 1 轉錄 + Phase 2 視覺分析，分離 VRAM 使用
- **pytest 測試框架** — 23 tests 覆蓋 DB CRUD、API endpoints、轉錄 guard、向量搜尋

### Localization
- **繁體中文化** — Web UI、DaVinci Plugin、API 錯誤訊息全面中文化

### Improvements
- EDL reel name 改用檔名 stem
- Markers 改為 clip marker（非 timeline marker）
- Health check 動態讀取 vision model
- Docker Compose 移除廢棄 version 欄位
- LLaVA retry on empty/garbage response + dynamic ingest timeout

### Bug Fixes
- FCPXML spec compliance 修正
- NG clip color 改為 Orange（Red 在 DaVinci 不可見）
- Auto/manual tag legend 改用 inline color（替代 Tailwind class）
- Stream endpoint 使用 ROOT 路徑
- XSS / CORS / path validation 安全修復

### Breaking Changes
- 移除 EDL+ markers export（FPS 限制 + 不支援 CJK）

---

## v0.1.0 (2026-03-31)

Initial release — Local Media Asset Manager MVP
- Ingest pipeline (FFprobe + Whisper + LLaVA vision)
- Web UI with grid/list view, rating, tagging
- DaVinci Resolve plugin
- SRT/VTT/EDL export
- ChromaDB semantic search
- Tauri desktop app
- Docker deployment
