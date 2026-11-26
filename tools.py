"""Adminlar uchun tezkor tarjima: sahifa (rasm) yoki fayl (PDF/ZIP/CBZ) yuborilsa — tarjima qilib, o'sha
formatda qaytaradi. Oddiy foydalanuvchilar uchun emas (ular faqat o'qiydi)."""
import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import BufferedInputFile, FSInputFile, Message

import chapters
import translator
import typeset
from core import (CAT, MAX_DOWNLOAD, MAX_PAGES, bot, download, esc, gem_error_text, kb, log, rp, safe_delete,
                  safe_edit, tell_owners, translate_image)

router = Router(name="tools")
CACHE: dict[str, dict] = {}


def can_translate(e) -> bool:
    return CAT.has(e.from_user.id, "chapter")


@dataclass
class Item:
    order: int
    kind: str          # "page" | "chapter"
    msg: Message
    file_id: str
    name: str
    mime: str = ""
    as_file: bool = False
    cancelled: bool = False


QUEUES: dict[int, list[Item]] = {}
WORKERS: dict[int, asyncio.Task] = {}
CURRENT: dict[int, Item] = {}


def cancel(uid) -> bool:
    n = len(QUEUES.get(uid, []))
    if uid in QUEUES:
        QUEUES[uid].clear()
    cur = CURRENT.get(uid)
    if cur:
        cur.cancelled = True
    return bool(cur or n)


async def enqueue(item: Item):
    uid = item.msg.from_user.id
    QUEUES.setdefault(uid, []).append(item)
    t = WORKERS.get(uid)
    if not t or t.done():
        WORKERS[uid] = asyncio.create_task(worker(uid))


async def worker(uid: int):
    await asyncio.sleep(1.0)  # albomdagi barcha rasmlar kelib olsin
    while QUEUES.get(uid):
        q = QUEUES[uid]
        q.sort(key=lambda it: it.order)
        item = q.pop(0)
        CURRENT[uid] = item
        try:
            await (do_page(item) if item.kind == "page" else do_file(item))
        except Exception:
            log.exception("tarjima ishida xato")
            try:
                await item.msg.answer("❌ Kutilmagan xatolik yuz berdi. Qayta urinib ko‘ring.")
            except Exception:
                pass
        finally:
            CURRENT.pop(uid, None)
    QUEUES.pop(uid, None)


async def do_page(item: Item):
    msg, uid = item.msg, item.msg.from_user.id
    left = len(QUEUES.get(uid, []))
    status = await msg.answer("⏳ Tarjima qilinyapti…" + (f" (navbatda yana {left} ta)" if left else ""),
                              reply_parameters=rp(msg))

    async def notify(t):
        await safe_edit(status, t)

    try:
        await bot.send_chat_action(chat_id=msg.chat.id, action=ChatAction.UPLOAD_PHOTO)
        img = await asyncio.to_thread(typeset.load_image, await download(item.file_id))
        out, blocks, lang = await translate_image(img, uid, notify)
    except translator.GeminiError as e:
        await safe_edit(status, gem_error_text(e))
        if e.kind == "key":
            await tell_owners(f"⚠️ Gemini kaliti xatosi: <code>{esc(str(e))[:300]}</code>")
        return
    except Exception:
        log.exception("sahifa xatosi")
        await safe_edit(status, "❌ Rasmni ochib bo‘lmadi yoki xatolik yuz berdi.")
        return
    shown = [b for b in blocks if b.kind != "sfx"]
    if not shown:
        await safe_edit(status, "🤷 Bu sahifada tarjima qiladigan matn topilmadi.")
        return
    jpeg = await asyncio.to_thread(typeset.to_jpeg, out, 90)
    name = os.path.splitext(os.path.basename(item.name))[0] + "_uz.jpg"
    texts = chapters.chunk_messages(f"📝 <b>Sahifa matni</b> {chapters.flag(lang)} → 🇺🇿",
                                    chapters.script_lines(blocks, lang))
    cid = os.urandom(5).hex()
    CACHE[cid] = {"text": texts}
    while len(CACHE) > 200:
        CACHE.pop(next(iter(CACHE)))
    markup = kb([[("📝 Matn", f"tt:{cid}")]])
    caption = f"✅ Tayyor! {chapters.flag(lang)} → 🇺🇿 · {len(shown)} ta matn"
    as_doc = item.as_file or out.height > 3 * out.width or len(jpeg) > 9_500_000
    try:
        if as_doc:
            await msg.answer_document(BufferedInputFile(jpeg, name), caption=caption, reply_markup=markup,
                                      reply_parameters=rp(msg))
        else:
            await msg.answer_photo(BufferedInputFile(jpeg, name), caption=caption, reply_markup=markup,
                                   reply_parameters=rp(msg))
    except TelegramBadRequest:
        await msg.answer_document(BufferedInputFile(jpeg, name), caption=caption, reply_markup=markup,
                                  reply_parameters=rp(msg))
    await safe_delete(status)


