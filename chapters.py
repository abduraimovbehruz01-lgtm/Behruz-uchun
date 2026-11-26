"""Bob (butun qism) bilan ishlash: ZIP/CBZ/PDF ichidan sahifalarni olish, tarjima qilingan sahifalardan
PDF va ZIP yasash, matnni chiroyli ko'rinishga keltirish. Telegram'ga bog'liq emas — alohida sinash mumkin."""
import html
import io
import math
import os
import re
import shutil
import zipfile

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")
ARCHIVE_EXT = (".zip", ".cbz")
MAX_UNPACKED = 400 * 1024 * 1024   # «zip-bomba»dan himoya (bitta bob uchun)
MAX_ONE = 60 * 1024 * 1024
MAX_NESTED = 150 * 1024 * 1024     # katta ZIP ichidagi bitta bob arxivi/PDF


class ChapterError(Exception):
    pass


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def kind_of(name: str, mime: str = "") -> str:
    """'image' | 'archive' | 'pdf' | 'rar' | ''"""
    n = (name or "").lower()
    m = (mime or "").lower()
    if n.endswith(IMG_EXT) or m.startswith("image/"):
        return "image"
    if n.endswith(ARCHIVE_EXT) or m in ("application/zip", "application/x-zip-compressed", "application/x-cbz",
                                        "application/vnd.comicbook+zip"):
        return "archive"
    if n.endswith(".pdf") or m == "application/pdf":
        return "pdf"
    if n.endswith((".rar", ".cbr", ".7z", ".cb7")) or "rar" in m or "7z" in m:
        return "rar"
    return ""


def sniff(src) -> str:
    """Fayl turini ichidagi baytlardan aniqlaydi: 'archive' | 'pdf' | ''. src — yo'l yoki baytlar."""
    try:
        if isinstance(src, (bytes, bytearray)):
            head = bytes(src[:8])
        else:
            with open(src, "rb") as f:
                head = f.read(8)
    except OSError:
        return ""
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "archive"
    if head.startswith(b"%PDF"):
        return "pdf"
    return ""


def _open_zip(src):
    try:
        return zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    except (zipfile.BadZipFile, OSError):
        raise ChapterError("ZIP fayl ochilmadi (buzilgan yoki boshqa format).")


