"""Manga tarjimon — Telegram bot.

Manga / manhva / manxua sahifasini yuborasiz → bot matnlarni topadi, o'zbekchaga tarjima qiladi va rasmning
o'ziga yozib qaytaradi. Albom (bir nechta rasm), ZIP/CBZ arxiv yoki PDF (butun bob) ham qabul qilinadi.

Sozlamalar (Render → Environment):
  BOT_TOKEN        — @BotFather bergan token (majburiy)
  GEMINI_API_KEY   — aistudio.google.com kaliti (majburiy)
  OWNER_IDS        — admin(lar)ning Telegram ID si, vergul bilan (limit yo'q, /stat buyrug'i)
  DAILY_LIMIT      — har bir foydalanuvchiga kunlik bepul sahifalar soni (standart 40; 0 = cheksiz)
  MAX_PAGES        — bitta bobdan eng ko'pi bilan nechta sahifa (standart 60)
  ALLOWED_IDS      — to'ldirilsa, botdan faqat shu ID lar foydalana oladi
  REQUIRED_CHANNEL — majburiy obuna kanali, masalan @kanalim (bot kanalda admin bo'lishi shart)
  GEMINI_MODEL     — modellar ro'yxati, vergul bilan (ixtiyoriy)
  GEMINI_PARALLEL  — bir vaqtda nechta sahifa Gemini'ga yuboriladi (standart 2)
  TEST_KEY         — /sinov manzili uchun maxfiy kalit (ixtiyoriy, tekshirish uchun)
"""
import asyncio
import hashlib
import html
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, ReplyParameters)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import chapters
import translator
import typeset

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _ids(name):
    out = set()
    for x in os.environ.get(name, "").replace(";", ",").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            out.add(int(x))
    return out


