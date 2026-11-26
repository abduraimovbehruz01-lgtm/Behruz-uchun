"""Bosh admin va sozlamalar: yordamchi adminlar va ularning huquqlari, adminlar harakatlari jurnali,
majburiy obuna kanallari, bot admin qilingan kanallarni aniqlash (rasmlar ombori yoki majburiy obuna)."""
import re
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, KeyboardButton, Message, ReplyKeyboardMarkup

import catalog
from core import (CAT, REQUIRED_CHANNEL, SUB_OK, STATE, admin_keyboard, bot, esc, jlog, kb, log, safe_edit,
                  storage_chat, tell_owners)

router = Router(name="staff")
DEFAULT_RIGHTS = ("manga", "chapter")
PENDING_SUB: dict[int, float] = {}     # admin kanal tanlash oynasini ochgan vaqt (ikki marta so'ramaslik uchun)
B_CANCEL = "❌ Bekor qilish"


def adm(e) -> bool:
    return CAT.is_admin(e.from_user.id)


def owner(e) -> bool:
    return CAT.is_owner(e.from_user.id)


def can(e, right) -> bool:
    return CAT.has(e.from_user.id, right)


async def deny(c: CallbackQuery):
    await c.answer("Bu huquq sizda yo‘q", show_alert=True)


class AddAdmin(StatesGroup):
    uid = State()


class SubAdd(StatesGroup):
    wait = State()


def users_keyboard():
    """«Kontaktdan tanlash» tugmasi: Telegram foydalanuvchi tanlash oynasini ochadi va ID ni yuboradi."""
    from aiogram.types import KeyboardButtonRequestUsers
    try:
        req = KeyboardButtonRequestUsers(request_id=1, user_is_bot=False, max_quantity=1, request_name=True,
                                         request_username=True)
    except Exception:
        req = KeyboardButtonRequestUsers(request_id=1, user_is_bot=False)
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Kontaktdan tanlash", request_users=req)],
                                         [KeyboardButton(text=B_CANCEL)]], resize_keyboard=True, one_time_keyboard=True)


def chat_keyboard():
    """«Kanalni tanlash» tugmasi: kanal tanlansa, Telegram botni o'sha kanalga admin qilib qo'shadi."""
    from aiogram.types import KeyboardButtonRequestChat
    req = None
    try:
        from aiogram.types import ChatAdministratorRights
        rights = ChatAdministratorRights(is_anonymous=False, can_manage_chat=False, can_delete_messages=False,
                                         can_manage_video_chats=False, can_restrict_members=False,
                                         can_promote_members=False, can_change_info=False, can_invite_users=True,
                                         can_post_stories=False, can_edit_stories=False, can_delete_stories=False)
        req = KeyboardButtonRequestChat(request_id=2, chat_is_channel=True, user_administrator_rights=rights,
                                        bot_administrator_rights=rights, request_title=True, request_username=True)
    except Exception as e:
        log.debug("kanal tanlash tugmasi (huquqlarsiz): %r", e)
    if req is None:
        req = KeyboardButtonRequestChat(request_id=2, chat_is_channel=True, request_title=True, request_username=True)
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📢 Kanalni tanlash", request_chat=req)],
                                         [KeyboardButton(text=B_CANCEL)]], resize_keyboard=True, one_time_keyboard=True)


# ---------------------------------------------------------------- adminlar

def admins_view():
    lines = ["👥 <b>Adminlar</b>", ""]
    for oid in sorted(CAT.owners):
        lines.append(f"👑 Bosh admin — <code>{oid}</code>")
    items = sorted(CAT.admins.items(), key=lambda kv: kv[1].get("added") or 0)
    if items:
        lines.append("")
        for i, (aid, a) in enumerate(items, 1):
            lines.append(f"{i}. {esc(a.get('name') or str(aid))} — {len(a.get('rights') or [])} ta huquq")
    else:
        lines.append("\nHozircha yordamchi admin yo‘q. Har biriga faqat kerakli huquqni berasiz — masalan, faqat "
                     "bob yuklash.")
    rows = [[(f"👤 {(a.get('name') or str(aid))[:30]}", f"adm:a:{aid}")] for aid, a in items[:30]]
    rows.append([("➕ Admin qo‘shish", "adm:aadd")])
    return "\n".join(lines), kb(rows)


