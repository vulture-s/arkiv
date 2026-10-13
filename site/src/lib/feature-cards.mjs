// Second- and third-tier homepage features as cards. Each `art` is a small SVG
// drawing of the UI idea (viewBox 0 0 160 90, stroked in currentColor; `.a`
// marks the cyan accent). Facts and entry points were checked against the code
// on 2026-10-05 — see the commit message that introduced the tiers.
const T = (x, y, s, cls = '') => `<text x="${x}" y="${y}" class="t ${cls}">${s}</text>`;

export const MORE = [
  { group: '轉錄', items: [
    { t: '校正字典與熱詞', d: '專有名詞先餵給 Whisper，轉完再批次修正；先預覽、可還原。', w: '設定 → 進階',
      art: `<rect x="14" y="20" width="56" height="20" rx="4"/>${T(22,34,'arciv','x')}<path d="M76 30h14" class="a"/><path d="M86 26l4 4-4 4" class="a"/><rect x="94" y="20" width="52" height="20" rx="4" class="a"/>${T(102,34,'arkiv','a')}<rect x="14" y="52" width="56" height="20" rx="4"/>${T(22,66,'達芬記','x')}<path d="M76 62h14" class="a"/><path d="M86 58l4 4-4 4" class="a"/><rect x="94" y="52" width="52" height="20" rx="4" class="a"/>${T(102,66,'達文西','a')}` },
    { t: '字幕顯示時間規則', d: '最短／最長顯示時間、句間空隙、每秒字數，匯出自動套用；違規率 10.2% → 1.7%。', w: '自動',
      art: `<path d="M10 66h140"/><path d="M10 62v8M150 62v8"/><rect x="14" y="40" width="34" height="14" rx="2"/><rect x="54" y="40" width="44" height="14" rx="2" class="a"/><rect x="104" y="40" width="40" height="14" rx="2"/><path d="M48 32v6M54 32v6" class="a"/>${T(40,26,'gap','a')}${T(58,82,'≥0.8s','')}` },
    { t: '多語逐字稿', d: '同一支素材保留中、日、英各一份，選一份當主要的拿去搜尋和匯出。', w: 'Inspector',
      art: `<rect x="14" y="14" width="30" height="16" rx="3" class="a"/>${T(24,26,'中','a')}<rect x="48" y="14" width="30" height="16" rx="3"/>${T(58,26,'日')}<rect x="82" y="14" width="30" height="16" rx="3"/>${T(88,26,'EN')}<path d="M14 30h132"/><path d="M20 46h110M20 58h92M20 70h120"/>` },
    { t: 'YouTube 章節', d: '依場景時間軸產生章節清單，貼到影片說明就能用。', w: 'Inspector「YT 章節」',
      art: `<rect x="12" y="16" width="44" height="30" rx="6"/><path d="M29 24v14l12-7z" class="a"/>${T(66,24,'00:00 開場')}${T(66,42,'00:18 桌面')}${T(66,60,'01:12 特寫')}${T(66,78,'02:05 收尾')}` },
  ]},
  { group: '拍攝現場', items: [
    { t: '轉存自動歸檔', d: '依 {date}/{camera}/{reel} 自動建資料夾，先預覽再拷。', w: 'DIT 介面・指令列',
      art: `<path d="M16 18h24l6 6h20v14H16z"/>${T(20,33,'0817')}<path d="M30 40v12h16"/><path d="M46 46h22l5 5h17v12H46z"/>${T(50,60,'A7V')}<path d="M62 64v10h16"/><path d="M78 68h20l5 5h17v12H78z" class="a"/>${T(82,82,'A001','a')}` },
    { t: '插卡自動轉存', d: '記憶卡一插上就開始拷，已經插著的卡不會重拷。', w: '指令列', tag: 'cli',
      art: `<path d="M18 22h24l8 8v40H18z"/><path d="M24 22v8M30 22v8M36 22v8"/><path d="M58 46h30" class="a"/><path d="M82 40l6 6-6 6" class="a"/><rect x="96" y="28" width="50" height="36" rx="4"/><path d="M104 54h34" class="a"/><circle cx="138" cy="38" r="3" class="a"/>` },
    { t: '攝影日報', d: '20 欄 DIT 規格的 CSV：Reel、Scene、Take、TC、機型、鏡頭、ISO……', w: '指令列', tag: 'cli',
      art: `<rect x="12" y="12" width="136" height="66" rx="4"/><path d="M12 28h136M12 44h136M12 60h136M46 12v66M80 12v66M114 12v66"/>${T(16,24,'Reel','a')}${T(50,24,'Take','a')}${T(84,24,'TC','a')}${T(118,24,'ISO','a')}` },
    { t: '監看資料夾', d: '新檔案拷進指定資料夾、大小穩定後就自動匯入。', w: '指令列', tag: 'cli',
      art: `<path d="M20 26h34l8 8h56v38H20z"/><circle cx="80" cy="52" r="12" class="a"/><path d="M80 44v8l6 4" class="a"/><path d="M124 18a14 14 0 0 1 14 14" class="a"/><path d="M134 30l4 2 2-4" class="a"/>` },
  ]},
  { group: '素材管理', items: [
    { t: '素材庫助手', d: '用口語問，分辨找素材、續篩、找相似還是看統計；記得前 10 輪，結果點了回到素材。', w: '「CHAT」',
      art: `<rect x="12" y="12" width="92" height="22" rx="10"/>${T(22,27,'上週的手部特寫？')}<rect x="56" y="42" width="92" height="36" rx="10" class="a"/><rect x="64" y="50" width="22" height="20" rx="2" class="a"/><rect x="90" y="50" width="22" height="20" rx="2" class="a"/><rect x="116" y="50" width="22" height="20" rx="2" class="a"/>` },
    { t: '精選集', d: '把散在各處的素材收成一組，可以整組複製成新專案。', w: '★・跨專案需 Pro',
      art: `<rect x="40" y="30" width="46" height="34" rx="3"/><rect x="50" y="22" width="46" height="34" rx="3"/><rect x="60" y="14" width="46" height="34" rx="3"/><path d="M122 30l4 9 10 1-7 7 2 10-9-5-9 5 2-10-7-7 10-1z" class="a"/>` },
    { t: '跨專案搜尋', d: '一次搜所有專案，結果依專案分組。', w: 'Pro', tag: 'pro',
      art: `<rect x="14" y="14" width="56" height="16" rx="3"/><rect x="14" y="37" width="56" height="16" rx="3"/><rect x="14" y="60" width="56" height="16" rx="3"/><path d="M70 22h14M70 45h14M70 68h14M84 22v46"/><circle cx="114" cy="42" r="16" class="a"/><path d="M126 54l14 14" class="a"/>` },
    { t: '360 素材', d: '雙魚眼 .insv／.360 攤平成全景，在 Inspector 裡拖曳環視。', w: 'Inspector',
      art: `<circle cx="26" cy="30" r="14"/><circle cx="26" cy="62" r="14"/><path d="M48 46h14" /><path d="M58 42l4 4-4 4"/><rect x="70" y="24" width="78" height="44" rx="3"/><path d="M70 46h78M90 24v44M110 24v44M130 24v44"/><circle cx="110" cy="46" r="5" class="a"/><path d="M98 46h-6M122 46h6" class="a"/>` },
  ]},
  { group: '格式與進階', items: [
    { t: '影片、音訊、圖片同一個庫', d: 'mp4／mov／mts／mxf、wav／mp3／flac、jpg／png／webp；瀏覽器播不了的自動產生 proxy。', w: '自動',
      art: `<rect x="12" y="24" width="40" height="42" rx="3"/><path d="M12 32h40M12 58h40M18 24v8M26 24v8M34 24v8M42 24v8M18 58v8M26 58v8M34 58v8M42 58v8"/><path d="M62 45h4M70 36v18M74 30v30M78 38v14M82 32v26M86 40v10M90 34v22M94 42v6" class="a"/><rect x="106" y="24" width="42" height="42" rx="3"/><circle cx="118" cy="36" r="4"/><path d="M106 60l14-14 10 10 6-6 12 12"/>` },
    { t: '內建範例庫', d: '空的素材庫一鍵載入 4 支範例短片，馬上能試搜尋。', w: '空白素材庫畫面',
      art: `<rect x="20" y="12" width="56" height="32" rx="3"/><rect x="84" y="12" width="56" height="32" rx="3"/><rect x="20" y="50" width="56" height="32" rx="3"/><rect x="84" y="50" width="56" height="32" rx="3"/><path d="M44 22v12l10-6z" class="a"/>` },
    { t: 'HTTP API 與 MCP', d: '分權限 token：12 種權限、IP 白名單、到期時間；AI 助手也能透過 MCP 查。', w: '指令列・API', tag: 'cli',
      art: `${T(14,56,'{','big')}${T(132,56,'}','big')}<circle cx="64" cy="45" r="10" class="a"/><path d="M74 45h34M98 45v8M106 45v6" class="a"/>` },
    { t: 'NAS 共享向量庫', d: '多台電腦共用同一份搜尋索引（Postgres + pgvector）。', w: '環境變數', tag: 'cli',
      art: `<rect x="12" y="14" width="34" height="22" rx="2"/><rect x="12" y="54" width="34" height="22" rx="2"/><rect x="114" y="14" width="34" height="22" rx="2"/><ellipse cx="80" cy="34" rx="20" ry="7" class="a"/><path d="M60 34v26c0 4 9 7 20 7s20-3 20-7V34" class="a"/><path d="M46 25h14M46 65h14M100 25h14"/>` },
  ]},
];