def _int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNERS = _ids("OWNER_IDS")
ALLOWED = _ids("ALLOWED_IDS")
DAILY_LIMIT = _int("DAILY_LIMIT", 40)
MAX_PAGES = max(1, _int("MAX_PAGES", 60))
PARALLEL = max(1, _int("GEMINI_PARALLEL", 2))
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()
TEST_KEY = os.environ.get("TEST_KEY", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
PORT = _int("PORT", 10000)
MAX_DOWNLOAD = 20 * 1024 * 1024        # Telegram botlarga 20 MB dan kattasini yuklab olishga ruxsat bermaydi
TZ = timezone(timedelta(hours=5))      # Toshkent

GEM = translator.Translator(os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_MODEL") or None)
GEM_SEM = asyncio.Semaphore(PARALLEL)
CPU_SEM = asyncio.Semaphore(1)

try:
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if TOKEN else None
except Exception as _e:  # token formati noto'g'ri
    log.error("BOT_TOKEN noto‘g‘ri: %s", _e)
    bot = None
dp = Dispatcher()
dp.message.filter(F.chat.type == "private")
HTTP: aiohttp.ClientSession | None = None
STARTED = time.time()

# ---------------------------------------------------------------- holat (xotirada)
USAGE: dict[str, int] = {}             # "sana:uid" → bugun tarjima qilingan sahifalar
USERS: set[int] = set()
STATS = {"pages": 0, "chapters": 0, "errors": 0}
CACHE: "OrderedDict[str, dict]" = OrderedDict()   # tugmalar uchun: matn va asl sifatli fayl
CONTEXT: dict[int, tuple[float, list]] = {}       # oldingi sahifa matni (ismlar bir xil chiqishi uchun)
SUB_OK: dict[int, float] = {}
OWNER_NOTE = {"t": 0.0}


@dataclass
class Item:
    order: int
    kind: str          # "page" | "chapter"
    msg: Message
    file_id: str
    name: str
    size: int = 0
    mime: str = ""
    as_file: bool = False   # fayl (document) qilib yuborilgan — javob ham fayl bo'ladi
    cancelled: bool = False


QUEUES: dict[int, list[Item]] = {}
WORKERS: dict[int, asyncio.Task] = {}
CURRENT: dict[int, Item] = {}


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def is_owner(uid: int) -> bool:
    return uid in OWNERS


def used(uid: int) -> int:
    return USAGE.get(f"{today()}:{uid}", 0)


def remaining(uid: int) -> int:
    if is_owner(uid) or DAILY_LIMIT <= 0:
        return 10 ** 9
    return max(0, DAILY_LIMIT - used(uid))


def add_usage(uid: int, n: int = 1):
    k = f"{today()}:{uid}"
    USAGE[k] = USAGE.get(k, 0) + n
    STATS["pages"] += n
    if len(USAGE) > 5000:
        t = today()
        for key in [x for x in USAGE if not x.startswith(t)]:
            USAGE.pop(key, None)


def remember(texts, file_bytes=None, name="sahifa_uz.jpg") -> str:
    cid = hashlib.sha1(f"{time.time()}:{os.urandom(6).hex()}".encode()).hexdigest()[:12]
    CACHE[cid] = {"text": texts, "file": file_bytes, "name": name}
    while len(CACHE) > 300:
        CACHE.popitem(last=False)
    with_file = [k for k, v in CACHE.items() if v["file"]]
    for k in with_file[:-20]:          # asl sifatli fayllar faqat oxirgi 20 tasi uchun saqlanadi
        CACHE[k]["file"] = None
    return cid


def get_ctx(uid):
    t, lines = CONTEXT.get(uid, (0.0, []))
    return lines if time.time() - t < 1800 else []


def set_ctx(uid, lines):
    if lines:
        CONTEXT[uid] = (time.time(), lines[-14:])


def rp(msg: Message) -> ReplyParameters:
    return ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)


async def safe_edit(msg, text, kb=None):
    if msg is None:
        return
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.debug("edit: %s", e)
    except Exception as e:
        log.debug("edit: %s", e)


async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def download(file_id: str, timeout: int = 180) -> bytes:
    buf = await bot.download(file_id, timeout=timeout)
    buf.seek(0)
    return buf.read()


def gem_error_text(e: translator.GeminiError) -> str:
    if e.kind == "quota":
        return ("😔 Bugun Google Gemini’ning bepul limiti tugadi.\n"
                "U har kuni soat 13:00 atrofida (Toshkent vaqti) yangilanadi — keyin yana yuboring.")
    if e.kind == "key":
        return "⚠️ Botning Gemini kaliti ishlamayapti. Admin sozlamani tekshirishi kerak."
    if e.kind == "blocked":
        return "⚠️ Bu sahifani Google xavfsizlik filtri o‘tkazmadi."
    if e.kind == "busy":
        return "⏳ Google serverlari hozir juda band. Birozdan keyin qayta yuboring."
    return "❌ Tarjima qilib bo‘lmadi. Qayta yuborib ko‘ring."


async def tell_owners(text: str):
    if not OWNERS or time.time() - OWNER_NOTE["t"] < 3600:
        return
    OWNER_NOTE["t"] = time.time()
    for oid in OWNERS:
        try:
            await bot.send_message(oid, text)
        except Exception:
            pass


def limit_text() -> str:
    return (f"⛔ Bugungi limit tugadi ({DAILY_LIMIT} ta sahifa).\n"
            "Ertaga yana yuboring — limit har kuni yarim tunda (Toshkent vaqti) yangilanadi.")


# ---------------------------------------------------------------- kirish nazorati

async def subscribed(uid: int) -> bool:
    if not REQUIRED_CHANNEL or is_owner(uid):
        return True
    if time.time() - SUB_OK.get(uid, 0) < 600:
        return True
    chat = int(REQUIRED_CHANNEL) if REQUIRED_CHANNEL.lstrip("-").isdigit() else REQUIRED_CHANNEL
    try:
        m = await bot.get_chat_member(chat_id=chat, user_id=uid)
        ok = m.status in ("creator", "administrator", "member") or bool(getattr(m, "is_member", False))
    except Exception as e:
        log.warning("obuna tekshiruvi ishlamadi (bot kanalda adminmi?): %s", e)
        return True    # sozlama xato bo'lsa foydalanuvchilarni to'sib qo'ymaymiz
    if ok:
        SUB_OK[uid] = time.time()
    return ok


def sub_kb():
    url = REQUIRED_CHANNEL_URL or (f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
                                   if not REQUIRED_CHANNEL.lstrip("-").isdigit() else "https://t.me")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanalga o‘tish", url=url)],
        [InlineKeyboardButton(text="✅ Obuna bo‘ldim", callback_data="sub")],
    ])