def admin_editor(uid):
    a = CAT.admins.get(uid) or {}
    have = set(a.get("rights") or [])
    who = esc(a.get("name") or str(uid)) + (f" (@{esc(a['username'])})" if a.get("username") else "")
    added = datetime.fromtimestamp(a.get("added") or time.time(), catalog.TZ).strftime("%d.%m.%Y")
    text = (f"👤 <b>{who}</b>\n🆔 <code>{uid}</code> · qo‘shilgan: {added}\n\n"
            "Huquqni bosib yoqing (✅) yoki o‘chiring (❌):")
    rows = [[(("✅ " if r in have else "❌ ") + label, f"adm:ar:{uid}:{r}")] for r, label in catalog.RIGHTS.items()]
    rows.append([("✅ Hammasini yoqish" if len(have) < len(catalog.RIGHTS) else "❌ Hammasini o‘chirish",
                  f"adm:aall:{uid}")])
    rows.append([("🗑 Adminlikdan olish", f"adm:adel:{uid}"), ("⬅️ Adminlar", "adm:admins")])
    return text, kb(rows)


@router.callback_query(F.data == "adm:admins", adm)
async def cb_admins(c: CallbackQuery, state: FSMContext):
    if not owner(c):
        await deny(c)
        return
    await state.clear()
    await c.answer()
    await send_admins(c.from_user.id)


async def send_admins(uid):
    text, markup = admins_view()
    await bot.send_message(uid, text, reply_markup=markup)


@router.callback_query(F.data == "adm:aadd", adm)
async def cb_aadd(c: CallbackQuery, state: FSMContext):
    if not owner(c):
        await deny(c)
        return
    await state.set_state(AddAdmin.uid)
    await c.answer()
    await bot.send_message(c.from_user.id, "➕ <b>Yangi admin</b>\n\nUni qanday topamiz:\n"
                                           "• pastdagi «👤 Kontaktdan tanlash» tugmasini bosing;\n"
                                           "• yoki uning <b>Telegram ID</b> raqamini yozing (u @userinfobot’dan bilib oladi);\n"
                                           "• yoki uning biror xabarini shu yerga <b>forward</b> qiling.",
                           reply_markup=users_keyboard())


async def _cancel_to_menu(msg: Message, state: FSMContext):
    await state.clear()
    PENDING_SUB.pop(msg.from_user.id, None)
    await msg.answer("❌ Bekor qilindi.", reply_markup=admin_keyboard(msg.from_user.id))


@router.message(AddAdmin.uid, F.users_shared)
async def aa_shared(msg: Message, state: FSMContext):
    us = msg.users_shared
    users = list(getattr(us, "users", None) or [])
    if users:
        u = users[0]
        await finish_add(msg, state, int(u.user_id), getattr(u, "first_name", "") or "", getattr(u, "username", "") or "")
        return
    ids = list(getattr(us, "user_ids", None) or [])
    if ids:
        await finish_add(msg, state, int(ids[0]))
        return
    await msg.answer("🤷 Foydalanuvchi tanlanmadi. Qayta urinib ko‘ring yoki ID sini yozing.")


@router.message(AddAdmin.uid, F.forward_origin)
async def aa_forward(msg: Message, state: FSMContext):
    o = msg.forward_origin
    user = getattr(o, "sender_user", None)
    if user is None:
        await msg.answer("🙈 Bu odam xabarlarini forward qilishni yopib qo‘ygan — ID sini bilib bo‘lmadi.\n"
                         "U @userinfobot’ga yozib ID sini olsin va sizga yuborsin, siz esa raqamni shu yerga yozing.")
        return
    await finish_add(msg, state, int(user.id), user.first_name or "", user.username or "")


@router.message(AddAdmin.uid, F.text, ~F.text.startswith("/"))
async def aa_text(msg: Message, state: FSMContext):
    t = msg.text.strip()
    if t == B_CANCEL:
        await _cancel_to_menu(msg, state)
        return
    if not re.fullmatch(r"\d{5,15}", t):
        await msg.answer("🔢 ID faqat raqamlardan iborat bo‘ladi (masalan <code>123456789</code>). "
                         "Yoki «👤 Kontaktdan tanlash» tugmasini bosing.")
        return
    await finish_add(msg, state, int(t))


