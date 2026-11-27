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
import math
import os
import re
import wave
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
VOICE_GAP_SEC = float(os.environ.get("MANGA_VOICE_GAP", "1.3"))   # ovozli videoda matnsiz panel
VOICE_TAIL_SEC = 0.8                                                # ovozli video oxirida biroz jimlik
ZOOM = 0.07                                                      # sekin yaqinlashish miqdori
MAX_PANELS = int(os.environ.get("MANGA_MAX_PANELS", "200"))
CRF = os.environ.get("MANGA_CRF", "21")
FONT = os.environ.get("MANGA_FONT") or video.FONT_BOLD
AW = 480        # tahlil uchun sahifa eni (piksel)
SS = 2          # kadr sifati: zoom uchun 2x kattalikda tayyorlanadi

IMG_EXT = re.compile(r"\.(jpe?g|png|webp|bmp|gif|tiff?)$", re.I)
PDF_W = 1080                                                         # PDF sahifalari shu enga keltiriladi
SLICE_AT = int(os.environ.get("MANGA_SLICE_AT", "9000"))             # eni 1080 da shundan baland bo'lsa — bo'linadi
PDF_AREA = float(os.environ.get("MANGA_PDF_AREA", "24")) * 1e6     # uzun sahifa shu maydondan (px) oshmaydigan enda
PDF_MIN_W = 600                                                      # uzun sahifa eni bundan kichik qilinmaydi
SLICE_AREA = 6e6                                                     # bitta bo'lak maydoni (px)
ROW_BUDGET = int(os.environ.get("MANGA_ROW_BUDGET", "120000"))       # bitta PDF: tahlil qatorlari chegarasi
MAX_RATIO = float(os.environ.get("MANGA_MAX_RATIO", "4.5"))          # bo'yi enidan shuncha marta katta panel bo'linadi
PIECE_RATIO = 3.0                                                    # bo'lingan panel bo'lagining taxminiy nisbati


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


def _pdf_sizes(pdf_path, max_pages):
    """[(sahifa, eni_pt, bo'yi_pt)] — CropBox bo'yicha, aylantirish hisobga olingan. O'qib bo'lmasa None."""
    import subprocess
    try:
        r = subprocess.run(["pdfinfo", "-box", "-f", "1", "-l", str(max_pages), pdf_path],
                           capture_output=True, text=True, timeout=180)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    crop, rot = {}, {}
    for line in r.stdout.splitlines():
        mc = re.match(r"Page\s+(\d+)\s+CropBox:\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", line)
        if mc:
            x0, y0, x1, y1 = (float(v) for v in mc.group(2, 3, 4, 5))
            crop[int(mc.group(1))] = (abs(x1 - x0), abs(y1 - y0))
            continue
        mr = re.match(r"Page\s+(\d+)\s+rot:\s+(-?\d+)", line)
        if mr:
            rot[int(mr.group(1))] = int(mr.group(2)) % 360
    out = []
    for p in sorted(crop):
        w, h = crop[p]
        if rot.get(p, 0) in (90, 270):
            w, h = h, w
        if w > 0 and h > 0:
            out.append((p, w, h))
    return out or None


def _pdftoppm(args, width, timeout=600):
    import subprocess
    r = subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=90", "-cropbox", "-scale-to-x", str(width),
                        "-scale-to-y", "-1", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        log.warning("pdftoppm xato: %s", err[-500:])
        if "password" in err.lower():
            raise RuntimeError("PDF parol bilan himoyalangan — parolsiz nusxasini yuboring")
        raise RuntimeError("PDF o‘qilmadi (fayl buzilgan yoki noto‘g‘ri formatda)")