export const NEXT = [
  { t: '拖進剪輯軟體', d: '從搜尋結果直接把素材，或 IN／OUT 那一段，拖進 Resolve 或 Finder。',
    art: `<rect x="12" y="12" width="52" height="32" rx="3"/><path d="M64 44l34 18" class="a dash"/><path d="M96 54l4 9-9 1" class="a"/><path d="M12 72h136"/><rect x="40" y="64" width="34" height="16" rx="2"/><rect x="96" y="64" width="40" height="16" rx="2" class="a dash"/>` },
  { t: '首次啟動精靈', d: '偵測硬體、選裝置、顯示模型下載進度，不用再自己照指令裝。',
    art: `<circle cx="28" cy="30" r="10" class="a"/>${T(25,34,'1','a')}<path d="M38 30h24"/><circle cx="72" cy="30" r="10"/>${T(69,34,'2')}<path d="M82 30h24"/><circle cx="116" cy="30" r="10"/>${T(113,34,'3')}<rect x="18" y="58" width="124" height="8" rx="4"/><rect x="18" y="58" width="58" height="8" rx="4" class="a fill"/>` },
  { t: '多語轉錄改進', d: '同一句中英夾雜的訪談（評估中），以及日文特有的幻覺句與重複清理。',
    art: `<path d="M12 45h4M20 36v18M24 30v30M28 38v14M32 32v26M36 40v10M40 34v22"/><path d="M50 45h14" class="a"/><path d="M60 41l4 4-4 4" class="a"/>${T(74,36,'這個 shot')}${T(74,56,'要 re-take')}${T(74,76,'もう一度','a')}` },
  { t: '跨案分析', d: '統計產品在畫面裡出現的頻率與時長、比對多支影片的拍攝風格。',
    art: `<path d="M18 78h128M18 14v64"/><rect x="30" y="50" width="16" height="28"/><rect x="56" y="34" width="16" height="44" class="a"/><rect x="82" y="58" width="16" height="20"/><rect x="108" y="24" width="16" height="54" class="a"/>` },
];
