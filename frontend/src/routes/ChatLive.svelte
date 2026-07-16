<!-- E2 — chat wired to live /api/chat. Natural-language → 5-intent classifier
     → vector search → LLM response. When the assistant returns scene_ids
     (compilation intent), resolve them to media items and show thumbnails
     inline. Requires chat_write scope (token-free on loopback). -->
<script>
  import { onMount, onDestroy, tick } from 'svelte'
  import { VERSION } from '../lib/version.js'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { resolvedTheme } from '../lib/prefs.js'
  import { pushToast } from '../lib/toast.js'

  $: theme = $resolvedTheme
  let messages = [] // {role:'user'|'assistant', text, intent?, scenes?:[{id,name,thumb}]}
  let input = ''
  let busy = false
  let abortController = null // in-flight chat request; Esc aborts it
  let convId = null
  let err = ''
  let mediaById = new Map() // id → {filename, thumb} for scene resolution
  let scroller
  let conversations = [] // [{id, title, updated_at, …}] — past chats (newest first)

  const fmtTime = (s) => (s || '').slice(5, 16).replace('T', ' ')
  const parseSceneIds = (json) => {
    if (!json) return []
    try { const v = JSON.parse(json); return Array.isArray(v) ? v : [] } catch { return [] }
  }

  async function loadConversations() {
    try {
      const r = await api.listConversations()
      conversations = r.conversations || []
    } catch (e) { /* non-fatal — history sidebar just stays empty */ }
  }

  // Restore a past conversation: rebuild the thread from stored messages,
  // resolving each assistant turn's scene_ids back to thumbnails. Sets convId
  // so the next prompt continues this conversation.
  async function openConversation(id) {
    if (busy) return
    err = ''
    try {
      const r = await api.getChatHistory(id)
      convId = (r.conversation && r.conversation.id) || id
      const rebuilt = []
      for (const m of r.messages || []) {
        if (m.role === 'user') {
          rebuilt.push({ role: 'user', text: m.content })
        } else {
          const scenes = await resolveScenes(parseSceneIds(m.scene_ids_json))
          rebuilt.push({ role: 'assistant', text: m.content || '', intent: m.intent, scenes })
        }
      }
      messages = rebuilt
      scrollDown()
    } catch (e) {
      err = e.status === 404 ? '找不到這個對話' : e.message
    }
  }

  function newChat() {
    if (busy) return
    convId = null
    messages = []
    err = ''
  }

  // Honest phrasing — chat finds candidate clips, it does NOT cut a final video.
  const suggestions = ['找生肉切割的鏡頭', '找店內空景的畫面', '哪些素材有餐廳']
  const EXPORTS = ['edl', 'fcpxml', 'srt']
  // Authenticated download — a plain <a href> can't carry the Bearer token, so
  // exports would 401 on a tokened backend (Codex review P2 pattern).
  async function exportScene(sc, fmt) {
    const stem = (sc.name || `media_${sc.id}`).replace(/\.[^.]+$/, '')
    try {
      await api.downloadFile(api.exportPath(sc.id, fmt), `${stem}.${fmt}`)
      pushToast(`已匯出 · ${stem}.${fmt}`)
    } catch (e) {
      console.error('export failed', e)
      pushToast(`匯出失敗: ${e.message}`, 'error')
    }
  }

  // Prefetch the first page as a cheap cache; misses are resolved by id below
  // (so scene_ids outside the first page still get a thumbnail — Codex review P2).
  async function loadMediaIndex() {
    try {
      const m = await api.getMedia({ limit: 200 })
      mediaById = new Map(
        (m.items || []).map((it) => [
          String(it.id),
          { filename: it.filename || it.name, thumb: it.thumbnail_path ? api.thumbUrlFromPath(it.thumbnail_path) : it.thumb },
        ])
      )
    } catch (e) {
      /* non-fatal — resolveScenes will fetch by id */
    }
  }

  // Resolve each scene id: index hit first, else fetch /api/media/{id} detail.
  // Never permanently fails for ids outside the prefetched page.
  async function resolveScenes(ids) {
    return Promise.all(
      (ids || []).map(async (id) => {
        const key = String(id)
        let m = mediaById.get(key)
        if (!m) {
          try {
            const d = await api.getMediaDetail(id)
            m = {
              filename: d.filename || d.name,
              thumb: d.thumbnail_path ? api.thumbUrlFromPath(d.thumbnail_path) : d.thumb,
            }
            mediaById.set(key, m) // cache for next time
          } catch (e) {
            m = null
          }
        }
        return { id, name: m?.filename || `#${id}`, thumb: m?.thumb || null }
      })
    )
  }

  async function scrollDown() {
    await tick()
    if (scroller) scroller.scrollTop = scroller.scrollHeight
  }

  async function send(text) {
    const prompt = (text ?? input).trim()
    if (!prompt || busy) return
    err = ''
    input = ''
    messages = [...messages, { role: 'user', text: prompt }]
    busy = true
    scrollDown()
    const wasNew = convId == null
    abortController = new AbortController()
    try {
      const r = await api.chat(prompt, convId, { signal: abortController.signal })
      convId = r.conversation_id || convId
      // a brand-new conversation now exists server-side → refresh the sidebar
      if (wasNew) loadConversations()
      const scenes = await resolveScenes(r.scene_ids)
      messages = [
        ...messages,
        {
          role: 'assistant',
          text: r.assistant_text || '',
          intent: r.intent,
          scenes,
        },
      ]
    } catch (e) {
      if (e.name === 'AbortError') {
        messages = [...messages, { role: 'assistant', text: '（已中斷）', error: true }]
      } else {
        err = e.status === 401 ? '需要 chat_write token（tailnet）— 本機 loopback 可直接用' : e.message
        messages = [...messages, { role: 'assistant', text: `⚠ ${err}`, error: true }]
      }
    } finally {
      busy = false
      abortController = null
      scrollDown()
    }
  }

  // Esc during a running request aborts it (the in-flight LLM generation).
  function abort() {
    if (abortController) abortController.abort()
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  // Global Esc → interrupt the current chat task (works anywhere in the view).
  function onGlobalKey(e) {
    if (e.key === 'Escape' && busy) { e.preventDefault(); abort() }
  }

  onMount(() => {
    loadMediaIndex()
    loadConversations()
    window.addEventListener('keydown', onGlobalKey)
  })
  onDestroy(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">{VERSION}</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">chat · 5-intent · vector + LLM</Mono>
    <a class="ak-btn" href="#/main-live">← 返回素材庫</a>
  </div>

  <div class="body">
    <aside class="convs">
      <div class="convhead">
        <Eyebrow>對話紀錄</Eyebrow>
        <button class="ak-btn newchat" on:click={newChat} disabled={busy}>＋ 新對話</button>
      </div>
      <div class="convlist">
        {#each conversations as c (c.id)}
          <button class="convitem" class:active={c.id === convId} on:click={() => openConversation(c.id)} disabled={busy}>
            <span class="convtitle">{c.title || '未命名對話'}</span>
            <Mono dim style="font-size:9px;letter-spacing:0.04em;">{fmtTime(c.updated_at)}</Mono>
          </button>
        {/each}
        {#if conversations.length === 0}
          <Mono dim style="font-size:10.5px;padding:8px 2px;display:block;">尚無紀錄</Mono>
        {/if}
      </div>
    </aside>

    <div class="chatmain">
    <div class="thread" bind:this={scroller}>
      {#if messages.length === 0}
        <div class="empty">
          <Eyebrow>arkiv chat</Eyebrow>
          <div class="ak-display emptytitle">問你的素材庫。</div>
          <Mono dim style="font-size:12px;line-height:1.6;max-width:440px;">
            用中文問。例如「幫我把生肉切割的鏡頭剪成一段」→ 自動分類意圖 + 語意搜尋 + 回答 + 列出符合的片段。
          </Mono>
          <div class="suggest">
            {#each suggestions as s}
              <button class="ak-btn sg" on:click={() => send(s)}>{s}</button>
            {/each}
          </div>
        </div>
      {/if}

      {#each messages as m}
        <div class="msg {m.role}" class:error={m.error}>
          <Mono dim style="font-size:9.5px;letter-spacing:0.1em;">
            {m.role === 'user' ? 'YOU' : 'ARKIV'}{#if m.intent} · {m.intent}{/if}
          </Mono>
          <div class="text">{m.text}</div>
          {#if m.scenes && m.scenes.length}
            <Mono dim style="font-size:9px;letter-spacing:0.1em;margin-top:4px;display:block;">候選素材 · {m.scenes.length} 段 · 可導出 EDL/FCPXML/SRT 到 Resolve 剪</Mono>
            <div class="scenes">
              {#each m.scenes as sc (sc.id)}
                <div class="scene">
                  <a class="scenelink" href={`#/main-live?ids=${m.scenes.map((s) => s.id).join(',')}&sel=${sc.id}`} title="在素材庫開啟（grid 過濾成這批相關素材）">
                    <div class="scenethumb">
                      {#if sc.thumb}<img src={sc.thumb} alt={sc.name} loading="lazy" />{/if}
                    </div>
                    <Mono dim style="font-size:9.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;">{sc.name}</Mono>
                  </a>
                  <div class="exports">
                    {#each EXPORTS as fmt}
                      <button class="exp" on:click={() => exportScene(sc, fmt)}>{fmt.toUpperCase()}</button>
                    {/each}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}

      {#if busy}
        <div class="msg assistant"><Mono dim style="font-size:11px;color:var(--cyan);">● 生成中…　<span style="opacity:0.6">Esc 中斷</span></Mono></div>
      {/if}
    </div>

    <div class="composer">
      <input
        class="ak-input chatinput"
        placeholder="問你的素材庫…（Enter 送出）"
        bind:value={input}
        on:keydown={onKey}
        disabled={busy}
      />
      <button class="ak-btn ak-btn--primary" on:click={() => send()} disabled={busy || !input.trim()}>送出 →</button>
    </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  /* Match the global button.ak-btn look for the back-link <a> (global rule is
     element-qualified to <button>, so an <a class="ak-btn"> is otherwise unstyled). */
  .topbar a.ak-btn {
    font-family: var(--ak-mono); font-size: var(--fs-tiny); text-transform: uppercase;
    letter-spacing: var(--tr-uppercase); color: var(--ink); background: transparent;
    border: 1px solid var(--rule-hi); padding: 6px 10px; line-height: 1;
    text-decoration: none; cursor: pointer; white-space: nowrap;
  }
  .topbar a.ak-btn:hover { background: var(--surface-2); border-color: var(--ink); }
  .body { display: grid; grid-template-columns: 240px 1fr; min-height: 0; }
  .convs { border-right: 1px solid var(--rule); display: flex; flex-direction: column; min-height: 0; }
  .convhead { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 14px 16px; border-bottom: 1px solid var(--rule); }
  .newchat { font-size: 9.5px; padding: 5px 8px; white-space: nowrap; }
  .convlist { flex: 1; overflow: auto; padding: 8px; display: flex; flex-direction: column; gap: 2px; }
  .convitem {
    display: flex; flex-direction: column; gap: 3px; align-items: flex-start;
    text-align: left; padding: 8px 10px; background: transparent; border: none;
    border-left: 2px solid transparent; cursor: pointer; font-family: inherit; width: 100%;
  }
  .convitem:hover { background: var(--surface-2); }
  .convitem.active { border-left-color: var(--invert); background: var(--surface-2); }
  .convitem:disabled { cursor: default; opacity: 0.6; }
  .convtitle {
    font-size: 12px; color: var(--ink); line-height: 1.3; width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chatmain { display: flex; flex-direction: column; min-height: 0; }
  .thread { flex: 1; overflow: auto; padding: 24px 10%; display: flex; flex-direction: column; gap: 20px; }
  .empty { display: flex; flex-direction: column; gap: 10px; padding-top: 40px; }
  .emptytitle { font-size: var(--fs-display-sm); color: var(--ink); margin: 4px 0; }
  .suggest { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; align-items: flex-start; }
  .sg { text-align: left; text-transform: none; letter-spacing: 0; font-family: var(--ak-sans); }
  .msg { display: flex; flex-direction: column; gap: 6px; }
  .msg.user { align-items: flex-end; }
  .msg.user .text { background: var(--surface-2); padding: 10px 14px; max-width: 80%; }
  .msg.assistant .text { color: var(--ink-2); line-height: 1.6; max-width: 80%; }
  .msg.error .text { color: var(--cyan); }
  .text { font-size: 13.5px; white-space: pre-wrap; }
  .scenes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 6px; max-width: 560px; }
  .scene { display: flex; flex-direction: column; gap: 4px; }
  .scenelink { display: flex; flex-direction: column; gap: 4px; text-decoration: none; color: inherit; cursor: pointer; }
  .scenelink:hover .scenethumb { box-shadow: inset 0 0 0 1px var(--ink); }
  .scenethumb { aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; box-shadow: inset 0 0 0 1px var(--rule); }
  .scenethumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .exports { display: flex; gap: 4px; margin-top: 2px; }
  .exp {
    font-family: var(--ak-mono); font-size: 8.5px; letter-spacing: 0.06em;
    color: var(--quiet); text-decoration: none; padding: 1px 4px;
    border: 1px solid var(--rule); line-height: 1.3;
    background: transparent; cursor: pointer; appearance: none;
  }
  .exp:hover { color: var(--ink); border-color: var(--ink); }
  .composer { border-top: 1px solid var(--rule); padding: 16px 10%; display: flex; gap: 10px; align-items: center; }
  .chatinput { flex: 1; font-size: 13px; }
</style>
