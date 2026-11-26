"""Foydalanuvchilar qismi: manga kodi, katalog, qidiruv, manga kartasi, boblar, botda o'qish (albom/PDF),
sevimlilar, kuzatish, jeton bilan bob ochish."""
import asyncio
import os
import re
import shutil
import tempfile
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InputMediaPhoto, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import catalog
import chapters as chapfiles
from core import CAT, SUB_OK, bot, esc, kb, log, missing_channels, retry, safe_delete, safe_edit, sub_markup
from core import download, STATE, rp

router = Router(name="reader")
fmt = catalog.fmt_num
READING: dict[int, float] = {}

B_CATALOG, B_POPULAR, B_FAVS, B_CONT, B_ACC, B_SEARCH = (
    "📚 Katalog", "🔥 Mashhur", "❤️ Sevimlilar", "📖 Davom ettirish", "💰 Hisobim", "🔎 Qidirish")
MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text=B_CATALOG), KeyboardButton(text=B_POPULAR)],
    [KeyboardButton(text=B_FAVS), KeyboardButton(text=B_CONT)],
    [KeyboardButton(text=B_ACC), KeyboardButton(text=B_SEARCH)],
], resize_keyboard=True)
PAGE = 10


def welcome(name):
    return (f"👋 Assalomu alaykum, {esc(name)}!\n\n"
            "📚 Bu yerda manga va manhvalarni <b>o‘zbek tilida</b> o‘qiysiz.\n\n"
            "🔢 Manga <b>kodini</b> yuboring (masalan <code>101</code>) — darhol ochiladi.\n"
            "📚 «Katalog» — barcha mangalar, 🔎 nomini yozsangiz — qidirib topaman.\n"
            "🔔 Mangani kuzatsangiz, yangi bob chiqqanda xabar beraman.")


async def who(event):
    """Foydalanuvchini bazadan oladi; bloklangan bo'lsa None."""
    fu = event.from_user
    u = await CAT.user(fu.id, fu.first_name or "", fu.username or "")
    return None if u.get("blocked") else u


async def check_sub(msg_or_cb, after=""):
    uid = msg_or_cb.from_user.id
    miss = await missing_channels(uid)
    if not miss:
        return True
    chat_id = msg_or_cb.message.chat.id if isinstance(msg_or_cb, CallbackQuery) else msg_or_cb.chat.id
    await bot.send_message(chat_id, "📢 Botdan foydalanish uchun quyidagi kanalga obuna bo‘ling, so‘ng "
                                    "«✅ Obuna bo‘ldim» tugmasini bosing.", reply_markup=sub_markup(miss, after))
    if isinstance(msg_or_cb, CallbackQuery):
        await msg_or_cb.answer()
    return False


# ---------------------------------------------------------------- manga kartasi

def card_text(m, u):
    chs = CAT.chapter_list(m["code"])
    lines = [f"📖 <b>{esc(m['title'])}</b>", f"🔢 Kod: <code>{m['code']}</code>"]
    if m.get("genres"):
        lines.append("🏷 " + esc(", ".join(m["genres"])))
    info = f"📚 Boblar: {len(chs)}"
    if any(int(c.get("price") or 0) > 0 for c in chs):
        info += " · 💎 pullik boblar bor"
    lines.append(info + f" · 👁 {m.get('views', 0)}")
    st, days = catalog.status_text(m), catalog.days_text(m.get("days"))
    if st or days:
        lines.append(" · ".join(x for x in (st, f"📅 {days}" if days else "") if x))
    head = "\n".join(lines)
    desc = (m.get("desc") or "").strip()
    room = 1000 - len(head)
    if desc and room > 60:
        if len(desc) > room - 10:
            desc = desc[:room - 12].rsplit(" ", 1)[0] + "…"
        head += "\n\n" + esc(desc)
    return head


def continue_target(m, u):
    chs = CAT.chapter_list(m["code"])
    if not chs:
        return None, ""
    last = (u.get("progress") or {}).get(m["code"])
    if last is None:
        return chs[0], "📖 O‘qishni boshlash"
    nxt = CAT.neighbor(m["code"], last)
    if nxt:
        return nxt, f"▶️ Davom etish: {fmt(nxt['num'])}-bob"
    cur = CAT.chapter(m["code"], last)
    return (cur or chs[-1]), f"🔁 {fmt((cur or chs[-1])['num'])}-bob (oxirgi)"


