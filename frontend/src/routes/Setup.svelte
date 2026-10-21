<!-- First-run setup wizard (roadmap 9.8a). Layout follows the C4 onboarding artboard
     in routes/Edge.svelte: a step rail on the left, one step's detail on the right.
     Three steps: ① this computer ② AI models ③ footage. Skippable at every point;
     Settings → System can open it again. -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import { push } from 'svelte-spa-router'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Mono from '../lib/Mono.svelte'
  import * as api from '../lib/api.js'
  import { pushToast } from '../lib/toast.js'
  import { modelRows, blockers, pullLine } from '../lib/setupWizard.js'

  let setup = null
  let error = null
  let step = 1
  let pulling = false
  let timer = null

  async function refresh() {
    try {
      setup = await api.setupStatus()
      error = null
      pulling = !!setup.pull?.running
    } catch (e) {
      error = e?.message || String(e)
    }
  }

  async function poll() {
    try {
      const p = await api.setupPullStatus()
      setup = { ...setup, pull: p }
      if (!p.running) {
        clearInterval(timer)
        timer = null
        pulling = false
        await refresh()
        if (p.message && p.message !== 'done') pushToast(p.message, 'error')
      }
    } catch { /* keep polling; a blip must not freeze the bar */ }
  }

  async function startPull() {
    try {
      const r = await api.setupPull({})
      if (!r.queued) return pushToast(r.message || '所有模型都已就緒')
      pulling = true
      timer = setInterval(poll, 1500)
      poll()
    } catch (e) {
      pushToast(e?.message || String(e), 'error')
    }
  }

  async function finish(skipped) {
    try {
      await api.setupComplete({ skipped })
    } catch (e) {
      return pushToast(e?.message || String(e), 'error')
    }
    push(skipped ? '/' : '/ingest-setup')
  }

  onMount(async () => {
    await refresh()
    if (pulling) timer = setInterval(poll, 1500)
  })
  onDestroy(() => timer && clearInterval(timer))

  $: hw = setup?.hardware
  $: rows = modelRows(setup)
  $: missing = rows.filter((r) => !r.present)
  $: problems = blockers(setup)
  $: pullItems = setup?.pull?.items || []
  $: itemFor = (name) => pullItems.find((i) => i.name === name)
  $: steps = [
    { n: 1, title: '檢查這台電腦', sub: '硬體、轉錄裝置、ffmpeg' },
    { n: 2, title: '下載 AI 模型', sub: missing.length ? `還缺 ${missing.length} 個` : '都已就緒' },
    { n: 3, title: '加入素材', sub: '選資料夾、開始匯入' },
  ].map((s) => ({ ...s, status: s.n < step ? 'done' : s.n === step ? 'active' : 'pending' }))
</script>

