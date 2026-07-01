<!-- Left sidebar: projects · smart pools · tags · storage. -->
<script>
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import { PROJECTS, TAGS } from './mockData.js'
  // Live overrides — all default to mock so mock screens stay byte-identical.
  export let liveProjects = null // [{id,name,count,active,health?}]
  export let livePools = null // [[label, count], ...]
  export let liveTags = null // [{name, count}]
  export let onTag = null // (name) => void; live tag-click → filter
  export let liveCollections = null // [{key,title,count,items}]; null → section hidden
  export let onCollection = null // (collection) => void; click → filter to members
  export let liveStorage = null // {pct, used_gb, total_gb} from /api/stats.disk; null → mock placeholder
  export let onPool = null // (label) => void; click a Smart Pool → rating filter
  export let activePool = null // currently-active pool label (for row highlight)
  export let liveCameras = null // [{model, count}] normalized camera category; null → section hidden
  export let onCamera = null // (model) => void; click → filter grid to that camera category
  export let activeCamera = null // currently-filtered camera model (for row highlight)

  const MOCK_POOLS = [
    ['All media', 247],
    ['Needs review', 34],
    ['Orphans', 2],
    ['Recently ingested', 18],
    ['No transcript', 12],
  ]
  $: projects = liveProjects ?? PROJECTS
  $: pools = livePools ?? MOCK_POOLS
  $: tags = liveTags ?? TAGS
  // Cap the cloud so a long tail (74+ on a real library) doesn't bury the useful
  // ones. Tags arrive sorted by count desc, so the top slice is the most-used.
  const TAG_CAP = 24
  let showAllTags = false
  $: visibleTags = showAllTags ? tags : tags.slice(0, TAG_CAP)
  // Storage footer: real disk usage when wired (live), else the design placeholder.
  const gb = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)} TB` : `${Math.round(n)} GB`)
  $: storage = liveStorage
    ? { pct: liveStorage.pct, label: `${gb(liveStorage.used_gb)} · ${gb(liveStorage.total_gb)} · disk` }
    : { pct: 40, label: '4.8 TB · 12 TB · NAS' }
</script>

<aside class="pool">
  <section>
    <Eyebrow style="margin-bottom:10px;">Projects · {projects.length}</Eyebrow>
    <div class="col">
      {#each projects as p (p.id)}
        <div class="proj" class:active={p.active} style="opacity:{p.health ? 0.6 : 1};">
          <div class="projrow">
            <span class="projname" class:activename={p.active}>{p.name}</span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{p.count}</Mono>
          </div>
          {#if p.health}
            <Mono dim style="font-size:9.5px;letter-spacing:0.06em;display:block;margin-top:1px;">◇ {p.health}</Mono>
          {/if}
        </div>
      {/each}
    </div>
  </section>

  <section>
    <Eyebrow style="margin-bottom:10px;">Smart Pools</Eyebrow>
    <div class="col">
      {#each pools as [label, count]}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
        <div class="poolrow" class:clickpool={onPool} class:activepool={onPool && activePool === label}
          on:click={() => onPool && onPool(label)}>
          <span class="ellip">{label}</span>
          <Mono dim style="font-size:10px;flex:0 0 auto;">{count}</Mono>
        </div>
      {/each}
    </div>
  </section>

  {#if liveCameras && liveCameras.length}
    <section>
      <Eyebrow style="margin-bottom:10px;">Cameras · 機型</Eyebrow>
      <div class="col">
        {#each liveCameras as c (c.model)}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="poolrow camrow" class:activecam={activeCamera === c.model}
            on:click={() => onCamera && onCamera(c.model)}>
            <span class="ellip">{c.model}</span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{c.count}</Mono>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  {#if liveCollections && liveCollections.length}
    <section>
      <Eyebrow style="margin-bottom:10px;">Smart Collections · auto</Eyebrow>
      <div class="col">
        {#each liveCollections as c (c.key)}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="poolrow collrow" on:click={() => onCollection && onCollection(c)}>
            <span class="ellip">{c.title}</span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{c.count}</Mono>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <section class="tagsec">
    <Eyebrow style="margin-bottom:10px;">Tags · auto · {tags.length}</Eyebrow>
    <div class="tags">
      {#each visibleTags as t (t.name)}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
        <span class="tag" class:clickable={onTag} class:folded={t.aliases && t.aliases.length}
          title={t.aliases && t.aliases.length ? `含別名：${t.aliases.join('、')}` : null}
          on:click={() => onTag && onTag(t.name)}>{t.name} <span class="tagcount">{t.count}</span></span>
      {/each}
    </div>
    {#if tags.length > TAG_CAP}
      <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
      <span class="moretag" on:click={() => (showAllTags = !showAllTags)}>
        {showAllTags ? '收合' : `+${tags.length - TAG_CAP} 更多`}
      </span>
    {/if}
  </section>

  <div class="spacer"></div>

  <section class="storage">
    <div class="strow">
      <Eyebrow>Storage</Eyebrow>
      <Mono dim style="font-size:10px;">{storage.pct}%</Mono>
    </div>
    <div class="bar"><div class="barfill" style="width:{storage.pct}%;"></div></div>
    <Mono dim style="font-size:10.5px;margin-top:5px;letter-spacing:0.02em;">{storage.label}</Mono>
  </section>
</aside>

<style>
  .pool {
    border-right: 1px solid var(--rule); padding: 20px 14px 14px 16px;
    /* scroll the whole sidebar — projects + pools + collections + tag cloud +
       storage can exceed the viewport; overflow:hidden clipped the bottom with
       no way to reach it (same bug as the inspector). */
    display: flex; flex-direction: column; gap: 20px; min-height: 0; overflow-y: auto;
  }
  .col { display: flex; flex-direction: column; }
  .proj {
    padding: 5px 6px 5px 0; border-left: 2px solid transparent;
    padding-left: 10px; cursor: pointer;
  }
  .proj.active { border-left-color: var(--invert); padding-left: 8px; }
  .projrow { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
  .projname {
    font-size: 12.5px; font-weight: 400; color: var(--ink-2);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
  }
  .projname.activename { font-weight: 600; color: var(--ink); }
  .poolrow {
    display: flex; justify-content: space-between; align-items: baseline; gap: 6px;
    padding: 4px 10px; font-size: 12.5px; color: var(--ink-2); cursor: pointer;
  }
  .ellip { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .clickpool { border-left: 2px solid transparent; }
  .clickpool:hover { color: var(--ink); }
  .poolrow.activepool { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .collrow:hover { color: var(--ink); }
  .camrow { border-left: 2px solid transparent; }
  .camrow:hover { color: var(--ink); }
  .camrow.activecam { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  /* Don't let the tag section shrink below its content: as a flex item with
     min-height:0 it collapsed to a short box while the (expanded, 700+) tag
     cloud overflowed unclipped and painted over the Storage footer below.
     flex-shrink:0 keeps it full-height so the whole sidebar scrolls as one
     column and Storage flows after the tags instead of colliding. */
  .tagsec { flex-shrink: 0; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-family: var(--ak-mono); font-size: 10.5px; padding: 3px 6px;
    border: 1px solid var(--rule); color: var(--ink-2); cursor: pointer; white-space: nowrap;
  }
  .tagcount { color: var(--quiet); }
  .tag.clickable:hover { border-color: var(--ink); color: var(--ink); }
  /* folded = absorbed near-synonyms via the alias map; subtle dotted underline. */
  .tag.folded { border-style: dashed; }
  .moretag {
    display: inline-block; margin-top: 8px; font-family: var(--ak-mono);
    font-size: 10.5px; color: var(--quiet); cursor: pointer;
  }
  .moretag:hover { color: var(--ink); }
  .spacer { flex: 1; }
  .storage { border-top: 1px solid var(--rule); padding-top: 12px; }
  .strow { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
  .bar { height: 2px; background: var(--surface-3); position: relative; }
  .barfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--ink); transition: width 0.2s; }
</style>
