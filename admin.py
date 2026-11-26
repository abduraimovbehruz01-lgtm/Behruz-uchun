"""Admin qismi (bot ichida): admin panel menyusi, manga qo'shish va tahrirlash (holati, chiqish kunlari),
bob yuklash — bittalab yoki ko'p bobni birdan (ZIP ichida papkalar, bir nechta fayl, havola orqali katta fayl),
takroriy bobni aniqlash, tarjima (glossariy bilan), chop etish, narxlash, rejalashtirish.
Adminlar, jurnal va majburiy obuna — staff.py; sahifa tahriri va glossariy — editor.py."""
import asyncio
import os
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import catalog
import chapters as chapfiles
import translator
import typeset
import promo
from core import (A_ADD, A_ADMINS, A_CHAP, A_EXIT, A_JOURNAL, A_LIST, A_PROMO, A_SCHED, A_SRC, A_STAT, A_SUBS,
                  ADMIN_BUTTONS, CAT, GEM,
                  MAX_DOWNLOAD, MAX_PAGES, STATE, LinkError, admin_keyboard, bot, channels, download, download_to, esc,
                  fetch_url, find_url, gem_error_text, jlog, kb, log, no_keyboard, safe_delete, safe_edit,
                  storage_chat, storage_pieces, tell_owners, translate_image, upload_photos, user_tmp)
from reader import send_home, show_card

router = Router(name="admin")
fmt = catalog.fmt_num
ranges = catalog.ranges
PRICES = (1, 2, 3, 5, 10)
FREE_COUNTS = (0, 3, 5, 10)
COLLECT: dict[int, list] = {}      # albom qilib yuborilgan sahifalar (message_id, file_id)
PENDING: dict[int, dict] = {}      # ketma-ket kelayotgan bob fayllari — biroz kutib, hammasi birga ko'riladi
SETTLE: dict[int, asyncio.Task] = {}
SETTLE_DELAY = 1.5
JOBS: dict[int, dict] = {}         # admin → {"task": ..., "cancel": bool}
DOWNLOADING: set[int] = set()      # havoladan fayl yuklayotgan adminlar (server uxlab qolmasin)
RETRY: dict[int, dict] = {}        # «o'xshash» deb to'xtatilgan boblarni baribir yuklash uchun
SUGGEST: dict[str, dict] = {}      # glossariyga taklif qilingan ismlar (editor.py saqlaydi)


def adm(e) -> bool:
    return CAT.is_admin(e.from_user.id)


def can(e, right) -> bool:
    return CAT.has(e.from_user.id, right)


async def deny(c: CallbackQuery):
    await c.answer("Bu huquq sizda yo‘q", show_alert=True)


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
    bulk = State()
    busy = State()


class EditF(StatesGroup):
    value = State()


CANCEL = ("❌ Bekor qilish", "adm:cancel")
SKIP = ("⏭ O‘tkazib yuborish", "adm:skip")


def price_text(p) -> str:
    return "bepul" if not int(p or 0) else f"{int(p)} 🪙"


# ---------------------------------------------------------------- menyu

def menu_text(uid=None):
    st = storage_chat()
    title = CAT.cfg.get("storage_title")
    db = "✅ Firebase" if getattr(CAT.s, "persistent", False) else "⚠️ vaqtincha xotira (server qayta ishga tushsa o‘chadi)"
    store = (f"✅ «{esc(title)}»" if st and title else "✅ ulangan" if st
             else "❌ ulanmagan — yopiq kanal oching va botni unga admin qiling")
    lines = ["🛠 <b>Admin panel</b>", "", f"📦 Rasmlar ombori: {store}", f"🗄 Baza: {db}", f"🤖 Gemini: {esc(GEM.status)}"]
    sch = CAT.scheduled()
    if sch:
        lines.append(f"⏰ Rejada: {len(sch)} ta bob (eng yaqini {catalog.when_text(sch[0][0])})")
    chs = channels()
    lines.append(f"📢 Majburiy obuna: {len(chs)} ta kanal" if chs else "📢 Majburiy obuna: yo‘q")
    srcs = [x for x in CAT.cfg.get("sources") or [] if isinstance(x, dict)]
    if srcs:
        lines.append(f"🔗 Hamkor manbalar: {len(srcs)} ta")
    pr = CAT.cfg.get("promo") or {}
    lines.append(f"📣 Reklama kanali: «{esc(pr.get('title') or 'kanal')}»" + ("" if pr.get("on", True) else " (to‘xtatilgan)")
                 if pr.get("id") else "📣 Reklama kanali: ulanmagan")
    if uid is not None and not CAT.rights(uid):
        lines.append("\n⚠️ Sizga hali birorta huquq berilmagan — bosh admin bilan bog‘laning.")
    elif uid is None or CAT.has(uid, "chapter"):
        lines.append("\n💡 Menga sahifa yoki fayl yuborsangiz, uni tarjima qilib qaytaraman (sinab ko‘rish uchun).")
    return "\n".join(lines)


async def send_panel(uid):
    """Admin paneli: holat matni va ekran pastidagi doimiy tugmalar."""
    st = storage_chat()
    if st and not CAT.cfg.get("storage_title"):   # oldin ulangan ombor kanalining nomini bir marta bilib olamiz
        try:
            ch = await bot.get_chat(st)
            if getattr(ch, "title", None):
                await CAT.set_cfg(storage_title=ch.title)
        except Exception:
            pass
    await bot.send_message(uid, menu_text(uid), reply_markup=admin_keyboard(uid))
    u = await CAT.user(uid)
    if u.get("kb") != "admin":
        await CAT.save_user(uid, kb="admin")


@router.message(Command("admin"), adm)
async def cmd_admin(msg: Message, state: FSMContext):
    await state.clear()
    await send_panel(msg.from_user.id)


@router.message(F.text.in_(set(ADMIN_BUTTONS)), adm)
async def on_admin_button(msg: Message, state: FSMContext):
    """Pastki admin tugmalari. Qaysi bosqichda bo'lmasin, tugma bosilsa — o'sha bo'limga o'tiladi."""
    uid, t = msg.from_user.id, msg.text
    await state.clear()
    COLLECT.pop(uid, None)
    PENDING.pop(uid, None)
    need = {A_ADD: "manga", A_LIST: "manga", A_CHAP: "chapter", A_SCHED: "chapter", A_STAT: "stats",
            A_SUBS: "settings", A_SRC: "chapter", A_PROMO: "settings"}.get(t)
    if (need and not CAT.has(uid, need)) or (t == A_ADMINS and not CAT.is_owner(uid)) or \
            (t == A_JOURNAL and not (CAT.is_owner(uid) or CAT.has(uid, "settings"))):
        await msg.answer("Bu huquq sizda yo‘q.", reply_markup=admin_keyboard(uid))
        return
    import staff
    if t == A_EXIT:
        await bot.send_message(uid, "🚪 Admin paneldan chiqdingiz. Qaytish uchun: /admin", reply_markup=no_keyboard())
        await CAT.save_user(uid, kb="")
        await send_home(uid, uid, msg.from_user.first_name or "do‘stim")
    elif t == A_ADD:
        await start_add_manga(uid, state)
    elif t == A_LIST:
        await send_manga_list(uid, 0)
    elif t == A_CHAP:
        await choose_manga(uid, state)
    elif t == A_SCHED:
        await send_scheduled(uid)
    elif t == A_STAT:
        await send_stats(uid)
    elif t == A_JOURNAL:
        await staff.send_journal(uid, 0)
    elif t == A_ADMINS:
        await staff.send_admins(uid)
    elif t == A_SUBS:
        await staff.send_subs(uid)
    elif t == A_PROMO:
        await staff.send_promo(uid)
    elif t == A_SRC:
        import autosrc
        await autosrc.send_sources(uid)


