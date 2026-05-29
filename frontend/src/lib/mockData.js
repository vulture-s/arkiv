// Mock data — verbatim from design-2026-05-27/components.jsx.
// Screens consume these until real API wiring (supervised phase).

export const MEDIA = [
  { id: 1,  name: 'A7S3_C001_240515.mov',   dur: '00:02:47', size: '1.4 GB', rating: 'good', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 24-70 GM',  iso: 800,  ap: 'f/2.8', fl: '35mm', fps: 24, res: '3840×2160', tags: ['cycling','road','interview'], lang: 'zh' },
  { id: 2,  name: 'A7S3_C002_240515.mov',   dur: '00:00:32', size: '184 MB', rating: 'ng',   kind: 'video', cam: 'Sony α7S III',  lens: 'FE 24-70 GM',  iso: 1600, ap: 'f/2.8', fl: '24mm', fps: 24, res: '3840×2160', tags: ['cycling','b-roll'], lang: 'zh' },
  { id: 3,  name: 'INTERVIEW_HEVIN_01.wav', dur: '00:18:04', size: '92 MB',  rating: 'good', kind: 'audio', cam: 'Zoom F3',      lens: 'Sennheiser MKH416', iso: '—', ap: '—',    fl: '—',  fps: '—', res: '48kHz 24bit',  tags: ['interview','podcast','zh'], lang: 'zh' },
  { id: 4,  name: 'A7S3_C003_240516.mov',   dur: '00:01:12', size: '512 MB', rating: 'rev',  kind: 'video', cam: 'Sony α7S III',  lens: 'FE 70-200 GM', iso: 400,  ap: 'f/4',   fl: '135mm', fps: 24, res: '3840×2160', tags: ['portrait','golden hour'], lang: 'zh' },
  { id: 5,  name: 'GH6_4824.mp4',           dur: '00:00:08', size: '64 MB',  rating: 'good', kind: 'video', cam: 'Panasonic GH6', lens: 'Leica 12-60',  iso: 200,  ap: 'f/4',   fl: '12mm', fps: 60, res: '3840×2160', tags: ['b-roll','landscape','slow-mo'], lang: 'en' },
  { id: 6,  name: 'A7S3_C004_240516.mov',   dur: '00:03:21', size: '1.7 GB', rating: 'none', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 24-70 GM',  iso: 800,  ap: 'f/2.8', fl: '50mm', fps: 24, res: '3840×2160', tags: ['product','tabletop'], lang: 'ja' },
  { id: 7,  name: 'GH6_4825.mp4',           dur: '00:00:14', size: '78 MB',  rating: 'good', kind: 'video', cam: 'Panasonic GH6', lens: 'Leica 12-60',  iso: 400,  ap: 'f/5.6', fl: '35mm', fps: 60, res: '3840×2160', tags: ['b-roll','street'], lang: 'en' },
  { id: 8,  name: 'INTERVIEW_HEVIN_02.wav', dur: '00:24:18', size: '124 MB', rating: 'rev',  kind: 'audio', cam: 'Zoom F3',      lens: 'Sennheiser MKH416', iso: '—', ap: '—',    fl: '—',  fps: '—', res: '48kHz 24bit',  tags: ['interview','podcast'], lang: 'zh' },
  { id: 9,  name: 'A7S3_C005_240517.mov',   dur: '00:00:42', size: '256 MB', rating: 'good', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 35 1.4',    iso: 1250, ap: 'f/1.4', fl: '35mm', fps: 24, res: '3840×2160', tags: ['portrait','night','available light'], lang: 'zh' },
  { id: 10, name: 'A7S3_C006_240517.mov',   dur: '00:01:48', size: '780 MB', rating: 'none', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 35 1.4',    iso: 1600, ap: 'f/1.4', fl: '35mm', fps: 24, res: '3840×2160', tags: ['portrait','night'], lang: 'zh' },
  { id: 11, name: 'GH6_4826.mp4',           dur: '00:00:22', size: '108 MB', rating: 'ng',   kind: 'video', cam: 'Panasonic GH6', lens: 'Leica 12-60',  iso: 1600, ap: 'f/4',   fl: '60mm', fps: 60, res: '3840×2160', tags: ['b-roll','rejected'], lang: 'en' },
  { id: 12, name: 'A7S3_C007_240518.mov',   dur: '00:02:04', size: '1.1 GB', rating: 'good', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 24-70 GM',  iso: 640,  ap: 'f/4',   fl: '70mm', fps: 24, res: '3840×2160', tags: ['cycling','tibet','documentary'], lang: 'zh' },
  { id: 13, name: 'BACKGROUND_AMB.wav',     dur: '00:08:32', size: '46 MB',  rating: 'good', kind: 'audio', cam: 'Zoom H6',      lens: 'XY pair',           iso: '—', ap: '—',    fl: '—',  fps: '—', res: '48kHz 24bit',  tags: ['ambient','nature'], lang: '—' },
  { id: 14, name: 'A7S3_C008_240518.mov',   dur: '00:04:16', size: '2.1 GB', rating: 'good', kind: 'video', cam: 'Sony α7S III',  lens: 'FE 70-200 GM', iso: 400,  ap: 'f/4',   fl: '200mm', fps: 24, res: '3840×2160', tags: ['cycling','tibet','wide'], lang: 'zh' },
  { id: 15, name: 'GH6_4827.mp4',           dur: '00:00:19', size: '88 MB',  rating: 'none', kind: 'video', cam: 'Panasonic GH6', lens: 'Leica 12-60',  iso: 800,  ap: 'f/4',   fl: '24mm', fps: 60, res: '3840×2160', tags: ['b-roll'], lang: 'en' },
  { id: 16, name: 'A7S3_C009_240519.mov',   dur: '00:01:03', size: '420 MB', rating: 'rev',  kind: 'video', cam: 'Sony α7S III',  lens: 'FE 24-70 GM',  iso: 800,  ap: 'f/4',   fl: '50mm', fps: 24, res: '3840×2160', tags: ['product','interview'], lang: 'ja' },
]

export const PROJECTS = [
  { id: 'bd', name: 'Bicycle Diaries', count: 247, active: true,  size: '4.8 TB' },
  { id: 'vr', name: 'vulture.s reels', count: 89,  active: false, size: '1.2 TB' },
  { id: 'fr', name: 'Furutech RCA spot', count: 152, active: false, size: '2.1 TB' },
  { id: 'kq', name: 'KOL_2026Q1', count: 38, active: false, size: '—', health: 'NAS unmounted' },
]

export const TAGS = [
  { name: 'cycling', count: 88 },
  { name: 'portrait', count: 41 },
  { name: 'product', count: 62 },
  { name: 'interview', count: 23 },
  { name: 'tibet', count: 19 },
  { name: 'b-roll', count: 71 },
  { name: 'ambient', count: 12 },
  { name: 'documentary', count: 28 },
]
