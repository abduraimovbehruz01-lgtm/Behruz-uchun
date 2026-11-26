"""Bob tahriri — sahifalarni ko'rish, almashtirish, o'chirish, surish va qo'shish, bob nomi — hamda
manga glossariysi (ismlar va atamalar lug'ati: tarjimada har doim bir xil yoziladi)."""
import asyncio
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

import admin
import catalog
import chapters as chapfiles
import translator
import typeset
from core import (CAT, MAX_DOWNLOAD, bot, download, esc, gem_error_text, jlog, kb, log, safe_delete, safe_edit,
                  storage_chat, storage_pieces, translate_image, upload_photos)

router = Router(name="editor")
fmt = catalog.fmt_num


def adm(e) -> bool:
    return CAT.is_admin(e.from_user.id)


def can(e, *rights) -> bool:
    return any(CAT.has(e.from_user.id, r) for r in rights)


async def deny(c: CallbackQuery):
    await c.answer("Bu huquq sizda yo‘q", show_alert=True)


class PageEdit(StatesGroup):
    wait = State()


class ChapTitle(StatesGroup):
    value = State()


class Gloss(StatesGroup):
    add = State()
    delete = State()


# ---------------------------------------------------------------- sahifalarni ko'rish va tahrirlash

def page_markup(code, num, i, n):
    nn = fmt(num)
    nav = []
    if i > 0:
        nav.append(("◀️", f"pe:{code}:{nn}:{i - 1}"))
    nav.append((f"{i + 1}/{n}", "noop"))
    if i < n - 1:
        nav.append(("▶️", f"pe:{code}:{nn}:{i + 1}"))
    rows = [nav, [("🔄 Almashtirish", f"pr:{code}:{nn}:{i}"), ("🗑 O‘chirish", f"pd:{code}:{nn}:{i}")]]
    move = []
    if i > 0:
        move.append(("⬆️ Oldinga", f"pm:{code}:{nn}:{i}:-1"))
    if i < n - 1:
        move.append(("⬇️ Keyinga", f"pm:{code}:{nn}:{i}:1"))
    if move:
        rows.append(move)
    rows.append([("➕ Shundan keyin rasm qo‘shish", f"pa:{code}:{nn}:{i}")])
    rows.append([("⬅️ Bobga qaytish", f"ac:{code}:{nn}")])
    return kb(rows)


def page_caption(code, num, i, n):
    return f"🖼 <b>{esc(CAT.get(code)['title'])}</b> — {fmt(num)}-bob\nRasm {i + 1}/{n}"


async def show_page(chat_id, code, num, i, message=None):
    pages = (await CAT.get_pages(code, num)).get("pages") or []
    if not pages:
        await bot.send_message(chat_id, "🤷 Bu bobda rasm yo‘q.")
        return
    i = max(0, min(int(i), len(pages) - 1))
    cap, markup = page_caption(code, num, i, len(pages)), page_markup(code, num, i, len(pages))
    if message is not None and getattr(message, "photo", None):
        try:
            await message.edit_media(InputMediaPhoto(media=pages[i], caption=cap), reply_markup=markup)
            return
        except Exception as e:
            if "not modified" in str(e):
                return
            log.debug("sahifa tahriri (edit_media): %s", e)
    await bot.send_photo(chat_id, pages[i], caption=cap, reply_markup=markup)


def _parse(data: str):
    parts = data.split(":")
    return parts[1], float(parts[2]), [int(x) for x in parts[3:]]


