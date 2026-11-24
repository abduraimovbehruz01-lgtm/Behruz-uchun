"""Manga sahifasidagi asl matnni o'chirib, o'rniga o'zbekcha tarjimani yozish.

Gemini har bir matn bloki uchun «box_2d» (matn turgan to'rtburchak) va tarjimani beradi. Bu modul:
  1) to'rtburchak ichidagi harflarni topib o'chiradi (pufak rangiga bo'yaydi yoki atrofidan to'ldiradi);
  2) pufakning ichki chegarasini topadi (oq joy qayergacha davom etishini aniqlaydi);
  3) o'zbekcha matnni shu joyga sig'adigan eng katta o'lchamda, o'rtaga tekislab yozadi.
Uzun webtoon rasmlarini bo'laklarga bo'lish ham shu yerda.
"""
from __future__ import annotations

import io
import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

Image.MAX_IMAGE_PIXELS = 150_000_000  # uzun webtoon rasmlari uchun

HERE = os.path.dirname(os.path.abspath(__file__))
PLATE = os.environ.get("PLATE", "1") != "0"  # rastr ustidagi matnga oq plashka
FONT_CANDIDATES = [
    os.environ.get("FONT_PATH", "").strip(),
    os.path.join(HERE, "fonts", "ComicRelief-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


# ---------------------------------------------------------------- shriftlar

class FontSet:
    """Asosiy shrift + zaxira shriftlar. Matndagi harf asosiy shriftda bo'lmasa, zaxirasi olinadi."""

    def __init__(self, paths=None):
        self.paths = [p for p in (paths or FONT_CANDIDATES) if p and os.path.exists(p)]
        self._cmaps: dict[str, set[int] | None] = {}

    def _cmap(self, path):
        if path not in self._cmaps:
            try:
                from fontTools.ttLib import TTFont
                f = TTFont(path, lazy=True, fontNumber=0)
                self._cmaps[path] = set((f.getBestCmap() or {}).keys())
                f.close()
            except Exception:
                self._cmaps[path] = None  # bilib bo'lmadi — hamma harf bor deb hisoblaymiz
        return self._cmaps[path]

    def pick(self, text: str):
        need = {ord(c) for c in text if not c.isspace()}
        for p in self.paths:
            cm = self._cmap(p)
            if cm is None or need <= cm:
                return p
        return self.paths[0] if self.paths else None

    def clean(self, text: str, path) -> str:
        cm = self._cmap(path) if path else None
        if not cm:
            return text
        return "".join(c for c in text if c.isspace() or ord(c) in cm)


@lru_cache(maxsize=1024)
def get_font(path, size: int):
    if not path:
        return ImageFont.load_default(size)
    return ImageFont.truetype(path, size)


_FONTS: FontSet | None = None


def default_fonts() -> FontSet:
    global _FONTS
    if _FONTS is None:
        _FONTS = FontSet()
    return _FONTS


def normalize_uz(s: str) -> str:
    """O'zbekcha tutuq va o‘/g‘ belgilarini shriftda bor ‘ ’ belgilariga keltiradi, ortiqcha bo'shliqni olib tashlaydi."""
    s = (s or "").replace("ʻ", "‘").replace("ʼ", "’").replace("ʽ", "‘")
    for ch in "`´‛":
        s = s.replace(ch, "‘")
    s = re.sub(r"([OoGg])'", "\\1‘", s)
    s = s.replace("'", "’")
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- rasm yordamchilari

def load_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, subsampling=0 if quality >= 90 else 2)
    return buf.getvalue()


def api_image(img: Image.Image, max_side: int = 2048) -> bytes:
    """Gemini'ga yuboriladigan nusxa: uzun tomoni max_side dan oshmaydi (koordinatalar baribir 0–1000 nisbiy)."""
    w, h = img.size
    k = min(1.0, max_side / max(w, h))
    if k < 1.0:
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    return to_jpeg(img, 88)


def split_points(img: Image.Image, ratio: float = 2.3, target: float = 1.5) -> list[tuple[int, int]]:
    """Juda uzun (webtoon) rasmni bo'laklarga bo'ladi. Kesish joyi — iloji boricha bir xil rangli (bo'sh) qator,
    shunda pufaklar o'rtasidan kesilmaydi. Qaytaradi: [(y0, y1), ...]."""
    w, h = img.size
    if h <= ratio * w:
        return [(0, h)]
    small_w = min(w, 400)
    k = small_w / w
    g = np.asarray(img.convert("L").resize((small_w, max(1, round(h * k))), Image.BILINEAR), dtype=np.float32)
    row_std = g.std(axis=1)
    edge = np.abs(np.diff(g, axis=0)).mean(axis=1)
    edge = np.concatenate([edge, edge[-1:]])
    edge = np.convolve(edge, np.ones(5) / 5, mode="same")
    seg = target * w * k
    cuts = [0]
    y = 0.0
    n = len(row_std)
    while n - y > seg * 1.35:
        lo = int(y + seg * 0.6)
        hi = int(min(n - seg * 0.3, y + seg * 1.4))
        if hi <= lo + 2:
            break
        quiet = row_std[lo:hi] < 4.0
        best = None
        if quiet.any():
            # eng uzun bo'sh qatorlar ketma-ketligining o'rtasi (maqsadga yaqinrog'i afzal)
            runs, start = [], None
            for i, q in enumerate(quiet):
                if q and start is None:
                    start = i
                if (not q or i == len(quiet) - 1) and start is not None:
                    end = i if not q else i + 1
                    runs.append((start, end))
                    start = None
            goal = y + seg - lo
            scored = [((e - s) - 0.08 * abs((s + e) / 2 - goal), (s + e) // 2) for s, e in runs]
            best = lo + max(scored)[1]
        else:
            best = lo + int(np.argmin(edge[lo:hi]))
        cuts.append(best)
        y = best
    out = []
    full = [round(c / k) for c in cuts] + [h]
    for a, b in zip(full, full[1:]):
        if b - a > 4:
            out.append((a, b))
    return out or [(0, h)]


# ---------------------------------------------------------------- bloklar

@dataclass
class Block:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 (piksel) — modeldagi matn to'rtburchagi
    text: str                       # o'zbekcha tarjima
    kind: str = "speech"
    original: str = ""


def _iou(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if not inter:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(1, ua)


def blocks_from_model(items, width: int, height: int) -> list[Block]:
    """Gemini javobidagi bloklarni pikselli Block ro'yxatiga aylantiradi (xato qutilarni tashlab yuboradi)."""
    out: list[Block] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        b = it.get("box_2d") or it.get("box") or []
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            continue
        try:
            ymin, xmin, ymax, xmax = (float(v) for v in b)
        except (TypeError, ValueError):
            continue
        if max(ymin, xmin, ymax, xmax) <= 1.5:  # 0–1 oralig'ida berilgan bo'lsa
            ymin, xmin, ymax, xmax = ymin * 1000, xmin * 1000, ymax * 1000, xmax * 1000
        if max(ymin, xmin, ymax, xmax) > 1100:
            continue
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        ymin, xmin, ymax, xmax = (min(1000.0, max(0.0, v)) for v in (ymin, xmin, ymax, xmax))
        x0, x1 = int(xmin / 1000 * width), int(math.ceil(xmax / 1000 * width))
        y0, y1 = int(ymin / 1000 * height), int(math.ceil(ymax / 1000 * height))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        text = str(it.get("translation") or it.get("tarjima") or "").strip()
        kind = str(it.get("kind") or "speech").strip().lower()
        orig = str(it.get("original") or it.get("asl") or "").strip()
        box = (x0, y0, x1, y1)
        if any(_iou(box, o.box) > 0.6 for o in out):
            continue  # takroriy blok
        out.append(Block(box, text, kind, orig))
    return out


# ---------------------------------------------------------------- 1) matnni o'chirish

@dataclass
class Erased:
    box: tuple[int, int, int, int]  # haqiqiy matn chegarasi (model qutisi + topilgan harflar)
    simple: bool                    # fon bir xil rangli (oq/qora pufak, oddiy fon)
    bg: int                         # fon yorqinligi (0–255)
    bg_rgb: tuple[int, int, int]
    erased_px: int = 0
    textured: bool = False          # rastr/rasm ustidagi matn — ostiga oq «plashka» qo'yiladi
    ink: tuple = None               # o'chirilgan siyohning chegarasi


def _background(crop: np.ndarray):
    """Fon rangini va u bir tekismi (pufak) yoki naqshli (rasm, rastr) ekanini aniqlaydi."""
    blur = cv2.GaussianBlur(crop, (5, 5), 0)
    hist = np.bincount((blur >> 3).ravel(), minlength=32)
    bg = int(hist.argmax()) * 8 + 4
    nb = np.abs(blur.astype(np.int16) - bg) <= 24
    if nb.any():
        bg = int(np.median(blur[nb]))
    raw = np.abs(crop.astype(np.int16) - bg) <= 26
    frac_blur = float((np.abs(blur.astype(np.int16) - bg) <= 24).mean())
    frac_raw = float(raw.mean())
    simple = frac_blur >= 0.5 and frac_raw >= 0.45
    return bg, simple


def _text_mask(crop: np.ndarray, bg: int, simple: bool, bw: int, bh: int) -> np.ndarray:
    if simple:
        return (np.abs(crop.astype(np.int16) - bg) > 55).astype(np.uint8)
    k = int(max(9, min(41, 0.3 * min(bw, bh)))) | 1
    est = cv2.medianBlur(crop, k)
    return (np.abs(crop.astype(np.int16) - est.astype(np.int16)) > 40).astype(np.uint8)


def _main_cluster(sel, stats, gsz, mbox):
    """Siyoh bo'laklarini yaqinligiga qarab guruhlaydi va matn guruhini qoldiradi: chetda yotgan mayda
    rasm bo'laklari (shtrix, nuqta) o'chirilmaydi va matn chegarasini kattalashtirmaydi."""
    ids = list(sel)
    if len(ids) <= 1 or len(ids) > 400:
        return sel
    gap = 0.8 * gsz
    parent = {i: i for i in ids}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    bx = {i: (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 0] + stats[i, 2]), int(stats[i, 1] + stats[i, 3]))
          for i in ids}
    for k, i in enumerate(ids):
        a = bx[i]
        for j in ids[k + 1:]:
            b = bx[j]
            if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups: dict[int, list[int]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    area = {r: sum(int(stats[i, 4]) for i in g) for r, g in groups.items()}
    main = max(area.values())
    mx0, my0, mx1, my1 = mbox
    out = set()
    for r, g in groups.items():
        gx0 = min(bx[i][0] for i in g); gy0 = min(bx[i][1] for i in g)
        gx1 = max(bx[i][2] for i in g); gy1 = max(bx[i][3] for i in g)
        inter = max(0, min(gx1, mx1) - max(gx0, mx0)) * max(0, min(gy1, my1) - max(gy0, my0))
        inside = inter >= 0.5 * max(1, (gx1 - gx0) * (gy1 - gy0))
        if area[r] >= 0.25 * main or (inside and area[r] >= 0.04 * main):
            out.update(g)
    return out


def _erase(rgb: np.ndarray, gray: np.ndarray, box, original: str = "") -> Erased | None:
    """Model qutisidagi matnni topib o'chiradi.

    Siyoh bo'laklari (connected components) qutidan kengroq «tashqi oynada» ko'riladi: harf o'lchamidan katta yoki
    oyna chetigacha cho'zilgan bo'lak — pufak chizig'i, panel chegarasi yoki rasm, ularga tegilmaydi. Qutiga tegib
    turgan harfga o'xshash bo'laklar to'liq o'chiriladi (model qutisi harfni chala olgan bo'lsa ham)."""
    H, W = gray.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    bw, bh = x1 - x0, y1 - y0
    if bw < 4 or bh < 4:
        return None
    pad = int(round(max(4, 0.10 * min(bw, bh), 0.004 * max(W, H))))
    ix0, iy0, ix1, iy1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    inner = gray[iy0:iy1, ix0:ix1]
    bg, simple = _background(inner)
    g_est = est_glyph(original, bw, bh) or float(min(bw, bh))
    g_est = float(min(max(g_est, 8.0), max(bw, bh)))
    M = int(max(2.2 * g_est, 3 * pad))
    ox0, oy0, ox1, oy1 = max(0, ix0 - M), max(0, iy0 - M), min(W, ix1 + M), min(H, iy1 + M)
    outer = gray[oy0:oy1, ox0:ox1]
    oh, ow = outer.shape
    a0, b0, a1, b1 = ix0 - ox0, iy0 - oy0, ix1 - ox0, iy1 - oy0
    inner_area = max(1, (a1 - a0) * (b1 - b0))
    mask = _text_mask(outer, bg, simple, bw, bh)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    in_lbl = np.unique(lab[b0:b1, a0:a1])
    in_lbl = [int(i) for i in in_lbl if i > 0]

    gsz = g_est
    if simple:  # harf o'lchamini siyoh bo'laklarining o'zidan ham baholaymiz (quti katta berilgan bo'lsa)
        ext = [max(int(stats[i, 2]), int(stats[i, 3])) for i in in_lbl
               if stats[i, 4] >= 4 and stats[i, 0] >= a0 and stats[i, 1] >= b0
               and stats[i, 0] + stats[i, 2] <= a1 and stats[i, 1] + stats[i, 3] <= b1]
        if len(ext) >= 3:
            gsz = min(g_est, max(8.0, 1.4 * float(np.percentile(ext, 75))))

    def textlike(i):
        x, y, w, h, a = (int(v) for v in stats[i])
        if x == 0 or y == 0 or x + w >= ow or y + h >= oh:
            return False  # oyna chetigacha cho'zilgan — chiziq yoki rasm
        if simple:
            return max(w, h) <= 1.8 * gsz and a < 0.3 * inner_area
        return a < 0.85 * inner_area

    sel = {i for i in in_lbl if textlike(i)}
    if sel:  # qutidan biroz chiqib qolgan qo'shni harflar
        g = 0.5 * gsz
        for _ in range(2):
            ux0 = min(int(stats[i, 0]) for i in sel)
            uy0 = min(int(stats[i, 1]) for i in sel)
            ux1 = max(int(stats[i, 0] + stats[i, 2]) for i in sel)
            uy1 = max(int(stats[i, 1] + stats[i, 3]) for i in sel)
            added = False
            for i in range(1, n):
                if i in sel or stats[i, 4] < 4:
                    continue
                x, y, w, h = (int(v) for v in stats[i, :4])
                near = x + w >= ux0 - g and x <= ux1 + g and y + h >= uy0 - g and y <= uy1 + g
                local = x >= a0 - gsz and y >= b0 - gsz and x + w <= a1 + gsz and y + h <= b1 + gsz
                if near and local and textlike(i):
                    sel.add(i)
                    added = True
            if not added:
                break
    if not simple:  # rastr nuqtalari harf emas — faqat kattaroq bo'laklarni hisobga olamiz
        min_a = max(20.0, 0.03 * gsz * gsz)
        big_sel = {i for i in sel if stats[i, 4] >= min_a}
        if big_sel:
            sel = big_sel
    sel = _main_cluster(sel, stats, gsz, (x0 - ox0, y0 - oy0, x1 - ox0, y1 - oy0))
    keep = np.isin(lab, list(sel)) if sel else np.zeros_like(mask, dtype=bool)
    kpx = int(keep.sum())
    if kpx:
        ys, xs = np.nonzero(keep)
        ink = (ox0 + int(xs.min()), oy0 + int(ys.min()), ox0 + int(xs.max()) + 1, oy0 + int(ys.max()) + 1)
    else:
        ink = (x0, y0, x1, y1)
    m = keep.astype(np.uint8)
    if kpx:
        r = int(max(1, min(4, round(gsz / 13))))
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    sub = rgb[oy0:oy1, ox0:ox1]
    m_in = m[b0:b1, a0:a1]
    bgsel = (np.abs(inner.astype(np.int16) - bg) <= 24) & (m_in == 0)
    if bgsel.sum() > 10:
        bg_rgb = tuple(int(v) for v in np.median(rgb[iy0:iy1, ix0:ix1][bgsel], axis=0))
    else:
        bg_rgb = (bg, bg, bg)
    textured = False
    if not simple:
        rest = inner[m_in == 0]
        textured = rest.size > 20 and float(rest.std()) > 28
    # matn chegarasi: model qutisi va topilgan siyoh birlashmasi (haddan tashqari kengaytirmasdan)
    fbox = (max(min(x0, ink[0]), x0 - 2 * pad), max(min(y0, ink[1]), y0 - 2 * pad),
            min(max(x1, ink[2]), x1 + 2 * pad), min(max(y1, ink[3]), y1 + 2 * pad))
    if textured and PLATE:
        # oq plashka baribir yopadi — atrofni buzmaslik uchun hech narsani o'chirmaymiz
        cover = (max(0, min(ix0, ink[0] - 2)), max(0, min(iy0, ink[1] - 2)),
                 min(W, max(ix1, ink[2] + 2)), min(H, max(iy1, ink[3] + 2)))
        return Erased(cover, simple, int(bg), bg_rgb, kpx, True, cover)
    if kpx:
        if simple:
            sub[m > 0] = bg_rgb
        else:
            radius = int(max(3, min(9, min(bw, bh) // 12)))
            ys, xs = np.nonzero(m)
            c0, d0 = max(0, int(xs.min()) - 2 * radius), max(0, int(ys.min()) - 2 * radius)
            c1, d1 = min(ow, int(xs.max()) + 2 * radius + 1), min(oh, int(ys.max()) + 2 * radius + 1)
            part = np.ascontiguousarray(sub[d0:d1, c0:c1])
            sub[d0:d1, c0:c1] = cv2.inpaint(part, m[d0:d1, c0:c1] * 255, radius, cv2.INPAINT_TELEA)
        gray[oy0:oy1, ox0:ox1] = cv2.cvtColor(np.ascontiguousarray(sub), cv2.COLOR_RGB2GRAY)
    return Erased(fbox, simple, int(bg), bg_rgb, kpx, textured, ink)


# ---------------------------------------------------------------- 2) matn uchun joy

def _region(gray: np.ndarray, er: Erased, others, taken: np.ndarray):
    """Yangi matn yoziladigan joy (mask) va uning sahifadagi boshlanish nuqtasi."""
    H, W = gray.shape
    x0, y0, x1, y1 = er.ink if er.ink and not er.textured else er.box
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    mx, my = int(max(2.0 * bw, 0.15 * W)), int(max(2.0 * bh, 0.12 * H))
    wx0, wy0, wx1, wy1 = max(0, x0 - mx), max(0, y0 - my), min(W, x1 + mx), min(H, y1 + my)
    win = gray[wy0:wy1, wx0:wx1]
    hh, ww = win.shape
    bx0, by0, bx1, by1 = x0 - wx0, y0 - wy0, x1 - wx0, y1 - wy0

    def rect_mask(ex, ey):
        r = np.zeros((hh, ww), np.uint8)
        r[max(0, by0 - ey):min(hh, by1 + ey), max(0, bx0 - ex):min(ww, bx1 + ex)] = 1
        return r

    blocked = (taken[wy0:wy1, wx0:wx1] > 0)
    for ox0, oy0, ox1, oy1 in others:
        a0, b0, a1, b1 = max(0, ox0 - wx0), max(0, oy0 - wy0), min(ww, ox1 - wx0), min(hh, oy1 - wy0)
        if a1 > a0 and b1 > b0:
            blocked[b0:b1, a0:a1] = True

    if not er.simple:
        ex, ey = int(0.08 * bw) + 2, int(0.08 * bh) + 2
        if bw < 0.6 * bh:  # yaponcha vertikal ustun — biroz kengroq joy
            ex = max(ex, int((min(0.6 * bh, 1.8 * bw) - bw) / 2))
        # panel chegarasidan chiqmasin
        qx0, qy0, qx1, qy1 = _avoid_lines(gray, max(0, x0 - ex), max(0, y0 - ey), min(W, x1 + ex), min(H, y1 + ey))
        r = np.zeros((hh, ww), np.uint8)
        r[max(0, qy0 - wy0):max(0, qy1 - wy0), max(0, qx0 - wx0):max(0, qx1 - wx0)] = 1
        if not r.any():
            r = rect_mask(ex, ey)
        r[blocked] = 0
        return r, (wx0, wy0), False

    bgm = (np.abs(win.astype(np.int16) - er.bg) <= 30).astype(np.uint8)
    bgm[blocked] = 0
    n, lab = cv2.connectedComponents(bgm, connectivity=4)
    tb = lab[by0:by1, bx0:bx1]
    vals = tb[tb > 0]
    if vals.size == 0:
        r = rect_mask(2, 2)
        r[blocked] = 0
        return r, (wx0, wy0), False
    lbl = int(np.bincount(vals).argmax())
    reg = (lab == lbl).astype(np.uint8)
    ys, xs = np.nonzero(reg)
    rx0, ry0, rx1, ry1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    # skaner dog'lari va o'chirishdan qolgan mayda nuqtalar joyni bo'lib yubormasin
    inv = (1 - reg[ry0:ry1, rx0:rx1]).astype(np.uint8)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    hole_max = max(40.0, 0.01 * (rx1 - rx0) * (ry1 - ry0))
    sub_reg = reg[ry0:ry1, rx0:rx1]
    for j in range(1, n2):
        x, y, w, h, a = (int(v) for v in st2[j])
        if x == 0 or y == 0 or x + w >= rx1 - rx0 or y + h >= ry1 - ry0 or a > hole_max:
            continue
        sub_reg[lab2 == j] = 1
    reg[blocked] = 0
    leak = ((rx0 == 0 and wx0 > 0) or (ry0 == 0 and wy0 > 0) or (rx1 == ww and wx1 < W) or (ry1 == hh and wy1 < H)
            or len(xs) > 0.15 * W * H)
    if leak:
        reg &= rect_mask(int(0.5 * bw + 0.03 * W), int(0.35 * bh + 0.02 * H))
        ys, xs = np.nonzero(reg)
        if len(xs) == 0:
            return rect_mask(2, 2), (wx0, wy0), False
        rx0, ry0, rx1, ry1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    rw, rh = rx1 - rx0, ry1 - ry0
    m = int(np.clip(0.08 * min(rw, rh), 3, 0.02 * W + 4))
    if leak:
        m = max(2, m // 2)
    need = 0.6 * bw * bh
    allowed = reg
    for mm in (m, m // 2, 1):
        if mm < 1:
            continue
        e = cv2.erode(reg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * mm + 1, 2 * mm + 1)))
        if e.sum() >= need or mm == 1:
            allowed = e
            break
    return allowed, (wx0, wy0), not leak


# ---------------------------------------------------------------- 3) matnni joylash

@dataclass
class Layout:
    font: object
    size: int
    sw: int
    lines: list = field(default_factory=list)  # [(x, y, matn, eni)] — oyna koordinatalarida
    forced: bool = False


def _greedy(widths, space, maxw):
    lines, cur, cw = [], [], 0.0
    for i, w in enumerate(widths):
        if not cur:
            cur, cw = [i], w
        elif cw + space + w <= maxw:
            cur.append(i)
            cw += space + w
        else:
            lines.append(cur)
            cur, cw = [i], w
    if cur:
        lines.append(cur)
    return lines


def _balanced(widths, space, maxw):
    """Shu qatorlar sonida eng tor kenglikni topadi — qatorlar uzunligi bir-biriga yaqin bo'ladi."""
    base = _greedy(widths, space, maxw)
    n = len(base)
    lo, hi = max(widths), maxw
    best = base
    for _ in range(12):
        if hi - lo < 1:
            break
        mid = (lo + hi) / 2
        cand = _greedy(widths, space, mid)
        if len(cand) <= n:
            best, hi = cand, mid
        else:
            lo = mid
    return best


def _split_long(word: str, font, maxw: float) -> list[str]:
    parts, cur = [], ""
    for ch in word:
        if cur and font.getlength(cur + ch + "-") > maxw:
            parts.append(cur + "-")
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def est_glyph(original: str, bw: int, bh: int):
    """Asl matn harflarining taxminiy o'lchami (piksel): quti yuzasi / harflar soni."""
    t = re.sub(r"\s+", "", original or "")
    if not t:
        return None
    cjk = sum(1 for c in t if ord(c) >= 0x2E80)
    latin = len(t) - cjk
    units = cjk * 1.3 + latin * 0.75 + (0.3 * (original or "").count(" ") if latin else 0)
    return math.sqrt(bw * bh / max(1.0, units))


def _fit(text: str, allowed: np.ndarray, er: Erased, origin, fonts: FontSet, W: int, H: int, closed: bool,
         original: str = ""):
    text = normalize_uz(text)
    path = fonts.pick(text)
    text = fonts.clean(text, path)
    words = text.split()
    if not words or not allowed.any():
        return None
    al = (allowed > 0).astype(np.uint8)
    hh, ww = al.shape
    ii = cv2.integral(al)
    ys, xs = np.nonzero(al)
    ax0, ay0, ax1, ay1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    dist = cv2.distanceTransform(al, cv2.DIST_L2, 3)
    dmax = float(dist.max())
    ry, rx = np.nonzero(dist >= 0.85 * dmax)
    cx, cy = float(rx.mean()), float(ry.mean())
    if not al[int(cy), int(cx)]:  # botiq shakl — eng yaqin «tog' cho'qqisi» nuqtasiga
        j = int(np.argmin((rx - cx) ** 2 + (ry - cy) ** 2))
        cx, cy = float(rx[j]), float(ry[j])
    ib = er.ink or er.box
    tx = (ib[0] + ib[2]) / 2 - origin[0]
    ty = (ib[1] + ib[3]) / 2 - origin[1]
    if not closed:  # ochiq joyda (chegarasiz matn) — asl matn turgan joyga yaqinroq
        cx, cy = 0.5 * cx + 0.5 * tx, 0.5 * cy + 0.5 * ty

    min_size = max(9, int(round(0.0115 * W)))
    max_size = max(min_size, int(round(0.036 * W)), 12)
    g = est_glyph(original, ib[2] - ib[0], ib[3] - ib[1])
    if g:  # tarjima asl matndan sezilarli katta bo'lib ketmasin
        max_size = max(min_size, min(max_size, int(round(1.3 * g))))
    target_ratio = float(np.clip(1.5 * (ax1 - ax0) / max(1, ay1 - ay0), 0.8, 8.0))

    def rect_ok(x0, y0, x1, y1, need):
        if x0 < 0 or y0 < 0 or x1 > ww or y1 > hh or x1 <= x0 or y1 <= y0:
            return False
        s = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
        return s >= need * (x1 - x0) * (y1 - y0)

    def stroke(size):
        return max(1, round(size / 15)) if er.simple or er.textured else max(2, round(size / 7))

    def attempt(size, need=0.985, choose=False, fracs=(1.0, 0.85, 0.72, 0.6, 0.5, 0.42)):
        font = get_font(path, size)
        asc, desc = font.getmetrics()
        sw = stroke(size)
        th = asc + desc + 2 * sw
        lh = asc + desc + max(1, size // 8)
        space = font.getlength(" ")
        widths = [font.getlength(w) for w in words]
        full = (ax1 - ax0) - 2 * sw
        if max(widths) > full:
            return None
        centers_y = (cy, (ay0 + ay1) / 2) if abs(cy - (ay0 + ay1) / 2) > 2 else (cy,)
        found, seen = [], set()
        for frac in fracs:
            maxw = max(max(widths), frac * full)
            groups = _balanced(widths, space, maxw)
            key = tuple(len(gr) for gr in groups)
            if key in seen:
                continue
            seen.add(key)
            lines = [" ".join(words[i] for i in gr) for gr in groups]
            lw = [sum(widths[i] for i in gr) + space * (len(gr) - 1) + 2 * sw for gr in groups]
            block_h = lh * (len(lines) - 1) + th
            if block_h > ay1 - ay0:
                continue
            for vi, vy in enumerate(centers_y):
                top = min(max(vy - block_h / 2, ay0), ay1 - block_h)
                placed = []
                for k, (ln, w) in enumerate(zip(lines, lw)):
                    lx = min(max(cx - w / 2, ax0), ax1 - w)
                    ly = top + k * lh
                    rx0, ry0 = int(math.floor(lx)), int(math.floor(ly))
                    rx1, ry1 = int(math.ceil(lx + w)), int(math.ceil(ly + th))
                    if not rect_ok(rx0, ry0, rx1, ry1, need):
                        break
                    placed.append((lx, ly, ln, w))
                else:
                    lay = Layout(font, size, sw, placed)
                    if not choose:
                        return lay
                    ratio = max(lw) / block_h
                    found.append((abs(math.log(ratio / target_ratio)) + 0.05 * vi, len(found), lay))
                    break
        return min(found)[2] if found else None

    def search(need):
        lo, hi, best = min_size, max_size, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if attempt(mid, need):
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return attempt(best, need, choose=True) if best else None

    best = search(0.985) or search(0.93)
    if best:
        return best
    # Sig'madi — eng kichik o'lchamda, kerak bo'lsa so'zlarni bo'lib, majburan joylaymiz
    size = min_size
    font = get_font(path, size)
    sw = max(2, round(size / 7))
    asc, desc = font.getmetrics()
    lh = asc + desc + max(1, size // 8)
    th = asc + desc + 2 * sw
    maxw = max(ax1 - ax0, int(1.15 * (er.box[2] - er.box[0])), 4 * size) - 2 * sw
    pieces = []
    for w in words:
        pieces.extend(_split_long(w, font, maxw) if font.getlength(w) > maxw else [w])
    space = font.getlength(" ")
    widths = [font.getlength(p) for p in pieces]
    groups = _greedy(widths, space, maxw)
    lines = [" ".join(pieces[i] for i in gr) for gr in groups]
    lw = [sum(widths[i] for i in gr) + space * (len(gr) - 1) + 2 * sw for gr in groups]
    block_h = lh * (len(lines) - 1) + th
    top = min(max(cy - block_h / 2, -origin[1]), H - origin[1] - block_h)
    placed = []
    for k, (ln, w) in enumerate(zip(lines, lw)):
        lx = min(max(cx - w / 2, -origin[0]), W - origin[0] - w)
        placed.append((lx, top + k * lh, ln, w))
    return Layout(font, size, sw, placed, forced=True)


def _avoid_lines(gray: np.ndarray, x0, y0, x1, y1):
    """To'rtburchakni panel chegarasi kabi to'g'ri qora chiziqlardan oldin to'xtatadi."""
    sub = (gray[y0:y1, x0:x1] < 90).astype(np.uint8)
    if sub.size == 0 or min(sub.shape) < 6:
        return x0, y0, x1, y1
    h, w = sub.shape
    # faqat uzluksiz uzun chiziqlar (rastr nuqtalari emas): balandligining 60% idan uzun tik chiziq va h.k.
    vert = cv2.erode(sub, np.ones((max(3, int(0.6 * h)), 1), np.uint8))
    horz = cv2.erode(sub, np.ones((1, max(3, int(0.6 * w))), np.uint8))
    cols = np.nonzero(vert.any(axis=0))[0]
    rows = np.nonzero(horz.any(axis=1))[0]
    nx0, nx1, ny0, ny1 = x0, x1, y0, y1
    if cols.size:
        left, right = cols[cols < 0.35 * w], cols[cols > 0.65 * w]
        if left.size:
            nx0 = x0 + int(left.max()) + 3
        if right.size:
            nx1 = x0 + int(right.min()) - 2
    if rows.size:
        top, bot = rows[rows < 0.35 * h], rows[rows > 0.65 * h]
        if top.size:
            ny0 = y0 + int(top.max()) + 3
        if bot.size:
            ny1 = y0 + int(bot.min()) - 2
    return nx0, ny0, nx1, ny1


def _plate(pil: Image.Image, er: Erased, lay: Layout, origin, gray=None):
    """Rastr/rasm ustidagi matn uchun: o'chirilgan joy va yangi matnni yumshoq chetli oq plashka bilan yopadi."""
    ox, oy = origin
    W, H = pil.size
    pad = max(4, int(0.3 * lay.size))
    tx0 = min(ox + l[0] for l in lay.lines)
    tx1 = max(ox + l[0] + l[3] for l in lay.lines)
    ty0 = oy + lay.lines[0][1]
    ty1 = oy + lay.lines[-1][1] + (lay.font.getmetrics()[0] + lay.font.getmetrics()[1]) + 2 * lay.sw
    x0, y0 = int(max(0, min(tx0, er.box[0]) - pad)), int(max(0, min(ty0, er.box[1]) - pad))
    x1, y1 = int(min(W, max(tx1, er.box[2]) + pad)), int(min(H, max(ty1, er.box[3]) + pad))
    if gray is not None:
        x0, y0, x1, y1 = _avoid_lines(gray, x0, y0, x1, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    feather = max(2, pad // 3)
    mask = Image.new("L", (x1 - x0 + 2 * feather, y1 - y0 + 2 * feather), 0)
    ImageDraw.Draw(mask).rounded_rectangle((feather, feather, feather + x1 - x0, feather + y1 - y0),
                                           radius=max(4, min(x1 - x0, y1 - y0) // 5), fill=255)
    from PIL import ImageFilter
    mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
    pil.paste((255, 255, 255), (x0 - feather, y0 - feather), mask)
    return x0, y0, x1, y1


def _draw(draw: ImageDraw.ImageDraw, lay: Layout, er: Erased, origin, rgb: np.ndarray):
    ox, oy = origin
    if er.textured:
        dark_bg = False
    elif er.simple:
        dark_bg = er.bg < 110
    else:
        # naqshli fonda — matn ostidagi o'rtacha yorqinlikka qarab
        x0 = int(max(0, ox + min(l[0] for l in lay.lines)))
        y0 = int(max(0, oy + lay.lines[0][1]))
        x1 = int(min(rgb.shape[1], ox + max(l[0] + l[3] for l in lay.lines)))
        y1 = int(min(rgb.shape[0], oy + lay.lines[-1][1] + lay.size * 1.3))
        patch = rgb[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
        dark_bg = float(patch.mean()) < 110 if patch.size else False
    fill = (245, 245, 245) if dark_bg else (18, 18, 18)
    if er.textured:
        stroke_fill = (255, 255, 255)
    elif er.simple and not lay.forced:
        stroke_fill = er.bg_rgb
    else:
        stroke_fill = (0, 0, 0) if dark_bg else (255, 255, 255)
    for x, y, ln, _w in lay.lines:
        draw.text((ox + x + lay.sw, oy + y + lay.sw), ln, font=lay.font, fill=fill,
                  stroke_width=lay.sw, stroke_fill=stroke_fill)


# ---------------------------------------------------------------- asosiy funksiya

@dataclass
class RenderStats:
    blocks: int = 0
    drawn: int = 0
    forced: int = 0
    skipped: int = 0


def render(img: Image.Image, blocks: list[Block], fonts: FontSet | None = None, debug: bool = False):
    """Rasmdagi bloklarni o'chirib, tarjimani yozadi. Qaytaradi: (yangi rasm, RenderStats)."""
    fonts = fonts or default_fonts()
    rgb = np.array(img.convert("RGB"))
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    st = RenderStats(blocks=len(blocks))
    todo = []
    for b in blocks:
        if not b.text.strip() or b.kind == "sfx":
            st.skipped += 1
            continue
        er = _erase(rgb, gray, b.box, b.original)
        if er is None:
            st.skipped += 1
            continue
        todo.append((b, er))
    taken = np.zeros((H, W), np.uint8)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    for i, (b, er) in enumerate(todo):
        others = [(e.ink or e.box) for j, (_bb, e) in enumerate(todo) if j != i]
        allowed, origin, closed = _region(gray, er, others, taken)
        lay = _fit(b.text, allowed, er, origin, fonts, W, H, closed, b.original)
        if not lay:
            st.skipped += 1
            continue
        plate = _plate(pil, er, lay, origin, gray) if er.textured and PLATE else None
        if plate:
            taken[plate[1]:plate[3], plate[0]:plate[2]] = 1
        if debug:
            ys, xs = np.nonzero(allowed)
            if len(xs):
                draw.rectangle((origin[0] + xs.min(), origin[1] + ys.min(), origin[0] + xs.max(), origin[1] + ys.max()),
                               outline=(0, 160, 255))
            draw.rectangle(er.box, outline=(255, 0, 0))
        _draw(draw, lay, er, origin, rgb)
        st.drawn += 1
        st.forced += int(lay.forced)
        for x, y, _ln, w in lay.lines:
            a0, b0 = int(max(0, origin[0] + x - 2)), int(max(0, origin[1] + y - 2))
            a1, b1 = int(min(W, origin[0] + x + w + 2)), int(min(H, origin[1] + y + lay.size * 1.4 + 2))
            taken[b0:b1, a0:a1] = 1
    return pil, st