async def gate(msg: Message) -> bool:
    uid = msg.from_user.id
    USERS.add(uid)
    if ALLOWED and uid not in ALLOWED and not is_owner(uid):
        await msg.answer("🔒 Bu bot hozircha faqat ma’lum foydalanuvchilar uchun.")
        return False
    if not await subscribed(uid):
        await msg.answer("📢 Botdan foydalanish uchun kanalimizga obuna bo‘ling, so‘ng «✅ Obuna bo‘ldim» "
                         "tugmasini bosing.", reply_markup=sub_kb())
        return False
    return True


# ---------------------------------------------------------------- tarjima

async def translate_image(img, uid: int, notify=None):
    """Sahifani (kerak bo'lsa bo'laklab) Gemini'ga yuboradi va tarjimani rasmga yozadi.
    Qaytaradi: (yangi rasm, bloklar, asl til)."""
    segs = await asyncio.to_thread(typeset.split_points, img)
    w = img.width
    out = img if len(segs) == 1 else img.copy()
    all_blocks, lang = [], ""
    ctx = get_ctx(uid)
    for k, (y0, y1) in enumerate(segs):
        seg = img if len(segs) == 1 else img.crop((0, y0, w, y1))
        jpeg = await asyncio.to_thread(typeset.api_image, seg)
        async with GEM_SEM:
            res = await GEM.read_page(HTTP, jpeg, context=ctx, part=(k, len(segs)), notify=notify)
        blocks = typeset.blocks_from_model(res["blocks"], seg.width, seg.height)
        lang = lang or res.get("source_language", "")
        if blocks:
            async with CPU_SEM:
                rendered, _st = await asyncio.to_thread(typeset.render, seg, blocks)
            if len(segs) == 1:
                out = rendered
            else:
                out.paste(rendered, (0, y0))
        all_blocks.extend(blocks)
        ctx = [(b.original, b.text) for b in blocks if b.text] or ctx
    set_ctx(uid, [(b.original, b.text) for b in all_blocks if b.text])
    return out, all_blocks, lang


