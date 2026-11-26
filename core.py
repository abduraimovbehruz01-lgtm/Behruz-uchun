"""Umumiy qism: sozlamalar, bot obyekti, katalog, Gemini, yordamchi funksiyalar."""
import asyncio
import html
import logging
import os
import time

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ReplyParameters

import catalog
import store
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
DAILY_LIMIT = _int("DAILY_LIMIT", 40)          # adminlarning sinov tarjimasi uchun (bosh adminga cheksiz)
MAX_PAGES = max(1, _int("MAX_PAGES", 80))
PARALLEL = max(1, _int("GEMINI_PARALLEL", 2))
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()
STORAGE_CHAT_ENV = os.environ.get("STORAGE_CHAT", "").strip()
TEST_KEY = os.environ.get("TEST_KEY", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
PORT = _int("PORT", 10000)
MAX_DOWNLOAD = 20 * 1024 * 1024

GEM = translator.Translator(os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_MODEL") or None)
GEM_SEM = asyncio.Semaphore(PARALLEL)
CPU_SEM = asyncio.Semaphore(1)
CAT = catalog.Catalog(store.make_store(), owners=OWNERS)

try:
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if TOKEN else None
except Exception as _e:
    log.error("BOT_TOKEN noto‘g‘ri: %s", _e)
    bot = None

STATE = {"http": None, "username": "ManhoraBot", "owner_note": 0.0}
CONTEXT: dict[int, tuple[float, list]] = {}
SUB_OK: dict[int, float] = {}
esc = html.escape


def http() -> aiohttp.ClientSession:
    if STATE["http"] is None or getattr(STATE["http"], "closed", False):
        STATE["http"] = aiohttp.ClientSession()
    return STATE["http"]


def rp(msg) -> ReplyParameters:
    return ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)


def kb(rows) -> InlineKeyboardMarkup:
    """[[("matn", "callback"), ...], ...] → inline klaviatura. URL uchun ("matn", "url:https://...")."""
    out = []
    for row in rows:
        r = []
        for text, data in row:
            if data.startswith("url:"):
                r.append(InlineKeyboardButton(text=text, url=data[4:]))
            else:
                r.append(InlineKeyboardButton(text=text, callback_data=data))
        if r:
            out.append(r)
    return InlineKeyboardMarkup(inline_keyboard=out)


async def safe_edit(msg, text, markup=None):
    if msg is None:
        return
    try:
        await msg.edit_text(text, reply_markup=markup)
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


async def retry(factory, tries=4):
    """Telegram «juda tez» (429) desa — aytilgan vaqt kutib, qayta yuboradi."""
    for i in range(tries):
        try:
            return await factory()
        except TelegramRetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 0.5)
    return await factory()


async def download(file_id: str, timeout: int = 180) -> bytes:
    buf = await bot.download(file_id, timeout=timeout)
    buf.seek(0)
    return buf.read()


def gem_error_text(e: translator.GeminiError) -> str:
    if e.kind == "quota":
        return ("😔 Bugun Google Gemini’ning bepul limiti tugadi.\n"
                "U har kuni soat 13:00 atrofida (Toshkent vaqti) yangilanadi — keyin yana urinib ko‘ring.")
    if e.kind == "key":
        return "⚠️ Gemini kaliti ishlamayapti. Render’dagi GEMINI_API_KEY ni tekshirish kerak."
    if e.kind == "blocked":
        return "⚠️ Bu sahifani Google xavfsizlik filtri o‘tkazmadi."
    if e.kind == "busy":
        return "⏳ Google serverlari hozir juda band. Birozdan keyin qayta urinib ko‘ring."
    return "❌ Tarjima qilib bo‘lmadi. Qayta urinib ko‘ring."


async def tell_owners(text: str, force=False):
    if not OWNERS or (not force and time.time() - STATE["owner_note"] < 3600):
        return
    STATE["owner_note"] = time.time()
    for oid in OWNERS:
        try:
            await bot.send_message(oid, text)
        except Exception:
            pass


