<!-- Grid card size: S / M / L. Same segmented look as ViewToggle. -->
<script>
  import { gridDensity } from './prefs.js'
  import { t } from './i18n.js'
  const SIZES = [['s', 'S'], ['m', 'M'], ['l', 'L']]
</script>

<div class="vt" role="group" aria-label={$t('卡片大小')} title={$t('卡片大小')}>
  {#each SIZES as [key, label], i}
    {#if i}<div class="sep"></div>{/if}
    <button class="vbtn" class:active={$gridDensity === key} aria-pressed={$gridDensity === key}
      on:click={() => gridDensity.set(key)}>{label}</button>
  {/each}
</div>

<style>
  .vt { display: flex; border: 1px solid var(--rule); }
  .vbtn {
    font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 4px 8px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1;
    height: var(--tb-h, auto); box-sizing: border-box; min-width: 24px;
  }
  .vbtn.active { background: var(--invert); color: var(--invert-ink); }
  .sep { width: 1px; background: var(--rule); }
</style>
