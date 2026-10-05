# arkiv UI tour: eased camera moves over the real screenshot + captions.
# Usage: python3 site/scripts/make-ui-tour.py site/public/img/arkiv-ui.webp site/public/video/arkiv-ui-tour.mp4
# Needs Pillow, ffmpeg (libx264), Noto Sans CJK TC (TOUR_FONT / TOUR_MONO to override).
# Placeholder until a real screen recording of a clean demo library replaces it (Hevin 2026-10-05).
# Source: site/public/img/arkiv-ui.webp (3200x1780, real arkiv v1.2.0 screenshot).
import math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

SRC = sys.argv[1]; OUT = sys.argv[2]
# Captions are rendered by the page (HTML, synced to currentTime) so they stay
# legible on a phone; set BURN_CAPTIONS=1 to bake them into the frames instead.
BURN = os.environ.get('BURN_CAPTIONS') == '1'
W, H, FPS = 1600, 890, 30
src = Image.open(SRC).convert('RGB')          # 3200x1780 = 2x of the 1600x890 layout
SX = src.width / W
FONT = ImageFont.truetype(os.environ.get('TOUR_FONT', os.path.expanduser('~/Library/Fonts/NotoSansCJKtc-Bold.otf')), 34)
MONO = ImageFont.truetype(os.environ.get('TOUR_MONO', os.path.expanduser('~/Library/Fonts/NotoSansMonoCJKtc-Bold.otf')), 22)
CYAN = (24, 182, 220); INK = (243, 242, 238)

FULL = (800, 445, 1.0)
SHOTS = [  # (cx, cy, zoom) in 1600x890 layout coords, caption
    ((430, 40, 2.6),  '口語搜尋：語意和條件寫在同一行'),
    ((180, 340, 2.0), 'Smart Pool 自動分池，拍攝日一路點到某一天'),
    ((900, 520, 2.0), 'GOOD／REV／N·G 直接標在素材上'),
    ((1420, 280, 2.4), '視覺模型逐場景寫描述，沒有對白也搜得到'),
    ((1420, 740, 2.6), '一鍵匯出 EDL、FCPXML、SRT'),
]
MOVE, HOLD, START_HOLD, END_HOLD = 1.2, 2.6, 2.0, 1.0
# timeline of keyframes: (t, (cx,cy,z), caption_or_None, index)
keys = [(0.0, FULL, None, 0), (START_HOLD, FULL, None, 0)]
t = START_HOLD
for i, (cam, cap) in enumerate(SHOTS, 1):
    t += MOVE; keys.append((t, cam, cap, i)); t += HOLD; keys.append((t, cam, cap, i))
t += MOVE; keys.append((t, FULL, None, 0)); t += END_HOLD; keys.append((t, FULL, None, 0))
TOTAL = t

def ease(u): return u * u * (3 - 2 * u)   # smoothstep

def cam_at(tt):
    for (t0, c0, _, _), (t1, c1, _, _) in zip(keys, keys[1:]):
        if t0 <= tt <= t1:
            u = 0 if t1 == t0 else ease((tt - t0) / (t1 - t0))
            z = math.exp(math.log(c0[2]) + (math.log(c1[2]) - math.log(c0[2])) * u)
            return (c0[0] + (c1[0] - c0[0]) * u, c0[1] + (c1[1] - c0[1]) * u, z)
    return FULL

def caption_at(tt):
    # caption shows only while holding on a shot; fades 0.3s in/out
    for (t0, c0, cap, idx), (t1, c1, cap1, _) in zip(keys, keys[1:]):
        if cap and cap == cap1 and t0 <= tt <= t1:
            a = min(1, (tt - t0) / 0.3, (t1 - tt) / 0.3)
            return cap, idx, max(0.0, a)
    return None, 0, 0.0

def frame(tt):
    cx, cy, z = cam_at(tt)
    w, h = W / z, H / z
    x0 = min(max(cx - w / 2, 0), W - w); y0 = min(max(cy - h / 2, 0), H - h)
    box = tuple(round(v * SX) for v in (x0, y0, x0 + w, y0 + h))
    im = src.crop(box).resize((W, H), Image.LANCZOS)
    cap, idx, a = caption_at(tt)
    if BURN and cap and a > 0:
        ov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
        tw = d.textlength(cap, font=FONT); pad = 26
        bx0, by1 = 48, H - 48; bx1, by0 = bx0 + tw + pad * 2 + 70, by1 - 76
        d.rounded_rectangle((bx0, by0, bx1, by1), 14, fill=(10, 10, 12, int(225 * a)))
        d.text((bx0 + pad, by0 + 25), f'{idx:02d}', font=MONO, fill=CYAN + (int(255 * a),))
        d.text((bx0 + pad + 62, by0 + 14), cap, font=FONT, fill=INK + (int(255 * a),))
        im = Image.alpha_composite(im.convert('RGBA'), ov).convert('RGB')
    return im

n = round(TOTAL * FPS)
ff = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
    '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'slow', '-crf', '28',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', OUT], stdin=subprocess.PIPE)
for k in range(n):
    ff.stdin.write(frame(k / FPS).tobytes())
ff.stdin.close(); ff.wait()
print(f'{n} frames, {TOTAL:.1f}s -> {OUT}')
# Caption windows for the page script (seconds): the hold on each shot.
for (t0, _, cap, idx), (t1, _, cap1, _) in zip(keys, keys[1:]):
    if cap and cap == cap1: print(f'  {idx:02d} {t0:.1f}-{t1:.1f} {cap}')
