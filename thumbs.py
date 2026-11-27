"""Ilova uchun kichik rasmlar.

  • bob rasmi — 1-sahifaning yuqori qismi (240×320, boblar to'rida ko'rinadi);
  • muqovaning kichik nusxasi — 300×420 (katalog va reytingda).
Rasmlar ombor kanaliga 10 tadan yuklanadi (har biri ~20 KB) va file_id lari bob/manga ma'lumotida saqlanadi.
Yangi chop etilgan boblar va oldingi boblar uchun fon rejimida, daqiqada bir yasaladi."""
import asyncio
import time

import typeset
from core import CAT, download, log, storage_chat, upload_photos

BATCH = 10
EVERY = 60
STATE = {"busy": False, "last": 0.0, "fail": {}}


def chapter_thumb(raw: bytes) -> bytes:
    img = typeset.load_image(raw).convert("RGB")
    w, h = img.size
    img = img.crop((0, 0, w, min(h, int(w * 4 / 3))))          # sahifaning yuqori qismi, 3:4
    img = img.resize((240, 320), typeset._RS.LANCZOS)
    return typeset.to_jpeg(img, 74)


def cover_thumb(raw: bytes) -> bytes:
    img = typeset.load_image(raw).convert("RGB")
    w, h = img.size
    tw, th = 300, 420
    k = max(tw / w, th / h)
    img = img.resize((max(tw, round(w * k)), max(th, round(h * k))), typeset._RS.LANCZOS)
    w, h = img.size
    x, y = (w - tw) // 2, max(0, (h - th) // 3)
    return typeset.to_jpeg(img.crop((x, y, x + tw, y + th)), 76)


def todo(limit=BATCH):
    """Kichik rasmi yo'q (yoki muqovasi almashgan) narsalar: [("cover", kod, None) | ("chap", kod, raqam)]."""
    items = []
    for code, m in CAT.manga.items():
        if m.get("cover") and m.get("cover_t_src") != m.get("cover") and STATE["fail"].get("c" + code, 0) < 3:
            items.append(("cover", code, None))
        chs = sorted(((cid, c) for cid, c in (m.get("chapters") or {}).items() if c.get("published")),
                     key=lambda x: -float(x[1].get("num") or 0))
        for cid, c in chs:
            if not c.get("thumb") and STATE["fail"].get(cid, 0) < 3:
                items.append(("chap", code, float(c["num"])))
        if len(items) >= limit * 3:
            break
    # avval muqovalar, keyin eng yangi boblar
    items.sort(key=lambda x: 0 if x[0] == "cover" else 1)
    return items[:limit]


async def run_once() -> int:
    import traffic
    if STATE["busy"] or not storage_chat() or traffic.over():
        return 0
    items = todo()
    if not items:
        return 0
    STATE["busy"] = True
    try:
        jpegs, done = [], []
        for kind, code, num in items:
            key = ("c" + code) if kind == "cover" else f"{code}_{num:g}".replace(".", "-")
            try:
                m = CAT.get(code)
                if kind == "cover":
                    raw = await download(m["cover"])
                    jpegs.append(await asyncio.to_thread(cover_thumb, raw))
                else:
                    pages = (await CAT.get_pages(code, num)).get("pages") or []
                    if not pages:
                        STATE["fail"][key] = 9
                        continue
                    raw = await download(pages[0])
                    jpegs.append(await asyncio.to_thread(chapter_thumb, raw))
                done.append((kind, code, num, m.get("cover") if kind == "cover" else None))
            except Exception as e:
                STATE["fail"][key] = STATE["fail"].get(key, 0) + 1
                log.warning("kichik rasm yasalmadi (%s %s %s): %s", kind, code, num, e)
        if not jpegs:
            return 0
        ids = await upload_photos(jpegs, storage_chat(), caption="🖼 Ilova uchun kichik rasmlar")
        for (kind, code, num, src), fid in zip(done, ids):
            if not CAT.get(code):
                continue
            if kind == "cover":
                await CAT.update_manga(code, cover_t=fid, cover_t_src=src)
            elif CAT.chapter(code, num):
                await CAT.set_chapter(code, num, thumb=fid)
        return len(ids)
    finally:
        STATE["busy"] = False


async def tick(now=None):
    now = now or time.time()
    if now - STATE["last"] < EVERY:
        return 0
    STATE["last"] = now
    return await run_once()
