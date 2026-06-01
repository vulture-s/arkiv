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
        <div class="poolrow">
          <span class="ellip">{label}</span>
          <Mono dim style="font-size:10px;flex:0 0 auto;">{count}</Mono>
        </div>
      {/each}
    </div>
  </section>

  <section class="tagsec">
    <Eyebrow style="margin-bottom:10px;">Tags · auto</Eyebrow>
    <div class="tags">
      {#each tags as t (t.name)}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
        <span class="tag" class:clickable={onTag} on:click={() => onTag && onTag(t.name)}>{t.name} <span class="tagcount">{t.count}</span></span>
      {/each}
    </div>
  </section>

  <div class="spacer"></div>

  <section class="storage">
    <div class="strow">
      <Eyebrow>Storage</Eyebrow>
      <Mono dim style="font-size:10px;">40%</Mono>
    </div>
    <div class="bar"><div class="barfill"></div></div>
    <Mono dim style="font-size:10.5px;margin-top:5px;letter-spacing:0.02em;">4.8 TB · 12 TB · NAS</Mono>
  </section>
</aside>

<style>
  .pool {
    border-right: 1px solid var(--rule); padding: 20px 14px 14px 16px;
    display: flex; flex-direction: column; gap: 20px; min-height: 0; overflow: hidden;
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
  .tagsec { min-height: 0; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-family: var(--ak-mono); font-size: 10.5px; padding: 3px 6px;
    border: 1px solid var(--rule); color: var(--ink-2); cursor: pointer; white-space: nowrap;
  }
  .tagcount { color: var(--quiet); }
  .tag.clickable:hover { border-color: var(--ink); color: var(--ink); }
  .spacer { flex: 1; }
  .storage { border-top: 1px solid var(--rule); padding-top: 12px; }
  .strow { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
  .bar { height: 2px; background: var(--surface-3); position: relative; }
  .barfill { position: absolute; left: 0; top: 0; bottom: 0; width: 40%; background: var(--ink); }
</style>
