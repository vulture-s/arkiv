<!-- Rating — type weight + border only. No shapes, no color. -->
<script>
  export let value // 'good' | 'ng' | 'rev' | 'none'
</script>

{#if value === 'good'}
  <span class="ak-rate ak-rate--good">GOOD</span>
{:else if value === 'ng'}
  <span class="ak-rate ak-rate--ng">N·G</span>
{:else if value === 'rev'}
  <span class="ak-rate ak-rate--rev">REV</span>
{:else}
  <span class="ak-rate ak-rate--none">—</span>
{/if}
