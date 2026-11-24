"""Sinov uchun soxta manga sahifalari: pufaklar, yapon (vertikal) va ingliz matni, qora pufak,
hikoya qutisi, rastr ustidagi matn, bir-biriga tegib turgan pufaklar, uzun webtoon.
Har bir matn uchun Gemini javobiga o'xshash blok (box_2d 0–1000) qaytariladi."""
import math
import random

from PIL import Image, ImageDraw, ImageFont

CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
CJK_SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(path, size, index=0):
    return ImageFont.truetype(path, size, index=index)


def vertical(draw, x_right, y_top, columns, font, fill=(0, 0, 0), stroke=0, stroke_fill=None, gap=1.08):
    """Yaponcha vertikal matn: ustunlar o'ngdan chapga. Qaytaradi: haqiqiy chegara (x0,y0,x1,y1)."""
    size = font.size
    xs, ys = [], []
    for ci, col in enumerate(columns):
        x = x_right - (ci + 1) * size * 1.25
        y = y_top
        for ch in col:
            draw.text((x, y), ch, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
            bb = draw.textbbox((x, y), ch, font=font, stroke_width=stroke)
            xs += [bb[0], bb[2]]
            ys += [bb[1], bb[3]]
            y += size * gap
    return min(xs), min(ys), max(xs), max(ys)


def horizontal(draw, cx, cy, lines, font, fill=(0, 0, 0), stroke=0, stroke_fill=None, gap=1.15):
    size = font.size
    total = size * gap * len(lines)
    y = cy - total / 2
    xs, ys = [], []
    for ln in lines:
        w = draw.textlength(ln, font=font)
        x = cx - w / 2
        draw.text((x, y), ln, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        bb = draw.textbbox((x, y), ln, font=font, stroke_width=stroke)
        xs += [bb[0], bb[2]]
        ys += [bb[1], bb[3]]
        y += size * gap
    return min(xs), min(ys), max(xs), max(ys)


def bubble(draw, cx, cy, rx, ry, tail=None, fill="white", outline="black", width=4):
    if tail:
        tx, ty = tail
        a = math.atan2(ty - cy, tx - cx)
        p1 = (cx + rx * 0.8 * math.cos(a + 0.35), cy + ry * 0.8 * math.sin(a + 0.35))
        p2 = (cx + rx * 0.8 * math.cos(a - 0.35), cy + ry * 0.8 * math.sin(a - 0.35))
        draw.polygon([p1, (tx, ty), p2], fill=fill, outline=outline, width=width)
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=outline, width=width)
    if tail:  # pufak va dum orasidagi chiziqni o'chirish (dum uchi yopiq qoladi)
        ix, iy = cx + (tx - cx) * 0.78, cy + (ty - cy) * 0.78
        draw.polygon([(cx + rx * 0.7 * math.cos(a + 0.3), cy + ry * 0.7 * math.sin(a + 0.3)), (ix, iy),
                      (cx + rx * 0.7 * math.cos(a - 0.3), cy + ry * 0.7 * math.sin(a - 0.3))], fill=fill)


def screentone(img, box, step=7, r=2):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill="white")
    for y in range(y0, y1, step):
        off = (step // 2) if ((y - y0) // step) % 2 else 0
        for x in range(x0 + off, x1, step):
            d.ellipse((x - r, y - r, x + r, y + r), fill=(40, 40, 40))


def art(draw, rnd, box, n=40):
    x0, y0, x1, y1 = box
    for _ in range(n):
        a = (rnd.randint(x0, x1), rnd.randint(y0, y1))
        b = (a[0] + rnd.randint(-60, 60), a[1] + rnd.randint(-60, 60))
        draw.line([a, b], fill=(30, 30, 30), width=rnd.choice([1, 2, 3]))


def to_block(bb, W, H, translation, kind="speech", original="", jitter=0.0, rnd=None):
    x0, y0, x1, y1 = bb
    if jitter and rnd:
        w, h = x1 - x0, y1 - y0
        dx, dy = rnd.uniform(-jitter, jitter) * w, rnd.uniform(-jitter, jitter) * h
        sx, sy = rnd.uniform(-jitter, jitter) * w, rnd.uniform(-jitter, jitter) * h
        x0, x1 = x0 + dx - sx / 2, x1 + dx + sx / 2
        y0, y1 = y0 + dy - sy / 2, y1 + dy + sy / 2
    return {"box_2d": [round(y0 / H * 1000), round(x0 / W * 1000), round(y1 / H * 1000), round(x1 / W * 1000)],
            "original": original, "translation": translation, "kind": kind}


def page_jp(jitter=0.04, seed=1):
    rnd = random.Random(seed)
    W, H = 1100, 1600
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    # panellar
    for box in [(40, 40, 1060, 760), (40, 790, 1060, 1560)]:
        d.rectangle(box, outline="black", width=6)
    art(d, rnd, (60, 450, 1040, 740), 70)          # 1-panel pastida «rasm»
    for c in [(560, 600, 70), (760, 640, 55)]:      # qahramon boshlari
        d.ellipse((c[0] - c[2], c[1] - c[2], c[0] + c[2], c[1] + c[2]), outline="black", width=3, fill=(235, 235, 235))
    blocks = []
    fj = _font(CJK_SERIF, 34)
    fb = _font(CJK, 44)
    # 1) o'ngdagi pufak — 2 ustun
    bubble(d, 850, 260, 150, 190, tail=(760, 520))
    bb = vertical(d, 905, 120, ["お前、ここで", "何してるんだ？！"], fj)
    blocks.append(to_block(bb, W, H, "Sen bu yerda nima qilyapsan?!", original="お前、ここで何してるんだ？！",
                           jitter=jitter, rnd=rnd))
    # 2) chapdagi pufak — 3 ustun, dumli
    bubble(d, 330, 300, 190, 150, tail=(420, 520))
    bb = vertical(d, 425, 185, ["すみません", "先輩…", "手伝いたくて"], fj)
    blocks.append(to_block(bb, W, H, "Kechirasiz, senpai... Shunchaki yordam bermoqchi edim.",
                           original="すみません先輩…手伝いたくて", jitter=jitter, rnd=rnd))
    # 3) qora pufak, oq matn
    d.rounded_rectangle((470, 830, 640, 1080), radius=40, fill=(10, 10, 10))
    bb = vertical(d, 590, 860, ["黙れ！"], fb, fill=(255, 255, 255))
    blocks.append(to_block(bb, W, H, "Jim bo‘l!", original="黙れ！", jitter=jitter, rnd=rnd))
    # 4) hikoya qutisi (gorizontal)
    d.rectangle((70, 820, 400, 920), fill="white", outline="black", width=3)
    bb = horizontal(d, 235, 870, ["三年後…"], _font(CJK_SERIF, 40))
    blocks.append(to_block(bb, W, H, "Uch yildan so‘ng...", kind="narration", original="三年後…",
                           jitter=jitter, rnd=rnd))
    # 5) rastr ustidagi matn (oq hoshiyali qora harflar)
    screentone(img, (680, 830, 1040, 1250))
    d = ImageDraw.Draw(img)
    bb = vertical(d, 1000, 880, ["大丈夫、", "俺がいる"], _font(CJK, 38), stroke=4, stroke_fill="white")
    blocks.append(to_block(bb, W, H, "Qo‘rqma, men yoningdaman.", original="大丈夫、俺がいる", jitter=jitter, rnd=rnd))
    # 6) bir-biriga tegib turgan ikki pufak
    art(d, rnd, (60, 1100, 650, 1540), 50)
    bubble(d, 230, 1320, 130, 120)
    bubble(d, 440, 1420, 150, 115)
    bb = vertical(d, 270, 1240, ["誰だ？"], fj)
    blocks.append(to_block(bb, W, H, "Kim u?", original="誰だ？", jitter=jitter, rnd=rnd))
    bb = vertical(d, 515, 1345, ["わからない", "でも…", "来てる！"], _font(CJK_SERIF, 28))
    blocks.append(to_block(bb, W, H, "Bilmadim... lekin u biz tomonga kelyapti!", original="わからないでも…来てる！",
                           jitter=jitter, rnd=rnd))
    # 7) chegarasiz matn (oq fonda, pufaksiz)
    bb = horizontal(d, 860, 1420, ["ドキドキ…"], _font(CJK_SERIF, 28))
    blocks.append(to_block(bb, W, H, "Yuragim gupillab ketyapti...", kind="thought", original="ドキドキ…",
                           jitter=jitter, rnd=rnd))
    return img, blocks


def page_en(jitter=0.03, seed=2):
    rnd = random.Random(seed)
    W, H = 900, 1300
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((30, 30, 870, 1270), outline="black", width=5)
    art(d, rnd, (50, 700, 850, 1250), 90)
    f = _font(LATIN, 26)
    blocks = []
    bubble(d, 300, 220, 220, 120, tail=(380, 420))
    bb = horizontal(d, 300, 220, ["WHAT ARE YOU", "DOING HERE?!"], f)
    blocks.append(to_block(bb, W, H, "Bu yerda nima qilyapsan?!", original="WHAT ARE YOU DOING HERE?!",
                           jitter=jitter, rnd=rnd))
    bubble(d, 640, 470, 200, 150, tail=(560, 680))
    bb = horizontal(d, 640, 470, ["I TOLD YOU", "TO STAY HOME!", "IT'S DANGEROUS!"], f)
    blocks.append(to_block(bb, W, H, "Senga uyda qol degandim-ku! Bu yer xavfli!",
                           original="I TOLD YOU TO STAY HOME! IT'S DANGEROUS!", jitter=jitter, rnd=rnd))
    bubble(d, 250, 600, 110, 70)
    bb = horizontal(d, 250, 600, ["BUT..."], f)
    blocks.append(to_block(bb, W, H, "Lekin...", original="BUT...", jitter=jitter, rnd=rnd))
    return img, blocks


def webtoon(seed=3):
    """Uzun lenta: 720x5200, orasida bo'sh oq joylar."""
    rnd = random.Random(seed)
    W, H = 720, 5200
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = _font(LATIN, 24)
    items = []
    y = 150
    texts = [("HEY! WAIT FOR ME!", "Hoy! Meni kutib tur!"), ("WHY ARE YOU", "Nega bunchalik"),
             ("SO LATE AGAIN?", None), ("SORRY, SORRY!", "Kechir, kechir!"),
             ("THE BUS LEFT", "Avtobus ketib qoldi"), ("WITHOUT US...", None)]
    k = 0
    while y < H - 600:
        # panel (rangli fon)
        d.rectangle((40, y, W - 40, y + 520), fill=(rnd.randint(150, 220), rnd.randint(150, 220), 200), outline="black", width=3)
        art(d, rnd, (60, y + 20, W - 60, y + 500), 30)
        bubble(d, W // 2, y + 640, 230, 70)
        a = texts[k % len(texts)]
        bb = horizontal(d, W // 2, y + 640, [a[0]], f)
        items.append((bb, a[1] or "Ha, bilaman..."))
        k += 1
        y += 900
    return img, items
