// Live 3D-LUT preview for the inspector player (log footage → Rec.709).
//
// The proxy stays log on purpose, so the original is one toggle away; this draws
// the playing <video> into a WebGL2 canvas through the clip's .cube. WebGL2 is in
// WKWebView (Tauri) and every current browser; without it the toggle is hidden.
//
// Input convention matches the backend generator: the LUT expects code value/1023.
// Proxies are encoded full range (yuvj420p), so the decoded RGB already is that.

// Parse a .cube (Resolve/Adobe 3D format). R varies fastest. → {size, rgba: Float32Array}
export function parseCube(text) {
  let size = 0
  const vals = []
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const head = line.split(/\s+/)[0].toUpperCase()
    if (head === 'LUT_3D_SIZE') { size = parseInt(line.split(/\s+/)[1], 10); continue }
    if (head === 'LUT_1D_SIZE') throw new Error('1D LUT not supported')
    if (/^[A-Z_]+$/.test(head)) continue // TITLE / DOMAIN_MIN / DOMAIN_MAX …
    const p = line.split(/\s+/).map(Number)
    if (p.length !== 3 || p.some((v) => !Number.isFinite(v))) throw new Error(`bad LUT row: ${line.slice(0, 40)}`)
    vals.push(p)
  }
  if (!size) throw new Error('missing LUT_3D_SIZE')
  if (vals.length !== size ** 3) throw new Error(`LUT has ${vals.length} rows, expected ${size ** 3}`)
  const rgba = new Float32Array(size ** 3 * 4)
  vals.forEach((v, i) => { rgba[i * 4] = v[0]; rgba[i * 4 + 1] = v[1]; rgba[i * 4 + 2] = v[2]; rgba[i * 4 + 3] = 1 })
  return { size, rgba }
}

export function webgl2Available() {
  try { return !!document.createElement('canvas').getContext('webgl2') } catch { return false }
}

const VS = `#version 300 es
in vec2 p; out vec2 uv;
void main() { uv = vec2((p.x + 1.0) * 0.5, (1.0 - p.y) * 0.5); gl_Position = vec4(p, 0.0, 1.0); }`
const FS = `#version 300 es
precision highp float; precision highp sampler3D;
uniform sampler2D frame; uniform sampler3D lut; uniform float size; uniform bool enabled;
in vec2 uv; out vec4 o;
void main() {
  vec3 c = texture(frame, uv).rgb;
  // half-texel remap so 0 and 1 hit the first/last lattice points, not the edge
  vec3 q = c * ((size - 1.0) / size) + 0.5 / size;
  o = vec4(enabled ? texture(lut, q).rgb : c, 1.0);
}`

function compile(gl, type, src) {
  const s = gl.createShader(type)
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'shader')
  return s
}

// → {setLut(cube), redraw(), destroy()}; draws on every presented video frame.
export function createLutRenderer(canvas, video) {
  const gl = canvas.getContext('webgl2', { premultipliedAlpha: false, preserveDrawingBuffer: false })
  if (!gl) throw new Error('WebGL2 unavailable')
  const prog = gl.createProgram()
  gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS))
  gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS))
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link')
  gl.useProgram(prog)

  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  const loc = gl.getAttribLocation(prog, 'p')
  gl.enableVertexAttribArray(loc)
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)

  const frameTex = gl.createTexture()
  gl.activeTexture(gl.TEXTURE0)
  gl.bindTexture(gl.TEXTURE_2D, frameTex)
  for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
    [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v)
  const lutTex = gl.createTexture()
  gl.uniform1i(gl.getUniformLocation(prog, 'frame'), 0)
  gl.uniform1i(gl.getUniformLocation(prog, 'lut'), 1)
  const uSize = gl.getUniformLocation(prog, 'size')
  const uEnabled = gl.getUniformLocation(prog, 'enabled')
  let hasLut = false
  let dead = false
  let rvfc = 0
  let raf = 0

  function setLut(cube) {
    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_3D, lutTex)
    // RGBA16F is filterable in core WebGL2 (32F would need OES_texture_float_linear)
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA16F, cube.size, cube.size, cube.size, 0, gl.RGBA, gl.FLOAT, cube.rgba)
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_3D, k, v)
    gl.uniform1f(uSize, cube.size)
    hasLut = true
    redraw()
  }

  function redraw() {
    if (dead || video.readyState < 2 || !video.videoWidth) return
    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
    }
    gl.viewport(0, 0, canvas.width, canvas.height)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, frameTex)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video)
    gl.uniform1i(uEnabled, hasLut ? 1 : 0)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  // Every presented frame when rVFC exists (Safari/WKWebView + Chromium), else rAF.
  function loop() {
    if (dead) return
    redraw()
    if (video.requestVideoFrameCallback) rvfc = video.requestVideoFrameCallback(loop)
    else raf = requestAnimationFrame(loop)
  }
  const onStill = () => redraw()
  video.addEventListener('seeked', onStill)
  video.addEventListener('loadeddata', onStill)
  loop()

  function destroy() {
    dead = true
    if (rvfc && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(rvfc)
    if (raf) cancelAnimationFrame(raf)
    video.removeEventListener('seeked', onStill)
    video.removeEventListener('loadeddata', onStill)
    const lose = gl.getExtension('WEBGL_lose_context')
    if (lose) lose.loseContext()
  }
  return { setLut, redraw, destroy }
}
