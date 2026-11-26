"""Admin qismi (bot ichida): manga qo'shish va tahrirlash, bob yuklash (kerak bo'lsa tarjima bilan),
boblarni chop etish/narxlash/o'chirish, statistika, ombor kanalini avtomatik aniqlash."""
import asyncio
import time

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import catalog
import chapters as chapfiles
import translator
import typeset
from core import (CAT, GEM, OWNERS, bot, download, esc, gem_error_text, kb, log, safe_delete, safe_edit,
                  storage_chat, storage_pieces, tell_owners, translate_image, upload_photos, MAX_PAGES, MAX_DOWNLOAD, STATE)
from reader import notify_followers, show_card

router = Router(name="admin")
fmt = catalog.fmt_num
PRICES = (1, 2, 3, 5, 10)
COLLECT: dict[int, list] = {}      # albom qilib yuborilgan sahifalar (message_id, file_id)
JOBS: dict[int, dict] = {}         # admin → {"task": ..., "cancel": bool}


def adm(e) -> bool:
    return CAT.is_admin(e.from_user.id)


def can(e, right) -> bool:
    return CAT.has(e.from_user.id, right)


class AddManga(StatesGroup):
    title = State()
    desc = State()
    genres = State()
    cover = State()
    code = State()


class AddChap(StatesGroup):
    manga = State()
    files = State()
    num = State()
    tr = State()
    busy = State()


class EditF(StatesGroup):
    value = State()


CANCEL = ("❌ Bekor qilish", "adm:cancel")
SKIP = ("⏭ O‘tkazib yuborish", "adm:skip")


# ---------------------------------------------------------------- menyu

def menu_text():
    st = storage_chat()
    db = "✅ Firebase" if getattr(CAT.s, "persistent", False) else "⚠️ vaqtincha xotira (server qayta ishga tushsa o‘chadi)"
    return ("🛠 <b>Admin panel</b>\n\n"
            f"📦 Rasmlar ombori: {'✅ ulangan' if st else '❌ ulanmagan — yopiq kanal oching va botni unga admin qiling'}\n"
            f"🗄 Baza: {db}\n🤖 Gemini: {esc(GEM.status)}\n\n"
            "💡 Menga sahifa yoki fayl yuborsangiz, uni tarjima qilib qaytaraman (sinab ko‘rish uchun).")


def menu_markup(uid):
    rows = []
    if CAT.has(uid, "manga"):
        rows.append([("➕ Manga qo‘shish", "adm:add"), ("📚 Mangalar", "adm:list:0")])
    if CAT.has(uid, "chapter"):
        rows.append([("📤 Bob qo‘shish", "adm:chap")])
    if CAT.has(uid, "stats"):
        rows.append([("📊 Statistika", "adm:stat")])
    return kb(rows)


