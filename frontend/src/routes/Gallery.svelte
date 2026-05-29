<!-- Seg 1 audit surface — mounts each shared primitive once. -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Rating from '../lib/Rating.svelte'
  import Frame from '../lib/Frame.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Mono from '../lib/Mono.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import { MEDIA, PROJECTS, TAGS } from '../lib/mockData.js'
</script>

<div class="gallery">
  <Eyebrow>seg1 · shared primitives gallery</Eyebrow>
  <h1 class="ak-display title">primitives.</h1>

  <section>
    <Eyebrow>ArkivLogo</Eyebrow>
    <div class="row"><ArkivLogo size={18} /><ArkivLogo size={28} /><ArkivLogo size={40} /></div>
  </section>

  <section>
    <Eyebrow>Rating</Eyebrow>
    <div class="row"><Rating value="good" /><Rating value="ng" /><Rating value="rev" /><Rating value="none" /></div>
  </section>

  <section>
    <Eyebrow>Frame</Eyebrow>
    <div class="row">
      <Frame style="padding: var(--s-4); width: 120px;"><Mono dim>rule</Mono></Frame>
      <Frame hi style="padding: var(--s-4); width: 120px;"><Mono>invert</Mono></Frame>
    </div>
  </section>

  <section>
    <Eyebrow>Mono</Eyebrow>
    <div class="row"><Mono>00:02:47</Mono><Mono dim>1.4 GB</Mono></div>
  </section>

  <section>
    <Eyebrow>Thumb — video variants (seed 0–5)</Eyebrow>
    <div class="thumbgrid">
      {#each [0, 1, 2, 3, 4, 5] as s}
        <div class="cell"><Thumb seed={s} kind="video" /></div>
      {/each}
    </div>
  </section>

  <section>
    <Eyebrow>Thumb — audio</Eyebrow>
    <div class="thumbgrid">
      {#each [2, 8, 13] as s}
        <div class="cell"><Thumb seed={s} kind="audio" /></div>
      {/each}
    </div>
  </section>

  <section>
    <Eyebrow>mockData sanity</Eyebrow>
    <Mono dim>MEDIA {MEDIA.length} · PROJECTS {PROJECTS.length} · TAGS {TAGS.length}</Mono>
  </section>
</div>

<style>
  .gallery {
    padding: var(--s-12);
    display: flex;
    flex-direction: column;
    gap: var(--s-8);
  }
  .title {
    font-size: var(--fs-display-sm);
    margin: var(--s-2) 0 var(--s-4);
    color: var(--ink);
  }
  section {
    display: flex;
    flex-direction: column;
    gap: var(--s-3);
  }
  .row {
    display: flex;
    align-items: center;
    gap: var(--s-4);
    flex-wrap: wrap;
  }
  .thumbgrid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: var(--grid-gutter);
    max-width: 900px;
  }
  .cell {
    aspect-ratio: 16 / 9;
    background: var(--surface);
  }
</style>