async def do_page(item: Item):
    msg, uid = item.msg, item.msg.from_user.id
    if remaining(uid) <= 0:
        await msg.answer(limit_text(), reply_parameters=rp(msg))
        return
    left = len(QUEUES.get(uid, []))
    status = await msg.answer("⏳ Tarjima qilinyapti…" + (f" (navbatda yana {left} ta)" if left else ""),
                              reply_parameters=rp(msg))

    async def notify(t):
        await safe_edit(status, t)

    try:
        await bot.send_chat_action(chat_id=msg.chat.id, action=ChatAction.UPLOAD_PHOTO)
        data = await download(item.file_id)
        img = await asyncio.to_thread(typeset.load_image, data)
        out, blocks, lang = await translate_image(img, uid, notify)
    except translator.GeminiError as e:
        await safe_edit(status, gem_error_text(e))
        if e.kind == "key":
            await tell_owners(f"⚠️ Gemini kaliti xatosi: <code>{html.escape(str(e))[:300]}</code>")
        return
    except Exception:
        STATS["errors"] += 1
        log.exception("sahifa xatosi")
        await safe_edit(status, "❌ Rasmni ochib bo‘lmadi yoki xatolik yuz berdi. Boshqa rasm yuborib ko‘ring.")
        return
    add_usage(uid, 1)
    shown = [b for b in blocks if b.kind != "sfx"]
    if not shown:
        await safe_edit(status, "🤷 Bu sahifada tarjima qiladigan matn topilmadi.")
        return
    jpeg = await asyncio.to_thread(typeset.to_jpeg, out, 90)
    name = os.path.splitext(os.path.basename(item.name))[0] + "_uz.jpg"
    texts = chapters.chunk_messages(f"📝 <b>Sahifa matni</b> {chapters.flag(lang)} → 🇺🇿",
                                    chapters.script_lines(blocks, lang))
    as_doc = item.as_file or out.height > 3 * out.width or len(jpeg) > 9_500_000
    cid = remember(texts, None if as_doc else jpeg, name)
    row = [InlineKeyboardButton(text="📝 Matn", callback_data=f"t:{cid}")]
    if not as_doc:
        row.append(InlineKeyboardButton(text="📎 Fayl qilib", callback_data=f"f:{cid}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[row])
    caption = f"✅ Tayyor! {chapters.flag(lang)} → 🇺🇿 · {len(shown)} ta matn"
    try:
        if as_doc:
            await msg.answer_document(BufferedInputFile(jpeg, name), caption=caption, reply_markup=kb,
                                      reply_parameters=rp(msg))
        else:
            await msg.answer_photo(BufferedInputFile(jpeg, name), caption=caption, reply_markup=kb,
                                   reply_parameters=rp(msg))
    except TelegramBadRequest as e:
        log.warning("rasm sifatida ketmadi (%s) — fayl qilib yuboramiz", e)
        await msg.answer_document(BufferedInputFile(jpeg, name), caption=caption, reply_markup=kb,
                                  reply_parameters=rp(msg))
    await safe_delete(status)


async def do_chapter(item: Item):
    msg, uid = item.msg, item.msg.from_user.id
    if remaining(uid) <= 0:
        await msg.answer(limit_text(), reply_parameters=rp(msg))
        return
    status = await msg.answer("📦 Fayl yuklab olinmoqda…", reply_parameters=rp(msg))
    tmp = tempfile.mkdtemp(prefix="bob_")
    try:
        try:
            data = await download(item.file_id, timeout=300)
            await safe_edit(status, "📂 Sahifalar ajratilmoqda…")
            pages, total = await asyncio.to_thread(chapters.extract, data, item.name, item.mime, MAX_PAGES)
            del data
        except chapters.ChapterError as e:
            await safe_edit(status, f"❌ {html.escape(str(e))}")
            return
        except Exception:
            STATS["errors"] += 1
            log.exception("bob faylini ochishda xato")
            await safe_edit(status, "❌ Faylni ochib bo‘lmadi.")
            return
        if not pages:
            await safe_edit(status, "🤷 Fayl ichida rasm topilmadi. ZIP ichida JPG/PNG/WEBP sahifalar bo‘lishi kerak.")
            return
        notes = []
        if total > len(pages):
            notes.append(f"ℹ️ Faylda {total} ta sahifa bor — bir martada {MAX_PAGES} tasini tarjima qilaman.")
        allow = remaining(uid)
        if allow < len(pages):
            notes.append(f"ℹ️ Bugungi limitingizdan {allow} ta sahifa qolgan edi — shuncha sahifani tarjima qildim.")
            pages = pages[:allow]
        n = len(pages)
        stop_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⛔ To‘xtatish",
                                                                               callback_data="stop")]])
        STATS["chapters"] += 1
        paths, script, langs = [], [], []
        stop_reason, last, found = "", 0.0, 0
        for i in range(n):
            name, raw = pages[i]
            pages[i] = (name, b"")            # xotirani bo'shatamiz
            if item.cancelled:
                stop_reason = "⛔ To‘xtatildi."
                break
            if i == 0 or time.time() - last > 4:
                last = time.time()
                await safe_edit(status, f"📖 Bob tarjima qilinyapti: <b>{i}/{n}</b> sahifa tayyor…", stop_kb)

            async def notify(t, i=i):
                await safe_edit(status, f"📖 {i}/{n} sahifa tayyor.\n{t}", stop_kb)

            img = None
            try:
                img = await asyncio.to_thread(typeset.load_image, raw)
                out, blocks, lang = await translate_image(img, uid, notify)
                if lang:
                    langs.append(lang)
            except translator.GeminiError as e:
                if e.kind in ("quota", "key", "busy"):
                    stop_reason = gem_error_text(e)
                    if e.kind == "key":
                        await tell_owners(f"⚠️ Gemini kaliti xatosi: <code>{html.escape(str(e))[:300]}</code>")
                    break
                if img is None:
                    continue
                out, blocks = img, []         # bu sahifa asl holicha qoladi
                script.append(f"--- {i + 1}-sahifa: tarjima qilinmadi")
            except Exception:
                STATS["errors"] += 1
                log.exception("bob sahifasi xatosi")
                if img is None:
                    continue
                out, blocks = img, []
            p = os.path.join(tmp, f"{i + 1:03d}.jpg")
            await asyncio.to_thread(out.convert("RGB").save, p, "JPEG", quality=88)
            paths.append(p)
            add_usage(uid, 1)
            found += sum(1 for b in blocks if b.kind != "sfx")
            lines = chapters.script_lines(blocks, as_html=False)
            script.append(f"--- {i + 1}-sahifa ---\n" + ("\n".join(lines) if lines else "(matn yo‘q)"))
            del img, out
        if not paths:
            await safe_edit(status, stop_reason or "❌ Birorta sahifani tarjima qilib bo‘lmadi.")
            return
        await safe_edit(status, f"📦 PDF va ZIP tayyorlanmoqda… ({len(paths)} sahifa)")
        stem = chapters.safe_stem(item.name)
        is_pdf = chapters.kind_of(item.name, item.mime) == "pdf"
        ext = ".cbz" if item.name.lower().endswith(".cbz") else ".zip"
        # yuborilgan formatda qaytaramiz: PDF → PDF; ZIP/CBZ → ZIP/CBZ (+ telefonda o'qish uchun PDF)
        pdfs, zips = await asyncio.to_thread(chapters.make_outputs, paths, tmp, stem, "\n\n".join(script),
                                             48 * 1024 * 1024, True, not is_pdf, ext)
        lang = max(set(langs), key=langs.count) if langs else ""
        head = (f"✅ Bob tarjima qilindi: <b>{len(paths)}</b> sahifa, {found} ta matn "
                f"{chapters.flag(lang)} → 🇺🇿")
        if len(paths) < n and not stop_reason:
            head += f"\n⚠️ {n - len(paths)} ta sahifani ochib bo‘lmadi."
        if stop_reason:
            head += "\n" + stop_reason
        if notes:
            head += "\n" + "\n".join(notes)
        first = zips if zips else pdfs
        extra = pdfs if zips else []
        for k, pth in enumerate(first):
            await msg.answer_document(FSInputFile(pth, filename=os.path.basename(pth)),
                                      caption=head if k == 0 else None,
                                      reply_parameters=rp(msg) if k == 0 else None)
        for k, pth in enumerate(extra):
            await msg.answer_document(FSInputFile(pth, filename=os.path.basename(pth)),
                                      caption="📖 Telefonda o‘qish uchun PDF ko‘rinishi." if k == 0 else None)
        await safe_delete(status)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- navbat (har bir foydalanuvchiga alohida)