async def finish_add(msg: Message, state: FSMContext, uid: int, name: str = "", username: str = ""):
    if uid in CAT.owners:
        await state.clear()
        await msg.answer("👑 Bu bosh admin — uning huquqlari cheklanmaydi.",
                         reply_markup=admin_keyboard(msg.from_user.id))
        return
    if uid == getattr(bot, "id", None):
        await msg.answer("🤖 Bu botning o‘zi 🙂 Boshqa odamni tanlang.")
        return
    await state.clear()
    if not name:
        try:
            ch = await bot.get_chat(uid)
            name, username = ch.first_name or "", username or (ch.username or "")
        except Exception:
            pass
    if uid in CAT.admins:
        await msg.answer("ℹ️ U allaqachon admin — huquqlarini quyida o‘zgartirishingiz mumkin.",
                         reply_markup=admin_keyboard(msg.from_user.id))
    else:
        await CAT.set_admin(uid, DEFAULT_RIGHTS, name=name, by=msg.from_user.id, username=username)
        await jlog(msg, f"👥 Yangi admin: {name or uid} (ID {uid})")
        note = ""
        try:
            await bot.send_message(uid, f"👋 Sizni @{STATE['username']} botida <b>admin</b> qilishdi!\n"
                                        "Boshqaruv paneli: /admin")
        except Exception:
            note = ("\n\nℹ️ U hali botni ochmagan — xabar bormadi. Botga kirib /admin yozsa, panel chiqadi.")
        await msg.answer(f"✅ {esc(name or str(uid))} admin qilindi.{note}",
                         reply_markup=admin_keyboard(msg.from_user.id))
    text, markup = admin_editor(uid)
    await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:a:"), adm)
