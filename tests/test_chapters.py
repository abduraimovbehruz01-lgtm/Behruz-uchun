"""chapters.py sinovi: ZIP/PDF dan sahifa olish, PDF/ZIP yasash, matn formatlash."""
import io, os, sys, tempfile, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
import chapters as C
import typeset

tmp = tempfile.mkdtemp()
# 1) ZIP: tabiiy tartib, keraksiz fayllar tashlanadi
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    for n in ["bob/10.jpg", "bob/2.jpg", "bob/1.png", "__MACOSX/bob/._1.png", "bob/.DS_Store", "bob/info.txt", "bob/3.webp"]:
        im = Image.new("RGB", (60, 90), (int(n[4:5]) * 20 if n[4:5].isdigit() else 0, 100, 100))
        b = io.BytesIO(); fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(n.rsplit(".", 1)[-1], "JPEG")
        im.save(b, fmt) if n.rsplit(".", 1)[-1] in ("jpg", "png", "webp") else None
        zf.writestr(n, b.getvalue() if n.rsplit(".", 1)[-1] in ("jpg", "png", "webp") else b"x")
pages, total = C.zip_pages(buf.getvalue(), limit=3)
assert [p[0] for p in pages] == ["1.png", "2.jpg", "3.webp"] and total == 4, (pages, total)
print("1 ok: ZIP tartibi", [p[0] for p in pages], "jami", total)
try:
    C.zip_pages(b"bu zip emas", 5); assert False
except C.ChapterError as e:
    print("1b ok:", e)
# 2) fayl turi
assert C.kind_of("Bob 5.CBZ") == "archive" and C.kind_of("a.pdf") == "pdf" and C.kind_of("x", "image/png") == "image"
assert C.kind_of("a.cbr") == "rar" and C.kind_of("a.docx") == ""
print("2 ok: fayl turlari")
# 3) PDF yasash va pypdfium2 bilan qayta o'qish
paths = []
for i, (w, h) in enumerate([(1100, 1600), (720, 3000), (900, 1300)]):
    p = os.path.join(tmp, f"{i:03d}.jpg")
    Image.new("RGB", (w, h), (30 * i, 120, 200)).save(p, "JPEG", quality=85)
    paths.append(p)
gp = os.path.join(tmp, "gray.jpg"); Image.new("L", (500, 700), 128).save(gp, "JPEG"); paths.append(gp)
pdf = C.write_pdf(paths, os.path.join(tmp, "out.pdf"))
import pypdfium2 as pdfium
doc = pdfium.PdfDocument(pdf)
sizes = [tuple(round(v) for v in doc[i].get_size()) for i in range(len(doc))]
assert sizes == [(1100, 1600), (720, 3000), (900, 1300), (500, 700)], sizes
px = doc[0].render(scale=0.1).to_pil().convert("RGB").getpixel((50, 80))
assert abs(px[2] - 200) < 20, px
doc.close()
print("3 ok: PDF", sizes, "rang", px)
# 4) PDF dan sahifa olish
pp, cnt = C.pdf_pages(open(pdf, "rb").read(), limit=2, width=800)
im = Image.open(io.BytesIO(pp[0][1]))
assert cnt == 4 and len(pp) == 2 and im.size[0] == 800, (cnt, len(pp), im.size)
print("4 ok: PDF sahifalari", [x[0] for x in pp], im.size)
try:
    C.pdf_pages(b"%PDF-1.4 buzuq", 3); assert False
except C.ChapterError as e:
    print("4b ok:", e)
# 5) chiqish fayllari: hajm bo'yicha bo'linadi
biggest = max(os.path.getsize(x) for x in paths)
pdfs, zips = C.make_outputs(paths, tmp, "Bob_1_uz", "1. 何 → Nima", max_bytes=64 * 1024 + biggest + 2048)
assert len(pdfs) == len(zips) >= 2, (pdfs, zips)
with zipfile.ZipFile(zips[0]) as zf:
    names = zf.namelist()
assert "tarjima_matni.txt" in names, names
print("5 ok:", [os.path.basename(x) for x in pdfs + zips])
pdfs, zips = C.make_outputs(paths, tmp, "Bob_1_uz", "")
assert len(pdfs) == 1
# 6) matn
bl = [typeset.Block((0, 0, 1, 1), "Nima?!", "speech", "何？！"), typeset.Block((0, 0, 1, 1), "", "speech", "x"),
      typeset.Block((0, 0, 1, 1), "<b>Bum</b>", "sfx", "ドン")]
lines = C.script_lines(bl)
assert len(lines) == 2 and "&lt;b&gt;" in lines[1] and "[SFX]" in lines[1], lines
msgs = C.chunk_messages("Sarlavha", ["x" * 3000, "y" * 3000, "z"], limit=3900)
assert len(msgs) == 2 and all(len(m) <= 3900 for m in msgs), [len(m) for m in msgs]
assert C.safe_stem("Naruto bob 5 (scan).cbz") == "Naruto bob 5 scan_uz", C.safe_stem("Naruto bob 5 (scan).cbz")
assert C.flag("yaponcha") == "🇯🇵"
print("6 ok: matn va nomlar")
print("HAMMASI O'TDI")