async def enqueue(item: Item):
    uid = item.msg.from_user.id
    QUEUES.setdefault(uid, []).append(item)
    t = WORKERS.get(uid)
    if not t or t.done():
        WORKERS[uid] = asyncio.create_task(worker(uid))


async def worker(uid: int):
    await asyncio.sleep(1.0)  # albomdagi barcha rasmlar kelib olsin (tartib saqlanishi uchun)
    while QUEUES.get(uid):
        q = QUEUES[uid]
        q.sort(key=lambda it: it.order)
        item = q.pop(0)
        CURRENT[uid] = item
        try:
            if item.kind == "page":
                await do_page(item)
            else:
                await do_chapter(item)
        except Exception:
            STATS["errors"] += 1
            log.exception("navbatdagi ishda xato")
            try:
                await item.msg.answer("❌ Kutilmagan xatolik yuz berdi. Qayta urinib ko‘ring.")
            except Exception:
                pass
        finally:
            CURRENT.pop(uid, None)
    QUEUES.pop(uid, None)


def queued_pages(uid: int) -> int:
    n = sum(1 for it in QUEUES.get(uid, []) if it.kind == "page")
    cur = CURRENT.get(uid)
    return n + (1 if cur and cur.kind == "page" else 0)


# ---------------------------------------------------------------- matnlar