def card_markup(m, u, uid):
    code = m["code"]
    rows = []
    target, label = continue_target(m, u)
    if target:
        rows.append([(label, f"r:{code}:{fmt(target['num'])}")])
        rows.append([(f"📑 Boblar ro‘yxati ({len(CAT.chapter_list(code))})", f"cl:{code}:0")])
    else:
        rows.append([("⏳ Boblar tez orada", f"noop")])
    fav = code in (u.get("favs") or [])
    fol = code in (u.get("follows") or [])
    rows.append([("💔 Sevimlidan olish" if fav else "❤️ Sevimli", f"fav:{code}"),
                 ("🔕 Kuzatishni to‘xtatish" if fol else "🔔 Kuzatish", f"fol:{code}")])
    rows.append([("📤 Ulashish", f"url:https://t.me/share/url?url=https://t.me/{STATE['username']}?start={code}")])
    if CAT.is_admin(uid):
        rows.append([("⚙️ Boshqarish (admin)", f"am:{code}")])
    return kb(rows)


async def show_card(chat_id, m, uid, u=None, edit=None):
    u = u or await CAT.user(uid)
    text, markup = card_text(m, u), card_markup(m, u, uid)
    if edit is not None:
        try:
            if getattr(edit, "photo", None):
                await edit.edit_caption(caption=text, reply_markup=markup)
            else:
                await edit.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    if m.get("cover"):
        try:
            await bot.send_photo(chat_id, m["cover"], caption=text, reply_markup=markup)
            return
        except Exception as e:
            log.warning("muqova yuborilmadi: %s", e)
    await bot.send_message(chat_id, text, reply_markup=markup)


# ---------------------------------------------------------------- ro'yxatlar

def manga_rows(items, page, nav_prefix, back=None):
    total = len(items)
    part = items[page * PAGE:(page + 1) * PAGE]
    rows = [[(f"{m['title'][:40]} · {len(CAT.chapter_list(m['code']))} bob", f"m:{m['code']}")] for m in part]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"{nav_prefix}:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(("➡️", f"{nav_prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    if back:
        rows.append([back])
    return kb(rows)


async def send_or_edit(cb_or_msg, text, markup):
    if isinstance(cb_or_msg, CallbackQuery):
        msg = cb_or_msg.message
        await cb_or_msg.answer()
        if msg is not None and not getattr(msg, "photo", None):
            try:
                await msg.edit_text(text, reply_markup=markup)
                return
            except Exception:
                pass
        await bot.send_message(cb_or_msg.from_user.id, text, reply_markup=markup)
    else:
        await cb_or_msg.answer(text, reply_markup=markup)


async def show_catalog(event, page=0, genre=None):
    items = CAT.manga_list(genre=genre)
    if not items:
        await send_or_edit(event, "📭 Hozircha katalog bo‘sh — tez orada mangalar qo‘shiladi.", None)
        return
    title = f"📚 <b>Katalog</b>{' · ' + esc(genre) if genre else ''} — {len(items)} ta manga\n(oxirgi yangilanganlar tepada)"
    gi = CAT.genres()
    back = ("🏷 Janrlar", "gen") if gi and not genre else (("📚 Hammasi", "cat:0") if genre else None)
    prefix = f"catg:{CAT.genres().index(genre)}" if genre in gi else "cat"
    await send_or_edit(event, title, manga_rows(items, page, prefix, back))


async def show_chapters(cb_or_msg, code, page, uid):
    m = CAT.get(code)
    if not m:
        return
    unpub = CAT.is_admin(uid)
    chs = CAT.chapter_list(code, unpublished=unpub)
    u = await CAT.user(uid)
    per = 24
    part = chs[page * per:(page + 1) * per]
    btns = []
    for c in part:
        mark = ""
        if not c.get("published"):
            mark = "⏰" if c.get("sched") else "📝"
        elif not CAT.can_read(uid, u, c):
            mark = "🔒"
        elif (u.get("progress") or {}).get(code) == float(c["num"]):
            mark = "👁"
        btns.append((f"{mark}{fmt(c['num'])}", f"r:{code}:{fmt(c['num'])}"))
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"cl:{code}:{page - 1}"))
    if (page + 1) * per < len(chs):
        nav.append(("➡️", f"cl:{code}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🏠 Manga sahifasi", f"m:{code}")])
    text = (f"📑 <b>{esc(m['title'])}</b> — boblar ({len(chs)} ta)\n"
            "🔒 — pullik, 👁 — oxirgi o‘qigan bobingiz" + (", 📝 — chop etilmagan" if unpub else ""))
    await send_or_edit(cb_or_msg, text, kb(rows))


