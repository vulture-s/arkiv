// Second- and third-tier homepage features as cards. Each `art` is a small SVG
// drawing of the UI idea (viewBox 0 0 160 90, stroked in currentColor; `.a`
// marks the cyan accent). Facts and entry points were checked against the code
// on 2026-10-05 — see the commit message that introduced the tiers.
const T = (x, y, s, cls = '', extra = '') => `<text x="${x}" y="${y}" class="t ${cls}" ${extra}>${s}</text>`;
// Motion (2026-10-22): every card has one moving idea, played only while on screen
// (.mini-art.on) and shown in its finished state under prefers-reduced-motion.
// D(s) staggers an element inside the card's 6 s loop; classes live in features.css
// (m-in fade, m-pop scale, m-draw stroke, m-move slide from --dx/--dy, m-fillx grow,
// m-h1/m-h2 alternate halves, m-pan look-around). Cyan (.a) marks only what arkiv
// produces — the corrected word, the cut range, the result — never decoration.
const D = (s, more = '') => `style="--d:${s}s${more ? ';' + more : ''}"`;

export const MORE = [
  { group: '轉錄', items: [
    { t: '校正字典與熱詞', d: '專有名詞先餵給 Whisper，轉完再批次修正；先預覽、可還原。', w: '設定 → 進階',
      art: `<rect x="14" y="20" width="56" height="20" rx="4"/>${T(22,34,'arciv','x')}<path d="M76 30h14" class="a m-draw" pathLength="1" ${D(.2)}/><path d="M86 26l4 4-4 4" class="a m-in" ${D(.4)}/><g class="m-pop" ${D(.5)}><rect x="94" y="20" width="52" height="20" rx="4" class="a"/>${T(102,34,'arkiv','a')}</g><rect x="14" y="52" width="56" height="20" rx="4"/>${T(22,66,'達芬記','x')}<path d="M76 62h14" class="a m-draw" pathLength="1" ${D(1)}/><path d="M86 58l4 4-4 4" class="a m-in" ${D(1.2)}/><g class="m-pop" ${D(1.3)}><rect x="94" y="52" width="52" height="20" rx="4" class="a"/>${T(102,66,'達文西','a')}</g>` },
    { t: '字幕顯示時間規則', d: '最短／最長顯示時間、句間空隙、每秒字數，匯出自動套用；違規率 10.2% → 1.7%。', w: '自動',
      art: `<path d="M10 66h140"/><path d="M10 62v8M150 62v8"/><rect x="14" y="40" width="34" height="14" rx="2"/><rect x="54" y="40" width="44" height="14" rx="2" class="a m-move" ${D(.2,'--dx:-8px;--dy:0px')}/><rect x="104" y="40" width="40" height="14" rx="2"/><path d="M48 32v6M54 32v6" class="a m-in" ${D(1.1)}/>${T(40,26,'gap','a m-in',D(1.1))}${T(58,82,'≥0.8s','m-in',D(1.3))}` },
    { t: '多語逐字稿', d: '同一支素材保留中、日、英各一份，選一份當主要的拿去搜尋和匯出。', w: 'Inspector',
      art: `<rect x="14" y="14" width="30" height="16" rx="3" class="a"/>${T(24,26,'中','a')}<rect x="48" y="14" width="30" height="16" rx="3"/>${T(58,26,'日')}<rect x="82" y="14" width="30" height="16" rx="3"/>${T(88,26,'EN')}<path d="M14 30h132"/><path d="M20 46h110" class="m-draw" pathLength="1" ${D(.3)}/><path d="M20 58h92" class="m-draw" pathLength="1" ${D(.7)}/><path d="M20 70h120" class="m-draw" pathLength="1" ${D(1.1)}/>` },
    { t: 'YouTube 章節', d: '依場景時間軸產生章節清單，貼到影片說明就能用。', w: 'Inspector「YT 章節」',
      art: `<rect x="12" y="16" width="44" height="30" rx="6"/><path d="M29 24v14l12-7z"/>${T(66,24,'00:00','a m-in',D(.3))}${T(100,24,'開場','m-in',D(.3))}${T(66,42,'00:18','a m-in',D(.7))}${T(100,42,'桌面','m-in',D(.7))}${T(66,60,'01:12','a m-in',D(1.1))}${T(100,60,'特寫','m-in',D(1.1))}${T(66,78,'02:05','a m-in',D(1.5))}${T(100,78,'收尾','m-in',D(1.5))}` },
  ]},
  { group: '拍攝現場', items: [
    { t: '轉存自動歸檔', d: '依 {date}/{camera}/{reel} 自動建資料夾，先預覽再拷。', w: 'DIT 介面・指令列',
      art: `<g class="m-in" ${D(.2)}><path d="M16 18h24l6 6h20v14H16z"/>${T(20,33,'0817')}</g><g class="m-in" ${D(.6)}><path d="M30 40v12h16"/><path d="M46 46h22l5 5h17v12H46z"/>${T(50,60,'A7V')}</g><g class="m-in" ${D(1)}><path d="M62 64v10h16"/><path d="M78 68h20l5 5h17v12H78z" class="a"/>${T(82,82,'A001','a')}</g>` },
    { t: '插卡自動轉存', d: '記憶卡一插上就開始拷，已經插著的卡不會重拷。', w: '指令列', tag: 'cli',
      art: `<path d="M18 22h24l8 8v40H18z"/><path d="M24 22v8M30 22v8M36 22v8"/><path d="M58 46h30" class="a m-draw" pathLength="1" ${D(.2)}/><path d="M82 40l6 6-6 6" class="a m-in" ${D(.5)}/><rect x="96" y="28" width="50" height="36" rx="4"/><path d="M104 54h34" class="a m-draw" pathLength="1" ${D(.6)}/><circle cx="138" cy="38" r="3" class="a m-pop" ${D(1.4)}/>` },
    { t: '攝影日報', d: '20 欄 DIT 規格的 CSV：Reel、Scene、Take、TC、機型、鏡頭、ISO……', w: '指令列', tag: 'cli',
      art: `<rect x="12" y="12" width="136" height="66" rx="4"/><path d="M12 28h136M12 44h136M12 60h136M46 12v66M80 12v66M114 12v66"/>${T(16,24,'Reel')}${T(50,24,'Take')}${T(84,24,'TC')}${T(118,24,'ISO')}<g class="m-in" ${D(.3)}>${T(16,40,'A001','a')}${T(50,40,'T3','a')}${T(84,40,'10:02','a')}${T(118,40,'800','a')}</g><g class="m-in" ${D(.7)}>${T(16,56,'A001','a')}${T(50,56,'T4','a')}${T(84,56,'10:05','a')}${T(118,56,'800','a')}</g><g class="m-in" ${D(1.1)}>${T(16,72,'A002','a')}${T(50,72,'T1','a')}${T(84,72,'11:40','a')}${T(118,72,'1600','a')}</g>` },
    { t: '監看資料夾', d: '新檔案拷進指定資料夾、大小穩定後就自動匯入。', w: '指令列', tag: 'cli',
      art: `<path d="M20 26h34l8 8h56v38H20z"/><path d="M40 40h12l4 4v14H40z" class="m-move" ${D(.2,'--dx:0px;--dy:-30px')}/><g class="m-pop" ${D(.9)}><circle cx="90" cy="50" r="10" class="a"/><path d="M90 44v6l5 3" class="a"/></g>${T(64,86,'已匯入','a m-in',D(1.5))}` },
  ]},
  { group: '素材管理', items: [
    { t: '素材庫助手', d: '用口語問，分辨找素材、續篩、找相似還是看統計；記得前 10 輪，結果點了回到素材。', w: '「CHAT」',
      art: `<g class="m-in" ${D(.2)}><rect x="12" y="12" width="92" height="22" rx="10"/>${T(22,27,'上週的手部特寫？')}</g><g class="m-pop" ${D(.9)}><rect x="56" y="42" width="92" height="36" rx="10" class="a"/><rect x="64" y="50" width="22" height="20" rx="2" class="a"/><rect x="90" y="50" width="22" height="20" rx="2" class="a"/><rect x="116" y="50" width="22" height="20" rx="2" class="a"/></g>` },
    { t: '精選集', d: '把散在各處的素材收成一組，可以整組複製成新專案。', w: '★・跨專案需 Pro',
      art: `<rect x="12" y="12" width="28" height="18" rx="2"/>${T(46,25,'婚禮')}<rect x="12" y="36" width="28" height="18" rx="2"/>${T(46,49,'九月')}<rect x="12" y="60" width="28" height="18" rx="2"/>${T(46,73,'日常')}<rect x="100" y="16" width="48" height="60" rx="4" class="a"/>${T(106,29,'★ 精選','a')}<path d="M70 21L98 40" class="a m-draw" pathLength="1" ${D(.2)}/><path d="M70 45L98 52" class="a m-draw" pathLength="1" ${D(.6)}/><path d="M70 69L98 64" class="a m-draw" pathLength="1" ${D(1)}/><rect x="106" y="36" width="36" height="8" rx="2" class="a m-in" ${D(.5)}/><rect x="106" y="48" width="36" height="8" rx="2" class="a m-in" ${D(.9)}/><rect x="106" y="60" width="36" height="8" rx="2" class="a m-in" ${D(1.3)}/>` },
    { t: '跨專案搜尋', d: '一次搜所有專案，結果依專案分組。', w: 'Pro', tag: 'pro',
      art: `<rect x="12" y="8" width="136" height="16" rx="8"/>${T(22,20,'戶外 黃昏')}<circle cx="134" cy="15" r="4"/><path d="M137 18l3 3"/><g class="m-in" ${D(.4)}>${T(14,43,'婚禮案')}<rect x="70" y="34" width="18" height="12" rx="1" class="a"/><rect x="92" y="34" width="18" height="12" rx="1" class="a"/><rect x="114" y="34" width="18" height="12" rx="1" class="a"/></g><g class="m-in" ${D(.8)}>${T(14,63,'九月promo')}<rect x="70" y="54" width="18" height="12" rx="1" class="a"/><rect x="92" y="54" width="18" height="12" rx="1" class="a"/></g><g class="m-in" ${D(1.2)}>${T(14,83,'眼鏡素材')}<rect x="70" y="74" width="18" height="12" rx="1" class="a"/></g>` },
    { t: '多個素材庫', d: '工作案、生活紀錄各開一個庫；側欄點一下就切換，下次打開回到上次那個。有匯入在跑時不讓切，免得做到一半被中斷。', w: '側欄・桌面 App',
      art: `<rect x="10" y="12" width="64" height="16" rx="3"/>${T(16,24,'內建素材庫')}<rect x="10" y="34" width="64" height="16" rx="3"/>${T(16,46,'眼鏡素材')}<rect x="10" y="56" width="64" height="16" rx="3" class="dash"/>${T(16,68,'＋ 新增')}<rect x="10" y="12" width="64" height="16" rx="3" class="a m-h1"/><rect x="10" y="34" width="64" height="16" rx="3" class="a m-h2"/>${T(88,16,'內建素材庫','a m-h1')}${T(88,16,'眼鏡素材','a m-h2')}<rect x="88" y="24" width="28" height="22" rx="2"/><rect x="120" y="24" width="28" height="22" rx="2"/><rect x="88" y="50" width="28" height="22" rx="2"/><rect x="120" y="50" width="28" height="22" rx="2"/>` },
    { t: '拖進剪輯軟體', d: '搜尋結果直接拖進 Resolve、Premiere 或 Finder，拖的是原始檔本身；標了 IN／OUT 就拖那一段，剛好從 IN 開始。', w: '卡片・Inspector・桌面 App',
      art: `<rect x="10" y="12" width="58" height="36" rx="3"/>${T(10,60,'A7520260818','sm')}<path d="M10 74h58"/><path d="M24 70v8M50 70v8" class="a"/><rect x="92" y="12" width="58" height="68" rx="3"/>${T(96,24,'Media Pool','sm')}<rect x="98" y="32" width="22" height="14" rx="2"/><rect x="124" y="32" width="22" height="14" rx="2" class="a fill-soft m-move" ${D(.3,'--dx:-100px;--dy:37px')}/><rect x="98" y="52" width="22" height="14" rx="2"/>` },
    { t: '360 素材', d: '雙魚眼 .insv／.360 攤平成全景，在 Inspector 裡拖曳環視。', w: 'Inspector',
      art: `<circle cx="26" cy="30" r="14"/><circle cx="26" cy="62" r="14"/><path d="M48 46h14" /><path d="M58 42l4 4-4 4"/><rect x="70" y="24" width="78" height="44" rx="3"/><path d="M70 46h78M90 24v44M110 24v44M130 24v44"/><g class="m-pan"><circle cx="110" cy="46" r="5" class="a"/><path d="M98 46h-6M122 46h6" class="a"/></g>` },
  ]},
  { group: '格式與進階', items: [
    { t: 'Log 素材還原', d: 'S-Log3、D-Log、C-Log、N-Log、F-Log、Apple Log、V-Log 自動換算成 Rec.709；播放時一鍵切回原始 log，也能匯入自己的 .cube。', w: 'Inspector「COLOR」',
      art: `<rect x="12" y="22" width="52" height="40" rx="3"/><path d="M12 54l14-12 10 8 12-14 16 18"/>${T(18,78,'LOG')}<path d="M72 42h16" class="a m-draw" pathLength="1" ${D(.3)}/><path d="M84 38l4 4-4 4" class="a m-in" ${D(.6)}/><g class="m-in" ${D(.8)}><rect x="96" y="22" width="52" height="40" rx="3" class="a"/><path d="M96 54l14-12 10 8 12-14 16 18" class="a"/>${T(102,78,'709','a')}</g>` },
    { t: '影片、音訊、圖片同一個庫', d: 'mp4／mov／mts／mxf、wav／mp3／flac、jpg／png／webp；瀏覽器播不了的自動產生 proxy。', w: '自動',
      art: `<rect x="12" y="24" width="40" height="42" rx="3"/><path d="M12 32h40M12 58h40M18 24v8M26 24v8M34 24v8M42 24v8M18 58v8M26 58v8M34 58v8M42 58v8"/>${T(16,80,'proxy','a m-in',D(.9))}<path d="M62 45h4M70 36v18M74 30v30M78 38v14M82 32v26M86 40v10M90 34v22M94 42v6" class="m-in" ${D(.3)}/><rect x="106" y="24" width="42" height="42" rx="3" class="m-in" ${D(.5)}/><circle cx="118" cy="36" r="4" class="m-in" ${D(.5)}/><path d="M106 60l14-14 10 10 6-6 12 12" class="m-in" ${D(.5)}/>` },
    { t: '內建範例庫', d: '空的素材庫一鍵載入 4 支範例短片，馬上能試搜尋。', w: '空白素材庫畫面',
      art: `<g class="m-pop" ${D(.2)}><rect x="14" y="10" width="62" height="32" rx="3"/><path d="M40 20v10l8-5z" class="a"/>${T(18,38,'wing_it','sm')}</g><g class="m-pop" ${D(.4)}><rect x="84" y="10" width="62" height="32" rx="3"/>${T(88,38,'coffee_run','sm')}</g><g class="m-pop" ${D(.6)}><rect x="14" y="50" width="62" height="32" rx="3"/>${T(18,78,'glass_half','sm')}</g><g class="m-pop" ${D(.8)}><rect x="84" y="50" width="62" height="32" rx="3"/>${T(88,78,'llama','sm')}</g>` },
    { t: 'HTTP API 與 MCP', d: '分權限 token：12 種權限、IP 白名單、到期時間；AI 助手也能透過 MCP 查。', w: '指令列・API', tag: 'cli',
      art: `${T(14,56,'{','big')}${T(132,56,'}','big')}<g class="m-move" ${D(.3,'--dx:-24px;--dy:0px')}><circle cx="64" cy="45" r="10" class="a"/><path d="M74 45h34M98 45v8M106 45v6" class="a"/></g>` },
    { t: 'NAS 共享向量庫', d: '多台電腦共用同一份搜尋索引（Postgres + pgvector）。', w: '環境變數', tag: 'cli',
      art: `<rect x="8" y="10" width="52" height="16" rx="3"/>${T(13,22,'MacBook')}<rect x="8" y="37" width="52" height="16" rx="3"/>${T(13,49,'PC')}<rect x="8" y="64" width="52" height="16" rx="3"/>${T(13,76,'mini')}<path d="M60 18L94 36" class="a m-draw" pathLength="1" ${D(.2)}/><path d="M60 45H94" class="a m-draw" pathLength="1" ${D(.5)}/><path d="M60 72L94 56" class="a m-draw" pathLength="1" ${D(.8)}/><ellipse cx="120" cy="30" rx="24" ry="7" class="a"/><path d="M96 30v30c0 4 11 7 24 7s24-3 24-7V30" class="a"/>${T(107,52,'index','a')}` },
  ]},
];