def start_text(name: str) -> str:
    lim = f"\n🎁 Har kuni {DAILY_LIMIT} ta sahifa bepul." if DAILY_LIMIT > 0 else ""
    return (f"👋 Assalomu alaykum, {html.escape(name)}!\n\n"
            "Men <b>manga tarjimon</b> botman. Manga, manhva yoki manxua sahifasini yuboring — matnlarni topib, "
            "<b>o‘zbek tiliga</b> tarjima qilaman va rasmning o‘ziga yozib qaytaraman.\n\n"
            "📷 <b>Bitta sahifa</b> — rasm qilib yuboring.\n"
            "🖼 <b>Bir nechta sahifa</b> — albom qilib yuboring, tartib bilan tarjima qilaman.\n"
            "📚 <b>Fayl</b> — PDF, ZIP yoki CBZ (butun bob, 20 MB gacha). Tarjima qilib, o‘sha formatda qaytaraman.\n\n"
            "🇯🇵 Yaponcha, 🇰🇷 koreyscha, 🇨🇳 xitoycha, 🇬🇧 inglizcha va boshqa tillardan tarjima qilaman."
            f"{lim}\n\nMaslahatlar: /yordam")


HELP_TEXT = (
    "💡 <b>Maslahatlar</b>\n\n"
    "• Sahifa qanchalik tiniq bo‘lsa, tarjima shunchalik aniq chiqadi. Telegram rasmni siqadi — imkon bo‘lsa "
    "📎 orqali <b>fayl</b> qilib yuboring.\n"
    "• Uzun webtoon (manhva) rasmlarini o‘zim bo‘laklarga bo‘lib tarjima qilaman.\n"
    "• Har bir tarjima ostida: <b>📝 Matn</b> — asl matn va tarjimasi, <b>📎 Fayl qilib</b> — siqilmagan rasm.\n"
    "• Fayl yuborsangiz, o‘sha formatda qaytaraman: PDF → PDF, ZIP/CBZ → ZIP/CBZ (qo‘shimcha PDF ham).\n"
    "• Bob tarjimasini to‘xtatish: /bekor\n"
    "• Rasm ichiga chizilgan tovush effektlari (BAM, ドン) o‘zgartirilmaydi — ular rasmning bir qismi.\n"
    "• RAR fayllarni ocha olmayman — ZIP yoki CBZ qilib yuboring."
)

HINT_TEXT = ("📷 Menga manga sahifasini rasm qilib yuboring — tarjima qilib beraman.\n"
             "Butun bob uchun ZIP, CBZ yoki PDF fayl yuboring. Yordam: /yordam")


# ---------------------------------------------------------------- buyruqlar va xabarlar

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    USERS.add(msg.from_user.id)
    await msg.answer(start_text(msg.from_user.first_name or "do‘stim"))
    if REQUIRED_CHANNEL or ALLOWED:
        await gate(msg)


@dp.message(Command("yordam", "help"))
async def cmd_help(msg: Message):
    await msg.answer(HELP_TEXT)


@dp.message(Command("bekor", "stop"))
async def cmd_cancel(msg: Message):
    uid = msg.from_user.id
    n = len(QUEUES.get(uid, []))
    if uid in QUEUES:
        QUEUES[uid].clear()
    cur = CURRENT.get(uid)
    if cur:
        cur.cancelled = True
    if cur or n:
        await msg.answer("⛔ To‘xtatildi. Tayyor bo‘lgan sahifalarni yuboraman.")
    else:
        await msg.answer("Hozir hech narsa tarjima qilinmayapti 🙂")


@dp.message(Command("stat"))
async def cmd_stat(msg: Message):
    if not is_owner(msg.from_user.id):
        await msg.answer(HINT_TEXT)
        return
    t = today()
    today_pages = sum(v for k, v in USAGE.items() if k.startswith(t))
    up = int(time.time() - STARTED)
    await msg.answer(
        "📊 <b>Statistika</b> (server ishga tushgandan beri)\n"
        f"👥 Foydalanuvchilar: {len(USERS)}\n"
        f"📄 Bugun: {today_pages} sahifa · jami: {STATS['pages']}\n"
        f"📚 Boblar: {STATS['chapters']} · ❌ Xatolar: {STATS['errors']}\n"
        f"🤖 Gemini: {html.escape(GEM.status)}\n"
        f"⏱ Ishlayapti: {up // 3600} soat {up % 3600 // 60} daqiqa\n"
        "ℹ️ Bepul serverda qayta ishga tushganda hisoblar nolga tushadi.")