# ---------------------------------------------------------------- o'qish

async def read_chapter(chat_id, uid, code, num, as_pdf=False):
    m = CAT.get(code)
    chap = CAT.chapter(code, num) if m else None
    if not chap or (not chap.get("published") and not CAT.is_admin(uid)):
        await bot.send_message(chat_id, "🤷 Bunday bob topilmadi.")
        return
    u = await CAT.user(uid)
    price = int(chap.get("price") or 0)
    if not CAT.can_read(uid, u, chap):
        bal = int(u.get("jetons") or 0)
        rows = []
        if bal >= price:
            rows.append([(f"🔓 {price} jetonga ochish", f"buy:{code}:{fmt(num)}")])
        rows.append([("🪙 Jeton sotib olish", "shop"), ("💎 VIP olish", "vipshop")])
        rows.append([("📑 Boblar", f"cl:{code}:0")])
        await bot.send_message(chat_id, f"🔒 <b>{esc(m['title'])}</b> — {fmt(num)}-bob pullik: <b>{price} 🪙</b>\n"
                                        f"Balansingiz: {bal} 🪙" + ("" if bal >= price else
                                                                     "\n\nJeton yetmaydi — to‘ldiring yoki VIP oling "
                                                                     "(VIP bilan barcha pullik boblar ochiq)."),
                               reply_markup=kb(rows))
        return
    if time.time() - READING.get(uid, 0) < 25:
        await bot.send_message(chat_id, "⏳ Oldingi bob yuborilyapti, biroz kuting…")
        return
    READING[uid] = time.time()
    try:
        data = await CAT.get_pages(code, num)
        pages = data.get("pages") or []
        if not pages:
            await bot.send_message(chat_id, "🤷 Bu bobda sahifa yo‘q.")
            return
        protect = price > 0
        if as_pdf:
            await send_pdf(chat_id, m, chap, data, protect)
        else:
            title = f" — {esc(chap['title'])}" if chap.get("title") else ""
            await bot.send_message(chat_id, f"📖 <b>{esc(m['title'])}</b> · {fmt(num)}-bob{title}\n"
                                            f"📄 {len(pages)} sahifa")
            for i in range(0, len(pages), 10):
                part = pages[i:i + 10]
                if len(part) == 1:
                    await retry(lambda: bot.send_photo(chat_id, part[0], protect_content=protect))
                else:
                    await retry(lambda: bot.send_media_group(chat_id, [InputMediaPhoto(media=p) for p in part],
                                                             protect_content=protect))
                await asyncio.sleep(0.7)
        prev, nxt = CAT.neighbor(code, num, -1), CAT.neighbor(code, num, 1)
        row = []
        if prev:
            row.append((f"⬅️ {fmt(prev['num'])}-bob", f"r:{code}:{fmt(prev['num'])}"))
        if nxt:
            row.append((f"{fmt(nxt['num'])}-bob ➡️", f"r:{code}:{fmt(nxt['num'])}"))
        rows = [row] if row else []
        rows.append([("📄 PDF qilib olish", f"pdf:{code}:{fmt(num)}")] if not as_pdf else [])
        rows.append([("📑 Boblar", f"cl:{code}:0"), ("🏠 Manga", f"m:{code}")])
        end = f"✅ {fmt(num)}-bob tugadi." + ("" if nxt else "\n🔔 Keyingi bob chiqqanda xabar olish uchun mangani kuzating.")
        await bot.send_message(chat_id, end, reply_markup=kb(rows))
        await CAT.set_progress(uid, code, num)
        await CAT.add_view(code)
    finally:
        READING.pop(uid, None)