def pdf_to_images(pdf_path, outdir, max_pages=300):
    """PDF sahifalarini rasmga aylantiradi (poppler). Oddiy sahifalar eni 1080 px.
    Juda uzun sahifa (webtoon) bo'lsa: JPEG 65 500 px dan baland bo'lolmaydi, poppler esa rasmni butun bo'yicha
    xotirada kattalashtiradi — shuning uchun en kichikroq tanlanadi (maydon ~24 Mpx, eni kamida 600 px) va sahifa
    bo'laklab chiziladi. Webtoon bo'laklari keyin ulanishi uchun bunday PDF ning hamma sahifalari bir xil enda.
    Qaytaradi: (rasmlar ro'yxati, foydalanuvchiga eslatma yoki None)."""
    os.makedirs(outdir, exist_ok=True)
    sizes = _pdf_sizes(pdf_path, max_pages)
    if not sizes:      # o'lchamlarni bilib bo'lmadi — oddiy usul
        _pdftoppm(["-l", str(max_pages), pdf_path, os.path.join(outdir, "pg")], PDF_W, timeout=900)
        files = [os.path.join(outdir, f) for f in os.listdir(outdir) if f.startswith("pg") and f.endswith(".jpg")]
        return sorted(files, key=natural_key), None
    width = PDF_W
    for _p, w, h in sizes:
        if PDF_W * h / w > SLICE_AT:
            width = min(width, max(PDF_MIN_W, int(math.sqrt(PDF_AREA * w / h))))
    slice_h = int(max(2000, min(SLICE_AT, SLICE_AREA // width)))
    rows, note, run = 0.0, None, []

    def flush():       # ketma-ket oddiy sahifalar — bitta chaqiruvda
        if not run:
            return
        p0, p1 = run[0], run[-1]
        _pdftoppm(["-f", str(p0), "-l", str(p1), pdf_path, os.path.join(outdir, f"r{p0}")], width)
        for f in os.listdir(outdir):
            m = re.fullmatch(rf"r{p0}-(\d+)\.jpg", f)
            if m:
                os.replace(os.path.join(outdir, f), os.path.join(outdir, f"{int(m.group(1)):04d}_00.jpg"))
        run.clear()

    for p, w, h in sizes:
        ph = int(math.ceil(h * width / w - 1e-6))
        r = ph * AW / width
        if rows + r > ROW_BUDGET and rows > 0:
            note = (f"⚠️ PDF juda uzun: dastlabki {p - 1} ta sahifa olindi. "
                    f"Qolgan {sizes[-1][0] - p + 1} tasidan video kerak bo‘lsa, alohida yuboring.")
            break
        rows += r
        if ph <= SLICE_AT:
            if run and run[-1] != p - 1:
                flush()
            run.append(p)
            continue
        flush()
        n = int(math.ceil(ph / slice_h))
        step = int(math.ceil(ph / n))
        for k in range(n):
            y = k * step
            hh = min(step, ph - y)
            if hh < 4:
                continue
            _pdftoppm(["-f", str(p), "-l", str(p), "-singlefile", "-x", "0", "-y", str(y), "-W", str(width),
                       "-H", str(hh), pdf_path, os.path.join(outdir, f"{p:04d}_{k:02d}")], width, timeout=300)
    flush()
    files = [os.path.join(outdir, f) for f in os.listdir(outdir) if re.fullmatch(r"\d{4}_\d{2}\.jpg", f)]
    return sorted(files, key=natural_key), note


def slice_tall(path):
    """Juda uzun rasmni (webtoon tasmasi) bo'laklarga bo'ladi (har biri ~6 Mpx). Qaytaradi: yo'llar ro'yxati.
    Rasm bir marta ochiladi, ortiqcha nusxalarsiz — bepul serverda xotira kam."""
    try:
        im = Image.open(path)
        w, h = im.size
        orient = im.getexif().get(0x0112, 1)
    except Exception:
        return [path]
    if orient in (5, 6, 7, 8):
        w, h = h, w
    if w < 8 or h * min(PDF_W, w) / w <= SLICE_AT:
        im.close()
        return [path]
    if im.format == "JPEG":
        while im.size[0] * im.size[1] > 60e6:          # juda katta JPEG — kichraytirib o'qiladi
            im.draft("RGB", (max(1, im.size[0] // 2), max(1, im.size[1] // 2)))
            if im.size[0] * im.size[1] > 60e6 and im.size[0] < 16:
                break
    elif w * h * max(1, len(im.getbands())) > 130e6:
        im.close()
        raise ValueError(f"rasm juda katta ({w}×{h}). Uni bir nechta qismga bo‘lib yuboring")
    if orient != 1:
        im = ImageOps.exif_transpose(im)
    im.load()
    width, height = im.size
    out_w = min(PDF_W, width)
    piece_h = int(max(2000, min(SLICE_AT, SLICE_AREA // max(1, out_w)))) * width // out_w
    n = int(math.ceil(height / piece_h))
    step = int(math.ceil(height / n))
    base = os.path.splitext(path)[0]
    out = []
    for k in range(n):
        piece = _to_rgb(im.crop((0, k * step, width, min(height, (k + 1) * step))))
        if piece.width > out_w:
            piece = piece.resize((out_w, max(1, round(piece.height * out_w / piece.width))), Image.LANCZOS)
        q = f"{base}_s{k:02d}.jpg"
        piece.save(q, "JPEG", quality=92)
        out.append(q)
        del piece
    im.close()
    del im
    try:
        os.remove(path)
    except OSError:
        pass
    return out


def _to_rgb(im):
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im if im.mode == "RGB" else im.convert("RGB")


def _open_rgb(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return _to_rgb(im)


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


def _flat_lines(a, axis, chunk=2048):
    """Har qator (axis=1) yoki ustun (axis=0) bo'yicha: yassi (oq/qora fon, ya'ni bo'sh oraliq) bo'lsa True.
    Bo'laklab hisoblanadi — juda uzun webtoon varag'ida ham xotira kam ketadi."""
    if axis == 1:
        out = np.empty(a.shape[0], dtype=bool)
        for i in range(0, a.shape[0], chunk):
            f = a[i:i + chunk].astype(np.float32)
            std, mean = f.std(axis=1), f.mean(axis=1)
            out[i:i + chunk] = (std < 6.0) & ((mean > 228) | (mean < 40))
        return out
    n = max(1, a.shape[0])
    s1 = np.zeros(a.shape[1], dtype=np.float64)
    s2 = np.zeros(a.shape[1], dtype=np.float64)
    for i in range(0, a.shape[0], chunk):
        f = a[i:i + chunk].astype(np.float64)
        s1 += f.sum(axis=0)
        s2 += (f * f).sum(axis=0)
    mean = s1 / n
    std = np.sqrt(np.maximum(0.0, s2 / n - mean * mean))
    return (std < 6.0) & ((mean > 228) | (mean < 40))


def _row_std(a, chunk=2048):
    out = np.empty(a.shape[0], dtype=np.float32)
    for i in range(0, a.shape[0], chunk):
        out[i:i + chunk] = a[i:i + chunk].astype(np.float32).std(axis=1)
    return out


def _block_std(a, chunk=2048):
    n = a.size
    if n == 0:
        return 0.0
    s1 = s2 = 0.0
    for i in range(0, a.shape[0], chunk):
        f = a[i:i + chunk].astype(np.float64)
        s1 += float(f.sum())
        s2 += float((f * f).sum())
    m = s1 / n
    return math.sqrt(max(0.0, s2 / n - m * m))


def _split_tall(a, box):
    """Juda baland panelni (oraliqsiz uzun rasm) bir nechta bo'lakka bo'ladi: kesish chizig'i iloji boricha
    «tinch» (kam chiziqli) qatorga tushadi. Video uchun ham, xotira uchun ham kerak."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= MAX_RATIO * w:
        return [box]
    n = int(math.ceil(h / (PIECE_RATIO * w)))
    step = h / n
    busy = _row_std(a[y0:y1, x0:x1])
    if busy.size >= 5:
        busy = np.convolve(busy, np.ones(5, dtype=np.float32) / 5, mode="same")
    cuts = [0]
    for i in range(1, n):
        ideal = int(i * step)
        lo = max(cuts[-1] + int(0.5 * step), ideal - int(0.3 * step))
        hi = min(h - int(0.5 * step), ideal + int(0.3 * step))
        if hi - lo < 2:
            cuts.append(ideal)
            continue
        rows = np.arange(lo, hi)
        score = busy[lo:hi] + 4.0 * np.abs(rows - ideal) / step
        cuts.append(int(lo + int(np.argmin(score))))
    cuts.append(h)
    return [(x0, y0 + cuts[i], x1, y0 + cuts[i + 1]) for i in range(n) if cuts[i + 1] - cuts[i] > 4]


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
        if len(_IMG_CACHE) >= 2:
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


def iter_sheets(pages):
    """Sahifalarni varaqlarga ajratadi: sahifalar chegarasida rasm uzilmasdan davom etsa (webtoon) — ulanadi.
    Varaqlar birma-bir qaytariladi — xotirada bir vaqtda faqat bittasi turadi."""
    cur, prev = None, None
    for path in pages:
        try:
            g, ow, oh = _gray_small(path)
        except Exception as e:
            log.warning("sahifa o'qilmadi %s: %r", path, e)
            continue
        top, bot = _edge_flat(g)
        join = (prev is not None and cur is not None and not prev["bot"] and not top
                and abs(ow - prev["ow"]) <= 0.05 * max(ow, prev["ow"]))
        if not join:
            if cur is not None:
                yield cur
            cur = _Sheet()
        cur.add(path, g, ow, oh)
        prev = {"bot": bot, "ow": ow}
    if cur is not None:
        yield cur


def build_sheets(pages):
    return list(iter_sheets(pages))


def detect_panels(pages, outdir, rtl=False):
    """Sahifalardan panellarni kesib, outdir ga p_001.jpg ... qilib saqlaydi.
    Qaytaradi: [{"path", "w", "h"}]. Hech narsa topilmasa — har varaq bitta panel."""
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        if f.startswith("p_") and f.endswith(".jpg"):
            os.remove(os.path.join(outdir, f))
    _IMG_CACHE.clear()
    res = []
    for sh in iter_sheets(pages):
        if len(res) >= MAX_PANELS:
            break
        a = sh.gray
        sh.rows = [a]
        boxes = []
        _cut(a, (0, 0, a.shape[1], a.shape[0]), 0, rtl, boxes)
        keep = []
        for box in boxes:
            for (x0, y0, x1, y1) in _split_tall(a, box):
                if (x1 - x0) < 40 or (y1 - y0) < 30:
                    continue
                if _block_std(a[y0:y1, x0:x1]) < 5.0:
                    continue
                keep.append((x0, y0, x1, y1))
        if not keep:
            keep = [b for b in _split_tall(a, (0, 0, a.shape[1], a.shape[0])) if (b[3] - b[1]) > 4]
        for box in keep:
            if len(res) >= MAX_PANELS:
                break
            im = _crop_sheet(sh, box)
            path = os.path.join(outdir, f"p_{len(res) + 1:03d}.jpg")
            im.save(path, "JPEG", quality=92)
            res.append({"path": path, "w": im.width, "h": im.height})
        sh.rows = []
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


def _cover(im, cw, ch, resample=Image.LANCZOS):
    """Rasmni cw x ch kadrni to'liq qoplaydigan qilib (markazdan) kattalashtiradi — oraliq ulkan rasm yasamasdan."""
    w, h = im.size
    sc = max(cw / w, ch / h)
    vw, vh = cw / sc, ch / sc
    l, t = (w - vw) / 2, (h - vh) / 2
    return im.resize((cw, ch), resample, box=(l, t, l + vw, t + vh))


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
        frame = _cover(im, cw, ch)
    else:                   # past/keng panel: orqada xira nusxa, o'rtada panel
        tiny = _cover(im, max(8, cw // 16), max(8, ch // 16), Image.BILINEAR)
        tiny = tiny.filter(ImageFilter.GaussianBlur(2.2))
        bgi = tiny.resize((cw, ch), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6 * SS))
        frame = ImageEnhance.Brightness(bgi).enhance(0.45)
        fh = max(2, int(round(h * cw / w)))
        fg = im.resize((cw, fh), Image.LANCZOS)
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


def min_duration(info):
    """Ovozli videoda panel eng kamida qancha turishi kerak (uzun panel juda tez surilib ketmasin)."""
    if info["mode"] == "pan":
        return info["travel"] / (2.0 * PAN_SPEED) + 0.6
    return 0.9


async def prepare_all(panels, workdir):
    """Har panel uchun asosiy kadr (b_XXX.jpg). Qaytaradi: infos ro'yxati."""
    cdir = os.path.join(workdir, "clips")
    os.makedirs(cdir, exist_ok=True)
    infos = []
    for i, p in enumerate(panels):
        infos.append(await asyncio.to_thread(prepare_clip, p["path"], os.path.join(cdir, f"b_{i:03d}.jpg")))
    return infos


async def render_video(panels, texts, workdir, on_progress=None, durs=None, infos=None):
    """Panellar + matnlar → ovozsiz video (concat). Qaytaradi (yo'l, jami soniya).
    durs — har panel davomiyligi (ovozli videoda ovozga qarab); bo'lmasa matn uzunligidan hisoblanadi.
    infos — prepare_all natijasi (qayta hisoblamaslik uchun)."""
    cdir = os.path.join(workdir, "clips")
    os.makedirs(cdir, exist_ok=True)
    if infos is None or len(infos) != len(panels):
        infos = await prepare_all(panels, workdir)
    if durs is None:
        durs = [clip_duration(texts[i] if i < len(texts) else "", infos[i]) for i in range(len(panels))]
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


# ---------------------------------------------------------------- ovoz

def snap_into_pauses(segs, pauses, lead=0.15):
    """Qatorlar chegarasini pauza ichiga suradi (keyingi gap boshlanishidan ~0.15 s oldin) — so'z boshi kesilib,
    oldingi panelga o'tib qolmasin."""
    if len(segs) < 2:
        return segs
    edges = [segs[0][0]] + [b for _, b in segs]
    for i in range(1, len(edges) - 1):
        b = edges[i]
        for ps, pe in pauses:
            if ps - 0.02 <= b <= pe + 0.08:
                if pe - ps > 0.04:
                    nb = max(ps + 0.5 * (pe - ps), pe - lead)
                    if edges[i - 1] + 0.3 < nb < edges[i + 1] - 0.3:
                        edges[i] = nb
                break
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def plan_voice(texts, segs, min_durs):
    """Ovozli video rejasi. texts — panel matnlari; segs — aytilgan (bo'sh bo'lmagan) qatorlarning ovozdagi
    (boshlanish, tugash) vaqtlari, tartib bilan; min_durs — har panel uchun eng kam davomiylik.
    Qaytaradi (durs, pieces): pieces[i] = (a, b) — panel ostida ovozning shu qismi, None — jimlik."""
    durs, pieces, k = [], [], 0
    for i, t in enumerate(texts):
        if t and k < len(segs):
            a, b = segs[k]
            k += 1
            durs.append(max(b - a, min_durs[i]))
            pieces.append((a, b))
        else:
            durs.append(max(VOICE_GAP_SEC, min_durs[i]))
            pieces.append(None)
    if durs:
        durs[-1] += VOICE_TAIL_SEC
    return durs, pieces


async def build_voice_track(src_audio, durs, pieces, out_wav, workdir, sr=44100):
    """Panellar tartibida ovoz yo'lagi: har panel ostiga ovozning o'z qismi (qolgani jimlik). Qaytaradi out_wav."""
    hq = os.path.join(workdir, "voice_hq.wav")
    await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src_audio, "-vn", "-ac", "1", "-ar", str(sr),
                     "-c:a", "pcm_s16le", hq], timeout=600)

    def build():
        with wave.open(hq) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        parts = []
        fade = int(0.012 * sr)
        for d, pc in zip(durs, pieces):
            n = int(round(d * sr))
            buf = np.zeros(n, dtype=np.int16)
            if pc is not None:
                a, b = int(round(pc[0] * sr)), int(round(pc[1] * sr))
                seg = x[max(0, a):max(0, min(len(x), b))].astype(np.float32)
                m = min(len(seg), n)
                seg = seg[:m]
                if m > 2 * fade:          # chegaralarda «chiq» eshitilmasin
                    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
                    seg[:fade] *= ramp
                    seg[-fade:] *= ramp[::-1]
                buf[:m] = seg.astype(np.int16)
            parts.append(buf)
        y = np.concatenate(parts) if parts else np.zeros(1, dtype=np.int16)
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(y.tobytes())

    await asyncio.to_thread(build)
    try:
        os.remove(hq)
    except OSError:
        pass
    return out_wav


async def mux_voice_music(video_in, voice_wav, out, total, voice_db=-20.0, music=None, target=-14.0):
    """Video + ovoz (baland, aniq) + qo'shiq (orqada past; gapirganda yana pasayadi). Video nusxalanadi.
    music: {"path", "start", "rms"}. Umumiy balandlik ≈ −14 LUFS."""
    total = float(total)
    gain = max(-12.0, min(24.0, -15.0 - float(voice_db)))

    async def mux(extra):
        fc = (f"[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,highpass=f=70,"
              f"volume={gain:.1f}dB,acompressor=threshold=0.08:ratio=3:attack=8:release=200:makeup=2,"
              f"apad=whole_dur={total:.3f}")
        if music:
            mg = max(-40.0, min(10.0, -29.0 - float(music.get("rms", -20.0))))
            fc += (",asplit=2[vo][sc];"
                   f"[2:a]atrim=start={float(music.get('start', 0)):.2f},asetpts=PTS-STARTPTS,aresample=44100,"
                   f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={mg:.1f}dB,"
                   f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0.0, total - 2.5):.2f}:d=2.5,atrim=end={total:.3f}[mu0];"
                   "[mu0][sc]sidechaincompress=threshold=0.05:ratio=3:attack=30:release=500[mua];"
                   "[vo][mua]amix=inputs=2:duration=first:normalize=0,")
        else:
            fc += ","
        fc += f"volume={extra:.1f}dB,alimiter=limit=0.84:attack=5:release=60:level=false[a]"
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video_in, "-i", voice_wav]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music["path"]]
        cmd += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[a]", "-t", f"{total:.3f}", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", out]
        await video.run(cmd, timeout=1800)

    await mux(0.0)
    lufs = await video.measure_lufs(out)
    if lufs is not None and abs(lufs - target) > 1.0:
        await mux(max(-10.0, min(14.0, target - lufs)))