<div class="wiz">
  <div class="topbar">
    <ArkivLogo size={16} /><Mono dim style="font-size:11px;">first run · setup</Mono>
    <div class="grow"></div>
    <button class="ak-btn" on:click={() => finish(true)}>略過，直接開始</button>
  </div>

  <div class="body">
    <div class="stepper">
      <Eyebrow style="margin-bottom:28px;">Setup · {step} of 3</Eyebrow>
      <div class="steps">
        {#each steps as s}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="step" on:click={() => (step = s.n)}>
            <span class="stepn" class:active={s.status === 'active'} class:done={s.status === 'done'}>{s.status === 'done' ? '✓' : s.n}</span>
            <div>
              <div class="ak-display steptitle" class:dim={s.status === 'pending'}>{s.title}</div>
              <Mono dim style="font-size:11px;margin-top:4px;display:block;">{s.sub}</Mono>
            </div>
          </div>
        {/each}
      </div>
      <div class="grow"></div>
      <div class="stepnote"><Mono dim style="font-size:10.5px;line-height:1.6;">全部在這台電腦上執行。只會下載模型，你的素材不會上傳到任何地方。</Mono></div>
    </div>

    <div class="detail">
      {#if error}
        <Mono>讀不到設定狀態：{error}</Mono>
        <button class="ak-btn" style="margin-top:12px;" on:click={refresh}>重試</button>
      {:else if !setup}
        <Mono dim>檢查中…</Mono>
      {:else if step === 1}
        <Eyebrow style="margin-bottom:10px;">Step 1 · 這台電腦</Eyebrow>
        <div class="ak-display big">先看看你的<br />硬體。</div>
        <div class="grid2">
          <Mono dim>系統</Mono><Mono>{hw.os} · {hw.arch}</Mono>
          <Mono dim>處理器</Mono><Mono>{hw.cpu} · {hw.cores} 核</Mono>
          <Mono dim>記憶體</Mono><Mono>{hw.ram_gb != null ? `${hw.ram_gb} GB` : '不明'}</Mono>
          <Mono dim>顯示卡</Mono><Mono>{hw.gpu ? `${hw.gpu.name}${hw.gpu.vram_gb && !hw.apple_silicon ? ` · ${hw.gpu.vram_gb} GB` : ''}` : '沒有偵測到獨立顯示卡'}</Mono>
          <Mono dim>轉錄裝置</Mono><Mono>{setup.transcribe.label}{setup.transcribe.note ? ` — ${setup.transcribe.note}` : ''}</Mono>
          <Mono dim>ffmpeg</Mono><Mono>{setup.ffmpeg ? '已安裝' : '沒有'}</Mono>
        </div>
        {#if problems.length}
          <div class="warn">
            {#each problems as p}<Mono style="display:block;">◇ {p}</Mono>{/each}
            <button class="ak-btn" style="margin-top:10px;" on:click={refresh}>處理好了，重新檢查</button>
          </div>
        {/if}
        <div class="grow"></div>
        <div class="nav"><button class="ak-btn primary" on:click={() => (step = 2)}>下一步：AI 模型</button></div>
      {:else if step === 2}
        <Eyebrow style="margin-bottom:10px;">Step 2 · AI 模型</Eyebrow>
        <div class="ak-display big">在本機跑的<br />模型。</div>
        {#if !setup.ollama.running}
          <div class="warn">
            <Mono style="display:block;">◇ Ollama 沒有在執行。畫面描述、語意搜尋與對話需要它——到 ollama.com 下載安裝並打開，再按重新檢查。</Mono>
            <Mono dim style="display:block;margin-top:4px;">沒有 Ollama 也能匯入與轉錄，只是少了這三項。</Mono>
            <button class="ak-btn" style="margin-top:10px;" on:click={refresh}>重新檢查</button>
          </div>
        {/if}
        <div class="models">
          {#each rows as r (r.key)}
            <div class="model">
              <Eyebrow>{r.label}</Eyebrow>
              <div class="mname">{r.name}</div>
              <Mono dim style="font-size:11px;">
                {#if r.present}已下載{:else if itemFor(r.name)}{pullLine(itemFor(r.name))}{:else}未下載 · {r.size}{/if}
              </Mono>
            </div>
          {/each}
        </div>
        <div class="dlbox">
          {#if missing.length}
            <Mono>還缺 {missing.length} 個模型。可以現在下載，或等第一次匯入時再自動下載。</Mono>
            <div class="grow"></div>
            <button class="ak-btn primary" disabled={pulling} on:click={startPull}>{pulling ? '下載中…' : '現在下載'}</button>
            <button class="ak-btn" on:click={() => (step = 3)}>等匯入時再下載</button>
          {:else}
            <Mono>模型都已就緒。</Mono>
            <div class="grow"></div>
            <button class="ak-btn primary" on:click={() => (step = 3)}>下一步：加入素材</button>
          {/if}
        </div>
        <div class="grow"></div>
        <Mono dim style="font-size:10.5px;margin-top:16px;">想換模型：設定 → 系統。下載可以關掉這頁繼續在背景跑。</Mono>
      {:else}
        <Eyebrow style="margin-bottom:10px;">Step 3 · 加入素材</Eyebrow>
        <div class="ak-display big">指給 arkiv<br />看你的素材。</div>
        <Mono dim style="font-size:13px;margin-top:14px;line-height:1.65;max-width:640px;display:block;">
          arkiv 不會搬動或複製你的檔案，只在原地建索引。NAS 上的資料夾請先在 Finder／檔案總管連上，再到匯入畫面選它。
        </Mono>
        <div class="grow"></div>
        <div class="nav">
          <button class="ak-btn" on:click={() => finish(true)}>先看範例片</button>
          <button class="ak-btn primary" on:click={() => finish(false)}>前往匯入</button>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .wiz { height: 100vh; display: grid; grid-template-rows: 52px 1fr; background: var(--bg); color: var(--ink); }
  .topbar { display: flex; align-items: center; gap: 14px; padding: 0 24px; border-bottom: 1px solid var(--rule); }
  .grow { flex: 1; }
  .body { display: grid; grid-template-columns: 320px 1fr; min-height: 0; }
  .stepper { border-right: 1px solid var(--rule); padding: 40px 32px; display: flex; flex-direction: column; }
  .steps { display: flex; flex-direction: column; gap: 22px; }
  .step { display: grid; grid-template-columns: 32px 1fr; gap: 14px; align-items: baseline; cursor: pointer; }
  .stepn { font-family: var(--ak-mono); font-size: 11px; width: 24px; height: 24px; color: var(--quiet); border: 1px solid var(--rule); display: inline-flex; align-items: center; justify-content: center; }
  .stepn.done { color: var(--ink); border-color: var(--ink); }
  .stepn.active { background: var(--invert); color: var(--invert-ink); border: none; font-weight: 700; }
  .steptitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; }
  .steptitle.dim { opacity: 0.55; }
  .stepnote { padding-top: 24px; border-top: 1px solid var(--rule); }
  .detail { padding: 40px 60px 30px; overflow: auto; display: flex; flex-direction: column; }
  .big { font-size: 56px; letter-spacing: -0.04em; line-height: 0.95; }
  .grid2 { margin-top: 32px; display: grid; grid-template-columns: 110px 1fr; row-gap: 10px; column-gap: 16px; font-size: 13px; max-width: 760px; }
  .warn { margin-top: 24px; padding: 14px 18px; border: 1px dashed var(--rule-hi); max-width: 760px; }
  .models { margin-top: 28px; display: grid; grid-template-columns: 1fr 1fr; gap: 18px; max-width: 900px; }
  .model { padding: 16px 18px; border: 1px solid var(--rule-hi); background: var(--surface); display: flex; flex-direction: column; gap: 8px; }
  .mname { font-family: var(--ak-mono); font-size: 15px; font-weight: 600; }
  .dlbox { margin-top: 24px; padding: 14px 18px; border: 1px dashed var(--rule-hi); display: flex; align-items: center; gap: 14px; max-width: 900px; }
  .nav { display: flex; gap: 12px; justify-content: flex-end; margin-top: 24px; }
  @media (max-width: 900px) {
    .body { grid-template-columns: 1fr; }
    .stepper { display: none; }
    .detail { padding: 24px 16px; }
    .big { font-size: 40px; }
    .models { grid-template-columns: 1fr; }
  }
</style>