async def send_pdf(chat_id, m, chap, data, protect):
    name = f"{chapfiles.safe_stem(m['title'])[:-3]} {fmt(chap['num'])}-bob.pdf"
    if data.get("pdf"):
        try:
            await bot.send_document(chat_id, data["pdf"], protect_content=protect)
            return
        except Exception as e:
            log.warning("saqlangan PDF ketmadi: %s", e)
    note = await bot.send_message(chat_id, "📄 PDF tayyorlanmoqda…")
    tmp = tempfile.mkdtemp(prefix="pdf_")
    try:
        paths = []
        for i, fid in enumerate(data["pages"]):
            p = os.path.join(tmp, f"{i:03d}.jpg")
            with open(p, "wb") as f:
                f.write(await download(fid))
            paths.append(p)
        out = os.path.join(tmp, "out.pdf")
        await asyncio.to_thread(chapfiles.write_pdf, paths, out)
        sent = await bot.send_document(chat_id, FSInputFile(out, filename=name), protect_content=protect)
        if sent.document:
            await CAT.set_pdf(m["code"], chap["num"], sent.document.file_id)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        await safe_delete(note)


# ---------------------------------------------------------------- buyruqlar va tugmalar

@router.message(CommandStart())
async def cmd_start(msg: Message, command: CommandObject = None):
    u = await who(msg)
    if u is None:
        return
    arg = ((command.args if command else "") or "").strip()
    if not await check_sub(msg, arg):
        return
    if not arg:
        await msg.answer(welcome(msg.from_user.first_name or "do‘stim"), reply_markup=MENU)
        return
    await open_arg(msg.chat.id, msg.from_user.id, arg)


async def open_arg(chat_id, uid, arg):
    mt = re.fullmatch(r"c?(\d+)[_-](\d+(?:[.-]\d+)?)", arg)
    if mt:
        await read_chapter(chat_id, uid, mt.group(1), float(mt.group(2).replace("-", ".")))
        return
    m = CAT.get(arg)
    if m and (not m.get("hidden") or CAT.is_admin(uid)):
        await show_card(chat_id, m, uid)
    else:
        await bot.send_message(chat_id, "🤷 Bunday kodli manga topilmadi.", reply_markup=MENU)


@router.callback_query(F.data.startswith("sub"))
async def cb_sub(c: CallbackQuery):
    SUB_OK.pop(c.from_user.id, None)
    miss = await missing_channels(c.from_user.id)
    if miss:
        await c.answer("Hali obuna bo‘lmagansiz 🙂", show_alert=True)
        return
    await c.answer("Rahmat! ✅")
    await safe_delete(c.message)
    arg = c.data.split(":", 1)[1] if ":" in c.data else ""
    if arg:
        await open_arg(c.from_user.id, c.from_user.id, arg)
    else:
        await bot.send_message(c.from_user.id, welcome(c.from_user.first_name or "do‘stim"), reply_markup=MENU)


HELP = ("💡 <b>Qanday foydalaniladi</b>\n\n"
        "🔢 Manga kodini yuboring (masalan <code>101</code>) — manga sahifasi ochiladi.\n"
        "📑 «Boblar ro‘yxati»dan bobni tanlang — sahifalar albom bo‘lib keladi, xohlasangiz PDF qilib olasiz.\n"
        "▶️ «Davom ettirish» — qolgan joyingizdan davom etasiz.\n"
        "🔔 «Kuzatish» — yangi bob chiqqanda xabar keladi.\n"
        "🔒 Pullik boblar jeton bilan ochiladi, 💎 VIP bilan esa hammasi ochiq.")


@router.message(Command("yordam", "help"))
async def cmd_help(msg: Message):
    await msg.answer(HELP, reply_markup=MENU)


@router.message(F.text == B_CATALOG)
async def on_catalog(msg: Message):
    if await who(msg) is not None and await check_sub(msg):
        await show_catalog(msg)


@router.message(F.text == B_POPULAR)
async def on_popular(msg: Message):
    if await who(msg) is None or not await check_sub(msg):
        return
    items = CAT.popular(10)
    if not items:
        await msg.answer("📭 Hozircha katalog bo‘sh.")
        return
    rows = [[(f"{i}. {m['title'][:38]} · 👁 {m.get('views', 0)}", f"m:{m['code']}")] for i, m in enumerate(items, 1)]
    await msg.answer("🔥 <b>Eng ko‘p o‘qilganlar</b>", reply_markup=kb(rows))