def storage_chat():
    v = STORAGE_CHAT_ENV or CAT.cfg.get("storage_chat")
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


# ---------------------------------------------------------------- majburiy obuna

def channels():
    out = []
    if REQUIRED_CHANNEL:
        out.append({"id": REQUIRED_CHANNEL, "url": REQUIRED_CHANNEL_URL})
    for c in CAT.cfg.get("channels") or []:
        if isinstance(c, dict) and c.get("id"):
            out.append(c)
    return out


async def missing_channels(uid: int):
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxati."""
    chs = channels()
    if not chs or CAT.is_admin(uid) or time.time() - SUB_OK.get(uid, 0) < 600:
        return []
    miss = []
    for c in chs:
        cid = c["id"]
        chat = int(cid) if str(cid).lstrip("-").isdigit() else cid
        try:
            m = await bot.get_chat_member(chat_id=chat, user_id=uid)
            ok = m.status in ("creator", "administrator", "member") or bool(getattr(m, "is_member", False))
        except Exception as e:
            log.warning("obuna tekshiruvi (%s): %s", cid, e)
            ok = True  # bot kanalda admin bo'lmasa — foydalanuvchini to'smaymiz
        if not ok:
            miss.append(c)
    if not miss:
        SUB_OK[uid] = time.time()
    return miss


def sub_markup(miss, after=""):
    rows = []
    for i, c in enumerate(miss, 1):
        url = c.get("url") or (f"https://t.me/{str(c['id']).lstrip('@')}" if not str(c["id"]).lstrip("-").isdigit()
                               else "https://t.me")
        rows.append([(f"📢 {i}-kanal" if len(miss) > 1 else "📢 Kanalga o‘tish", "url:" + url)])
    rows.append([("✅ Obuna bo‘ldim", "sub" + (":" + after if after else ""))])
    return kb(rows)


# ---------------------------------------------------------------- tarjima

def get_ctx(uid):
    t, lines = CONTEXT.get(uid, (0.0, []))
    return lines if time.time() - t < 1800 else []


def set_ctx(uid, lines):
    if lines:
        CONTEXT[uid] = (time.time(), lines[-14:])


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
            res = await GEM.read_page(http(), jpeg, context=ctx, part=(k, len(segs)), notify=notify)
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


def storage_pieces(img):
    """Ombor uchun: uzun webtoon rasmini telefonda o'qiladigan bo'laklarga bo'ladi va JPEG qiladi."""
    w, h = img.size
    k = min(1.0, 1600 / max(1, w))
    if k < 1:
        img = img.resize((round(w * k), max(1, round(h * k))), typeset._RS.LANCZOS)
        w, h = img.size
    out = []
    for y0, y1 in typeset.split_points(img, ratio=2.2, target=1.6):
        piece = img if (y0, y1) == (0, h) else img.crop((0, y0, w, y1))
        out.append(typeset.to_jpeg(piece, 88))
    return out


async def upload_photos(jpegs, chat, caption=""):
    """Rasmlarni ombor kanaliga 10 tadan albom qilib yuklaydi. Qaytaradi: file_id lar ro'yxati."""
    ids = []
    for i in range(0, len(jpegs), 10):
        part = jpegs[i:i + 10]
        if len(part) == 1:
            m = await retry(lambda: bot.send_photo(chat, BufferedInputFile(part[0], f"{i + 1}.jpg"),
                                                   caption=caption if i == 0 else None))
            ids.append(m.photo[-1].file_id)
        else:
            media = [InputMediaPhoto(media=BufferedInputFile(b, f"{i + j + 1}.jpg"),
                                     caption=caption if i == 0 and j == 0 else None) for j, b in enumerate(part)]
            msgs = await retry(lambda: bot.send_media_group(chat, media=media))
            ids.extend(m.photo[-1].file_id for m in msgs)
        await asyncio.sleep(1.2)
    return ids
