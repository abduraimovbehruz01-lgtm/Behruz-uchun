"""Manga-video: manga sahifalaridan panellarni kesadi va o'zbekcha subtitrli vertikal (9:16) video yasaydi.

Qismlar:
  detect_panels  — sahifalarni (webtoon bo'lsa ketma-ket ulab) bo'sh oraliqlar bo'yicha panellarga kesadi
  contact_sheets — raqamlangan panellar ro'yxati (rasm), matn yozish uchun
  prepare_clip   — bitta panel uchun kadr: xira fon + panel yoki uzun panelni pastga surish
  render_clip    — ffmpeg: zoom/surish + yuqori chapda «Telegram belgisi + MANHORA» + pastda subtitr
  concat_clips, mux_music — yig'ish va qo'shiq qo'shish
"""
import asyncio
import logging
import os
import re
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

import video

log = logging.getLogger("manga")

Image.MAX_IMAGE_PIXELS = 400_000_000

W = int(os.environ.get("MANGA_W", "720"))          # video eni; bo'yi 16:9 ga mos (720x1280)
H = int(round(W * 16 / 9 / 2)) * 2
FPS = int(os.environ.get("MANGA_FPS", "24"))
BADGE = os.environ.get("MANGA_BADGE", "MANHORA").strip() or "MANHORA"
MIN_SEC = float(os.environ.get("MANGA_MIN_SEC", "2.2"))          # matnli panel eng kamida shuncha ko'rinadi
SEC_PER_CHAR = float(os.environ.get("MANGA_SEC_PER_CHAR", "0.075"))
NO_TEXT_SEC = float(os.environ.get("MANGA_NO_TEXT_SEC", "1.8"))  # matnsiz panel
MAX_SEC = 9.0
PAN_SPEED = float(os.environ.get("MANGA_PAN_SPEED", "260"))      # uzun panel pastga surilish tezligi (px/s)
ZOOM = 0.07                                                      # sekin yaqinlashish miqdori
MAX_PANELS = int(os.environ.get("MANGA_MAX_PANELS", "200"))
CRF = os.environ.get("MANGA_CRF", "21")
FONT = os.environ.get("MANGA_FONT") or video.FONT_BOLD
AW = 480        # tahlil uchun sahifa eni (piksel)
SS = 2          # kadr sifati: zoom uchun 2x kattalikda tayyorlanadi

IMG_EXT = re.compile(r"\.(jpe?g|png|webp|bmp|gif|tiff?)$", re.I)


# ---------------------------------------------------------------- fayllar

def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def extract_zip(zip_path, outdir):
    """ZIP/CBZ ichidagi rasmlarni (papkalarni tekislab, tabiiy tartibda) outdir ga chiqaradi."""
    os.makedirs(outdir, exist_ok=True)
    res = []
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/") and IMG_EXT.search(n)
                 and "__MACOSX" not in n and not os.path.basename(n).startswith(".")]
        names.sort(key=natural_key)
        for i, n in enumerate(names):
            ext = os.path.splitext(n)[1].lower()
            dst = os.path.join(outdir, f"{i:04d}{ext}")
            with z.open(n) as src, open(dst, "wb") as f:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            res.append(dst)
    return res