@dp.message(F.photo)
async def on_photo(msg: Message):
    if not await gate(msg):
        return
    uid = msg.from_user.id
    if remaining(uid) - queued_pages(uid) <= 0:
        await msg.answer(limit_text(), reply_parameters=rp(msg))
        return
    ph = msg.photo[-1]
    await enqueue(Item(msg.message_id, "page", msg, ph.file_id, f"sahifa_{msg.message_id}.jpg", ph.file_size or 0))


@dp.message(F.document)
async def on_document(msg: Message):
    doc = msg.document
    kind = chapters.kind_of(doc.file_name or "", doc.mime_type or "")
    if kind == "rar":
        await msg.answer("📦 RAR/7z arxivni ocha olmayman. Iltimos, ZIP yoki CBZ qilib yuboring.",
                         reply_parameters=rp(msg))
        return
    if not kind:
        await msg.answer("🤔 Bu faylni tushunmadim. Rasm (JPG, PNG, WEBP), ZIP/CBZ arxiv yoki PDF yuboring.",
                         reply_parameters=rp(msg))
        return
    if not await gate(msg):
        return
    if (doc.file_size or 0) > MAX_DOWNLOAD:
        await msg.answer("📦 Fayl 20 MB dan katta — Telegram botlarga bundan kattasini yuklab olishga ruxsat "
                         "bermaydi. Bobni 2–3 qismga bo‘lib yuboring.", reply_parameters=rp(msg))
        return
    uid = msg.from_user.id
    if remaining(uid) - (queued_pages(uid) if kind == "image" else 0) <= 0:
        await msg.answer(limit_text(), reply_parameters=rp(msg))
        return
    await enqueue(Item(msg.message_id, "page" if kind == "image" else "chapter", msg, doc.file_id,
                       doc.file_name or "fayl", doc.file_size or 0, doc.mime_type or "", as_file=True))


@dp.message()
async def on_other(msg: Message):
    await msg.answer(HINT_TEXT)


@dp.callback_query(F.data.startswith("t:"))
async def cb_text(c: CallbackQuery):
    e = CACHE.get(c.data[2:])
    if not e:
        await c.answer("Bu sahifa eskirgan — qayta yuboring.", show_alert=True)
        return
    await c.answer()
    reply = None
    if c.message is not None and getattr(c.message, "message_id", None):
        reply = ReplyParameters(message_id=c.message.message_id, allow_sending_without_reply=True)
    for t in e["text"]:
        await bot.send_message(c.from_user.id, t, reply_parameters=reply)


@dp.callback_query(F.data.startswith("f:"))
async def cb_file(c: CallbackQuery):
    e = CACHE.get(c.data[2:])
    if not e or not e.get("file"):
        await c.answer("Fayl eskirgan — sahifani qayta yuboring.", show_alert=True)
        return
    await c.answer("Yuboryapman…")
    reply = None
    if c.message is not None and getattr(c.message, "message_id", None):
        reply = ReplyParameters(message_id=c.message.message_id, allow_sending_without_reply=True)
    await bot.send_document(c.from_user.id, BufferedInputFile(e["file"], e["name"]), reply_parameters=reply)


@dp.callback_query(F.data == "stop")
async def cb_stop(c: CallbackQuery):
    uid = c.from_user.id
    cur = CURRENT.get(uid)
    if cur:
        cur.cancelled = True
        if uid in QUEUES:
            QUEUES[uid].clear()
        await c.answer("To‘xtatilyapti — tayyor sahifalarni yuboraman.")
    else:
        await c.answer("Allaqachon tugagan.")