@router.message(F.text == B_FAVS)
async def on_favs(msg: Message):
    u = await who(msg)
    if u is None:
        return
    items = [CAT.get(c) for c in u.get("favs", []) if CAT.get(c)]
    if not items:
        await msg.answer("❤️ Sevimlilar ro‘yxatingiz bo‘sh. Manga sahifasida «❤️ Sevimli» tugmasini bosing.")
        return
    await msg.answer("❤️ <b>Sevimlilaringiz</b>", reply_markup=manga_rows(items, 0, "favp"))


@router.message(F.text == B_CONT)
async def on_continue(msg: Message):
    u = await who(msg)
    if u is None:
        return
    prog = u.get("progress") or {}
    items = [(code, num) for code, num in prog.items() if CAT.get(code)]
    if not items:
        await msg.answer("📖 Hali hech narsa o‘qimadingiz. Katalogdan boshlang yoki manga kodini yuboring.")
        return
    last = u.get("last")
    items.sort(key=lambda x: x[0] != last)
    rows = []
    for code, num in items[:10]:
        m = CAT.get(code)
        nxt = CAT.neighbor(code, num)
        target = nxt or CAT.chapter(code, num)
        if target:
            lbl = f"{m['title'][:30]} → {fmt(target['num'])}-bob" + ("" if nxt else " ✓")
            rows.append([(lbl, f"r:{code}:{fmt(target['num'])}")])
    await msg.answer("📖 <b>Davom ettirish</b>", reply_markup=kb(rows))


@router.message(F.text == B_ACC)
async def on_account(msg: Message):
    u = await who(msg)
    if u is None:
        return
    vip = float(u.get("vip_until") or 0)
    vip_t = (f"✅ {datetime.fromtimestamp(vip, catalog.TZ).strftime('%d.%m.%Y %H:%M')} gacha"
             if vip > time.time() else "yo‘q")
    await msg.answer(f"💰 <b>Hisobim</b>\n\n🪙 Jetonlar: <b>{int(u.get('jetons') or 0)}</b>\n💎 VIP: {vip_t}\n"
                     f"🔓 Sotib olingan boblar: {len(u.get('bought') or [])}\n🆔 ID: <code>{msg.from_user.id}</code>",
                     reply_markup=kb([[("🪙 Jeton sotib olish", "shop"), ("💎 VIP olish", "vipshop")]]))


@router.message(F.text == B_SEARCH)
async def on_search_btn(msg: Message):
    await msg.answer("🔎 Manga nomini yozing (yoki kodini yuboring):")


@router.callback_query(F.data.in_({"shop", "vipshop"}))
async def cb_shop(c: CallbackQuery):
    await c.answer("💳 To‘lov tizimi tez orada qo‘shiladi. Hozircha admin bilan bog‘laning.", show_alert=True)


@router.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.startswith("m:"))
async def cb_manga(c: CallbackQuery):
    u = await who(c)
    m = CAT.get(c.data[2:])
    if u is None or not m:
        await c.answer("Topilmadi")
        return
    if not await check_sub(c, m["code"]):
        return
    await c.answer()
    await show_card(c.from_user.id, m, c.from_user.id, u)


@router.callback_query(F.data.startswith("cat:") | F.data.startswith("catg:") | (F.data == "gen")
                       | F.data.startswith("favp:"))
async def cb_lists(c: CallbackQuery):
    d = c.data
    if d == "gen":
        gs = CAT.genres()
        rows = [[(g, f"catg:{i}:0")] for i, g in enumerate(gs)]
        rows.append([("📚 Hammasi", "cat:0")])
        await send_or_edit(c, "🏷 <b>Janrni tanlang</b>", kb(rows))
    elif d.startswith("catg:"):
        _, gi, page = d.split(":")
        gs = CAT.genres()
        g = gs[int(gi)] if int(gi) < len(gs) else None
        await show_catalog(c, int(page), g)
    elif d.startswith("favp:"):
        u = await CAT.user(c.from_user.id)
        items = [CAT.get(x) for x in u.get("favs", []) if CAT.get(x)]
        await send_or_edit(c, "❤️ <b>Sevimlilaringiz</b>", manga_rows(items, int(d.split(":")[1]), "favp"))
    else:
        await show_catalog(c, int(d.split(":")[1]))


@router.callback_query(F.data.startswith("cl:"))
async def cb_chapters(c: CallbackQuery):
    if await who(c) is None:
        return
    _, code, page = c.data.split(":")
    await show_chapters(c, code, int(page), c.from_user.id)


