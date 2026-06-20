<!-- Basic drag-to-look 360 viewer: maps an equirectangular still (a reprojected
     360 frame thumbnail, 2:1) onto the inside of a sphere and lets you pan/look
     around. Triage/prospecting only — NOT a reframe editor (that's Insta360 /
     Resolve). Still image, not video: the source is the extracted equirect frame
     (arkiv reprojects 360 for tagging; there's no equirect video proxy). -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import * as THREE from 'three'

  export let src // equirectangular image URL (the 360 frame thumbnail)

  let container
  let renderer, scene, camera, mesh, texture, raf
  let lon = 0, lat = 0
  let dragging = false, px = 0, py = 0, dLon = 0, dLat = 0
  let ready = false

  function loadTexture(url) {
    const tex = new THREE.TextureLoader().load(url, () => { ready = true })
    tex.colorSpace = THREE.SRGBColorSpace
    return tex
  }

  function init() {
    const w = container.clientWidth || 340
    const h = container.clientHeight || 191
    scene = new THREE.Scene()
    camera = new THREE.PerspectiveCamera(75, w / h, 1, 1100)
    renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(w, h)
    container.appendChild(renderer.domElement)

    const geo = new THREE.SphereGeometry(500, 60, 40)
    geo.scale(-1, 1, 1) // flip so the texture faces inward (we sit at the centre)
    texture = loadTexture(src)
    mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: texture }))
    scene.add(mesh)
    animate()
  }

  function animate() {
    raf = requestAnimationFrame(animate)
    lat = Math.max(-85, Math.min(85, lat))
    const phi = THREE.MathUtils.degToRad(90 - lat)
    const theta = THREE.MathUtils.degToRad(lon)
    camera.lookAt(
      500 * Math.sin(phi) * Math.cos(theta),
      500 * Math.cos(phi),
      500 * Math.sin(phi) * Math.sin(theta),
    )
    renderer.render(scene, camera)
  }

  function down(e) {
    dragging = true
    const p = e.touches ? e.touches[0] : e
    px = p.clientX; py = p.clientY; dLon = lon; dLat = lat
  }
  function move(e) {
    if (!dragging) return
    const p = e.touches ? e.touches[0] : e
    lon = dLon - (p.clientX - px) * 0.18
    lat = dLat + (p.clientY - py) * 0.18
  }
  function up() { dragging = false }

  // Re-point the texture when the selected 360 clip changes (component is reused).
  $: if (mesh && src) {
    const old = texture
    texture = loadTexture(src)
    mesh.material.map = texture
    mesh.material.needsUpdate = true
    lon = 0; lat = 0
    old?.dispose()
  }

  onMount(init)
  onDestroy(() => {
    cancelAnimationFrame(raf)
    texture?.dispose()
    mesh?.geometry?.dispose()
    mesh?.material?.dispose()
    renderer?.dispose()
  })
</script>

<svelte:window on:pointerup={up} on:pointermove={move} />
<div
  class="pano"
  class:grabbing={dragging}
  bind:this={container}
  on:pointerdown={down}
  role="img"
  aria-label="360 panorama — drag to look around"
>
  {#if !ready}<div class="loading">360 · loading…</div>{/if}
  <div class="hint">360 · 拖曳環視</div>
</div>

<style>
  .pano {
    position: absolute; inset: 0; cursor: grab; touch-action: none;
    background: var(--surface-2); overflow: hidden;
  }
  .pano.grabbing { cursor: grabbing; }
  .pano :global(canvas) { display: block; width: 100%; height: 100%; }
  .loading {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-family: var(--ak-mono); font-size: 11px; color: var(--ink-2); letter-spacing: 0.06em;
  }
  .hint {
    position: absolute; top: 8px; left: 8px; z-index: 2; pointer-events: none;
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase;
    color: #f3f2ee; background: rgba(0,0,0,0.45); padding: 2px 6px;
  }
</style>