async def cb_a(c: CallbackQuery):
    if not owner(c):
        await deny(c)
        return
    uid = int(c.data.split(":")[2])
    if uid not in CAT.admins:
        await c.answer("Bu odam endi admin emas", show_alert=True)
        return
    await c.answer()
    text, markup = admin_editor(uid)
    await bot.send_message(c.from_user.id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:ar:") | F.data.startswith("adm:aall:"), adm)
async def cb_rights(c: CallbackQuery):
    if not owner(c):
        await deny(c)
        return
    parts = c.data.split(":")
    uid = int(parts[2])
    a = CAT.admins.get(uid)
    if not a:
        await c.answer("Bu odam endi admin emas", show_alert=True)
        return
    have = set(a.get("rights") or [])
    who = a.get("name") or str(uid)
    if parts[1] == "ar":
        right = parts[3]
        if right not in catalog.RIGHTS:
            await c.answer()
            return
        have ^= {right}
        on = right in have
        await CAT.set_admin(uid, have)
        await jlog(c, f"🔑 {who}: {'berildi' if on else 'olindi'} — {catalog.RIGHTS[right]}")
        await c.answer("✅ Berildi" if on else "❌ Olindi")
    else:
        have = set() if len(have) == len(catalog.RIGHTS) else set(catalog.RIGHTS)
        await CAT.set_admin(uid, have)
        await jlog(c, f"🔑 {who}: {'barcha huquqlar berildi' if have else 'barcha huquqlar olindi'}")
        await c.answer("Saqlandi")
    text, markup = admin_editor(uid)
    await safe_edit(c.message, text, markup)


@router.callback_query(F.data.startswith("adm:adel:") | F.data.startswith("adm:adel2:"), adm)
async def cb_adel(c: CallbackQuery):
    if not owner(c):
        await deny(c)
        return
    parts = c.data.split(":")
    uid = int(parts[2])
    a = CAT.admins.get(uid)
    if not a:
        await c.answer("Bu odam endi admin emas", show_alert=True)
        return
    who = a.get("name") or str(uid)
    if parts[1] == "adel":
        await c.answer()
        await safe_edit(c.message, f"🗑 <b>{esc(who)}</b> adminlikdan olinsinmi?",
                        kb([[("✅ Ha", f"adm:adel2:{uid}"), ("❌ Yo‘q", f"adm:a:{uid}")]]))
        return
    await CAT.remove_admin(uid)
    await jlog(c, f"🗑 Adminlikdan olindi: {who} (ID {uid})")
    try:
        await bot.send_message(uid, "ℹ️ Siz endi botda admin emassiz.")
    except Exception:
        pass
    await c.answer("Olib tashlandi")
    text, markup = admins_view()
    await safe_edit(c.message, f"🗑 {esc(who)} adminlikdan olindi.\n\n" + text, markup)


# ---------------------------------------------------------------- jurnal

@router.callback_query(F.data.startswith("adm:journal:"), adm)
async def cb_journal(c: CallbackQuery):
    if not (owner(c) or can(c, "settings")):
        await deny(c)
        return
    await c.answer()
    msg = c.message if c.message is not None and (getattr(c.message, "text", "") or "").startswith("📜") else None
    await send_journal(c.from_user.id, int(c.data.split(":")[2]), msg)


async def send_journal(uid, page, edit=None):
    items = list(reversed(CAT.journal))
    per = 15
    part = items[page * per:(page + 1) * per]
    if not items:
        text = "📜 Jurnal hozircha bo‘sh — adminlar nima qilgani shu yerda ko‘rinadi."
    else:
        lines = [f"📜 <b>Jurnal</b> — oxirgi harakatlar ({len(items)} ta)", ""]
        for e in part:
            t = datetime.fromtimestamp(float(e.get("t") or 0), catalog.TZ).strftime("%d.%m %H:%M")
            lines.append(f"<code>{t}</code> <b>{esc(e.get('n') or str(e.get('u') or ''))}</b>: {esc(e.get('a', ''))}")
        text = "\n".join(lines)[:4000]
    nav = []
    if page > 0:
        nav.append(("⬅️ Yangilari", f"adm:journal:{page - 1}"))
    if (page + 1) * per < len(items):
        nav.append(("Eskilari ➡️", f"adm:journal:{page + 1}"))
    rows = [nav] if nav else []
    if edit is not None:
        await safe_edit(edit, text, kb(rows) if rows else None)
    else:
        await bot.send_message(uid, text, reply_markup=kb(rows) if rows else None)


# ---------------------------------------------------------------- majburiy obuna

def subs_view():
    chs = list(CAT.cfg.get("channels") or [])
    lines = ["📢 <b>Majburiy obuna</b>", "", "Foydalanuvchi botdan foydalanishdan oldin shu kanallarga a’zo bo‘lishi kerak."]
    if REQUIRED_CHANNEL:
        lines.append(f"\n🔒 Server sozlamasida: {esc(REQUIRED_CHANNEL)}")
    if chs:
        lines.append("")
        for i, ch in enumerate(chs, 1):
            lines.append(f"{i}. {esc(ch.get('title') or str(ch.get('id')))} — {esc(ch.get('url') or '')}")
    elif not REQUIRED_CHANNEL:
        lines.append("\nHozircha kanal yo‘q — bot hammaga ochiq.")
    rows = [[(f"🗑 {i}. {(ch.get('title') or str(ch.get('id')))[:30]}", f"adm:subdel:{ch.get('id')}")]
            for i, ch in enumerate(chs, 1)]
    rows.append([("➕ Kanal qo‘shish", "adm:subadd")])
    return "\n".join(lines), kb(rows)


@router.callback_query(F.data == "adm:subs", adm)
async def cb_subs(c: CallbackQuery, state: FSMContext):
    if not can(c, "settings"):
        await deny(c)
        return
    await state.clear()
    await c.answer()
    await send_subs(c.from_user.id)


async def send_subs(uid):
    text, markup = subs_view()
    await bot.send_message(uid, text, reply_markup=markup)


@router.callback_query(F.data == "adm:subadd", adm)
async def cb_subadd(c: CallbackQuery, state: FSMContext):
    if not can(c, "settings"):
        await deny(c)
        return
    await state.set_state(SubAdd.wait)
    PENDING_SUB[c.from_user.id] = time.time()
    await c.answer()
    await bot.send_message(c.from_user.id, "➕ <b>Majburiy obuna kanali</b>\n\n"
                                           "• pastdagi «📢 Kanalni tanlash» tugmasini bosing — bot o‘sha kanalga admin "
                                           "qilib qo‘shiladi;\n"
                                           "• yoki kanal manzilini yuboring (<code>@kanal</code> yoki t.me/kanal);\n"
                                           "• yoki kanaldan biror postni forward qiling.\n\n"
                                           "Bot kanalda <b>admin</b> bo‘lishi shart — aks holda kim a’zo ekanini "
                                           "tekshira olmaydi.", reply_markup=chat_keyboard())


@router.message(SubAdd.wait, F.chat_shared)
async def sa_shared(msg: Message, state: FSMContext):
    await do_add_sub(msg, state, msg.chat_shared.chat_id)


@router.message(SubAdd.wait, F.forward_origin)
async def sa_forward(msg: Message, state: FSMContext):
    o = msg.forward_origin
    chat = getattr(o, "chat", None) or getattr(o, "sender_chat", None)
    if chat is None:
        await msg.answer("Bu kanaldan forward qilingan post emas. Kanal postini forward qiling.")
        return
    await do_add_sub(msg, state, chat.id)


@router.message(SubAdd.wait, F.text, ~F.text.startswith("/"))
async def sa_text(msg: Message, state: FSMContext):
    t = msg.text.strip()
    if t == B_CANCEL:
        await _cancel_to_menu(msg, state)
        return
    if re.search(r"t\.me/(?:\+|joinchat/)", t):
        await msg.answer("🔒 Yopiq kanalning taklif havolasidan kanalni topib bo‘lmaydi. «📢 Kanalni tanlash» tugmasini "
                         "bosing yoki kanaldan biror postni forward qiling.")
        return
    m = re.search(r"(?:t\.me/|telegram\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", t)
    if not m:
        await msg.answer("Kanal manzilini <code>@kanal</code> ko‘rinishida yuboring yoki «📢 Kanalni tanlash» tugmasini bosing.")
        return
    await do_add_sub(msg, state, "@" + m.group(1))


async def do_add_sub(msg: Message, state: FSMContext, ref):
    ok, text = await add_sub_channel(msg, ref)
    if not ok:
        await msg.answer(text)
        return
    await state.clear()
    PENDING_SUB.pop(msg.from_user.id, None)
    await msg.answer(text, reply_markup=admin_keyboard(msg.from_user.id))
    view, markup = subs_view()
    await msg.answer(view, reply_markup=markup)


async def add_sub_channel(event, ref):
    """Kanalni majburiy obunaga qo'shadi. Qaytaradi: (muvaffaqiyat, xabar)."""
    try:
        chat = await bot.get_chat(ref)
    except Exception as e:
        log.info("kanal topilmadi %s: %s", ref, e)
        return False, "🤷 Kanal topilmadi. Manzilni tekshiring yoki botni avval kanalga admin qiling."
    if str(getattr(chat.type, "value", chat.type)) not in ("channel", "supergroup", "group"):
        return False, "Bu kanal yoki guruh emas."
    title = chat.title or ""
    st = storage_chat()
    if st is not None and str(st) == str(chat.id):
        return False, "📦 Bu rasmlar ombori kanali — uni majburiy obunaga qo‘shib bo‘lmaydi."
    chs = list(CAT.cfg.get("channels") or [])
    if any(str(x.get("id")) == str(chat.id) for x in chs):
        return False, f"ℹ️ «{esc(title)}» allaqachon ro‘yxatda."
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_adm = str(getattr(me.status, "value", me.status)) in ("administrator", "creator")
    except Exception:
        is_adm = False
    if not is_adm:
        return False, (f"⚠️ Bot «{esc(title)}» kanalida admin emas. Avval botni kanalga admin qiling, keyin qayta "
                       "urinib ko‘ring.")
    url = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else (getattr(chat, "invite_link", None) or "")
    if not url:
        try:
            url = (await bot.create_chat_invite_link(chat.id, name="Manhora obuna")).invite_link
        except Exception as e:
            log.info("taklif havolasi yaratilmadi: %s", e)
            return False, ("⚠️ Kanal yopiq, botda esa taklif havolasi yaratish huquqi yo‘q. Kanal sozlamalarida botga "
                           "«Foydalanuvchilarni qo‘shish» huquqini bering va qayta urinib ko‘ring.")
    chs.append({"id": chat.id, "title": title, "url": url})
    await CAT.set_cfg(channels=chs)
    SUB_OK.clear()
    await jlog(event, f"📢 Majburiy obunaga qo‘shildi: {title}")
    return True, f"✅ «{esc(title)}» majburiy obunaga qo‘shildi."


@router.callback_query(F.data.startswith("adm:subdel:"), adm)
async def cb_subdel(c: CallbackQuery):
    if not can(c, "settings"):
        await deny(c)
        return
    cid = c.data.split(":", 2)[2]
    chs = list(CAT.cfg.get("channels") or [])
    keep = [x for x in chs if str(x.get("id")) != cid]
    if len(keep) != len(chs):
        gone = [x for x in chs if str(x.get("id")) == cid][0]
        await CAT.set_cfg(channels=keep)
        SUB_OK.clear()
        await jlog(c, f"📢 Majburiy obunadan olindi: {gone.get('title') or cid}")
    await c.answer("O‘chirildi")
    text, markup = subs_view()
    await safe_edit(c.message, text, markup)


# ---------------------------------------------------------------- bot kanalga qo'shilganda

async def set_storage(chat, by):
    await CAT.set_cfg(storage_chat=chat.id, storage_title=chat.title or "")
    log.info("ombor kanali: %s (%s)", chat.title, chat.id)
    await jlog(by, f"📦 Rasmlar ombori: {chat.title or chat.id}")
    try:
        await bot.send_message(by, f"✅ «{esc(chat.title or 'kanal')}» rasmlar ombori qilib belgilandi. "
                                   "Endi bob yuklashingiz mumkin: /admin")
    except Exception:
        pass


@router.my_chat_member()
async def on_member(ev: ChatMemberUpdated):
    chat = ev.chat
    ctype = str(getattr(chat.type, "value", chat.type))
    if ctype not in ("channel", "supergroup", "group"):
        return
    st = str(getattr(ev.new_chat_member.status, "value", ev.new_chat_member.status))
    by = ev.from_user.id if ev.from_user else 0
    if st in ("administrator", "creator"):
        if not CAT.is_admin(by) or ctype != "channel":
            return
        if time.time() - PENDING_SUB.get(by, 0) < 900:
            return   # admin hozir majburiy obuna kanalini tanlayapti — o'sha yerda qo'shiladi
        cur = storage_chat()
        if cur is not None and str(cur) == str(chat.id):
            return
        public = bool(getattr(chat, "username", None))
        if cur is None and not public:
            await set_storage(chat, by)
            return
        try:
            await bot.send_message(by, f"🤖 Bot «{esc(chat.title or 'kanal')}» kanaliga admin qilindi. Bu kanal nima uchun?",
                                   reply_markup=kb([[("📢 Majburiy obunaga qo‘shish", f"chan:sub:{chat.id}")],
                                                    [("📦 Rasmlar ombori qilish", f"chan:st:{chat.id}")],
                                                    [("🚫 Hech narsa", "chan:no")]]))
        except Exception:
            pass
    elif st in ("left", "kicked"):
        if str(CAT.cfg.get("storage_chat")) == str(chat.id):
            await CAT.set_cfg(storage_chat=None, storage_title="")
            await tell_owners("⚠️ Bot rasmlar ombori kanalidan chiqarildi! Yangi bob yuklash uchun botni qayta admin qiling.",
                              force=True)
        chs = list(CAT.cfg.get("channels") or [])
        keep = [x for x in chs if str(x.get("id")) != str(chat.id)]
        if len(keep) != len(chs):
            await CAT.set_cfg(channels=keep)
            SUB_OK.clear()
            await tell_owners(f"⚠️ Bot «{esc(chat.title or 'kanal')}» kanalidan chiqarildi — u majburiy obuna "
                              "ro‘yxatidan olib tashlandi.", force=True)


@router.callback_query(F.data.startswith("chan:"), adm)
async def cb_chan(c: CallbackQuery):
    parts = c.data.split(":")
    if parts[1] == "no":
        await c.answer("Mayli")
        await safe_edit(c.message, "👌 Hech narsa o‘zgartirilmadi.")
        return
    if not can(c, "settings"):
        await deny(c)
        return
    cid = int(parts[2])
    if parts[1] == "sub":
        ok, text = await add_sub_channel(c, cid)
        await c.answer()
        await safe_edit(c.message, text)
        return
    try:
        chat = await bot.get_chat(cid)
    except Exception:
        await c.answer("Kanal topilmadi", show_alert=True)
        return
    chs = list(CAT.cfg.get("channels") or [])
    keep = [x for x in chs if str(x.get("id")) != str(cid)]
    if len(keep) != len(chs):
        await CAT.set_cfg(channels=keep)
        SUB_OK.clear()
    await set_storage(chat, c.from_user.id)
    await c.answer("📦 Ombor almashtirildi")
    await safe_edit(c.message, f"📦 «{esc(chat.title or '')}» endi rasmlar ombori.")