@dp.callback_query(F.data == "sub")
async def cb_sub(c: CallbackQuery):
    SUB_OK.pop(c.from_user.id, None)
    if await subscribed(c.from_user.id):
        await c.answer("Rahmat! Endi sahifa yuborishingiz mumkin ✅", show_alert=True)
        try:
            await c.message.delete()
        except Exception:
            pass
    else:
        await c.answer("Hali obuna bo‘lmagansiz 🙂", show_alert=True)


# ---------------------------------------------------------------- server

async def health(_req):
    return web.Response(text="ok")


async def sinov(req: web.Request):
    """Tekshirish uchun: POST /sinov (X-Test-Key sarlavhasi bilan, tanasi — rasm). ?json=1 — bloklar ro'yxati."""
    if not TEST_KEY or req.headers.get("X-Test-Key") != TEST_KEY:
        return web.Response(status=404)
    try:
        data = await req.read()
        img = await asyncio.to_thread(typeset.load_image, data)
        out, blocks, lang = await translate_image(img, 0)
    except translator.GeminiError as e:
        return web.json_response({"error": str(e), "kind": e.kind}, status=502)
    except Exception as e:
        log.exception("sinov")
        return web.json_response({"error": repr(e)}, status=500)
    if req.query.get("json"):
        return web.json_response({"lang": lang, "model": GEM.last_good, "blocks": [
            {"box": list(b.box), "kind": b.kind, "original": b.original, "translation": b.text} for b in blocks]})
    return web.Response(body=await asyncio.to_thread(typeset.to_jpeg, out, 90), content_type="image/jpeg")


async def keepalive():
    """Bepul serverda 15 daqiqa so'rov kelmasa, server uxlaydi. Uzun bob tarjimasi paytida o'zimizni uyg'oq tutamiz."""
    while True:
        await asyncio.sleep(240)
        if PUBLIC_URL and (QUEUES or CURRENT) and HTTP:
            try:
                async with HTTP.get(PUBLIC_URL + "/health", timeout=aiohttp.ClientTimeout(total=20)) as r:
                    await r.read()
            except Exception:
                pass


async def on_startup():
    global HTTP
    HTTP = aiohttp.ClientSession()
    asyncio.create_task(keepalive())
    log.info("Gemini: %s", await GEM.check(HTTP))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Botni boshlash"),
            BotCommand(command="yordam", description="Maslahatlar"),
            BotCommand(command="bekor", description="Bob tarjimasini to‘xtatish"),
        ])
        me = await bot.get_me()
        log.info("Telegram bot ulandi: @%s", me.username)
    except Exception as e:
        log.error("Telegram bilan aloqa yo‘q yoki token noto‘g‘ri: %s", e)


async def on_shutdown():
    if HTTP:
        await HTTP.close()


def main():
    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/sinov", sinov)
    if bot is None:
        log.error("BOT_TOKEN kiritilmagan yoki noto‘g‘ri — Render → Environment bo‘limida BOT_TOKEN ni qo‘shing. "
                  "Hozircha server faqat /health ga javob beradi.")
        web.run_app(app, host="0.0.0.0", port=PORT)
        return
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    if PUBLIC_URL:
        path = "/tg/" + hashlib.sha256(TOKEN.encode()).hexdigest()[:24]
        secret = hashlib.sha256(("maxfiy:" + TOKEN).encode()).hexdigest()[:48]

        async def set_hook():
            for attempt in range(5):
                try:
                    await bot.set_webhook(PUBLIC_URL + path, secret_token=secret,
                                          allowed_updates=dp.resolve_used_update_types())
                    log.info("Webhook o‘rnatildi: %s/tg/…", PUBLIC_URL)
                    return
                except Exception as e:
                    log.error("Webhook o‘rnatilmadi (%s) — qayta urinamiz", e)
                    await asyncio.sleep(5 * (attempt + 1))

        dp.startup.register(set_hook)
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=path)
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        async def run():  # kompyuterda sinash: polling + /health
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", PORT).start()
            await bot.delete_webhook(drop_pending_updates=False)
            log.info("Polling rejimi (PUBLIC_URL yo‘q)")
            await dp.start_polling(bot)

        asyncio.run(run())


if __name__ == "__main__":
    main()