@router.message(Command("admin"), adm)
async def cmd_admin(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(menu_text(), reply_markup=menu_markup(msg.from_user.id))


@router.message(Command("bekor"))
async def cmd_cancel(msg: Message, state: FSMContext):
    job = JOBS.get(msg.from_user.id)
    if job:
        job["cancel"] = True
    COLLECT.pop(msg.from_user.id, None)
    had = await state.get_state()
    await state.clear()
    import tools
    stopped = tools.cancel(msg.from_user.id)
    await msg.answer("⛔ Bekor qilindi." if (job or had or stopped) else "Hozir hech qanday jarayon yo‘q 🙂")


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    COLLECT.pop(c.from_user.id, None)
    await state.clear()
    await c.answer("Bekor qilindi")
    await safe_edit(c.message, "❌ Bekor qilindi.")


@router.callback_query(F.data == "adm:menu", adm)
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await bot.send_message(c.from_user.id, menu_text(), reply_markup=menu_markup(c.from_user.id))


@router.callback_query(F.data == "adm:stat", adm)
async def cb_stat(c: CallbackQuery):
    if not can(c, "stats"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    s = await CAT.stats()
    await c.answer()
    await bot.send_message(c.from_user.id,
                           "📊 <b>Statistika</b>\n\n"
                           f"📚 Mangalar: {s['manga']} · boblar: {s['chapters']}\n"
                           f"👥 Foydalanuvchilar: {s['users']} (bugun yangi: {s['new_today']})\n"
                           f"📖 Bugun o‘qilgan boblar: {s['reads_today']}\n"
                           f"🪙 Bugun sarflangan jetonlar: {s['jetons_today']}")


# ---------------------------------------------------------------- manga qo'shish

@router.callback_query(F.data == "adm:add", adm)
async def cb_add(c: CallbackQuery, state: FSMContext):
    if not can(c, "manga"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddManga.title)
    await c.answer()
    await bot.send_message(c.from_user.id, "➕ <b>Yangi manga</b>\n\n1/5. Manga nomini yuboring:", reply_markup=kb([[CANCEL]]))


@router.message(AddManga.title, F.text)
async def am_title(msg: Message, state: FSMContext):
    await state.update_data(title=msg.text.strip()[:150])
    await state.set_state(AddManga.desc)
    await msg.answer("2/5. Qisqacha tavsif (syujet) yozing:", reply_markup=kb([[SKIP], [CANCEL]]))


@router.message(AddManga.desc, F.text)
async def am_desc(msg: Message, state: FSMContext):
    await state.update_data(desc=msg.text.strip()[:2000])
    await ask_genres(msg.chat.id, state)


async def ask_genres(chat_id, state):
    await state.set_state(AddManga.genres)
    known = ", ".join(CAT.genres()[:12])
    await bot.send_message(chat_id, "3/5. Janrlarini vergul bilan yozing.\nMasalan: <i>Jangari, Fantastika, Romantika</i>"
                           + (f"\nBor janrlar: {esc(known)}" if known else ""), reply_markup=kb([[SKIP], [CANCEL]]))


@router.message(AddManga.genres, F.text)
async def am_genres(msg: Message, state: FSMContext):
    gs = [g.strip().capitalize() for g in msg.text.replace(";", ",").split(",") if g.strip()][:8]
    await state.update_data(genres=gs)
    await ask_cover(msg.chat.id, state)


async def ask_cover(chat_id, state):
    await state.set_state(AddManga.cover)
    await bot.send_message(chat_id, "4/5. Muqova rasmini yuboring:", reply_markup=kb([[SKIP], [CANCEL]]))


@router.message(AddManga.cover, F.photo)
async def am_cover(msg: Message, state: FSMContext):
    await state.update_data(cover=msg.photo[-1].file_id)
    await ask_code(msg.chat.id, state)


async def ask_code(chat_id, state):
    await state.set_state(AddManga.code)
    nxt = await CAT.next_code()
    await state.update_data(auto=nxt)
    await bot.send_message(chat_id, f"5/5. Manga kodi <b>{nxt}</b> bo‘lsinmi?\nBoshqa raqam xohlasangiz, yozib yuboring.",
                           reply_markup=kb([[(f"✅ {nxt} bo‘lsin", "adm:code")], [CANCEL]]))


@router.message(AddManga.code, F.text)
async def am_code(msg: Message, state: FSMContext):
    code = msg.text.strip()
    if not code.isdigit() or len(code) > 7:
        await msg.answer("Kod faqat raqamlardan iborat bo‘lsin (masalan 125).")
        return
    if CAT.get(code):
        await msg.answer(f"❌ {code} kodi band. Boshqa raqam yozing.")
        return
    await finish_manga(msg.chat.id, msg.from_user.id, state, code)


@router.callback_query(F.data == "adm:code", AddManga.code)
async def cb_code(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await finish_manga(c.from_user.id, c.from_user.id, state, (await state.get_data()).get("auto"))


@router.callback_query(F.data == "adm:skip")
async def cb_skip(c: CallbackQuery, state: FSMContext):
    st = await state.get_state()
    await c.answer()
    if st == AddManga.desc.state:
        await ask_genres(c.from_user.id, state)
    elif st == AddManga.genres.state:
        await ask_cover(c.from_user.id, state)
    elif st == AddManga.cover.state:
        await ask_code(c.from_user.id, state)


async def finish_manga(chat_id, uid, state, code):
    d = await state.get_data()
    await state.clear()
    try:
        m = await CAT.add_manga(d.get("title", "Nomsiz"), d.get("desc", ""), d.get("genres", []), d.get("cover"),
                                code=code, by=uid)
    except ValueError as e:
        await bot.send_message(chat_id, f"❌ {esc(str(e))}")
        return
    await show_card(chat_id, m, uid)
    await bot.send_message(chat_id, f"✅ Manga qo‘shildi! Kodi: <code>{m['code']}</code>\nEndi birinchi bobni yuklang:",
                           reply_markup=kb([[("📤 Bob qo‘shish", f"ach:{m['code']}")]]))


# ---------------------------------------------------------------- bob qo'shish

@router.callback_query(F.data == "adm:chap", adm)
async def cb_chap(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    await c.answer()
    items = CAT.manga_list(hidden=True)[:8]
    if not items:
        await bot.send_message(c.from_user.id, "Avval manga qo‘shing.", reply_markup=kb([[("➕ Manga qo‘shish", "adm:add")]]))
        return
    await state.set_state(AddChap.manga)
    rows = [[(f"{m['title'][:40]} ({m['code']})", f"ach:{m['code']}")] for m in items]
    rows.append([CANCEL])
    await bot.send_message(c.from_user.id, "📤 Qaysi mangaga bob qo‘shamiz? Tanlang yoki kodini yuboring:",
                           reply_markup=kb(rows))


@router.message(AddChap.manga, F.text)
async def ac_manga(msg: Message, state: FSMContext):
    m = CAT.get(msg.text.strip())
    if not m:
        await msg.answer("🤷 Bunday kodli manga yo‘q. Qayta yuboring.")
        return
    await start_files(msg.chat.id, msg.from_user.id, state, m["code"])


@router.callback_query(F.data.startswith("ach:"), adm)
async def cb_ach(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    await c.answer()
    await start_files(c.from_user.id, c.from_user.id, state, c.data[4:])


async def start_files(chat_id, uid, state, code):
    m = CAT.get(code)
    if not m:
        await bot.send_message(chat_id, "Manga topilmadi.")
        return
    if JOBS.get(uid):
        await bot.send_message(chat_id, "⏳ Oldingi bob hali tayyorlanyapti. Tugashini kuting yoki /bekor yozing.")
        return
    if not storage_chat():
        await bot.send_message(chat_id, "📦 Avval rasmlar omborini ulang: Telegram’da <b>yopiq kanal</b> oching va "
                                        f"@{STATE['username']} ni unga <b>admin</b> qiling. Bot kanalni o‘zi taniydi.")
        return
    COLLECT[uid] = []
    await state.set_state(AddChap.files)
    await state.update_data(code=code, file=None)
    await bot.send_message(chat_id,
                           f"📤 <b>{esc(m['title'])}</b> — yangi bob (taxminan {fmt(CAT.next_num(code))}-bob)\n\n"
                           "Bob faylini yuboring: <b>ZIP, CBZ yoki PDF</b> (20 MB gacha).\n"
                           "Yoki sahifalarni rasm qilib (albom) yuboring va «✅ Tayyor» ni bosing.",
                           reply_markup=kb([[("✅ Tayyor (rasmlar yuborildi)", "adm:fdone")], [CANCEL]]))


@router.message(AddChap.files, F.document)
async def ac_doc(msg: Message, state: FSMContext):
    doc = msg.document
    kind = chapfiles.kind_of(doc.file_name or "", doc.mime_type or "")
    if kind == "image":
        lst = COLLECT.setdefault(msg.from_user.id, [])
        lst.append((msg.message_id, doc.file_id))
        if len(lst) in (1, 10, 20, 40, 60):
            await msg.answer(f"📥 Qabul qilindi: {len(lst)} ta rasm. Hammasi yuborilgach «✅ Tayyor» ni bosing.",
                             reply_markup=kb([[("✅ Tayyor", "adm:fdone")]]))
        return
    if kind not in ("archive", "pdf"):
        await msg.answer("🤔 ZIP, CBZ, PDF yoki rasm yuboring." + (" RAR ochilmaydi — ZIP qiling." if kind == "rar" else ""))
        return
    if (doc.file_size or 0) > MAX_DOWNLOAD:
        await msg.answer("📦 Fayl 20 MB dan katta — Telegram botlarga yuklab olishga ruxsat bermaydi. Qismlarga bo‘ling.")
        return
    await state.update_data(file={"id": doc.file_id, "name": doc.file_name or "bob", "mime": doc.mime_type or ""})
    await ask_num(msg.chat.id, state)


@router.message(AddChap.files, F.photo)
async def ac_photo(msg: Message, state: FSMContext):
    lst = COLLECT.setdefault(msg.from_user.id, [])
    lst.append((msg.message_id, msg.photo[-1].file_id))
    if len(lst) in (1, 10, 20, 40, 60):
        await msg.answer(f"📥 Qabul qilindi: {len(lst)} ta rasm. Hammasi yuborilgach «✅ Tayyor» ni bosing.",
                         reply_markup=kb([[("✅ Tayyor", "adm:fdone")]]))


@router.callback_query(F.data == "adm:fdone", AddChap.files)
async def cb_fdone(c: CallbackQuery, state: FSMContext):
    if not COLLECT.get(c.from_user.id):
        await c.answer("Hali rasm yubormadingiz", show_alert=True)
        return
    await c.answer()
    await ask_num(c.from_user.id, state)


async def ask_num(chat_id, state):
    d = await state.get_data()
    nxt = CAT.next_num(d["code"])
    await state.set_state(AddChap.num)
    await bot.send_message(chat_id, f"🔢 Bob raqami <b>{fmt(nxt)}</b> bo‘lsinmi?\nBoshqa raqam bo‘lsa yozing (masalan 12 yoki 12.5).",
                           reply_markup=kb([[(f"✅ {fmt(nxt)}-bob", f"adm:num:{fmt(nxt)}")], [CANCEL]]))


@router.message(AddChap.num, F.text)
async def ac_num(msg: Message, state: FSMContext):
    num = catalog.parse_num(msg.text)
    if num is None:
        await msg.answer("Faqat raqam yozing, masalan 12 yoki 12.5")
        return
    await ask_tr(msg.chat.id, state, num)


@router.callback_query(F.data.startswith("adm:num:"), AddChap.num)
async def cb_num(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await ask_tr(c.from_user.id, state, float(c.data.split(":")[2]))


async def ask_tr(chat_id, state, num):
    d = await state.get_data()
    exists = CAT.chapter(d["code"], num)
    await state.update_data(num=num)
    await state.set_state(AddChap.tr)
    warn = f"\n⚠️ {fmt(num)}-bob allaqachon bor — yangisi uning o‘rniga yoziladi." if exists else ""
    await bot.send_message(chat_id, f"🌐 Sahifalarni o‘zbekchaga tarjima qilaymi?{warn}",
                           reply_markup=kb([[("🌐 Ha, tarjima qil", "adm:tr:1")],
                                            [("✅ Yo‘q, bu tayyor tarjima", "adm:tr:0")], [CANCEL]]))


@router.callback_query(F.data.startswith("adm:tr:"), AddChap.tr)
async def cb_tr(c: CallbackQuery, state: FSMContext):
    await c.answer()
    d = await state.get_data()
    await state.set_state(AddChap.busy)
    uid = c.from_user.id
    items = sorted(COLLECT.pop(uid, []))
    job = {"cancel": False}
    JOBS[uid] = job
    job["task"] = asyncio.create_task(process_chapter(uid, d["code"], d["num"], d.get("file"), items,
                                                      c.data.endswith(":1"), state, job))


async def process_chapter(uid, code, num, file, items, translate, state, job):
    m = CAT.get(code)
    status = await bot.send_message(uid, "⏳ Boshlanmoqda…", reply_markup=kb([[("⛔ To‘xtatish", "adm:stopjob")]]))
    stop_kb = kb([[("⛔ To‘xtatish", "adm:stopjob")]])
    try:
        if file:
            await safe_edit(status, "📥 Fayl yuklab olinmoqda…", stop_kb)
            data = await download(file["id"], timeout=300)
            pages, total = await asyncio.to_thread(chapfiles.extract, data, file["name"], file["mime"], MAX_PAGES)
            del data
            if total > len(pages):
                await bot.send_message(uid, f"ℹ️ Faylda {total} ta sahifa bor, birinchi {MAX_PAGES} tasi olinadi.")
        else:
            pages = []
            for i, (_mid, fid) in enumerate(items):
                pages.append((f"{i + 1}.jpg", await download(fid)))
        if not pages:
            await safe_edit(status, "🤷 Faylda rasm topilmadi.")
            return
        n = len(pages)
        jpegs, skipped, last, stop = [], 0, 0.0, ""
        for i in range(n):
            name, raw = pages[i]
            pages[i] = (name, b"")
            if job["cancel"]:
                stop = "⛔ To‘xtatildi."
                break
            if i == 0 or time.time() - last > 4:
                last = time.time()
                await safe_edit(status, f"{'🌐 Tarjima qilinyapti' if translate else '📦 Tayyorlanyapti'}: "
                                        f"<b>{i}/{n}</b> sahifa…", stop_kb)
            img = await asyncio.to_thread(typeset.load_image, raw)
            if translate:
                async def notify(t, i=i):
                    await safe_edit(status, f"🌐 {i}/{n} sahifa tayyor.\n{t}", stop_kb)
                try:
                    img, _blocks, _lang = await translate_image(img, uid, notify)
                except translator.GeminiError as e:
                    if e.kind in ("quota", "key", "busy"):
                        stop = gem_error_text(e)
                        if e.kind == "key":
                            await tell_owners(f"⚠️ Gemini kaliti xatosi: <code>{esc(str(e))[:300]}</code>")
                        break
                    skipped += 1
            jpegs.extend(await asyncio.to_thread(storage_pieces, img))
            del img
        if stop:
            await safe_edit(status, f"{stop}\nBob saqlanmadi — keyinroq qayta yuklang.")
            return
        await safe_edit(status, f"☁️ Omborga yuklanmoqda… ({len(jpegs)} rasm)")
        ids = await upload_photos(jpegs, storage_chat(), caption=f"{m['title']} · {fmt(num)}-bob")
        await CAT.save_chapter(code, num, ids, price=0, published=False)
        await safe_delete(status)
        extra = f"\n⚠️ {skipped} ta sahifa tarjimasiz qoldi (Google filtri)." if skipped else ""
        await bot.send_message(uid, f"✅ <b>{esc(m['title'])}</b> — {fmt(num)}-bob tayyor: {len(ids)} rasm.{extra}\n\n"
                                    "Avval ko‘rib chiqing, keyin narxini tanlab chop eting:",
                               reply_markup=publish_markup(code, num))
    except chapfiles.ChapterError as e:
        await safe_edit(status, f"❌ {esc(str(e))}")
    except Exception as e:
        log.exception("bob tayyorlashda xato")
        await safe_edit(status, f"❌ Xatolik: {esc(repr(e))[:200]}\nQayta urinib ko‘ring.")
    finally:
        JOBS.pop(uid, None)
        await state.clear()


def publish_markup(code, num, price=0):
    n = fmt(num)
    return kb([[("👁 Ko‘rib chiqish", f"r:{code}:{n}")],
               [("🆓 Bepul chop etish", f"pub:{code}:{n}:0")],
               [(f"🪙 {p}", f"pub:{code}:{n}:{p}") for p in PRICES],
               [("🗑 O‘chirish", f"cdel:{code}:{n}")]])


@router.callback_query(F.data == "adm:stopjob")
async def cb_stopjob(c: CallbackQuery):
    job = JOBS.get(c.from_user.id)
    if job:
        job["cancel"] = True
        await c.answer("To‘xtatilyapti…")
    else:
        await c.answer("Jarayon tugagan")


@router.callback_query(F.data.startswith("pub:"), adm)
async def cb_pub(c: CallbackQuery):
    if not can(c, "chapter"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    _, code, num, price = c.data.split(":")
    num, price = float(num), int(price)
    if not CAT.chapter(code, num):
        await c.answer("Bob topilmadi", show_alert=True)
        return
    newly = await CAT.set_chapter(code, num, published=True, price=price)
    await c.answer("✅ Chop etildi")
    m = CAT.get(code)
    text = f"✅ <b>{esc(m['title'])}</b> — {fmt(num)}-bob chop etildi ({'bepul' if not price else f'{price} 🪙'})."
    if not newly:
        await safe_edit(c.message, text)
        return
    fol = len(m.get("followers", []))
    await safe_edit(c.message, text + (f"\n🔔 {fol} ta kuzatuvchiga xabar yuborilmoqda…" if fol else ""))
    if fol:
        sent = await notify_followers(code, num)
        await safe_edit(c.message, text + f"\n🔔 {sent} ta kuzatuvchiga xabar yuborildi.")


# ---------------------------------------------------------------- mangalarni boshqarish

@router.callback_query(F.data.startswith("adm:list:"), adm)
async def cb_list(c: CallbackQuery):
    page = int(c.data.split(":")[2])
    items = CAT.manga_list(hidden=True)
    per = 10
    part = items[page * per:(page + 1) * per]
    rows = [[(f"{'🙈 ' if m.get('hidden') else ''}{m['title'][:36]} ({m['code']})", f"am:{m['code']}")] for m in part]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"adm:list:{page - 1}"))
    if (page + 1) * per < len(items):
        nav.append(("➡️", f"adm:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("➕ Manga qo‘shish", "adm:add")])
    await c.answer()
    await bot.send_message(c.from_user.id, f"📚 <b>Mangalar</b> — {len(items)} ta", reply_markup=kb(rows))


def manga_admin(code):
    m = CAT.get(code)
    allc = CAT.chapter_list(code, unpublished=True)
    drafts = sum(1 for x in allc if not x.get("published"))
    text = (f"⚙️ <b>{esc(m['title'])}</b> · kod <code>{code}</code>\n"
            f"📚 Boblar: {len(allc) - drafts} (qoralama: {drafts}) · 🔔 {len(m.get('followers', []))} · 👁 {m.get('views', 0)}\n"
            f"🏷 {esc(', '.join(m.get('genres', [])) or '—')} · 📌 {esc(m.get('status') or '—')}"
            + ("\n🙈 Yashirilgan (katalogda ko‘rinmaydi)" if m.get("hidden") else ""))
    rows = [[("✏️ Nomi", f"ae:{code}:title"), ("📝 Tavsif", f"ae:{code}:desc")],
            [("🏷 Janrlar", f"ae:{code}:genres"), ("🖼 Muqova", f"ae:{code}:cover"), ("📌 Holati", f"ae:{code}:status")],
            [("📤 Bob qo‘shish", f"ach:{code}"), ("📑 Boblar", f"acl:{code}:0")],
            [("👁 Ko‘rsatish" if m.get("hidden") else "🙈 Yashirish", f"ahide:{code}"), ("🗑 O‘chirish", f"adel:{code}")],
            [("🏠 Manga sahifasi", f"m:{code}")]]
    return text, kb(rows)


@router.callback_query(F.data.startswith("am:"), adm)
async def cb_am(c: CallbackQuery):
    code = c.data[3:]
    if not CAT.get(code):
        await c.answer("Topilmadi")
        return
    await c.answer()
    text, markup = manga_admin(code)
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


FIELD_PROMPT = {"title": "✏️ Yangi nomini yuboring:", "desc": "📝 Yangi tavsifni yuboring:",
                "genres": "🏷 Janrlarni vergul bilan yuboring:", "cover": "🖼 Yangi muqova rasmini yuboring:",
                "status": "📌 Holatini yozing (masalan: davom etmoqda, tugagan, to‘xtatilgan):"}


@router.callback_query(F.data.startswith("ae:"), adm)
async def cb_ae(c: CallbackQuery, state: FSMContext):
    if not can(c, "manga"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    _, code, field = c.data.split(":")
    await state.set_state(EditF.value)
    await state.update_data(code=code, field=field)
    await c.answer()
    await bot.send_message(c.from_user.id, FIELD_PROMPT[field], reply_markup=kb([[CANCEL]]))


@router.message(EditF.value, F.text | F.photo)
async def ef_value(msg: Message, state: FSMContext):
    d = await state.get_data()
    code, field = d["code"], d["field"]
    if field == "cover":
        if not msg.photo:
            await msg.answer("Rasm yuboring.")
            return
        val = msg.photo[-1].file_id
    elif not msg.text:
        await msg.answer("Matn yuboring.")
        return
    elif field == "genres":
        val = [g.strip().capitalize() for g in msg.text.replace(";", ",").split(",") if g.strip()][:8]
    else:
        val = msg.text.strip()[:2000 if field == "desc" else 150]
    await state.clear()
    if CAT.get(code):
        await CAT.update_manga(code, **{field: val})
        await msg.answer("✅ Saqlandi.")
        text, markup = manga_admin(code)
        await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ahide:"), adm)
async def cb_hide(c: CallbackQuery):
    code = c.data[6:]
    m = CAT.get(code)
    if not m or not can(c, "manga"):
        await c.answer()
        return
    await CAT.update_manga(code, hidden=not m.get("hidden"))
    await c.answer("🙈 Yashirildi" if m.get("hidden") else "👁 Ko‘rinadigan qilindi")
    text, markup = manga_admin(code)
    await safe_edit(c.message, text, markup)


@router.callback_query(F.data.startswith("adel:"), adm)
async def cb_adel(c: CallbackQuery):
    code = c.data[5:]
    m = CAT.get(code)
    if not m or not can(c, "manga"):
        await c.answer()
        return
    await c.answer()
    await bot.send_message(c.from_user.id, f"🗑 <b>{esc(m['title'])}</b> va uning barcha boblari o‘chirilsinmi?",
                           reply_markup=kb([[("✅ Ha, o‘chirilsin", f"adel2:{code}"), ("❌ Yo‘q", "adm:cancel")]]))


@router.callback_query(F.data.startswith("adel2:"), adm)
async def cb_adel2(c: CallbackQuery):
    if not can(c, "manga"):
        await c.answer()
        return
    await CAT.delete_manga(c.data[6:])
    await c.answer("O‘chirildi")
    await safe_edit(c.message, "🗑 Manga o‘chirildi.")


# ---------------------------------------------------------------- boblarni boshqarish

@router.callback_query(F.data.startswith("acl:"), adm)
async def cb_acl(c: CallbackQuery):
    _, code, page = c.data.split(":")
    page = int(page)
    chs = CAT.chapter_list(code, unpublished=True)
    per = 24
    btns = []
    for x in chs[page * per:(page + 1) * per]:
        mark = "📝" if not x.get("published") else ("🪙" if int(x.get("price") or 0) else "")
        btns.append((f"{mark}{fmt(x['num'])}", f"ac:{code}:{fmt(x['num'])}"))
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"acl:{code}:{page - 1}"))
    if (page + 1) * per < len(chs):
        nav.append(("➡️", f"acl:{code}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("📤 Bob qo‘shish", f"ach:{code}"), ("⬅️ Manga", f"am:{code}")])
    await c.answer()
    await bot.send_message(c.from_user.id, f"📑 Boblar ({len(chs)}): 📝 — qoralama, 🪙 — pullik", reply_markup=kb(rows))


def chapter_admin(code, num):
    x = CAT.chapter(code, num)
    m = CAT.get(code)
    price = int(x.get("price") or 0)
    text = (f"📄 <b>{esc(m['title'])}</b> — {fmt(num)}-bob\n{x.get('pages', 0)} rasm · "
            f"{'✅ chop etilgan' if x.get('published') else '📝 qoralama'} · narx: {'bepul' if not price else f'{price} 🪙'}")
    n = fmt(num)
    rows = [[("🆓" + (" ✓" if not price else ""), f"cp:{code}:{n}:0")]
            + [(f"🪙{p}" + (" ✓" if price == p else ""), f"cp:{code}:{n}:{p}") for p in PRICES],
            [("🙈 Chop etilmagan qilish", f"cunpub:{code}:{n}") if x.get("published")
             else ("✅ Chop etish", f"pub:{code}:{n}:{price}"), ("👁 Ko‘rish", f"r:{code}:{n}")],
            [("🗑 O‘chirish", f"cdel:{code}:{n}"), ("⬅️ Boblar", f"acl:{code}:0")]]
    return text, kb(rows)


@router.callback_query(F.data.startswith("ac:"), adm)
async def cb_ac(c: CallbackQuery):
    _, code, num = c.data.split(":")
    if not CAT.chapter(code, float(num)):
        await c.answer("Topilmadi")
        return
    await c.answer()
    text, markup = chapter_admin(code, float(num))
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("cp:") | F.data.startswith("cunpub:"), adm)
async def cb_cp(c: CallbackQuery):
    if not can(c, "chapter"):
        await c.answer("Bu huquq sizda yo‘q", show_alert=True)
        return
    parts = c.data.split(":")
    code, num = parts[1], float(parts[2])
    if not CAT.chapter(code, num):
        await c.answer()
        return
    if parts[0] == "cp":
        await CAT.set_chapter(code, num, price=int(parts[3]))
        await c.answer("Narx saqlandi")
    else:
        await CAT.set_chapter(code, num, published=False)
        await c.answer("Chop etilmagan qilindi")
    text, markup = chapter_admin(code, num)
    await safe_edit(c.message, text, markup)


@router.callback_query(F.data.startswith("cdel:"), adm)
async def cb_cdel(c: CallbackQuery):
    _, code, num = c.data.split(":")
    await c.answer()
    await bot.send_message(c.from_user.id, f"🗑 {num}-bob o‘chirilsinmi?",
                           reply_markup=kb([[("✅ Ha", f"cdel2:{code}:{num}"), ("❌ Yo‘q", "adm:cancel")]]))


@router.callback_query(F.data.startswith("cdel2:"), adm)
async def cb_cdel2(c: CallbackQuery):
    if not can(c, "chapter"):
        await c.answer()
        return
    _, code, num = c.data.split(":")
    if CAT.chapter(code, float(num)):
        await CAT.delete_chapter(code, float(num))
    await c.answer("O‘chirildi")
    await safe_edit(c.message, f"🗑 {num}-bob o‘chirildi.")


# ---------------------------------------------------------------- ombor kanali

@router.my_chat_member()
async def on_member(ev: ChatMemberUpdated):
    if ev.chat.type != "channel":
        return
    st = ev.new_chat_member.status
    if st in ("administrator", "creator") and ev.from_user and CAT.is_admin(ev.from_user.id):
        await CAT.set_cfg(storage_chat=ev.chat.id)
        log.info("ombor kanali: %s (%s)", ev.chat.title, ev.chat.id)
        try:
            await bot.send_message(ev.from_user.id, f"✅ «{esc(ev.chat.title or 'kanal')}» rasmlar ombori qilib "
                                                    "belgilandi. Endi bob yuklashingiz mumkin: /admin")
        except Exception:
            pass
    elif st in ("left", "kicked") and CAT.cfg.get("storage_chat") == ev.chat.id:
        await CAT.set_cfg(storage_chat=None)
        await tell_owners("⚠️ Bot rasmlar ombori kanalidan chiqarildi! Yangi bob yuklash uchun botni qayta admin qiling.",
                          force=True)