@router.callback_query(F.data.startswith("r:") | F.data.startswith("pdf:"))
async def cb_read(c: CallbackQuery):
    if await who(c) is None:
        return
    kind, code, num = c.data.split(":")
    if not await check_sub(c, f"c{code}_{num.replace('.', '-')}"):
        return
    await c.answer()
    await read_chapter(c.from_user.id, c.from_user.id, code, float(num), as_pdf=(kind == "pdf"))


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery):
    _, code, num = c.data.split(":")
    ok, why = await CAT.buy(c.from_user.id, code, float(num))
    if not ok:
        await c.answer(f"❌ {why}", show_alert=True)
        return
    u = await CAT.user(c.from_user.id)
    await c.answer(f"✅ Bob ochildi! Qoldiq: {u.get('jetons', 0)} 🪙")
    await safe_delete(c.message)
    await read_chapter(c.from_user.id, c.from_user.id, code, float(num))


@router.callback_query(F.data.startswith("fav:") | F.data.startswith("fol:"))
async def cb_toggle(c: CallbackQuery):
    kind, code = c.data.split(":")
    m = CAT.get(code)
    if not m:
        await c.answer()
        return
    if kind == "fav":
        on = await CAT.toggle_fav(c.from_user.id, code)
        await c.answer("❤️ Sevimlilarga qo‘shildi" if on else "Sevimlilardan olindi")
    else:
        on = await CAT.toggle_follow(c.from_user.id, code)
        await c.answer("🔔 Yangi bob chiqqanda xabar beraman" if on else "🔕 Kuzatish to‘xtatildi")
    try:
        u = await CAT.user(c.from_user.id)
        await c.message.edit_reply_markup(reply_markup=card_markup(m, u, c.from_user.id))
    except Exception:
        pass


@router.message(F.text.regexp(r"^\s*\d{1,7}\s*$"))
async def on_code(msg: Message):
    if await who(msg) is None:
        return
    code = msg.text.strip()
    if not await check_sub(msg, code):
        return
    await open_arg(msg.chat.id, msg.from_user.id, code)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(msg: Message):
    if await who(msg) is None or not await check_sub(msg):
        return
    res = CAT.search(msg.text, 10, hidden=CAT.is_admin(msg.from_user.id))
    if not res:
        await msg.answer("🤷 Hech narsa topilmadi. Boshqacha yozib ko‘ring yoki «📚 Katalog»ni oching.",
                         reply_markup=MENU)
    elif len(res) == 1:
        await show_card(msg.chat.id, res[0], msg.from_user.id)
    else:
        await msg.answer(f"🔎 Topildi: {len(res)} ta", reply_markup=manga_rows(res, 0, "noop"))


@router.message(F.photo | F.document | F.video | F.sticker | F.voice | F.animation)
async def on_media(msg: Message):
    await msg.answer("📚 Bu bot manga o‘qish uchun. Manga kodini yuboring yoki «📚 Katalog»ni oching.",
                     reply_markup=MENU)


async def notify_followers(code, nums):
    """Yangi bob(lar) chop etilganda kuzatuvchilarga bitta xabar yuboradi. nums — raqam yoki raqamlar ro'yxati.
    Qaytaradi: yuborilganlar soni."""
    m = CAT.get(code)
    nums = sorted(float(n) for n in (nums if isinstance(nums, (list, tuple, set)) else [nums]))
    if not m or not nums:
        return 0
    sent = 0
    first = nums[0]
    if len(nums) == 1:
        text = f"🔔 <b>{esc(m['title'])}</b>: yangi <b>{fmt(first)}-bob</b> chiqdi!"
    else:
        text = f"🔔 <b>{esc(m['title'])}</b>: <b>{len(nums)} ta yangi bob</b> chiqdi ({catalog.ranges(nums)})!"
    markup = kb([[("📖 O‘qish" if len(nums) == 1 else f"📖 {fmt(first)}-bobdan o‘qish", f"r:{code}:{fmt(first)}")],
                 [("🏠 Manga sahifasi", f"m:{code}")]])
    for uid in list(m.get("followers", [])):
        try:
            await retry(lambda: bot.send_message(uid, text, reply_markup=markup))
            sent += 1
        except TelegramForbiddenError:
            pass
        except Exception as e:
            log.debug("xabar yuborilmadi %s: %s", uid, e)
        await asyncio.sleep(0.05)
    return sent