def _open_rgb(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


# ---------------------------------------------------------------- panellarni topish

def _gray_small(path):
    """(kulrang massiv AW eniga keltirilgan, asl eni, asl bo'yi)."""
    im = Image.open(path)
    ow, oh = im.size
    try:
        im.draft("RGB", (AW * 2, max(AW * 2, int(AW * 2 * oh / max(1, ow)))))
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    g = im.convert("L")
    h = max(8, round(oh * AW / ow))
    g = g.resize((AW, h), Image.BOX)
    return np.asarray(g, dtype=np.uint8), ow, oh


def _flat_lines(a, axis):
    """Har qator (axis=1) yoki ustun (axis=0) bo'yicha: yassi (oq/qora fon, ya'ni bo'sh oraliq) bo'lsa True."""
    f = a.astype(np.float32)
    std = f.std(axis=axis)
    mean = f.mean(axis=axis)
    return (std < 6.0) & ((mean > 228) | (mean < 40))


def _segments(flags, min_gap, min_seg):
    """flags True = bo'sh oraliq. Kamida min_gap qalinlikdagi oraliqlar bilan ajralgan bo'laklar ro'yxati;
    min_seg dan kichik bo'laklar qo'shnisiga qo'shiladi."""
    idx = np.flatnonzero(~flags)
    if idx.size == 0:
        return []
    br = np.flatnonzero(np.diff(idx) - 1 >= min_gap)
    starts = np.r_[idx[0], idx[br + 1]]
    ends = np.r_[idx[br], idx[-1]] + 1
    segs = [[int(s), int(e)] for s, e in zip(starts, ends)]
    out = []
    for s, e in segs:
        if out and (e - s) < min_seg:
            out[-1][1] = e
        else:
            out.append([s, e])
    if len(out) > 1 and (out[0][1] - out[0][0]) < min_seg:
        out[1][0] = out[0][0]
        out.pop(0)
    return [(s, e) for s, e in out]


def _cut(a, box, depth, rtl, out):
    x0, y0, x1, y1 = box
    sub = a[y0:y1, x0:x1]
    h, w = sub.shape
    if h < 12 or w < 12 or depth > 8:
        return
    segs = _segments(_flat_lines(sub, 1), min_gap=5, min_seg=24)
    if not segs:
        return
    if len(segs) > 1:
        for s, e in segs:
            _cut(a, (x0, y0 + s, x1, y0 + e), depth + 1, rtl, out)
        return
    ya, yb = segs[0]
    sub2 = sub[ya:yb]
    csegs = _segments(_flat_lines(sub2, 0), min_gap=5, min_seg=max(40, int(0.12 * w)))
    if not csegs:
        return
    if len(csegs) > 1:
        if rtl:
            csegs = csegs[::-1]
        for c0, c1 in csegs:
            _cut(a, (x0 + c0, y0 + ya, x0 + c1, y0 + yb), depth + 1, rtl, out)
        return
    c0, c1 = csegs[0]
    out.append((x0 + c0, y0 + ya, x0 + c1, y0 + yb))


class _Sheet:
    """Bitta yoki bir nechta ketma-ket sahifa (webtoon bo'laklari) bitta uzun varaq qilib ulangan."""

    def __init__(self):
        self.pieces = []   # {"path", "ow", "oh", "ys", "ye"}
        self.rows = []

    def add(self, path, g, ow, oh):
        ys = sum(r.shape[0] for r in self.rows)
        self.rows.append(g)
        self.pieces.append({"path": path, "ow": ow, "oh": oh, "ys": ys, "ye": ys + g.shape[0]})

    @property
    def gray(self):
        return np.vstack(self.rows) if len(self.rows) > 1 else self.rows[0]


_IMG_CACHE = {}


def _cached_rgb(path):
    im = _IMG_CACHE.get(path)
    if im is None:
        if len(_IMG_CACHE) >= 3:
            _IMG_CACHE.pop(next(iter(_IMG_CACHE)))
        im = _IMG_CACHE[path] = _open_rgb(path)
    return im


def _crop_sheet(sheet, box, max_w=1440):
    x0, y0, x1, y1 = box
    over = [p for p in sheet.pieces if p["ye"] > y0 and p["ys"] < y1]
    ref = max(p["ow"] for p in over) / AW
    ow = int(min(max_w, max(8, round((x1 - x0) * ref))))
    k = ow / (x1 - x0)
    oh = max(8, int(round((y1 - y0) * k)))
    canvas = Image.new("RGB", (ow, oh), (255, 255, 255))
    for p in over:
        ya, yb = max(y0, p["ys"]), min(y1, p["ye"])
        sc = p["ow"] / AW
        im = _cached_rgb(p["path"])
        src = im.crop((int(round(x0 * sc)), int(round((ya - p["ys"]) * sc)),
                       int(round(x1 * sc)), int(round((yb - p["ys"]) * sc))))
        d0, d1 = int(round((ya - y0) * k)), int(round((yb - y0) * k))
        if src.width < 1 or src.height < 1 or d1 <= d0:
            continue
        canvas.paste(src.resize((ow, d1 - d0), Image.LANCZOS), (0, d0))
    return canvas


def _edge_flat(g):
    """Sahifaning eng tepasi va eng pastki qatorlari bo'sh (oq/qora) fonmi."""
    top = bool(_flat_lines(g[:3], 1).all())
    bot = bool(_flat_lines(g[-3:], 1).all())
    return top, bot


def build_sheets(pages):
    """Sahifalarni varaqlarga ajratadi: sahifalar chegarasida rasm uzilmasdan davom etsa (webtoon) — ulanadi."""
    sheets, prev = [], None
    for path in pages:
        try:
            g, ow, oh = _gray_small(path)
        except Exception as e:
            log.warning("sahifa o'qilmadi %s: %r", path, e)
            continue
        top, bot = _edge_flat(g)
        join = (prev is not None and not prev["bot"] and not top
                and abs(ow - prev["ow"]) <= 0.05 * max(ow, prev["ow"]))
        if not join:
            sheets.append(_Sheet())
        sheets[-1].add(path, g, ow, oh)
        prev = {"bot": bot, "ow": ow}
    return sheets


def detect_panels(pages, outdir, rtl=False):
    """Sahifalardan panellarni kesib, outdir ga p_001.jpg ... qilib saqlaydi.
    Qaytaradi: [{"path", "w", "h"}]. Hech narsa topilmasa — har varaq bitta panel."""
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        if f.startswith("p_") and f.endswith(".jpg"):
            os.remove(os.path.join(outdir, f))
    _IMG_CACHE.clear()
    sheets = build_sheets(pages)
    res = []
    for sh in sheets:
        a = sh.gray
        boxes = []
        _cut(a, (0, 0, a.shape[1], a.shape[0]), 0, rtl, boxes)
        keep = []
        for (x0, y0, x1, y1) in boxes:
            if (x1 - x0) < 40 or (y1 - y0) < 30:
                continue
            if float(a[y0:y1, x0:x1].std()) < 5.0:
                continue
            keep.append((x0, y0, x1, y1))
        if not keep:
            keep = [(0, 0, a.shape[1], a.shape[0])]
        for box in keep:
            if len(res) >= MAX_PANELS:
                break
            im = _crop_sheet(sh, box)
            path = os.path.join(outdir, f"p_{len(res) + 1:03d}.jpg")
            im.save(path, "JPEG", quality=92)
            res.append({"path": path, "w": im.width, "h": im.height})
    _IMG_CACHE.clear()
    return res


def contact_sheets(panels, outdir, per=20, cols=4):
    """Raqamlangan panellar ro'yxati (rasm): foydalanuvchi qaysi panelga qaysi matn yozishini ko'radi."""
    cw, ch, gap = 170, 240, 8
    font = ImageFont.truetype(FONT, 22) if os.path.exists(FONT) else ImageFont.load_default()
    paths = []
    for si in range(0, len(panels), per):
        chunk = panels[si:si + per]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cw + (cols + 1) * gap, rows * ch + (rows + 1) * gap), (24, 26, 32))
        d = ImageDraw.Draw(sheet)
        for i, p in enumerate(chunk):
            im = _open_rgb(p["path"])
            im.thumbnail((cw, ch), Image.LANCZOS)
            r, c = divmod(i, cols)
            x = gap + c * (cw + gap) + (cw - im.width) // 2
            y = gap + r * (ch + gap) + (ch - im.height) // 2
            sheet.paste(im, (x, y))
            n = str(si + i + 1)
            tw = d.textlength(n, font=font)
            bx, by = gap + c * (cw + gap), gap + r * (ch + gap)
            d.rounded_rectangle((bx, by, bx + tw + 18, by + 32), radius=8, fill=(42, 171, 238))
            d.text((bx + 9, by + 3), n, font=font, fill=(255, 255, 255))
        path = os.path.join(outdir, f"sheet_{si // per + 1}.jpg")
        sheet.save(path, "JPEG", quality=85)
        paths.append(path)
    return paths