def _junk(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return name.startswith("__MACOSX/") or "/__MACOSX/" in name or base.startswith(".") or \
        base.lower() in ("thumbs.db", "desktop.ini")


def _parent(name: str) -> str:
    return name.rsplit("/", 1)[0] if "/" in name else ""


def _zip_images(zf, limit, folder=None):
    infos = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if _junk(name) or not name.lower().endswith(IMG_EXT) or info.file_size > MAX_ONE:
            continue
        if folder is not None and _parent(name) != folder:
            continue
        infos.append(info)
    infos.sort(key=lambda i: natural_key(i.filename))
    out, total = [], 0
    for info in infos[:limit]:
        total += info.file_size
        if total > MAX_UNPACKED:
            raise ChapterError("Arxiv ichidagi rasmlar juda katta.")
        try:
            out.append((info.filename.replace("\\", "/").rsplit("/", 1)[-1], zf.read(info)))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
            raise ChapterError(f"Arxivdagi faylni o‘qib bo‘lmadi ({e.__class__.__name__}). Parolsiz ZIP yuboring.")
    return out, len(infos)


def zip_pages(src, limit: int):
    """Arxivdagi rasmlar (nomi bo'yicha tabiiy tartibda). src — baytlar yoki fayl yo'li.
    Qaytaradi: ([(nom, bayt), ...], jami_rasmlar_soni)."""
    with _open_zip(src) as zf:
        return _zip_images(zf, limit)


def zip_groups(src):
    """Katta ZIP ichidagi boblarni topadi: rasmli papkalar, ichki ZIP/CBZ va PDF fayllar.
    Qaytaradi: [{"label", "kind": "dir"|"zip"|"pdf", "key", "count"}] (tabiiy tartibda).
    Bitta element bo'lsa — bu oddiy bitta bob."""
    with _open_zip(src) as zf:
        dirs, nested = {}, []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if _junk(name):
                continue
            low = name.lower()
            if low.endswith(IMG_EXT):
                d = _parent(name)
                dirs[d] = dirs.get(d, 0) + 1
            elif low.endswith(ARCHIVE_EXT + (".pdf",)):
                nested.append({"label": name.rsplit("/", 1)[-1], "kind": "pdf" if low.endswith(".pdf") else "zip",
                               "key": info.filename, "count": 0})
    groups = [{"label": (d.rsplit("/", 1)[-1] or "bob"), "kind": "dir", "key": d, "count": c} for d, c in dirs.items()]
    if sum(1 for g in groups if g["count"] >= 2) + len(nested) >= 2:
        groups = [g for g in groups if g["count"] >= 2]   # muqova kabi yolg'iz rasmlar bob emas
    groups += nested
    groups.sort(key=lambda g: natural_key(g["key"]))
    return groups


def group_pages(src, group: dict, limit: int):
    """zip_groups() topgan bitta bobning sahifalari."""
    with _open_zip(src) as zf:
        if group["kind"] == "dir":
            return _zip_images(zf, limit, folder=group["key"])
        try:
            info = zf.getinfo(group["key"])
        except KeyError:
            raise ChapterError("Arxiv ichida fayl topilmadi.")
        if info.file_size > MAX_NESTED:
            raise ChapterError(f"«{group['label']}» juda katta.")
        data = zf.read(info)
    return zip_pages(data, limit) if group["kind"] == "zip" else pdf_pages(data, limit)


def _strip_ext(name: str) -> str:
    low = name.lower()
    for e in ARCHIVE_EXT + (".pdf", ".rar", ".cbr", ".7z"):
        if low.endswith(e):
            return name[:-len(e)]
    return name


_NUM = r"(\d{1,5}(?:[.,]\d{1,2})?)"


def guess_num(label: str, strict: bool = False):
    """Papka yoki fayl nomidan bob raqamini topadi: 'Chapter 12', 'Ch.012', '12-bob', '第12話', '012 - Nomi'.
    strict=True — faqat «bob/chapter/qism…» so'zi yonidagi raqam (oddiy matn ichidagi tasodifiy raqam emas)."""
    s = _strip_ext((label or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if not strict else (label or ""))
    s = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", s)                       # [Guruh], (2023), {1080p}
    s = re.sub(r"(?i)(?<![a-z])(?:vol(?:ume)?|v|tom|том|season|s)[\s._-]*\d+", " ", s)
    words = r"chapter|chap|ch|bob|qism|глава|гл|episode|ep|эпизод|часть" + ("" if strict else "|#")
    for p in (r"(?i)(?<![a-zа-я])(?:" + words + r")[\s._:#-]*" + _NUM,
              _NUM + r"\s*[-_ ]?\s*(?:bob|qism|화|話|话|回|chi\b)",
              r"第\s*" + _NUM):
        m = re.search(p, s)
        if m:
            return _to_num(m.group(1))
    if strict:
        return None
    nums = re.findall(r"(?<![\d.,])" + _NUM + r"(?![\d])", s)
    if not nums:
        return None
    first = re.match(r"\s*" + _NUM, s)
    return _to_num(first.group(1) if first else nums[-1])


def _to_num(t):
    try:
        v = float(t.replace(",", "."))
    except ValueError:
        return None
    return v if 0 <= v < 100000 else None


def assign_numbers(labels, start: float):
    """Har bir nomga bob raqami. Hammasi topilsa va takrorlanmasa — nomidagi raqamlar,
    aks holda tartib bo'yicha start, start+1, ... Qaytaradi: (raqamlar, taxminiy_mi)."""
    nums = [guess_num(x) for x in labels]
    if nums and all(n is not None for n in nums) and len(set(nums)) == len(nums):
        return nums, False
    return [float(start) + i for i in range(len(labels))], True


def dhash(raw: bytes, size: int = 16) -> str:
    """Rasmning 256 bitli «farq xeshi» — bir xil rasmni qayta siqilgan bo'lsa ham taniydi.
    Uzun webtoon bo'lagida faqat yuqori qismi (eni × 2·eni) olinadi."""
    from PIL import Image
    rs = getattr(Image, "Resampling", Image)
    with Image.open(io.BytesIO(raw)) as im:
        try:
            im.draft("L", (256, 256))   # JPEG'ni kichraytirib ochish — ancha tez
        except Exception:
            pass
        g = im.convert("L")
        w, h = g.size
        if h > 2 * w:
            g = g.crop((0, 0, w, 2 * w))
        g = g.resize((size + 1, size), rs.BILINEAR)
    px = g.tobytes()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (1 if px[y * (size + 1) + x] > px[y * (size + 1) + x + 1] else 0)
    return f"{bits:0{size * size // 4}x}"


def fingerprint(pages, k: int = 6):
    """Bobning barmoq izi: sahifalar soni va teng oraliqdagi k ta sahifaning dHash'i."""
    n = len(pages)
    if not n:
        return None
    idx = range(n) if n <= k else sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    hs = []
    for i in idx:
        try:
            hs.append(dhash(pages[i][1]))
        except Exception:
            hs.append("")
    return {"n": n, "h": hs}


def pdf_pages(data, limit: int, width: int = 1600):
    """PDF sahifalarini rasmga aylantiradi (eni ~width piksel). data — baytlar yoki fayl yo'li."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise ChapterError("PDF o‘qish kutubxonasi o‘rnatilmagan.")
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        raise ChapterError("PDF fayl ochilmadi (buzilgan yoki parolli).")
    try:
        count = len(pdf)
        out = []
        for i in range(min(count, limit)):
            page = pdf[i]
            try:
                w, h = page.get_size()
                scale = max(0.3, min(4.0, width / max(1.0, w)))
                if (w * scale) * (h * scale) > 40e6:
                    scale = math.sqrt(40e6 / max(1.0, w * h))
                img = page.render(scale=scale).to_pil().convert("RGB")
            finally:
                page.close()
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=92)
            out.append((f"{i + 1:03d}.jpg", buf.getvalue()))
        return out, count
    finally:
        pdf.close()


def extract(data, name: str, mime: str, limit: int):
    """Bitta bob faylidan sahifalar. data — baytlar yoki diskdagi fayl yo'li."""
    k = kind_of(name, mime)
    if k not in ("archive", "pdf"):
        k = sniff(data) or k
    if k == "archive":
        return zip_pages(data, limit)
    if k == "pdf":
        return pdf_pages(data, limit)
    raise ChapterError("Bu fayl turini tushunmadim.")


def _jpeg_info(path):
    from PIL import Image
    with Image.open(path) as im:
        cs = {"L": "/DeviceGray", "CMYK": "/DeviceCMYK"}.get(im.mode, "/DeviceRGB")
        return im.size[0], im.size[1], cs


def write_pdf(jpeg_paths, out_path):
    """JPEG sahifalardan PDF yasaydi: rasmlar qayta siqilmaydi va xotiraga bittalab o'qiladi."""
    n = len(jpeg_paths)
    offsets = {}
    with open(out_path, "wb") as f:
        def obj(num, body: bytes):
            offsets[num] = f.tell()
            f.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = " ".join(f"{3 + 3 * k} 0 R" for k in range(n))
        obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
        for k, p in enumerate(jpeg_paths):
            w, h, cs = _jpeg_info(p)
            pn, cn, imn = 3 + 3 * k, 4 + 3 * k, 5 + 3 * k
            obj(pn, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                     f"/Resources << /XObject << /Im0 {imn} 0 R >> >> /Contents {cn} 0 R >>").encode())
            content = f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q".encode()
            obj(cn, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
            size = os.path.getsize(p)
            decode = " /Decode [1 0 1 0 1 0 1 0]" if cs == "/DeviceCMYK" else ""
            offsets[imn] = f.tell()
            f.write((f"{imn} 0 obj\n<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {cs} "
                     f"/BitsPerComponent 8 /Filter /DCTDecode{decode} /Length {size} >>\nstream\n").encode())
            with open(p, "rb") as src:
                shutil.copyfileobj(src, f)
            f.write(b"\nendstream\nendobj\n")
        xref = f.tell()
        total = 3 + 3 * n
        f.write(f"xref\n0 {total}\n0000000000 65535 f \n".encode())
        for i in range(1, total):
            f.write(f"{offsets[i]:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out_path


def split_by_size(paths, max_bytes):
    """Fayllarni hajmi max_bytes dan oshmaydigan guruhlarga bo'ladi (Telegram 50 MB cheklovi uchun)."""
    groups, cur, size = [], [], 0
    for p in paths:
        s = os.path.getsize(p) + 1024
        if cur and size + s > max_bytes:
            groups.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += s
    if cur:
        groups.append(cur)
    return groups


def make_outputs(paths, out_dir, stem, script_text="", max_bytes=48 * 1024 * 1024, pdf=True, archive=True,
                 ext=".zip"):
    """Tarjima qilingan sahifalardan PDF(lar) va ZIP/CBZ(lar) yasaydi. Qaytaradi: ([pdf...], [arxiv...])."""
    groups = split_by_size(paths, max_bytes - 64 * 1024)
    pdfs, zips = [], []
    for gi, g in enumerate(groups):
        suffix = f"_{gi + 1}-qism" if len(groups) > 1 else ""
        if pdf:
            pp = os.path.join(out_dir, f"{stem}{suffix}.pdf")
            write_pdf(g, pp)
            pdfs.append(pp)
        if not archive:
            continue
        zp = os.path.join(out_dir, f"{stem}{suffix}{ext}")
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_STORED) as zf:
            for p in g:
                zf.write(p, os.path.basename(p))
            if script_text and gi == 0:
                zf.writestr("tarjima_matni.txt", script_text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zips.append(zp)
    return pdfs, zips


def safe_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or "bob"))[0]
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip(" .") or "bob"
    return stem[:60] + "_uz"


LANG_FLAG = {"yapon": "🇯🇵", "korey": "🇰🇷", "xitoy": "🇨🇳", "ingliz": "🇬🇧", "rus": "🇷🇺"}


def flag(lang: str) -> str:
    lang = (lang or "").lower()
    for k, v in LANG_FLAG.items():
        if k in lang:
            return v
    return "🌐"


def script_lines(blocks, lang="", as_html=True):
    """«📝 Matn» tugmasi va tarjima_matni.txt uchun: asl matn → tarjima."""
    esc = html.escape if as_html else (lambda x: x)
    out = []
    n = 0
    for b in blocks:
        if not (b.text or "").strip():
            continue
        n += 1
        orig = (b.original or "").strip()
        tag = " [SFX]" if b.kind == "sfx" else ""
        if as_html:
            out.append(f"<b>{n}.</b>{tag} <i>{esc(orig)}</i>\n   → {esc(b.text)}" if orig else f"<b>{n}.</b>{tag} {esc(b.text)}")
        else:
            out.append(f"{n}.{tag} {orig}\n   → {b.text}" if orig else f"{n}.{tag} {b.text}")
    return out


def chunk_messages(header: str, lines, limit: int = 3900):
    msgs, cur = [], header
    for ln in lines:
        add = ("\n\n" if cur else "") + ln
        if len(cur) + len(add) > limit and cur:
            msgs.append(cur)
            cur = ln
        else:
            cur += add
    if cur:
        msgs.append(cur)
    return msgs
