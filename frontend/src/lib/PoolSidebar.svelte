<!-- Left sidebar: projects · smart pools · tags · storage. -->
<script>
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import { PROJECTS, TAGS } from './mockData.js'
  import { DAY_CAP } from './shotYear.js'
  // Live overrides — all default to mock so mock screens stay byte-identical.
  export let liveProjects = null // [{id,name,count,active,health?}]; count null = unknown
  export let entitlement = null // /api/entitlements; null -> the line is omitted entirely
  export let livePools = null // [[label, count], ...]
  export let liveTags = null // [{name, count}]
  export let onTag = null // (name) => void; live tag-click → filter
  export let liveCollections = null // [{key,title,count,items}]; null → section hidden
  export let onCollection = null // (collection) => void; click → filter to members
  export let activeCollection = null // currently-filtered collection key (row highlight)
  export let liveBins = null // [{id,name,item_count}] cross-library 精選集; null → section hidden
  export let onBin = null // (bin) => void; click → open the bin
  export let liveStorage = null // {pct, used_gb, total_gb} from /api/stats.disk; null → mock placeholder
  export let onPool = null // (label) => void; click a Smart Pool → rating filter
  export let activePool = null // currently-active pool label (for row highlight)
  export let liveShotYears = null // [{year, count, label, days:[{date,count,label}]}]; null → section hidden
  export let onShotYear = null // (year) => void; click → filter the library to that shoot year
  export let activeShotYear = null // currently-filtered shoot year (also the expanded one)
  export let onShotDate = null // (isoDate) => void; click → narrow to that shoot day
  export let activeShotDate = null // currently-filtered shoot day (for row highlight)
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
  // Says what the install is actually entitled to, from the endpoint that enforces
  // it. Omitted when unknown rather than guessed: a wrong tier label is worse than
  // no label, and the refusal itself already explains the cap when someone hits it.
  $: tierLine = !entitlement
    ? null
    : entitlement.grandfathered
      ? '此安裝永久解除專案上限'
      : entitlement.pro
        ? 'Pro · 無限專案'
        : `${entitlement.projects_used} / ${entitlement.free_project_limit} 個免費專案`
  $: pools = livePools ?? MOCK_POOLS
  $: tags = liveTags ?? TAGS
  // Cap the cloud so a long tail (74+ on a real library) doesn't bury the useful
  // ones. Tags arrive sorted by count desc, so the top slice is the most-used.
  const TAG_CAP = 24
  let showAllTags = false
  $: visibleTags = showAllTags ? tags : tags.slice(0, TAG_CAP)
  // Same treatment for collections. Uncapped, a project that derives a dozen
  // topical collections pushes Storage off the bottom of the sidebar; the tag
  // cloud already solved this, so reuse the shape rather than invent one.
  // Collections arrive count-desc from the API, so the head is the useful slice.
  const COLLECTION_CAP = 12
  let showAllCollections = false
  $: visibleCollections = showAllCollections
    ? (liveCollections || [])
    : (liveCollections || []).slice(0, COLLECTION_CAP)
  // Day list expansion, capped like the tag cloud above. Only one year is open at a
  // time, so a single flag suffices — but it has to reset when the open year changes,
  // or a year with three days inherits "expanded" from the one with two hundred.
  let showAllDays = false
  $: if (activeShotYear !== null) showAllDays = false
  // Storage footer: real disk usage when wired (live), else the design placeholder.
  const gb = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)} TB` : `${Math.round(n)} GB`)
  $: storage = liveStorage
    ? { pct: liveStorage.pct, label: `${gb(liveStorage.used_gb)} · ${gb(liveStorage.total_gb)} · disk` }
    : { pct: 40, label: '4.8 TB · 12 TB · NAS' }
</script>

<aside class="pool">
  <section>
    <Eyebrow style="margin-bottom:{tierLine ? '3px' : '10px'};">Projects · {projects.length}</Eyebrow>
    {#if tierLine}
      <Mono dim style="font-size:9.5px;letter-spacing:0.06em;display:block;margin-bottom:9px;">{tierLine}</Mono>
    {/if}
    <div class="col">
      {#each projects as p (p.id)}
        <div class="proj" class:active={p.active} style="opacity:{p.health ? 0.6 : 1};">
          <div class="projrow">
            <span class="projname" class:activename={p.active}>{p.name}</span>
            {#if p.count != null}
              <Mono dim style="font-size:10px;flex:0 0 auto;">{p.count}</Mono>
            {/if}
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

  {#if liveShotYears && liveShotYears.length}
    <section>
      <Eyebrow style="margin-bottom:10px;">Shoot date · 拍攝日</Eyebrow>
      <div class="col">
        {#each liveShotYears as y (y.year)}
          <!-- Picking a year both filters to it and opens its days: one affordance,
               because a separate disclosure control would be a second way to say the
               same thing in a column that already has one idiom for everything. -->
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="poolrow yearrow" class:activeyear={activeShotYear === y.year}
            on:click={() => onShotYear && onShotYear(y.year)}>
            <span class="ellip">
              {#if y.days.length}<span class="caret">{activeShotYear === y.year ? '▾' : '▸'}</span>{/if}{y.label}
            </span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{y.count}</Mono>
          </div>
          {#if activeShotYear === y.year && y.days.length}
            {#each showAllDays ? y.days : y.days.slice(0, DAY_CAP) as d (d.date)}
              <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
              <div class="poolrow dayrow" class:activeday={activeShotDate === d.date}
                on:click={() => onShotDate && onShotDate(d.date)}>
                <span class="ellip">{d.label}</span>
                <Mono dim style="font-size:10px;flex:0 0 auto;">{d.count}</Mono>
              </div>
            {/each}
            {#if y.days.length > DAY_CAP}
              <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
              <span class="moretag daymore" on:click={() => (showAllDays = !showAllDays)}>
                {showAllDays ? '收合' : `+${y.days.length - DAY_CAP} 更多`}
              </span>
            {/if}
          {/if}
        {/each}
      </div>
    </section>
  {/if}

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
        {#each visibleCollections as c (c.key)}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="poolrow collrow" class:activecoll={activeCollection === c.key}
            on:click={() => onCollection && onCollection(c)}>
            <span class="ellip">{c.title}</span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{c.count}</Mono>
          </div>
        {/each}
      </div>
      {#if liveCollections.length > COLLECTION_CAP}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
        <span class="moretag" on:click={() => (showAllCollections = !showAllCollections)}>
          {showAllCollections ? '收合' : `+${liveCollections.length - COLLECTION_CAP} 更多`}
        </span>
      {/if}
    </section>
  {/if}

  {#if liveBins && liveBins.length}
    <section>
      <Eyebrow style="margin-bottom:10px;">精選集 · 跨庫</Eyebrow>
      <div class="col">
        {#each liveBins as bn (bn.id)}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="poolrow collrow" on:click={() => onBin && onBin(bn)}>
            <span class="ellip">★ {bn.name}</span>
            <Mono dim style="font-size:10px;flex:0 0 auto;">{bn.item_count}</Mono>
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
  .collrow { border-left: 2px solid transparent; }
  .collrow:hover { color: var(--ink); }
  .collrow.activecoll { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .yearrow { border-left: 2px solid transparent; }
  .yearrow:hover { color: var(--ink); }
  .yearrow.activeyear { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  /* Fixed-width so the year labels stay aligned whether or not a row has a caret. */
  .caret { display: inline-block; width: 11px; color: var(--quiet); font-size: 9px; }
  .dayrow {
    border-left: 2px solid transparent; padding-left: 26px;
    font-family: var(--ak-mono); font-size: 11px;
  }
  .dayrow:hover { color: var(--ink); }
  .dayrow.activeday { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .daymore { margin-top: 2px; margin-bottom: 4px; padding-left: 26px; }
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
