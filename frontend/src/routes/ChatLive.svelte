<!-- E2 — chat wired to live /api/chat. Natural-language → 5-intent classifier
     → vector search → LLM response. When the assistant returns scene_ids
     (compilation intent), resolve them to media items and show thumbnails
     inline. Requires chat_write scope (token-free on loopback). -->
<script>
  import { onMount, tick } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  const theme = 'dark'
  let messages = [] // {role:'user'|'assistant', text, intent?, scenes?:[{id,name,thumb}]}
  let input = ''
  let busy = false
  let convId = null
  let err = ''
  let mediaById = new Map() // id → {filename, thumb} for scene resolution
  let scroller

  const suggestions = ['幫我把生肉切割的鏡頭剪成一段', '找店內空景的畫面', '哪些素材有餐廳']

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
      /* non-fatal — scenes just won't show thumbs */
    }
  }

  function resolveScenes(ids) {
    return (ids || []).map((id) => {
      const m = mediaById.get(String(id))
      return { id, name: m?.filename || `#${id}`, thumb: m?.thumb || null }
    })
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
    try {
      const r = await api.chat(prompt, convId)
      convId = r.conversation_id || convId
      messages = [
        ...messages,
        {
          role: 'assistant',
          text: r.assistant_text || '',
          intent: r.intent,
          scenes: resolveScenes(r.scene_ids),
        },
      ]
    } catch (e) {
      err = e.status === 401 ? '需要 chat_write token（tailnet）— 本機 loopback 可直接用' : e.message
      messages = [...messages, { role: 'assistant', text: `⚠ ${err}`, error: true }]
    } finally {
      busy = false
      scrollDown()
    }
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  onMount(loadMediaIndex)
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">chat · 5-intent · vector + LLM</Mono>
  </div>

  <div class="body">
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
            <div class="scenes">
              {#each m.scenes as sc (sc.id)}
                <div class="scene">
                  <div class="scenethumb">
                    {#if sc.thumb}<img src={sc.thumb} alt={sc.name} loading="lazy" />{/if}
                  </div>
                  <Mono dim style="font-size:9.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;">{sc.name}</Mono>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}

      {#if busy}
        <div class="msg assistant"><Mono dim style="font-size:11px;color:var(--cyan);">● thinking…</Mono></div>
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

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .body { display: flex; flex-direction: column; min-height: 0; }
  .thread { flex: 1; overflow: auto; padding: 24px 18%; display: flex; flex-direction: column; gap: 20px; }
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
  .scenethumb { aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; box-shadow: inset 0 0 0 1px var(--rule); }
  .scenethumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .composer { border-top: 1px solid var(--rule); padding: 16px 18%; display: flex; gap: 10px; align-items: center; }
  .chatinput { flex: 1; font-size: 13px; }
</style>