export const NEXT = [
  { t: '多語轉錄改進', d: '同一句中英夾雜的訪談（評估中），以及日文特有的幻覺句與重複清理。',
    art: `<path d="M12 45h4M20 36v18M24 30v30M28 38v14M32 32v26M36 40v10M40 34v22"/><path d="M50 45h14" class="a m-draw" pathLength="1" ${D(.2)}/><path d="M60 41l4 4-4 4" class="a m-in" ${D(.4)}/>${T(74,36,'這個 shot','m-in',D(.6))}${T(74,56,'要 re-take','m-in',D(.9))}${T(74,76,'もう一度','a m-in',D(1.2))}` },
  { t: '跨案分析', d: '統計產品在畫面裡出現的頻率與時長、比對多支影片的拍攝風格。',
    art: `${T(12,24,'產品 A')}<rect x="52" y="16" width="60" height="10" rx="2" class="a fill-soft m-fillx" ${D(.2)}/>${T(116,24,'31s','m-in',D(.8))}${T(12,48,'產品 B')}<rect x="52" y="40" width="34" height="10" rx="2" class="a fill-soft m-fillx" ${D(.4)}/>${T(90,48,'12s','m-in',D(1))}${T(12,72,'產品 C')}<rect x="52" y="64" width="78" height="10" rx="2" class="a fill-soft m-fillx" ${D(.6)}/>${T(134,72,'48s','m-in',D(1.2))}` },
];