@router.callback_query(F.data.startswith("pe:"), adm)
async def cb_pe(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await deny(c)
        return
    code, num, (i,) = _parse(c.data)
    if not CAT.chapter(code, num):
        await c.answer("Bob topilmadi", show_alert=True)
        return
    await state.clear()
    await c.answer()
    await show_page(c.from_user.id, code, num, i, c.message)


@router.callback_query(F.data.startswith("pm:"), adm)
async def cb_pm(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    code, num, (i, d) = _parse(c.data)
    if not CAT.chapter(code, num):
        await c.answer()
        return
    pages = list((await CAT.get_pages(code, num)).get("pages") or [])
    j = i + d
    if not (0 <= i < len(pages) and 0 <= j < len(pages)):
        await c.answer()
        return
    pages[i], pages[j] = pages[j], pages[i]
    await CAT.set_pages(code, num, pages)
    await jlog(c, f"🖼 {CAT.get(code)['title']} — {fmt(num)}-bob: {i + 1}-rasm {j + 1}-o‘ringa surildi")
    await c.answer("Surildi")
    await show_page(c.from_user.id, code, num, j, c.message)


@router.callback_query(F.data.startswith("pd:"), adm)
async def cb_pd(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    code, num, (i,) = _parse(c.data)
    await c.answer()
    markup = kb([[("✅ Ha, o‘chirilsin", f"pd2:{code}:{fmt(num)}:{i}"), ("❌ Yo‘q", f"pe:{code}:{fmt(num)}:{i}")]])
    try:
        await c.message.edit_caption(caption=f"🗑 {i + 1}-rasm o‘chirilsinmi?", reply_markup=markup)
    except Exception:
        await bot.send_message(c.from_user.id, f"🗑 {i + 1}-rasm o‘chirilsinmi?", reply_markup=markup)


@router.callback_query(F.data.startswith("pd2:"), adm)
async def cb_pd2(c: CallbackQuery):
    if not can(c, "chapter"):
        await deny(c)
        return
    code, num, (i,) = _parse(c.data)
    if not CAT.chapter(code, num):
        await c.answer()
        return
    pages = list((await CAT.get_pages(code, num)).get("pages") or [])
    if len(pages) <= 1:
        await c.answer("Bobda kamida bitta rasm qolishi kerak. Butun bobni bob menyusidan o‘chiring.", show_alert=True)
        return
    if 0 <= i < len(pages):
        pages.pop(i)
        await CAT.set_pages(code, num, pages)
        await jlog(c, f"🖼 {CAT.get(code)['title']} — {fmt(num)}-bob: {i + 1}-rasm o‘chirildi")
    await c.answer("O‘chirildi")
    await show_page(c.from_user.id, code, num, min(i, len(pages) - 1), c.message)


def tr_markup(on):
    return kb([[("🌐 Tarjima: yoqilgan ✅" if on else "🌐 Tarjima: o‘chiq (bosib yoqing)", "ptr")], [admin.CANCEL]])


@router.callback_query(F.data.startswith("pr:") | F.data.startswith("pa:"), adm)
async def cb_prpa(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await deny(c)
        return
    kind = c.data.split(":")[0]
    code, num, (i,) = _parse(c.data)
    if not CAT.chapter(code, num):
        await c.answer()
        return
    await state.set_state(PageEdit.wait)
    await state.update_data(code=code, num=num, i=i, mode="replace" if kind == "pr" else "insert", tr=False)
    await c.answer()
    what = (f"🔄 Yangi rasmni yuboring — u {i + 1}-rasm o‘rniga qo‘yiladi." if kind == "pr"
            else f"➕ Qo‘shiladigan rasmni yuboring — u {i + 1}-rasmdan keyin qo‘yiladi.")
    await bot.send_message(c.from_user.id, what + "\nSifat yo‘qolmasligi uchun <b>fayl</b> qilib yuborgan ma’qul. "
                                                 "Asl tilda bo‘lsa — tarjimani yoqing.", reply_markup=tr_markup(False))


@router.callback_query(F.data == "ptr", PageEdit.wait)
async def cb_ptr(c: CallbackQuery, state: FSMContext):
    on = not (await state.get_data()).get("tr")
    await state.update_data(tr=on)
    await c.answer("🌐 Tarjima qilinadi" if on else "Tarjimasiz qo‘yiladi")
    try:
        await c.message.edit_reply_markup(reply_markup=tr_markup(on))
    except Exception:
        pass


@router.message(PageEdit.wait, F.photo | F.document)
async def pe_file(msg: Message, state: FSMContext):
    d = await state.get_data()
    if msg.photo:
        fid = msg.photo[-1].file_id
    else:
        doc = msg.document
        if chapfiles.kind_of(doc.file_name or "", doc.mime_type or "") != "image":
            await msg.answer("🖼 Rasm yuboring (JPG, PNG yoki WEBP).")
            return
        if (doc.file_size or 0) > MAX_DOWNLOAD:
            await msg.answer("📦 Rasm 20 MB dan katta — kichikroq qilib yuboring.")
            return
        fid = doc.file_id
    code, num, i, mode = d["code"], float(d["num"]), int(d["i"]), d["mode"]
    m = CAT.get(code)
    if not m or not CAT.chapter(code, num):
        await state.clear()
        await msg.answer("Bob topilmadi.")
        return
    await state.clear()
    status = await msg.answer("⏳ Tayyorlanyapti…")
    note = ""
    try:
        img = await asyncio.to_thread(typeset.load_image, await download(fid))
        if d.get("tr"):
            try:
                img, _b, _l = await translate_image(img, msg.from_user.id, glossary=CAT.glossary_pairs(code))
            except translator.GeminiError as e:
                note = "\n" + gem_error_text(e) + "\nRasm tarjimasiz qo‘yildi."
        jpegs = await asyncio.to_thread(storage_pieces, img)
        ids = await upload_photos(jpegs, storage_chat(), caption=f"{m['title']} · {fmt(num)}-bob (tahrir)")
    except Exception:
        log.exception("sahifa tahriri")
        await safe_edit(status, "❌ Rasmni tayyorlab bo‘lmadi. Boshqa rasm yuborib ko‘ring.")
        return
    pages = list((await CAT.get_pages(code, num)).get("pages") or [])
    if mode == "replace" and 0 <= i < len(pages):
        pages[i:i + 1] = ids
        at = i
    else:
        at = min(i + 1, len(pages))
        pages[at:at] = ids
    await CAT.set_pages(code, num, pages)
    await jlog(msg, f"🖼 {m['title']} — {fmt(num)}-bob: " + (f"{i + 1}-rasm almashtirildi" if mode == "replace"
                                                            else f"{i + 1}-rasmdan keyin rasm qo‘shildi"))
    await safe_delete(status)
    await msg.answer("✅ Saqlandi." + (f" Rasm uzun bo‘lgani uchun {len(ids)} bo‘lakka bo‘lindi." if len(ids) > 1 else "")
                     + note)
    await show_page(msg.chat.id, code, num, at)


# ---------------------------------------------------------------- bob nomi

@router.callback_query(F.data.startswith("ct:"), adm)
async def cb_ct(c: CallbackQuery, state: FSMContext):
    if not can(c, "chapter"):
        await deny(c)
        return
    _, code, num = c.data.split(":")
    if not CAT.chapter(code, float(num)):
        await c.answer()
        return
    await state.set_state(ChapTitle.value)
    await state.update_data(code=code, num=float(num))
    await c.answer()
    await bot.send_message(c.from_user.id, "🏷 Bob nomini yuboring (masalan: <i>Uyg‘onish</i>).\n"
                                           "Nomni olib tashlash uchun «-» yuboring.", reply_markup=kb([[admin.CANCEL]]))


@router.message(ChapTitle.value, F.text, ~F.text.startswith("/"))
async def ct_value(msg: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    code, num = d["code"], float(d["num"])
    if not CAT.chapter(code, num):
        await msg.answer("Bob topilmadi.")
        return
    title = "" if msg.text.strip() in ("-", "—") else msg.text.strip()[:100]
    await CAT.set_chapter(code, num, title=title)
    await jlog(msg, f"🏷 {CAT.get(code)['title']} — {fmt(num)}-bob nomi: {title or '(olib tashlandi)'}")
    text, markup = admin.chapter_admin(code, num)
    await msg.answer(text, reply_markup=markup)


# ---------------------------------------------------------------- glossariy

def gloss_view(code):
    m = CAT.get(code)
    items = CAT.glossary(code)
    lines = [f"📖 <b>{esc(m['title'])}</b> — glossariy ({len(items)} ta)",
             "Tarjimada bu ismlar va atamalar har doim shunday yoziladi.", ""]
    if items:
        for i, g in enumerate(items[:80], 1):
            lines.append(f"{i}. {esc(g['o'])} → {esc(g['t'])}")
        if len(items) > 80:
            lines.append(f"… va yana {len(items) - 80} ta")
    else:
        lines.append("Hozircha bo‘sh. «➕ Qo‘shish» ni bosing — yoki bob tarjimasidan keyin bot ismlarni o‘zi taklif qiladi.")
    rows = [[("➕ Qo‘shish", f"aga:{code}")]]
    if items:
        rows[0].append(("➖ O‘chirish", f"agd:{code}"))
        rows.append([("🧹 Hammasini tozalash", f"agx:{code}")])
    rows.append([("⬅️ Manga", f"am:{code}")])
    text = "\n".join(lines)
    return (text if len(text) <= 4000 else text[:3990] + "…"), kb(rows)


_NUMBERING = re.compile(r"^\s*(?:\d{1,3}[.)]\s+|[•*\-–]\s+)")
_SEP = re.compile(r"\s*(?:=|→|->|⇒)\s*|\s+[—–-]\s+")


def parse_pairs(text: str):
    out = []
    for line in (text or "").splitlines():
        line = _NUMBERING.sub("", line).strip()
        if not line:
            continue
        parts = _SEP.split(line, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            out.append((parts[0].strip()[:80], parts[1].strip()[:80]))
    return out


def parse_indexes(text: str, n: int):
    t = (text or "").lower().strip()
    if t in ("hammasi", "hamma", "all", "*"):
        return set(range(n))
    out = set()
    for m in re.finditer(r"(\d+)\s*(?:-|–|—)\s*(\d+)|(\d+)", t):
        if m.group(3):
            k = int(m.group(3)) - 1
            if 0 <= k < n:
                out.add(k)
        else:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            out.update(k - 1 for k in range(lo, min(hi, n) + 1) if k >= 1)
    return out


@router.callback_query(F.data.startswith("ag:"), adm)
async def cb_ag(c: CallbackQuery, state: FSMContext):
    code = c.data[3:]
    if not CAT.get(code):
        await c.answer("Topilmadi")
        return
    if not can(c, "manga", "chapter"):
        await deny(c)
        return
    await state.clear()
    await c.answer()
    text, markup = gloss_view(code)
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("aga:") | F.data.startswith("agd:"), adm)
async def cb_aga(c: CallbackQuery, state: FSMContext):
    code = c.data[4:]
    if not CAT.get(code) or not can(c, "manga", "chapter"):
        await c.answer()
        return
    await c.answer()
    await state.update_data(code=code)
    if c.data.startswith("aga:"):
        await state.set_state(Gloss.add)
        await bot.send_message(c.from_user.id, "➕ Har qatorga bittadan yozing: <code>Asl yozilishi = O‘zbekchasi</code>\n\n"
                                               "Masalan:\n<code>성진우 = Sung Jinwoo\nHunter = Ovchi\n"
                                               "Shadow Monarch = Soyalar Hukmdori</code>\n\n"
                                               "Bor so‘z qayta yozilsa — tarjimasi yangilanadi.",
                               reply_markup=kb([[admin.CANCEL]]))
    else:
        await state.set_state(Gloss.delete)
        await bot.send_message(c.from_user.id, "➖ O‘chiriladigan so‘zlar raqamini yozing.\nMasalan: <code>2, 5, 7-9</code>",
                               reply_markup=kb([[admin.CANCEL]]))


@router.message(Gloss.add, F.text, ~F.text.startswith("/"))
async def gl_add(msg: Message, state: FSMContext):
    code = (await state.get_data()).get("code")
    pairs = parse_pairs(msg.text)
    if not pairs:
        await msg.answer("🤔 Tushunmadim. Har qatorga <code>Asl = O‘zbekchasi</code> ko‘rinishida yozing.")
        return
    await state.clear()
    if not CAT.get(code):
        return
    before = len(CAT.glossary(code))
    out = await CAT.add_glossary(code, pairs)
    await jlog(msg, f"📖 {CAT.get(code)['title']} — glossariyga {len(pairs)} ta so‘z yozildi")
    added = len(out) - before
    await msg.answer(f"✅ Saqlandi: {added} ta yangi" + (f", {len(pairs) - added} ta yangilandi." if len(pairs) > added
                                                       else "."))
    text, markup = gloss_view(code)
    await msg.answer(text, reply_markup=markup)


@router.message(Gloss.delete, F.text, ~F.text.startswith("/"))
async def gl_delete(msg: Message, state: FSMContext):
    code = (await state.get_data()).get("code")
    items = CAT.glossary(code)
    idx = parse_indexes(msg.text, len(items))
    if not idx:
        await msg.answer("Raqam yozing, masalan <code>2, 5</code>")
        return
    await state.clear()
    await CAT.set_glossary(code, [g for k, g in enumerate(items) if k not in idx])
    await jlog(msg, f"📖 {CAT.get(code)['title']} — glossariydan {len(idx)} ta so‘z o‘chirildi")
    await msg.answer(f"🗑 {len(idx)} ta so‘z o‘chirildi.")
    text, markup = gloss_view(code)
    await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("agx:") | F.data.startswith("agx2:"), adm)
async def cb_agx(c: CallbackQuery):
    first = c.data.startswith("agx:")
    code = c.data.split(":", 1)[1]
    if not CAT.get(code) or not can(c, "manga", "chapter"):
        await c.answer()
        return
    if first:
        await c.answer()
        await safe_edit(c.message, "🧹 Glossariy butunlay tozalansinmi?",
                        kb([[("✅ Ha", f"agx2:{code}"), ("❌ Yo‘q", f"ag:{code}")]]))
        return
    await CAT.set_glossary(code, [])
    await jlog(c, f"🧹 {CAT.get(code)['title']} — glossariy tozalandi")
    await c.answer("Tozalandi")
    text, markup = gloss_view(code)
    await safe_edit(c.message, text, markup)


@router.callback_query(F.data.startswith("ags:"), adm)
async def cb_ags(c: CallbackQuery):
    s = admin.SUGGEST.pop(c.data[4:], None)
    if not s or not CAT.get(s["code"]):
        await c.answer("Eskirgan — glossariyni qo‘lda to‘ldiring.", show_alert=True)
        return
    if not can(c, "manga", "chapter"):
        await deny(c)
        return
    await CAT.add_glossary(s["code"], s["pairs"])
    await jlog(c, f"📖 {CAT.get(s['code'])['title']} — glossariyga {len(s['pairs'])} ta taklif saqlandi")
    await c.answer(f"✅ {len(s['pairs'])} ta so‘z saqlandi")
    text, markup = gloss_view(s["code"])
    await safe_edit(c.message, text, markup)