# ---------------------------------------------------------------- kadr tayyorlash

def text_duration(text):
    if not text:
        return NO_TEXT_SEC
    return max(MIN_SEC, min(MAX_SEC, 0.9 + SEC_PER_CHAR * len(text)))


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=font) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _plane_icon(d):
    """Telegram uslubidagi belgi: ko'k doira ichida oq qog'oz samolyot (o'zimiz chizamiz)."""
    S = 4
    im = Image.new("RGBA", (d * S, d * S), (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    g.ellipse((0, 0, d * S - 1, d * S - 1), fill=(42, 171, 238, 255))
    P = lambda x, y: (x * d * S, y * d * S)
    g.polygon([P(0.20, 0.49), P(0.80, 0.25), P(0.66, 0.77), P(0.47, 0.60)], fill=(255, 255, 255, 255))
    g.polygon([P(0.47, 0.60), P(0.45, 0.76), P(0.55, 0.66)], fill=(201, 223, 238, 255))
    g.line([P(0.47, 0.60), P(0.80, 0.25)], fill=(201, 223, 238, 255), width=max(1, S))
    return im.resize((d, d), Image.LANCZOS)


def overlay_layer(text, out_path):
    """Kadr ustiga qo'yiladigan shaffof qatlam: yuqori chapda belgi + «MANHORA», pastda qalin oq subtitr."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    k = W / 720
    # nishon (badge)
    bf = _font(int(24 * k))
    icon = int(34 * k)
    tw = d.textlength(BADGE, font=bf)
    pad, gap = int(7 * k), int(9 * k)
    bw, bh = int(pad + icon + gap + tw + 16 * k), int(48 * k)
    bx, by = int(24 * k), int(78 * k)
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 2, fill=(18, 22, 30, 175))
    layer.alpha_composite(_plane_icon(icon), (bx + pad, by + (bh - icon) // 2))
    d.text((bx + pad + icon + gap, by + bh // 2), BADGE, font=bf, fill=(255, 255, 255, 255), anchor="lm")
    # subtitr
    text = " ".join((text or "").split()).upper()
    if text:
        maxw = W - int(80 * k)
        size = int(56 * k)
        while True:
            f = _font(size)
            lines = _wrap(d, text, f, maxw)
            widest = max(d.textlength(t, font=f) for t in lines)
            if (len(lines) <= 3 and widest <= maxw) or size <= int(34 * k):
                break
            size -= 2
        lh = int(size * 1.22)
        total = lh * len(lines)
        y0 = int(0.74 * H - total / 2)
        sw = max(3, size // 13)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for i, t in enumerate(lines):
            y = y0 + i * lh + lh // 2
            sd.text((W // 2, y + sw), t, font=f, fill=(0, 0, 0, 190), anchor="mm", stroke_width=sw, stroke_fill=(0, 0, 0, 190))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, sw)))
        layer.alpha_composite(shadow)
        d = ImageDraw.Draw(layer)
        for i, t in enumerate(lines):
            y = y0 + i * lh + lh // 2
            d.text((W // 2, y), t, font=f, fill=(255, 255, 255, 255), anchor="mm",
                   stroke_width=sw, stroke_fill=(10, 10, 14, 255))
    layer.save(out_path, "PNG")
    return out_path


def prepare_clip(panel_path, base_path):
    """Panel uchun asosiy kadr tayyorlaydi. Qaytaradi {"mode": "zoom"|"pan", "travel": px}.
    zoom: xira fon + panel (yoki to'ldirib) — sekin yaqinlashadi; pan: uzun panel yuqoridan pastga suriladi."""
    im = _open_rgb(panel_path)
    w, h = im.size
    sh = h * W / w
    if sh > 1.08 * H:
        im = im.resize((W, max(H + 2, int(round(sh)))), Image.LANCZOS)
        im.save(base_path, "JPEG", quality=94)
        return {"mode": "pan", "travel": im.height - H}
    cw, ch = W * SS, H * SS
    if sh >= 0.88 * H:      # deyarli butun kadrni to'ldiradi
        sc = max(cw / w, ch / h)
        big = im.resize((max(cw, round(w * sc)), max(ch, round(h * sc))), Image.LANCZOS)
        l, t = (big.width - cw) // 2, (big.height - ch) // 2
        frame = big.crop((l, t, l + cw, t + ch))
    else:                   # past/keng panel: orqada xira nusxa, o'rtada panel
        sc = max(cw / w, ch / h)
        small = im.resize((max(8, int(w * sc / 6)), max(8, int(h * sc / 6))), Image.BILINEAR)
        bgi = small.resize((max(cw, int(w * sc)), max(ch, int(h * sc))), Image.BILINEAR)
        l, t = (bgi.width - cw) // 2, (bgi.height - ch) // 2
        bgi = bgi.crop((l, t, l + cw, t + ch)).filter(ImageFilter.GaussianBlur(18 * SS))
        frame = ImageEnhance.Brightness(bgi).enhance(0.45)
        fw = cw
        fh = max(2, int(round(h * cw / w)))
        fg = im.resize((fw, fh), Image.LANCZOS)
        frame.paste(fg, (0, (ch - fh) // 2))
    frame.save(base_path, "JPEG", quality=93)
    return {"mode": "zoom", "travel": 0}


def clip_duration(text, info):
    d = text_duration(text)
    if info["mode"] == "pan":
        d = max(d, min(14.0, info["travel"] / PAN_SPEED + 0.9))
    return d


async def render_clip(base_path, overlay_path, out_path, dur, info, maxrate):
    n = max(2, int(round(dur * FPS)))
    if info["mode"] == "pan":
        ease = f"clip((t-0.45)/({dur:.3f}-0.9),0,1)"
        main = (f"[0:v]crop={W}:{H}:0:'(ih-oh)*({ease})*({ease})*(3-2*({ease}))',setsar=1,format=yuv420p[a]")
        pre = ["-loop", "1", "-framerate", str(FPS), "-i", base_path]
    else:
        main = (f"[0:v]zoompan=z='1+{ZOOM}*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS},"
                "setsar=1,format=yuv420p[a]")
        pre = ["-i", base_path]
    fc = (main + ";[1:v]format=rgba,fade=t=in:st=0.05:d=0.25:alpha=1[o];"
          "[a][o]overlay=0:0:shortest=1,format=yuv420p[v]")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", video.THREADS, *pre,
           "-loop", "1", "-framerate", str(FPS), "-i", overlay_path,
           "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS), "-an",
           "-c:v", "libx264", "-preset", video.FINAL_PRESET, "-crf", CRF,
           "-maxrate", f"{maxrate}k", "-bufsize", f"{2 * maxrate}k", "-g", str(FPS * 2),
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-threads", video.THREADS,
           "-video_track_timescale", str(FPS * 1000), out_path]
    await video.run(cmd, timeout=max(900.0, dur * 40))


async def concat_clips(files, out):
    await video.concat(files, out)


async def mux_music(video_in, out, total, music=None, target=-14.0):
    """Tayyor (ovozsiz) videoga qo'shiq qo'shadi: boshida/oxirida yumshoq kirish-chiqish, balandligi −14 LUFS atrofida.
    music: {"path", "start", "rms"}. Qo'shiq bo'lmasa — video shunchaki qayta o'raladi."""
    total = float(total)
    if not music:
        await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video_in, "-c", "copy",
                         "-movflags", "+faststart", out], timeout=900)
        return

    async def mux(extra):
        g = max(-30.0, min(24.0, -17.0 - float(music.get("rms", -20.0)))) + extra
        fc = (f"[1:a]atrim=start={float(music.get('start', 0)):.2f},asetpts=PTS-STARTPTS,aresample=44100,"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={g:.1f}dB,"
              f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0.0, total - 2.5):.2f}:d=2.5,"
              f"atrim=end={total:.3f},alimiter=limit=0.84:attack=5:release=60:level=false[a]")
        await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video_in,
                         "-stream_loop", "-1", "-i", music["path"], "-filter_complex", fc,
                         "-map", "0:v:0", "-map", "[a]", "-t", f"{total:.3f}", "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", out],
                        timeout=1800)

    await mux(0.0)
    lufs = await video.measure_lufs(out)
    if lufs is not None and abs(lufs - target) > 1.0:
        await mux(max(-10.0, min(14.0, target - lufs)))


async def render_video(panels, texts, workdir, on_progress=None):
    """Panellar + matnlar → ovozsiz video (concat). Qaytaradi (yo'l, jami soniya)."""
    cdir = os.path.join(workdir, "clips")
    os.makedirs(cdir, exist_ok=True)
    infos, durs = [], []
    for i, p in enumerate(panels):
        info = await asyncio.to_thread(prepare_clip, p["path"], os.path.join(cdir, f"b_{i:03d}.jpg"))
        infos.append(info)
        durs.append(clip_duration(texts[i] if i < len(texts) else "", info))
    total = sum(durs)
    mr = video.video_maxrate(total)
    files = []
    for i, p in enumerate(panels):
        t = texts[i] if i < len(texts) else ""
        ov = await asyncio.to_thread(overlay_layer, t, os.path.join(cdir, f"o_{i:03d}.png"))
        out = os.path.join(cdir, f"c_{i:03d}.mp4")
        await render_clip(os.path.join(cdir, f"b_{i:03d}.jpg"), ov, out, durs[i], infos[i], mr)
        files.append(out)
        if on_progress:
            await on_progress(i + 1, len(panels))
    silent = os.path.join(workdir, "manga_silent.mp4")
    await concat_clips(files, silent)
    return silent, total