@router.message(Command("bekor"))
async def cmd_cancel(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    job = JOBS.get(uid)
    if job:
        job["cancel"] = True
    COLLECT.pop(uid, None)
    PENDING.pop(uid, None)
    had = await state.get_state()
    await state.clear()
    import tools
    stopped = tools.cancel(uid)
    await msg.answer("⛔ Bekor qilindi." if (job or had or stopped) else "Hozir hech qanday jarayon yo‘q 🙂",
                     reply_markup=admin_keyboard(uid) if CAT.is_admin(uid) else None)


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    COLLECT.pop(c.from_user.id, None)
    PENDING.pop(c.from_user.id, None)
    await state.clear()
    await c.answer("Bekor qilindi")
    await safe_edit(c.message, "❌ Bekor qilindi.")


@router.callback_query(F.data == "adm:menu", adm)
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await send_panel(c.from_user.id)


async def send_stats(uid):
    s = await CAT.stats()
    await bot.send_message(uid, "📊 <b>Statistika</b>\n\n"
                                f"📚 Mangalar: {s['manga']} · boblar: {s['chapters']}\n"
                                f"👥 Foydalanuvchilar: {s['users']} (bugun yangi: {s['new_today']})\n"
                                f"📖 Bugun o‘qilgan boblar: {s['reads_today']}\n"
                                f"🪙 Bugun sarflangan jetonlar: {s['jetons_today']}")


@router.callback_query(F.data == "adm:stat", adm)
async def cb_stat(c: CallbackQuery):
    if not can(c, "stats"):
        await deny(c)
        return
    await c.answer()
    await send_stats(c.from_user.id)


# ---------------------------------------------------------------- manga qo'shish

@router.callback_query(F.data == "adm:add", adm)
async def cb_add(c: CallbackQuery, state: FSMContext):
    if not can(c, "manga"):
        await deny(c)
        return
    await c.answer()
    await start_add_manga(c.from_user.id, state)


async def start_add_manga(uid, state):
    await state.clear()
    await state.set_state(AddManga.title)
    await bot.send_message(uid, "➕ <b>Yangi manga</b>\n\n1/5. Manga nomini yuboring:", reply_markup=kb([[CANCEL]]))


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
    await finish_manga(msg.chat.id, msg.from_user.id, state, code, msg)


@router.callback_query(F.data == "adm:code", AddManga.code)
async def cb_code(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await finish_manga(c.from_user.id, c.from_user.id, state, (await state.get_data()).get("auto"), c)


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


async def finish_manga(chat_id, uid, state, code, event=None):
    d = await state.get_data()
    await state.clear()
    try:
        m = await CAT.add_manga(d.get("title", "Nomsiz"), d.get("desc", ""), d.get("genres", []), d.get("cover"),
                                code=code, by=uid)
    except ValueError as e:
        await bot.send_message(chat_id, f"❌ {esc(str(e))}")
        return
    await jlog(event or uid, f"➕ Manga qo‘shildi: {m['title']} (kod {m['code']})")
    await show_card(chat_id, m, uid)
    await bot.send_message(chat_id, f"✅ Manga qo‘shildi! Kodi: <code>{m['code']}</code>\nEndi boblarni yuklang:",
                           reply_markup=kb([[("📤 Bob qo‘shish", f"ach:{m['code']}")]]))


# ---------------------------------------------------------------- bob qo'shish: fayllarni qabul qilish

@router.callback_query(F.data == "adm:chap", adm)
async def cb_chap(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await deny(c)
        return
    await c.answer()
    await choose_manga(c.from_user.id, state)


async def choose_manga(uid, state):
    items = CAT.manga_list(hidden=True)[:8]
    if not items:
        await bot.send_message(uid, "Avval manga qo‘shing.", reply_markup=kb([[("➕ Manga qo‘shish", "adm:add")]]))
        return
    await state.set_state(AddChap.manga)
    rows = [[(f"{m['title'][:40]} ({m['code']})", f"ach:{m['code']}")] for m in items]
    rows.append([CANCEL])
    await bot.send_message(uid, "📤 Qaysi mangaga bob qo‘shamiz? Tanlang yoki kodini yuboring:", reply_markup=kb(rows))


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
        await deny(c)
        return
    await c.answer()
    await start_files(c.from_user.id, c.from_user.id, state, c.data[4:])


async def start_files(chat_id, uid, state, code):
    m = CAT.get(code)
    if not m:
        await bot.send_message(chat_id, "Manga topilmadi.")
        return
    if JOBS.get(uid):
        await bot.send_message(chat_id, "⏳ Oldingi boblar hali tayyorlanyapti. Tugashini kuting yoki /bekor yozing.")
        return
    if not storage_chat():
        await bot.send_message(chat_id, "📦 Avval rasmlar omborini ulang: Telegram’da <b>yopiq kanal</b> oching va "
                                        f"@{STATE['username']} ni unga <b>admin</b> qiling. Bot kanalni o‘zi taniydi.")
        return
    COLLECT[uid] = []
    PENDING.pop(uid, None)
    RETRY.pop(uid, None)
    user_tmp(uid, fresh=True)
    await state.set_state(AddChap.files)
    await state.update_data(code=code)
    await bot.send_message(chat_id,
                           f"📤 <b>{esc(m['title'])}</b> — yangi bob (taxminan {fmt(CAT.next_num(code))}-bob)\n\n"
                           "Yuboring:\n"
                           "• bob fayli — <b>ZIP, CBZ yoki PDF</b> (20 MB gacha);\n"
                           "• <b>bir nechta bob</b> — bir nechta fayl birdan yoki ichida bob papkalari bo‘lgan bitta ZIP;\n"
                           "• <b>20 MB dan katta</b> fayl — Google Drive, Dropbox yoki Yandex Disk <b>havolasi</b>;\n"
                           "• yoki sahifalarni rasm qilib (albom) yuborib, «✅ Tayyor» ni bosing.",
                           reply_markup=kb([[("✅ Tayyor (rasmlar yuborildi)", "adm:fdone")], [CANCEL]]))


async def _collect_image(msg: Message, fid: str):
    lst = COLLECT.setdefault(msg.from_user.id, [])
    lst.append((msg.message_id, fid))
    if len(lst) in (1, 10, 20, 40, 60):
        await msg.answer(f"📥 Qabul qilindi: {len(lst)} ta rasm. Hammasi yuborilgach «✅ Tayyor» ni bosing.",
                         reply_markup=kb([[("✅ Tayyor", "adm:fdone")]]))


@router.message(AddChap.files, F.document)
async def ac_doc(msg: Message, state: FSMContext):
    doc = msg.document
    uid = msg.from_user.id
    kind = chapfiles.kind_of(doc.file_name or "", doc.mime_type or "")
    if kind == "image":
        await _collect_image(msg, doc.file_id)
        return
    if kind not in ("archive", "pdf"):
        await msg.answer("🤔 ZIP, CBZ, PDF yoki rasm yuboring." + (" RAR ochilmaydi — ZIP qiling." if kind == "rar" else ""))
        return
    if (doc.file_size or 0) > MAX_DOWNLOAD:
        await msg.answer(f"📦 «{esc(doc.file_name or 'fayl')}» 20 MB dan katta — Telegram botlarga bunday faylni bermaydi.\n\n"
                         "Uni <b>Google Drive</b> (yoki Dropbox, Yandex Disk)ga yuklang va «havolasi bor har kim ko‘ra "
                         "oladi» qilib, <b>havolasini</b> shu yerga yuboring — o‘zim yuklab olaman.")
        return
    p = PENDING.setdefault(uid, {"docs": [], "ver": 0})
    p["docs"].append({"id": doc.file_id, "name": doc.file_name or "bob", "mime": doc.mime_type or "",
                      "mid": msg.message_id})
    p["ver"] += 1
    SETTLE[uid] = asyncio.create_task(settle(uid, msg.chat.id, state, p["ver"]))


@router.message(AddChap.files, F.photo)
async def ac_photo(msg: Message, state: FSMContext):
    await _collect_image(msg, msg.photo[-1].file_id)


@router.message(AddChap.files, F.text, ~F.text.startswith("/"))
async def ac_link(msg: Message, state: FSMContext):
    url = find_url(msg.text)
    if not url:
        await msg.answer("Bob faylini, rasmlarni yoki fayl havolasini yuboring. Bekor qilish: /bekor")
        return
    uid = msg.from_user.id
    note = await msg.answer("🔗 Havoladan yuklab olinmoqda…")

    async def progress(got, size):
        await safe_edit(note, f"🔗 Yuklanmoqda: {got // 1048576} MB" + (f" / {size // 1048576} MB" if size else ""))

    DOWNLOADING.add(uid)
    try:
        path, name, ftype = await fetch_url(url, user_tmp(uid), progress=progress)
    except LinkError as e:
        await safe_edit(note, f"❌ {e}")
        return
    except Exception as e:
        log.warning("havola: %r", e)
        await safe_edit(note, "❌ Havoladan yuklab bo‘lmadi. Havola to‘g‘riligini va fayl ochiq ekanini tekshiring.")
        return
    finally:
        DOWNLOADING.discard(uid)
    if await state.get_state() != AddChap.files.state:
        return
    await inspect_file(msg.chat.id, uid, state, path, name, "application/pdf" if ftype == "pdf" else "application/zip",
                       note)


async def settle(uid, chat_id, state, ver):
    """Albom bo'lib kelgan fayllar to'liq kelishini biroz kutadi, keyin ularni birga ko'radi."""
    await asyncio.sleep(SETTLE_DELAY)
    p = PENDING.get(uid)
    if not p or p["ver"] != ver:
        return
    PENDING.pop(uid, None)
    if await state.get_state() != AddChap.files.state:
        return
    try:
        await _settle(uid, chat_id, state, p)
    except Exception as e:
        log.exception("fayllarni ko‘rishda xato")
        await bot.send_message(chat_id, f"❌ Xatolik: {esc(repr(e))[:200]}\nFaylni qayta yuboring.")


async def _settle(uid, chat_id, state, p):
    docs = sorted(p["docs"], key=lambda d: d["mid"])
    code = (await state.get_data()).get("code")
    if len(docs) == 1:
        doc = docs[0]
        note = await bot.send_message(chat_id, "📥 Fayl tekshirilyapti…")
        path = os.path.join(user_tmp(uid), f"fayl_{doc['mid']}")
        try:
            await download_to(doc["id"], path)
        except Exception as e:
            log.warning("fayl yuklanmadi: %r", e)
            await safe_edit(note, "❌ Faylni yuklab bo‘lmadi. Qayta yuboring.")
            return
        await inspect_file(chat_id, uid, state, path, doc["name"], doc["mime"], note)
        return
    labels = [x["name"] for x in docs]
    nums, guessed = chapfiles.assign_numbers(labels, CAT.next_num(code))
    srcs = [{"kind": "tg", "id": x["id"], "name": x["name"], "mime": x["mime"], "label": x["name"], "num": n}
            for x, n in zip(docs, nums)]
    await ask_bulk(chat_id, state, code, srcs, guessed)


async def inspect_file(chat_id, uid, state, path, name, mime, note=None):
    """Bitta faylni ko'radi: ichida bir nechta bob bo'lsa — ko'p bob rejimi, aks holda — bitta bob."""
    code = (await state.get_data()).get("code")
    kind = chapfiles.kind_of(name, mime)
    if kind not in ("archive", "pdf"):
        kind = chapfiles.sniff(path)
    groups = []
    if kind == "archive":
        try:
            groups = await asyncio.to_thread(chapfiles.zip_groups, path)
        except chapfiles.ChapterError as e:
            await safe_edit(note, f"❌ {esc(str(e))}")
            return
        if not groups:
            await safe_edit(note, "🤷 Arxiv ichida rasm topilmadi.")
            return
    elif kind != "pdf":
        await safe_edit(note, "🤔 Bu ZIP, CBZ yoki PDF fayl emas.")
        return
    await safe_delete(note)
    if len(groups) >= 2:
        labels = [g["label"] for g in groups]
        nums, guessed = chapfiles.assign_numbers(labels, CAT.next_num(code))
        srcs = [{"kind": "group", "path": path, "group": g, "label": g["label"], "num": n}
                for g, n in zip(groups, nums)]
        await ask_bulk(chat_id, state, code, srcs, guessed)
        return
    await state.update_data(src={"kind": "file", "path": path, "name": name, "mime": mime},
                            guess=chapfiles.guess_num(name))
    await ask_num(chat_id, state)


@router.callback_query(F.data == "adm:fdone", AddChap.files)
async def cb_fdone(c: CallbackQuery, state: FSMContext):
    items = sorted(COLLECT.pop(c.from_user.id, []))
    if not items:
        await c.answer("Hali rasm yubormadingiz", show_alert=True)
        return
    await c.answer()
    await state.update_data(src={"kind": "photos", "ids": [fid for _mid, fid in items]}, guess=None)
    await ask_num(c.from_user.id, state)


# ---------------------------------------------------------------- bitta bob: raqam va tarjima

async def ask_num(chat_id, state):
    d = await state.get_data()
    nxt = d.get("guess") or CAT.next_num(d["code"])
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
    old = CAT.chapter(d["code"], num)
    await state.update_data(num=num)
    await state.set_state(AddChap.tr)
    warn = ""
    if old:
        warn = (f"\n⚠️ {fmt(num)}-bob allaqachon bor — yangisi uning o‘rniga yoziladi"
                + (" (chop etilgan holda qoladi)." if old.get("published") else "."))
    await bot.send_message(chat_id, f"🌐 Sahifalarni o‘zbekchaga tarjima qilaymi?{warn}",
                           reply_markup=kb([[("🌐 Ha, tarjima qil", "adm:tr:1")],
                                            [("✅ Yo‘q, bu tayyor tarjima", "adm:tr:0")], [CANCEL]]))


@router.callback_query(F.data.startswith("adm:tr:"), AddChap.tr)
async def cb_tr(c: CallbackQuery, state: FSMContext):
    await c.answer()
    uid = c.from_user.id
    d = await state.get_data()
    src = d.get("src") or {"kind": "photos", "ids": [fid for _m, fid in sorted(COLLECT.pop(uid, []))]}
    await state.set_state(AddChap.busy)
    start_job(uid, d["code"], [dict(src, num=d["num"])], c.data.endswith(":1"), state, replace=True, single=True)


# ---------------------------------------------------------------- ko'p bob

async def ask_bulk(chat_id, state, code, srcs, guessed):
    srcs = sorted(srcs, key=lambda s: s["num"])
    await state.set_state(AddChap.bulk)
    await state.update_data(srcs=srcs, replace=False, guessed=guessed)
    await bot.send_message(chat_id, bulk_text(code, srcs, False, guessed), reply_markup=bulk_markup(code, srcs, False))


def bulk_text(code, srcs, replace, guessed):
    m = CAT.get(code)
    nums = [s["num"] for s in srcs]
    have = [n for n in nums if CAT.chapter(code, n)]
    lines = [f"📦 <b>{esc(m['title'])}</b>: {len(srcs)} ta bob topildi — {ranges(nums)}"]
    if guessed:
        lines.append(f"ℹ️ Fayl nomlaridan raqam aniqlanmadi — tartib bo‘yicha {fmt(nums[0])} dan boshlab qo‘yildi.")
    if have:
        lines.append(f"⚠️ Bazada bor: {ranges(have)} — " + ("♻️ yangisi bilan almashtiriladi" if replace
                                                          else "⏭ o‘tkazib yuboriladi"))
    lines.append("\n🌐 Tarjima qilaymi?")
    return "\n".join(lines)


def bulk_markup(code, srcs, replace):
    rows = [[("🌐 Ha, tarjima qil", "bt:1")], [("✅ Yo‘q, tayyor tarjima", "bt:0")]]
    if any(CAT.chapter(code, s["num"]) for s in srcs):
        rows.append([("⏭ Borlarini o‘tkazib yuborish" if replace else "♻️ Borlarini almashtirish", "brep")])
    rows.append([CANCEL])
    return kb(rows)


@router.callback_query(F.data == "brep", AddChap.bulk)
async def cb_brep(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    rep = not d.get("replace")
    await state.update_data(replace=rep)
    await c.answer("♻️ Almashtiriladi" if rep else "⏭ O‘tkazib yuboriladi")
    await safe_edit(c.message, bulk_text(d["code"], d["srcs"], rep, d.get("guessed")),
                    bulk_markup(d["code"], d["srcs"], rep))


@router.callback_query(F.data.startswith("bt:"), AddChap.bulk)
async def cb_bt(c: CallbackQuery, state: FSMContext):
    await c.answer()
    d = await state.get_data()
    await state.set_state(AddChap.busy)
    await safe_edit(c.message, bulk_text(d["code"], d["srcs"], d.get("replace"), d.get("guessed")).replace(
        "\n🌐 Tarjima qilaymi?", "\n⏳ Boshlandi" + (" (tarjima bilan)." if c.data.endswith(":1") else ".")))
    start_job(c.from_user.id, d["code"], d["srcs"], c.data.endswith(":1"), state, replace=bool(d.get("replace")))


# ---------------------------------------------------------------- tayyorlash (fon jarayoni)

def start_job(uid, code, srcs, translate, state, replace=True, force=False, single=False):
    job = {"cancel": False}
    JOBS[uid] = job
    job["task"] = asyncio.create_task(run_job(uid, code, srcs, translate, state, job, replace, force, single))
    return job


async def load_src(src, uid):
    """Bitta bob manbasidan sahifalar: [(nom, bayt)], jami_soni."""
    kind = src["kind"]
    if kind == "file":
        return await asyncio.to_thread(chapfiles.extract, src["path"], src.get("name", ""), src.get("mime", ""), MAX_PAGES)
    if kind == "group":
        return await asyncio.to_thread(chapfiles.group_pages, src["path"], src["group"], MAX_PAGES)
    if kind == "tg":
        path = os.path.join(user_tmp(uid), "tg_" + os.urandom(4).hex())
        try:
            await download_to(src["id"], path)
            return await asyncio.to_thread(chapfiles.extract, path, src.get("name", ""), src.get("mime", ""), MAX_PAGES)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    if kind == "photos":
        pages = []
        for i, fid in enumerate(src["ids"][:MAX_PAGES]):
            pages.append((f"{i + 1}.jpg", await download(fid)))
        return pages, len(src["ids"])
    raise chapfiles.ChapterError("Noma’lum manba.")


async def build_pages(uid, pages, translate, gloss, names, status, head, stop_kb, job):
    """Sahifalarni (kerak bo'lsa tarjima qilib) ombor uchun JPEG bo'laklarga aylantiradi."""
    n = len(pages)
    jpegs, skipped, bad, last = [], 0, 0, 0.0
    for i in range(n):
        name, raw = pages[i]
        pages[i] = (name, b"")
        if job["cancel"]:
            return {"stop": "⛔ To‘xtatildi.", "jpegs": [], "skipped": skipped, "bad": bad}
        if i == 0 or time.time() - last > 4:
            last = time.time()
            await safe_edit(status, f"{head}: {'🌐 tarjima' if translate else '📦 tayyorlanyapti'} — "
                                    f"<b>{i}/{n}</b> sahifa…", stop_kb)
        try:
            img = await asyncio.to_thread(typeset.load_image, raw)
        except Exception:
            bad += 1
            continue
        del raw
        if translate:
            async def notify(t, i=i):
                await safe_edit(status, f"{head}: {i}/{n} sahifa tayyor.\n{t}", stop_kb)
            try:
                img, _blocks, _lang = await translate_image(img, uid, notify, glossary=gloss, names=names)
            except translator.GeminiError as e:
                if e.kind in ("quota", "key", "busy"):
                    if e.kind == "key":
                        await tell_owners(f"⚠️ Gemini kaliti xatosi: <code>{esc(str(e))[:300]}</code>")
                    return {"stop": gem_error_text(e), "jpegs": [], "skipped": skipped, "bad": bad}
                skipped += 1
        jpegs.extend(await asyncio.to_thread(storage_pieces, img))
        del img
    return {"stop": "", "jpegs": jpegs, "skipped": skipped, "bad": bad}


async def run_job(uid, code, srcs, translate, state, job, replace=True, force=False, single=False):
    m = CAT.get(code)
    stop_kb = kb([[("⛔ To‘xtatish", "adm:stopjob")]])
    status = await bot.send_message(uid, "⏳ Boshlanmoqda…", reply_markup=stop_kb)
    gloss = CAT.glossary_pairs(code) if translate else []
    found: list = []
    done, existed, dups, failed, cut = [], [], [], [], []
    notr, bad, stop = 0, 0, ""
    total = len(srcs)
    try:
        for k, src in enumerate(srcs):
            if job["cancel"]:
                stop = "⛔ To‘xtatildi."
                break
            if not CAT.get(code):
                stop = "Manga o‘chirib yuborilgan."
                break
            num = float(src["num"])
            head = f"📦 {k + 1}/{total} · {fmt(num)}-bob" if total > 1 else f"📄 {fmt(num)}-bob"
            if not replace and CAT.chapter(code, num):
                existed.append(num)
                continue
            await safe_edit(status, f"{head}: fayl ochilmoqda…", stop_kb)
            try:
                pages, n_all = await load_src(src, uid)
            except chapfiles.ChapterError as e:
                failed.append((num, str(e)))
                continue
            except Exception as e:
                log.warning("bob manbasi ochilmadi: %r", e)
                failed.append((num, "fayl ochilmadi"))
                continue
            if not pages:
                failed.append((num, "rasm topilmadi"))
                continue
            if n_all > len(pages):
                cut.append(num)
            fp = await asyncio.to_thread(chapfiles.fingerprint, pages)
            if not force:
                twin = await CAT.find_duplicate(code, fp, exclude=num)
                if twin:
                    dups.append((src, twin))
                    continue
            res = await build_pages(uid, pages, translate, gloss, found, status, head, stop_kb, job)
            del pages
            if res["stop"]:
                stop = res["stop"]
                break
            notr += res["skipped"]
            bad += res["bad"]
            if not res["jpegs"]:
                failed.append((num, "rasmlar ochilmadi"))
                continue
            await safe_edit(status, f"{head}: ☁️ omborga yuklanmoqda… ({len(res['jpegs'])} rasm)", stop_kb)
            ids = await upload_photos(res["jpegs"], storage_chat(), caption=f"{m['title']} · {fmt(num)}-bob")
            was = CAT.chapter(code, num)
            saved = await CAT.save_chapter(code, num, ids, price=0, published=False, fp=fp, keep=True)
            done.append((num, len(ids), bool(was), saved))
            await jlog(uid, f"📤 {m['title']} — {fmt(num)}-bob " + ("yangilandi" if was else "yuklandi (qoralama)")
                       + f", {len(ids)} rasm")
    except Exception as e:
        log.exception("bob tayyorlashda xato")
        stop = f"❌ Xatolik: {esc(repr(e))[:200]}"
    finally:
        JOBS.pop(uid, None)
        await state.clear()
    await safe_delete(status)
    if dups:
        RETRY[uid] = {"code": code, "srcs": [s for s, _t in dups], "translate": translate, "replace": replace,
                      "single": single}
    await report(uid, code, single, done, existed, dups, failed, notr, bad, cut, stop)
    if translate and found and done:
        await suggest_names(uid, code, found)


async def report(uid, code, single, done, existed, dups, failed, notr, bad, cut, stop):
    m = CAT.get(code)
    if not m:
        await bot.send_message(uid, stop or "Manga topilmadi.")
        return
    title = esc(m["title"])
    extra = []
    if notr:
        extra.append(f"⚠️ {notr} ta sahifa tarjimasiz qoldi (Google filtri).")
    if bad:
        extra.append(f"⚠️ {bad} ta rasm ochilmadi va tashlab ketildi.")
    if cut:
        extra.append(f"ℹ️ {ranges(cut)}-bobda {MAX_PAGES} tadan ko‘p sahifa bor edi — birinchi {MAX_PAGES} tasi olindi.")
    tail = ("\n" + "\n".join(extra)) if extra else ""
    if single:
        if done:
            num, n_ids, was, saved = done[0]
            if was and saved.get("published"):
                await bot.send_message(uid, f"♻️ <b>{title}</b> — {fmt(num)}-bob yangilandi: {n_ids} rasm. Bob chop "
                                            f"etilgan holda qoldi — o‘quvchilar yangisini ko‘radi.{tail}",
                                       reply_markup=chapter_admin(code, num)[1])
            else:
                await bot.send_message(uid, f"✅ <b>{title}</b> — {fmt(num)}-bob tayyor: {n_ids} rasm.{tail}\n\n"
                                            "Avval ko‘rib chiqing, keyin narxini tanlab chop eting:",
                                       reply_markup=publish_markup(code, num))
        elif dups:
            twin = dups[0][1]
            await bot.send_message(uid, f"♻️ Bu bob <b>{title}</b>ning {fmt(twin['num'])}-bobi bilan bir xil ko‘rinadi — "
                                        "ikki marta yuklanmasin deb to‘xtatdim.",
                                   reply_markup=kb([[("⬆️ Baribir yuklash", "adm:force")], [("❌ Kerak emas", "adm:cancel")]]))
        elif failed:
            await bot.send_message(uid, f"❌ {esc(failed[0][1])}")
        else:
            await bot.send_message(uid, f"{stop or '❌ Xatolik.'}\nBob saqlanmadi — keyinroq qayta yuklang.")
        return
    lines = []
    if done:
        lines.append(f"📦 <b>{title}</b>: {len(done)} ta bob tayyor — {ranges([d[0] for d in done])}")
        upd = [d[0] for d in done if d[2]]
        if upd:
            lines.append(f"♻️ Yangilanganlari: {ranges(upd)}")
    else:
        lines.append(f"📦 <b>{title}</b>: birorta bob saqlanmadi.")
    if existed:
        lines.append(f"⏭ Bazada bor, o‘tkazib yuborildi: {ranges(existed)}")
    if dups:
        lines.append("♻️ Avval yuklangan bobga o‘xshaydi: "
                     + ", ".join(f"{fmt(s['num'])} (= {fmt(t['num'])}-bob)" for s, t in dups[:10]))
    for num, why in failed[:10]:
        lines.append(f"❌ {fmt(num)}-bob: {esc(why)}")
    lines += extra
    if stop:
        lines.append(stop + ("\nQolgan boblarni keyinroq yuklang." if done else ""))
    rows = []
    drafts = CAT.drafts(code)
    if drafts:
        rows.append([(f"📢 Qoralamalarni chop etish ({len(drafts)})", f"bp:{code}")])
    if dups:
        rows.append([("⬆️ O‘xshashlarni ham yuklash", "adm:force")])
    rows.append([("📑 Boblar", f"acl:{code}:0"), ("⚙️ Manga", f"am:{code}")])
    await bot.send_message(uid, "\n".join(lines), reply_markup=kb(rows))


async def suggest_names(uid, code, names):
    have = {catalog.norm(o) or o for o, _t in CAT.glossary_pairs(code)}
    cnt = {}
    for n in names:
        o, t = str(n.get("original") or "").strip(), str(n.get("translation") or "").strip()
        k = catalog.norm(o) or o
        if not o or not t or k in have:
            continue
        e = cnt.setdefault(k, [o, t, 0])
        e[2] += 1
    top = sorted(cnt.values(), key=lambda e: -e[2])[:15]
    if not top:
        return
    token = os.urandom(4).hex()
    SUGGEST[token] = {"code": code, "pairs": [(o, t) for o, t, _c in top]}
    while len(SUGGEST) > 100:
        SUGGEST.pop(next(iter(SUGGEST)))
    body = "\n".join(f"{esc(o)} = {esc(t)}" for o, t, _c in top)
    await bot.send_message(uid, "📖 <b>Glossariy uchun taklif</b>\nBu boblarda uchragan ismlar. Keyingi boblarda ham bir xil "
                                f"yozilishi uchun saqlab qo‘yaymi?\n\n<code>{body}</code>\n\n"
                                "✏️ Tuzatmoqchi bo‘lsangiz: ro‘yxatni nusxalang, o‘zgartiring va «📖 Glossariy → ➕ Qo‘shish» "
                                "orqali yuboring.",
                           reply_markup=kb([[("✅ Hammasini saqlash", f"ags:{token}")], [("📖 Glossariy", f"ag:{code}")]]))


@router.callback_query(F.data == "adm:stopjob")
async def cb_stopjob(c: CallbackQuery):
    job = JOBS.get(c.from_user.id)
    if job:
        job["cancel"] = True
        await c.answer("To‘xtatilyapti…")
    else:
        await c.answer("Jarayon tugagan")


@router.callback_query(F.data == "adm:force", adm)
async def cb_force(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    if JOBS.get(uid):
        await c.answer("⏳ Hozir boshqa jarayon ketyapti", show_alert=True)
        return
    r = RETRY.pop(uid, None)
    if not r or not CAT.get(r["code"]):
        await c.answer("Eskirgan — faylni qayta yuboring.", show_alert=True)
        return
    await c.answer("⬆️ Yuklanmoqda")
    await safe_edit(c.message, "⬆️ O‘xshash bo‘lsa ham yuklanyapti…")
    await state.set_state(AddChap.busy)
    start_job(uid, r["code"], r["srcs"], r["translate"], state, replace=r["replace"], force=True, single=r["single"])


# ---------------------------------------------------------------- chop etish

def publish_markup(code, num):
    n = fmt(num)
    return kb([[("👁 Ko‘rib chiqish", f"r:{code}:{n}"), ("🖼 Sahifalar", f"pe:{code}:{n}:0")],
               [("🆓 Bepul chop etish", f"pub:{code}:{n}:0")],
               [(f"🪙 {p}", f"pub:{code}:{n}:{p}") for p in PRICES],
               [("⏰ Keyinroq chiqarish", f"sc:{code}:{n}")],
               [("🗑 O‘chirish", f"cdel:{code}:{n}")]])


@router.callback_query(F.data.startswith("pub:"), adm)
async def cb_pub(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    _, code, num, price = c.data.split(":")
    num, price = float(num), int(price)
    if not CAT.chapter(code, num):
        await c.answer("Bob topilmadi", show_alert=True)
        return
    newly = await CAT.publish(code, num, price)
    await c.answer("✅ Chop etildi")
    m = CAT.get(code)
    await jlog(c, f"✅ {m['title']} — {fmt(num)}-bob chop etildi ({price_text(price)})")
    text = f"✅ <b>{esc(m['title'])}</b> — {fmt(num)}-bob chop etildi ({price_text(price)})."
    if not newly:
        await safe_edit(c.message, text)
        return
    fol = len(m.get("followers", []))
    await safe_edit(c.message, text + (f"\n🔔 {fol} ta kuzatuvchiga xabar yuborilmoqda…" if fol else ""))
    sent, posted = await promo.announce(code, [num])
    await safe_edit(c.message, text + announce_note(fol, sent, posted))


def announce_note(fol, sent, posted) -> str:
    out = f"\n🔔 {sent} ta kuzatuvchiga xabar yuborildi." if fol else ""
    return out + ("\n📣 Reklama kanaliga post chiqdi." if posted else "")


def bulk_items(code, free_k, price):
    """Qoralamalar va ularning narxi: manga boshidan free_k ta bob bepul (−1 — hammasi bepul)."""
    allc = CAT.chapter_list(code, unpublished=True)
    free = {x["id"] for x in allc} if free_k < 0 else {x["id"] for x in allc[:free_k]}
    return [(x, 0 if x["id"] in free else int(price)) for x in allc if not x.get("published")]


def bulk_summary(items):
    free = [x["num"] for x, p in items if not p]
    paid = [(x["num"], p) for x, p in items if p]
    parts = []
    if free:
        parts.append(f"🆓 bepul: {ranges(free)}")
    if paid:
        parts.append(f"🪙 {paid[0][1]} dan: {ranges([n for n, _p in paid])}")
    return "; ".join(parts)


@router.callback_query(F.data.startswith("bp:"), adm)
async def cb_bp(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    code = c.data[3:]
    m = CAT.get(code)
    drafts = CAT.drafts(code) if m else []
    if not drafts:
        await c.answer("Chop etilmagan bob yo‘q", show_alert=True)
        return
    await c.answer()
    rows = [[("🆓 Hammasi bepul", f"bp2:{code}:-1")],
            [(f"{k} ta" if k else "Hech biri", f"bp2:{code}:{k}") for k in FREE_COUNTS], [CANCEL]]
    await bot.send_message(c.from_user.id, f"📢 <b>{esc(m['title'])}</b>: {len(drafts)} ta qoralama bob "
                                           f"({ranges([x['num'] for x in drafts])}).\n\n"
                                           "Manga boshidan nechta bob <b>bepul</b> bo‘lsin?", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("bp2:"), adm)
async def cb_bp2(c: CallbackQuery):
    _, code, k = c.data.split(":")
    if not can(c, "chapter") or not CAT.get(code):
        await c.answer()
        return
    await c.answer()
    if int(k) < 0:
        await ask_when(c, code, -1, 0)
        return
    rows = [[(f"🪙 {p}", f"bp3:{code}:{k}:{p}") for p in PRICES], [CANCEL]]
    await safe_edit(c.message, f"🆓 Dastlabki {k} ta bob bepul.\nQolgan boblar narxi qancha bo‘lsin?", kb(rows))


@router.callback_query(F.data.startswith("bp3:"), adm)
async def cb_bp3(c: CallbackQuery):
    _, code, k, p = c.data.split(":")
    if not can(c, "chapter") or not CAT.get(code):
        await c.answer()
        return
    await c.answer()
    await ask_when(c, code, int(k), int(p))


async def ask_when(c, code, k, p):
    items = bulk_items(code, k, p)
    rows = [[("🚀 Hozir hammasini", f"bpn:{code}:{k}:{p}")],
            [("⏰ Har kuni bittadan", f"bpd:{code}:{k}:{p}")], [CANCEL]]
    await safe_edit(c.message, f"📢 {len(items)} ta bob: {bulk_summary(items)}.\n\nQachon chiqaramiz?", kb(rows))


@router.callback_query(F.data.startswith("bpn:"), adm)
async def cb_bpn(c: CallbackQuery):
    _, code, k, p = c.data.split(":")
    m = CAT.get(code)
    if not can(c, "chapter") or not m:
        await c.answer()
        return
    await c.answer("Chop etilyapti…")
    items = bulk_items(code, int(k), int(p))
    newly = []
    for x, price in items:
        if await CAT.publish(code, x["num"], price):
            newly.append(x["num"])
    await jlog(c, f"✅ {m['title']} — {len(newly)} ta bob chop etildi ({bulk_summary(items)})")
    text = f"✅ <b>{esc(m['title'])}</b>: {len(newly)} ta bob chop etildi.\n{bulk_summary(items)}"
    fol = len(m.get("followers", []))
    await safe_edit(c.message, text)
    if newly:
        sent, posted = await promo.announce(code, newly)
        await safe_edit(c.message, text + announce_note(fol, sent, posted))


@router.callback_query(F.data.startswith("bpd:"), adm)
async def cb_bpd(c: CallbackQuery):
    _, code, k, p = c.data.split(":")
    if not can(c, "chapter") or not CAT.get(code):
        await c.answer()
        return
    await c.answer()
    rows = [[(f"{h:02d}:00", f"bpd2:{code}:{k}:{p}:{h}") for h in catalog.SLOTS], [CANCEL]]
    await safe_edit(c.message, "⏰ Har kuni soat nechada chiqsin? (Toshkent vaqti)", kb(rows))


def drip_times(n, hour, now=None):
    now = now or time.time()
    off = 0 if catalog.slot_ts(0, hour, now) > now + 300 else 1
    return [catalog.slot_ts(off + i, hour, now) for i in range(n)]


@router.callback_query(F.data.startswith("bpd2:"), adm)
async def cb_bpd2(c: CallbackQuery):
    _, code, k, p, h = c.data.split(":")
    m = CAT.get(code)
    if not can(c, "chapter") or not m:
        await c.answer()
        return
    items = bulk_items(code, int(k), int(p))
    if not items:
        await c.answer("Chop etilmagan bob yo‘q", show_alert=True)
        return
    times = drip_times(len(items), int(h))
    for (x, price), ts in zip(items, times):
        await CAT.schedule(code, x["num"], ts, price, by=c.from_user.id)
    await c.answer("⏰ Rejalashtirildi")
    await jlog(c, f"⏰ {m['title']} — {len(items)} ta bob har kuni {int(h):02d}:00 da chiqadigan qilindi")
    await safe_edit(c.message, f"⏰ <b>{esc(m['title'])}</b>: {len(items)} ta bob har kuni {int(h):02d}:00 da bittadan "
                               f"chiqadi.\nBirinchisi: {catalog.when_text(times[0])}, oxirgisi: "
                               f"{catalog.when_text(times[-1])}.\n{bulk_summary(items)}",
                    kb([[("⏰ Rejadagilar", "adm:sched")]]))


# ---------------------------------------------------------------- rejalashtirish (bitta bob)

@router.callback_query(F.data.startswith("sc:"), adm)
async def cb_sc(c: CallbackQuery):
    _, code, num = c.data.split(":")
    x = CAT.chapter(code, float(num))
    if not can(c, "chapter") or not x:
        await c.answer()
        return
    await c.answer()
    cur = int(x.get("price") or 0)
    rows = [[("🆓 Bepul" + (" ✓" if not cur else ""), f"sc1:{code}:{num}:0")],
            [(f"🪙 {p}" + (" ✓" if cur == p else ""), f"sc1:{code}:{num}:{p}") for p in PRICES], [CANCEL]]
    await bot.send_message(c.from_user.id, f"⏰ <b>{fmt(float(num))}-bob</b> avtomatik chiqadi.\nAvval narxini tanlang:",
                           reply_markup=kb(rows))


@router.callback_query(F.data.startswith("sc1:"), adm)
async def cb_sc1(c: CallbackQuery):
    _, code, num, p = c.data.split(":")
    if not can(c, "chapter") or not CAT.chapter(code, float(num)):
        await c.answer()
        return
    await c.answer()
    now = time.time()
    row, rows = [], []
    for off in range(0, 8):
        if not any(catalog.slot_ts(off, h, now) > now + 300 for h in catalog.SLOTS):
            continue
        d = datetime.fromtimestamp(catalog.slot_ts(off, 12, now), catalog.TZ)
        label = "Bugun" if off == 0 else "Ertaga" if off == 1 else f"{catalog.DAYS_SHORT[d.weekday()]} {d:%d.%m}"
        row.append((label, f"sc2:{code}:{num}:{p}:{off}"))
        if len(row) == 3:
            rows.append(row)
            row = []
        if sum(len(r) for r in rows) + len(row) >= 7:
            break
    if row:
        rows.append(row)
    rows.append([CANCEL])
    await safe_edit(c.message, f"⏰ {fmt(float(num))}-bob ({price_text(p)}) qaysi kuni chiqsin?", kb(rows))


@router.callback_query(F.data.startswith("sc2:"), adm)
async def cb_sc2(c: CallbackQuery):
    _, code, num, p, off = c.data.split(":")
    if not can(c, "chapter") or not CAT.chapter(code, float(num)):
        await c.answer()
        return
    now = time.time()
    hours = [h for h in catalog.SLOTS if catalog.slot_ts(int(off), h, now) > now + 300]
    if not hours:
        await c.answer("Bu kun uchun vaqt qolmadi", show_alert=True)
        return
    await c.answer()
    rows = [[(f"{h:02d}:00", f"sc3:{code}:{num}:{p}:{off}:{h}") for h in hours], [CANCEL]]
    await safe_edit(c.message, "⏰ Soat nechada chiqsin? (Toshkent vaqti)", kb(rows))


@router.callback_query(F.data.startswith("sc3:"), adm)
async def cb_sc3(c: CallbackQuery):
    _, code, num, p, off, h = c.data.split(":")
    num = float(num)
    m = CAT.get(code)
    x = CAT.chapter(code, num)
    if not can(c, "chapter") or not x:
        await c.answer()
        return
    if x.get("published"):
        await c.answer("Bu bob allaqachon chop etilgan", show_alert=True)
        return
    ts = catalog.slot_ts(int(off), int(h))
    if ts <= time.time():
        await c.answer("Bu vaqt o‘tib ketdi — boshqasini tanlang", show_alert=True)
        return
    await CAT.schedule(code, num, ts, int(p), by=c.from_user.id)
    await c.answer("⏰ Rejalashtirildi")
    await jlog(c, f"⏰ {m['title']} — {fmt(num)}-bob {catalog.when_text(ts)} ga rejalashtirildi ({price_text(p)})")
    await safe_edit(c.message, f"⏰ <b>{esc(m['title'])}</b> — {fmt(num)}-bob <b>{catalog.when_text(ts)}</b> da "
                               f"avtomatik chop etiladi ({price_text(p)}).\nKuzatuvchilarga o‘shanda xabar boradi.",
                    kb([[("❌ Rejani bekor qilish", f"scx:{code}:{fmt(num)}")], [("⏰ Rejadagilar", "adm:sched")]]))


@router.callback_query(F.data.startswith("scx:"), adm)
async def cb_scx(c: CallbackQuery):
    _, code, num = c.data.split(":")
    num = float(num)
    x = CAT.chapter(code, num)
    if not can(c, "chapter") or not x:
        await c.answer()
        return
    if x.get("sched") and not x.get("published"):
        await CAT.unschedule(code, num)
        await jlog(c, f"❌ {CAT.get(code)['title']} — {fmt(num)}-bob rejasi bekor qilindi")
    await c.answer("Reja bekor qilindi")
    text, markup = chapter_admin(code, num)
    await safe_edit(c.message, text, markup)


@router.callback_query(F.data == "adm:sched", adm)
async def cb_sched(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    await c.answer()
    await send_scheduled(c.from_user.id)


async def send_scheduled(uid):
    items = CAT.scheduled()
    if not items:
        await bot.send_message(uid, "⏰ Rejalashtirilgan bob yo‘q.\n\nBob tayyor bo‘lgach, chop etish o‘rniga "
                                               "«⏰ Keyinroq chiqarish» ni tanlasangiz, u belgilangan vaqtda o‘zi chiqadi.")
        return
    lines = [f"⏰ <b>Rejalashtirilgan boblar</b> — {len(items)} ta\n"]
    for ts, code, num, x in items[:30]:
        price = x.get("sched_price") if x.get("sched_price") is not None else x.get("price")
        lines.append(f"• {catalog.when_text(ts)} — {esc(CAT.get(code)['title'][:28])} {fmt(num)}-bob ({price_text(price)})")
    if len(items) > 30:
        lines.append(f"… va yana {len(items) - 30} ta")
    rows = [[(f"❌ {CAT.get(code)['title'][:22]} · {fmt(num)}", f"scx:{code}:{fmt(num)}")] for _ts, code, num, _x in items[:10]]
    await bot.send_message(uid, "\n".join(lines), reply_markup=kb(rows))


# ---------------------------------------------------------------- mangalarni boshqarish

@router.callback_query(F.data.startswith("adm:list:"), adm)
async def cb_list(c: CallbackQuery):
    await c.answer()
    await send_manga_list(c.from_user.id, int(c.data.split(":")[2]))


async def send_manga_list(uid, page):
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
    await bot.send_message(uid, f"📚 <b>Mangalar</b> — {len(items)} ta" + ("" if items else "\nHozircha manga yo‘q."),
                           reply_markup=kb(rows))


def manga_admin(code):
    m = CAT.get(code)
    allc = CAT.chapter_list(code, unpublished=True)
    drafts = [x for x in allc if not x.get("published")]
    sched = sum(1 for x in drafts if x.get("sched"))
    days = catalog.days_text(m.get("days"))
    text = (f"⚙️ <b>{esc(m['title'])}</b> · kod <code>{code}</code>\n"
            f"📚 Boblar: {len(allc) - len(drafts)} (qoralama: {len(drafts)}" + (f", ⏰ rejada: {sched}" if sched else "")
            + f") · 🔔 {len(m.get('followers', []))} · 👁 {m.get('views', 0)}\n"
            f"🏷 {esc(', '.join(m.get('genres', [])) or '—')}\n"
            f"📌 {esc(catalog.status_text(m) or '—')}" + (f" · 📅 {days}" if days else "") + "\n"
            f"📖 Glossariy: {len(CAT.glossary(code))} ta so‘z"
            + (f" · ✍️ {esc(m['credit'])}" if m.get("credit") else "")
            + ("\n🙈 Yashirilgan (katalogda ko‘rinmaydi)" if m.get("hidden") else ""))
    rows = [[("✏️ Nomi", f"ae:{code}:title"), ("📝 Tavsif", f"ae:{code}:desc")],
            [("🏷 Janrlar", f"ae:{code}:genres"), ("🖼 Muqova", f"ae:{code}:cover")],
            [("📌 Holati", f"ast:{code}"), ("📅 Chiqish kunlari", f"ard:{code}")],
            [("📖 Glossariy", f"ag:{code}"), ("✍️ Tarjimon", f"ae:{code}:credit")],
            [("📤 Bob qo‘shish", f"ach:{code}"), ("📑 Boblar", f"acl:{code}:0")]]
    if drafts:
        rows.append([(f"📢 Qoralamalarni chop etish ({len(drafts)})", f"bp:{code}")])
    rows += [[("👁 Ko‘rsatish" if m.get("hidden") else "🙈 Yashirish", f"ahide:{code}"), ("🗑 O‘chirish", f"adel:{code}")],
             [("🏠 Manga sahifasi", f"m:{code}")]]
    return text, kb(rows)


@router.callback_query(F.data.startswith("am:"), adm)
async def cb_am(c: CallbackQuery, state: FSMContext):
    code = c.data[3:]
    if not CAT.get(code):
        await c.answer("Topilmadi")
        return
    await state.clear()
    await c.answer()
    text, markup = manga_admin(code)
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


FIELD_PROMPT = {"title": "✏️ Yangi nomini yuboring:", "desc": "📝 Yangi tavsifni yuboring:",
                "genres": "🏷 Janrlarni vergul bilan yuboring:", "cover": "🖼 Yangi muqova rasmini yuboring:",
                "credit": "✍️ Tarjimon yoki jamoa yozuvini yuboring (masalan: <i>Tarjima: Nur Team</i>).\n"
                          "U manga kartasida, bob boshida va reklama postida chiqadi. Olib tashlash uchun «-» yuboring."}
FIELD_NAME = {"title": "nomi", "desc": "tavsifi", "genres": "janrlari", "cover": "muqovasi", "credit": "tarjimon yozuvi"}


@router.callback_query(F.data.startswith("ae:"), adm)
async def cb_ae(c: CallbackQuery, state: FSMContext):
    if not can(c, "manga"):
        await deny(c)
        return
    _, code, field = c.data.split(":")
    if field not in FIELD_PROMPT:
        await c.answer()
        return
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
    elif field == "credit":
        val = "" if msg.text.strip() in ("-", "—") else msg.text.strip()[:100]
    else:
        val = msg.text.strip()[:2000 if field == "desc" else 150]
    await state.clear()
    m = CAT.get(code)
    if m:
        old_title = m["title"]
        await CAT.update_manga(code, **{field: val})
        await jlog(msg, f"✏️ {old_title} — {FIELD_NAME.get(field, field)} o‘zgartirildi")
        await msg.answer("✅ Saqlandi.")
        text, markup = manga_admin(code)
        await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ast:"), adm)
async def cb_status(c: CallbackQuery):
    parts = c.data.split(":")
    code = parts[1]
    m = CAT.get(code)
    if not m or not can(c, "manga"):
        await c.answer()
        return
    if len(parts) == 3 and parts[2] in catalog.STATUS:
        await CAT.update_manga(code, status=parts[2])
        await jlog(c, f"📌 {m['title']} — holati: {catalog.STATUS[parts[2]]}")
        await c.answer("Saqlandi")
        text, markup = manga_admin(code)
        await safe_edit(c.message, text, markup)
        return
    await c.answer()
    cur = catalog.status_key(m)
    rows = [[(label + (" ✓" if k == cur else ""), f"ast:{code}:{k}")] for k, label in catalog.STATUS.items()]
    rows.append([("⬅️ Orqaga", f"am:{code}")])
    await bot.send_message(c.from_user.id, f"📌 <b>{esc(m['title'])}</b> — holatini tanlang:", reply_markup=kb(rows))


def days_markup(code, days):
    sel = {int(d) for d in days or []}
    row1 = [((("✅ " if i in sel else "") + catalog.DAYS_SHORT[i]), f"ard:{code}:{i}") for i in range(4)]
    row2 = [((("✅ " if i in sel else "") + catalog.DAYS_SHORT[i]), f"ard:{code}:{i}") for i in range(4, 7)]
    return kb([row1, row2, [("📆 Har kuni", f"ard:{code}:all"), ("🚫 Belgilanmagan", f"ard:{code}:none")],
               [("✅ Tayyor", f"am:{code}")]])


@router.callback_query(F.data.startswith("ard:"), adm)
async def cb_days(c: CallbackQuery):
    parts = c.data.split(":")
    code = parts[1]
    m = CAT.get(code)
    if not m or not can(c, "manga"):
        await c.answer()
        return
    days = sorted({int(d) for d in m.get("days") or []})
    if len(parts) == 3:
        v = parts[2]
        if v == "all":
            days = list(range(7))
        elif v == "none":
            days = []
        elif v.isdigit() and 0 <= int(v) <= 6:
            days = sorted(set(days) ^ {int(v)})
        await CAT.update_manga(code, days=days)
        await c.answer(catalog.days_text(days) or "Belgilanmagan")
        await safe_edit(c.message, days_text_msg(m, days), days_markup(code, days))
        if v in ("all", "none") or len(days) <= 1:
            await jlog(c, f"📅 {m['title']} — chiqish kunlari: {catalog.days_text(days) or 'belgilanmagan'}")
        return
    await c.answer()
    await bot.send_message(c.from_user.id, days_text_msg(m, days), reply_markup=days_markup(code, days))


def days_text_msg(m, days):
    return (f"📅 <b>{esc(m['title'])}</b> — yangi bob qaysi kunlari chiqadi?\n"
            f"Hozir: <b>{catalog.days_text(days) or 'belgilanmagan'}</b>\n\nKunlarni bosib belgilang, keyin «✅ Tayyor».")


@router.callback_query(F.data.startswith("ahide:"), adm)
async def cb_hide(c: CallbackQuery):
    code = c.data[6:]
    m = CAT.get(code)
    if not m or not can(c, "manga"):
        await c.answer()
        return
    await CAT.update_manga(code, hidden=not m.get("hidden"))
    await jlog(c, f"{'🙈' if m.get('hidden') else '👁'} {m['title']} — "
                  + ("yashirildi" if m.get("hidden") else "katalogda ko‘rinadigan qilindi"))
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
    m = CAT.get(c.data[6:])
    if m:
        await CAT.delete_manga(c.data[6:])
        await jlog(c, f"🗑 Manga o‘chirildi: {m['title']} (kod {m['code']})")
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
        if not x.get("published"):
            mark = "⏰" if x.get("sched") else "📝"
        else:
            mark = "🪙" if int(x.get("price") or 0) else ""
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
    await bot.send_message(c.from_user.id, f"📑 Boblar ({len(chs)}): 📝 — qoralama, ⏰ — rejada, 🪙 — pullik",
                           reply_markup=kb(rows))


def chapter_admin(code, num):
    x = CAT.chapter(code, num)
    m = CAT.get(code)
    price = int(x.get("price") or 0)
    name = f" «{esc(x['title'])}»" if x.get("title") else ""
    state = "✅ chop etilgan" if x.get("published") else "📝 qoralama"
    text = (f"📄 <b>{esc(m['title'])}</b> — {fmt(num)}-bob{name}\n{x.get('pages', 0)} rasm · {state} · "
            f"narx: {price_text(price)}")
    if x.get("sched") and not x.get("published"):
        sp = x.get("sched_price") if x.get("sched_price") is not None else price
        text += f"\n⏰ {catalog.when_text(x['sched'])} da avtomatik chiqadi ({price_text(sp)})"
    n = fmt(num)
    rows = [[("🆓" + (" ✓" if not price else ""), f"cp:{code}:{n}:0")]
            + [(f"🪙{p}" + (" ✓" if price == p else ""), f"cp:{code}:{n}:{p}") for p in PRICES]]
    if x.get("published"):
        rows.append([("🙈 Chop etilmagan qilish", f"cunpub:{code}:{n}"), ("👁 Ko‘rish", f"r:{code}:{n}")])
    else:
        rows.append([("✅ Chop etish", f"pub:{code}:{n}:{price}"), ("👁 Ko‘rish", f"r:{code}:{n}")])
        rows.append([("❌ Rejani bekor qilish", f"scx:{code}:{n}") if x.get("sched")
                     else ("⏰ Keyinroq chiqarish", f"sc:{code}:{n}")])
    rows.append([("🖼 Sahifalar", f"pe:{code}:{n}:0"), ("🏷 Bob nomi", f"ct:{code}:{n}")])
    rows.append([("🗑 O‘chirish", f"cdel:{code}:{n}"), ("⬅️ Boblar", f"acl:{code}:0")])
    return text, kb(rows)


@router.callback_query(F.data.startswith("ac:"), adm)
async def cb_ac(c: CallbackQuery, state: FSMContext):
    _, code, num = c.data.split(":")
    if not CAT.chapter(code, float(num)):
        await c.answer("Topilmadi")
        return
    await state.clear()
    await c.answer()
    text, markup = chapter_admin(code, float(num))
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("cp:") | F.data.startswith("cunpub:"), adm)
async def cb_cp(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    parts = c.data.split(":")
    code, num = parts[1], float(parts[2])
    x = CAT.chapter(code, num)
    if not x:
        await c.answer()
        return
    title = CAT.get(code)["title"]
    if parts[0] == "cp":
        price = int(parts[3])
        fields = {"price": price}
        if x.get("sched"):
            fields["sched_price"] = price
        await CAT.set_chapter(code, num, **fields)
        await jlog(c, f"🪙 {title} — {fmt(num)}-bob narxi: {price_text(price)}")
        await c.answer("Narx saqlandi")
    else:
        await CAT.set_chapter(code, num, published=False)
        await jlog(c, f"🙈 {title} — {fmt(num)}-bob chop etilmagan qilindi")
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
        await jlog(c, f"🗑 {CAT.get(code)['title']} — {num}-bob o‘chirildi")
    await c.answer("O‘chirildi")
    await safe_edit(c.message, f"🗑 {num}-bob o‘chirildi.")