async def do_file(item: Item):
    msg, uid = item.msg, item.msg.from_user.id
    status = await msg.answer("📦 Fayl yuklab olinmoqda…", reply_parameters=rp(msg))
    tmp = tempfile.mkdtemp(prefix="tarjima_")
    try:
        try:
            data = await download(item.file_id, timeout=300)
            pages, total = await asyncio.to_thread(chapters.extract, data, item.name, item.mime, MAX_PAGES)
            del data
        except chapters.ChapterError as e:
            await safe_edit(status, f"❌ {esc(str(e))}")
            return
        if not pages:
            await safe_edit(status, "🤷 Fayl ichida rasm topilmadi.")
            return
        n = len(pages)
        paths, script, langs = [], [], []
        stop, last, found = "", 0.0, 0
        for i in range(n):
            name, raw = pages[i]
            pages[i] = (name, b"")
            if item.cancelled:
                stop = "⛔ To‘xtatildi."
                break
            if i == 0 or time.time() - last > 4:
                last = time.time()
                await safe_edit(status, f"📖 Tarjima qilinyapti: <b>{i}/{n}</b> sahifa…")

            async def notify(t, i=i):
                await safe_edit(status, f"📖 {i}/{n} sahifa tayyor.\n{t}")

            img = None
            try:
                img = await asyncio.to_thread(typeset.load_image, raw)
                out, blocks, lang = await translate_image(img, uid, notify)
                if lang:
                    langs.append(lang)
            except translator.GeminiError as e:
                if e.kind in ("quota", "key", "busy"):
                    stop = gem_error_text(e)
                    break
                if img is None:
                    continue
                out, blocks = img, []
            except Exception:
                log.exception("fayl sahifasi xatosi")
                if img is None:
                    continue
                out, blocks = img, []
            p = os.path.join(tmp, f"{i + 1:03d}.jpg")
            await asyncio.to_thread(out.convert("RGB").save, p, "JPEG", quality=88)
            paths.append(p)
            found += sum(1 for b in blocks if b.kind != "sfx")
            lines = chapters.script_lines(blocks, as_html=False)
            script.append(f"--- {i + 1}-sahifa ---\n" + ("\n".join(lines) if lines else "(matn yo‘q)"))
        if not paths:
            await safe_edit(status, stop or "❌ Birorta sahifani tarjima qilib bo‘lmadi.")
            return
        await safe_edit(status, f"📦 Fayl tayyorlanmoqda… ({len(paths)} sahifa)")
        stem = chapters.safe_stem(item.name)
        is_pdf = chapters.kind_of(item.name, item.mime) == "pdf"
        ext = ".cbz" if item.name.lower().endswith(".cbz") else ".zip"
        pdfs, zips = await asyncio.to_thread(chapters.make_outputs, paths, tmp, stem, "\n\n".join(script),
                                             48 * 1024 * 1024, True, not is_pdf, ext)
        lang = max(set(langs), key=langs.count) if langs else ""
        head = f"✅ Tarjima qilindi: <b>{len(paths)}</b> sahifa, {found} ta matn {chapters.flag(lang)} → 🇺🇿"
        if total > n:
            head += f"\nℹ️ Faylda {total} ta sahifa bor edi — birinchi {n} tasi olindi."
        if stop:
            head += "\n" + stop
        first, extra = (zips, pdfs) if zips else (pdfs, [])
        for k, pth in enumerate(first):
            await msg.answer_document(FSInputFile(pth, filename=os.path.basename(pth)), caption=head if k == 0 else None,
                                      reply_parameters=rp(msg) if k == 0 else None)
        for k, pth in enumerate(extra):
            await msg.answer_document(FSInputFile(pth, filename=os.path.basename(pth)),
                                      caption="📖 Telefonda o‘qish uchun PDF ko‘rinishi." if k == 0 else None)
        await safe_delete(status)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.message(StateFilter(None), F.photo, can_translate)
async def on_photo(msg: Message):
    ph = msg.photo[-1]
    await enqueue(Item(msg.message_id, "page", msg, ph.file_id, f"sahifa_{msg.message_id}.jpg"))


@router.message(StateFilter(None), F.document, can_translate)
async def on_document(msg: Message):
    doc = msg.document
    kind = chapters.kind_of(doc.file_name or "", doc.mime_type or "")
    if kind == "rar":
        await msg.answer("📦 RAR/7z arxivni ocha olmayman — ZIP yoki CBZ qilib yuboring.", reply_parameters=rp(msg))
        return
    if not kind:
        await msg.answer("🤔 Rasm, ZIP/CBZ yoki PDF yuboring.", reply_parameters=rp(msg))
        return
    if (doc.file_size or 0) > MAX_DOWNLOAD:
        await msg.answer("📦 Fayl 20 MB dan katta — Telegram ruxsat bermaydi. Qismlarga bo‘lib yuboring.",
                         reply_parameters=rp(msg))
        return
    await enqueue(Item(msg.message_id, "page" if kind == "image" else "chapter", msg, doc.file_id,
                       doc.file_name or "fayl", doc.mime_type or "", as_file=True))


@router.callback_query(F.data.startswith("tt:"))
async def cb_text(c):
    e = CACHE.get(c.data[3:])
    if not e:
        await c.answer("Eskirgan — qayta yuboring.", show_alert=True)
        return
    await c.answer()
    for t in e["text"]:
        await bot.send_message(c.from_user.id, t)
