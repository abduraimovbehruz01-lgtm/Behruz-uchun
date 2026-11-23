"""Kino-video bot: kinoni yuborasiz → bot hikoya matnini yozadi → siz o'qib ovoz yuborasiz →
bot sahnalarni qirqib, rang berib, ovozingiz bilan qisqa video yasaydi."""
import asyncio
import hashlib
import html
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import eleven
import gemini
import music
import video

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

# ---------------- Sozlamalar (Render → Environment) ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or None
ELEVEN_KEY = os.environ.get("ELEVEN_API_KEY", "").strip()
OWNER_IDS = {int(x) for x in re.findall(r"\d+", os.environ.get("OWNER_IDS", ""))}
WATERMARK = os.environ.get("WATERMARK", "").strip()  # videodagi qo'shimcha yozuv (standart: yo'q)
if WATERMARK.lower() in ("-", "0", "off", "none", "yoq", "yo'q", "yo‘q"):
    WATERMARK = ""
CTA_TEXT = os.environ.get("CTA_TEXT", "Kinoni ko‘rmoqchi bo‘lsangiz, profilimdagi botga {kod} kodini yuboring.")
CTA_LINE1 = os.environ.get("CTA_LINE1", "@behruzfilmbot").strip()
CAPTION_CTA = os.environ.get("CAPTION_CTA", "🔢 Kod: {kod} — kinoni ko‘rish uchun profilimdagi botga yuboring 🍿")
SPOKEN_CTA = os.environ.get("CTA_SPOKEN", "0") == "1"  # hikoya oxirida «...botga kodni yuboring» gapi (standart: yo'q)
CODE_LABEL = {"kino": os.environ.get("CODE_LABEL_KINO", "Kino kodi: {kod}"),
              "anime": os.environ.get("CODE_LABEL_ANIME", "Anime kodi: {kod}")}
ANIME_TAGS = os.environ.get("ANIME_HASHTAGS", "#anime #animelar #animeuz")
BASE_TAGS = os.environ.get("BASE_HASHTAGS", "#kino #kinolar #film #filmlar")
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
WORK_DIR = os.environ.get("WORK_DIR", "/var/data/kino")
MAX_BYTES = int(float(os.environ.get("MAX_FILE_GB", "2")) * 1024 ** 3)
DL_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "6"))
BOT_API_LIMIT = 20 * 1024 * 1024
TAIL = 0.8  # oxirgi gapdan keyin video yana shuncha soniya davom etadi
DEFAULT_LEN = int(os.environ.get("VIDEO_LEN", "120"))  # hikoya uzunligi (soniya), /uzunlik bilan o'zgaradi
LEN_CHOICES = (60, 90, 120, 180)
VIDEO_EXT = re.compile(r"\.(mkv|mp4|avi|mov|m4v|webm|ts|wmv|flv|mpg|mpeg|3gp)$", re.I)

bot = Bot(BOT_TOKEN or "0:none", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
gem = gemini.Gemini(GEMINI_API_KEY, GEMINI_MODEL)
HEAVY = asyncio.Semaphore(1)  # og'ir ffmpeg ishlari navbat bilan (bepul serverda 0.1 CPU)
HTTP: aiohttp.ClientSession | None = None
BIG = None
if API_ID and API_HASH and BOT_TOKEN:
    import tgdl
    BIG = tgdl.BigFiles(BOT_TOKEN, API_ID, API_HASH, DL_WORKERS)


@dataclass
class Job:
    uid: int
    chat_id: int
    dir: str
    msg_id: int
    file_id: str
    size: int
    file_name: str
    status: "Status | None" = None
    name: str | None = None
    code: str | None = None
    asking: str | None = None
    movie: str | None = None
    duration: float = 0.0
    has_audio: bool = True
    out_px: int = 1080
    chunks: list | None = None
    moments: list | None = None
    summary: str = ""
    characters: list = field(default_factory=list)
    target_sec: int = 120
    script: list | None = None
    title: str = ""
    caption: str = ""
    tags: list = field(default_factory=list)
    voice: str | None = None
    voice_parts: list = field(default_factory=list)   # [(fayl, soniya)] — ovoz bir nechta xabarda kelsa
    voice_anchors: dict | None = None                 # ElevenLabs bergan gap boshlanish vaqtlari
    expect_parts: int = 0                              # «1/2», «2/2» izohlaridan: nechta qism kutilyapti
    render_parts: list | None = None                   # joriy yig'ishda ishlatilgan ovoz qismlari
    render_started: float = 0.0
    part_msg: dict = field(default_factory=dict)       # ovoz fayli → Telegram xabar raqami (tartib va takrorni bilish)
    voice_timer: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    concat: str | None = None
    bg: str | None = None
    square: str | None = None
    timing: dict | None = None
    layout: str = "square"
    music_id: str | None = None
    music_name: str = ""
    no_music: bool = False
    busy: str | None = None
    task: asyncio.Task | None = None
    last: float = field(default_factory=time.time)
    variant: int = 0
    mode: str = "story"                                # oxirgi video: "story" (hikoya) yoki "plain" (oddiy)
    kind: str = "kino"                                 # "kino" yoki "anime" — videoning pastidagi yozuv uchun
    kind_manual: bool = False                          # foydalanuvchi o'zi tanlagan bo'lsa, Gemini o'zgartirmaydi
    plain: dict = field(default_factory=dict)          # oddiy video: sahnalar, kesilgan video, ovoz, tayyor fayl


JOBS: dict[int, Job] = {}
PENDING_AUDIO: dict[int, dict] = {}   # uid → {xabar raqami: fayl} — «diktor ovozimi yoki musiqami?» deb so'ralganlar
RESTART_WINDOW = 120   # yig'ish boshlanganidan keyin shuncha soniya ichida yana qism kelsa — qaytadan yig'amiz
PART_WAIT = 120        # «1/2» kabi belgi bo'lsa, qolgan qismlarni shuncha soniyagacha kutamiz
MUSIC_SEM = asyncio.Semaphore(1)      # qo'shiqlar birma-bir tahlil qilinadi
PLAIN_MIN = int(os.environ.get("PLAIN_MIN_SEC", "45"))    # oddiy video: eng qisqasi (soniya)
PLAIN_MAX = int(os.environ.get("PLAIN_MAX_SEC", "120"))   # oddiy video: eng uzuni (soniya)
MUSIC_DIR = os.path.join(WORK_DIR, "music")
GEMINI_STATUS = "tekshirilmagan"
ELEVEN_STATUS = ""


class Status:
    """Bitta xabarni tahrirlab, jarayonni ko'rsatib turadi (Telegram cheklovlari uchun siyraklashtirilgan)."""

    def __init__(self, chat_id, message_id):
        self.chat_id, self.message_id = chat_id, message_id
        self.text, self.t = None, 0.0
        self._sched = False

    async def set(self, text, force=False):
        now = time.monotonic()
        if text == self.text or (not force and now - self.t < 4):
            return
        self.text, self.t = text, now
        try:
            await bot.edit_message_text(text=text, chat_id=self.chat_id, message_id=self.message_id)
        except (TelegramBadRequest, TelegramRetryAfter):
            pass
        except Exception as e:
            log.warning("status: %r", e)

    def soon(self, text):
        """Sinxron joydan (masalan, yuklash jarayoni) chaqiriladi; 5 soniyada ko'pi bilan bir marta yangilaydi."""
        if self._sched or time.monotonic() - self.t < 5:
            return
        self._sched = True

        async def go():
            try:
                await self.set(text, force=True)
            finally:
                self._sched = False
        asyncio.create_task(go())

    async def delete(self):
        try:
            await bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except Exception:
            pass


def allowed(uid):
    return not OWNER_IDS or uid in OWNER_IDS


def esc(s):
    return html.escape(str(s), quote=False)


def short_err(e):
    s = str(e) or type(e).__name__
    return esc(s[:350])


def human_mb(n):
    return f"{n / 1024 / 1024:.0f} MB" if n < 1024 ** 3 else f"{n / 1024 ** 3:.2f} GB"


def split_name_code(text):
    """«Nomi, 125» yoki «Nomi | 125» → (nomi, kod). Faqat nom bo'lsa → (nomi, None)."""
    m = re.match(r"^(.*?)\s*[,|;\n]+\s*(?:kod(?:i)?[:\s]*)?#?(\w*\d\w*)\s*$", text.strip(), re.I | re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()[:120], m.group(2)
    return text.strip()[:120], None


def clean_code(text):
    t = re.sub(r"(?i)\bkod(i)?\b[:\s]*", "", text.strip()).lstrip("#").strip()
    return t[:20]


def split_sentences(text):
    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip(" -•\t")]
    if len(lines) >= 2:
        return lines
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]


def cta_item(job):
    dur = job.duration
    strong = [m["t"] for m in (job.moments or []) if m.get("kuchli") and m["t"] < dur * 0.92]
    if strong:
        t = strong[-1]
        a, b = max(0.0, t - 4), min(dur, t + 26)
    elif job.script:
        story = [s for s in job.script if not s.get("cta")]
        a, b = story[-1]["start"], story[-1]["end"]
    else:
        a, b = dur * 0.7, dur * 0.7 + 30
    return {"matn": CTA_TEXT.format(kod=job.code), "start": a, "end": b, "cta": True}


async def write_checked(job):
    """Matn yozdiradi; juda qisqa chiqsa (kerakligining 75% idan kam) — bir marta uzunroq qilib qayta yozdiradi."""
    res = await gem.write_script(HTTP, job.name, job.duration, job.moments, job.variant, job.target_sec,
                                 job.summary, job.characters)
    words = sum(len((x.get("matn") or "").split()) for x in res.get("jumlalar") or [])
    need = job.target_sec * 2.0
    if words < 0.75 * need:
        log.info("matn qisqa: %d so'z (kerak ~%d) — qayta yozdiriladi", words, need)
        try:
            res2 = await gem.write_script(HTTP, job.name, job.duration, job.moments, job.variant, job.target_sec,
                                          job.summary, job.characters, too_short=words)
            w2 = sum(len((x.get("matn") or "").split()) for x in res2.get("jumlalar") or [])
            if w2 > words:
                res, words = res2, w2
        except Exception as e:
            log.warning("qayta yozish bo'lmadi: %r", e)
    log.info("matn: %d so'z, %d gap", words, len(res.get("jumlalar") or []))
    return res


def build_script(job, res):
    dur = job.duration
    items = []
    for it in res.get("jumlalar") or []:
        t = re.sub(r"\s+", " ", (it.get("matn") or "")).strip()
        if t:
            items.append([t, gemini.parse_ts(it.get("boshlanish")), gemini.parse_ts(it.get("tugash"))])
    if len(items) < 2:
        raise ValueError("Gemini matn qaytarmadi")
    n = len(items)
    for i, it in enumerate(items):
        a, b = it[1], it[2]
        if a is None or a < 0 or a > dur - 2:
            a = dur * (0.05 + 0.85 * i / max(1, n - 1))
        if b is None or b <= a or b > dur:
            b = a + 20
        if b - a < 6:
            b = a + 6
        it[1], it[2] = round(a, 2), round(min(b, dur), 2)
    script = [{"matn": t, "start": a, "end": b} for t, a, b in items]
    job.title = (res.get("nomi") or job.name or "").strip()
    if not job.kind_manual and res.get("turi") in CODE_LABEL:
        job.kind = res["turi"]
    job.caption = hide_title((res.get("izoh") or "").strip(), job)
    job.tags = clean_tags(res.get("heshteglar") or [], job)
    if job.code and SPOKEN_CTA:
        script.append(cta_item(job))
    return script


def _title_words(job):
    """Kino nomi (yilsiz) — tavsif va heshteglarda chiqmasligi uchun."""
    res = []
    for t in (job.title, job.name):
        t = re.sub(r"[\(\[]?\b(19|20)\d\d\b[\)\]]?", "", t or "").strip(" -:,.")
        if len(t) >= 3:
            res.append(t)
    return res


def hide_title(text, job):
    for t in _title_words(job):
        text = re.sub(re.escape(t), "bu kino", text, flags=re.I)
    # gap boshida bo'lsa — bosh harf bilan
    return re.sub(r"(^|[.!?…]\s+)bu kino", lambda m: m.group(1) + "Bu kino", text)


def clean_tags(tags, job):
    banned = {re.sub(r"[\W_]+", "", t.lower()) for t in _title_words(job)}
    out, seen = [], set()
    base = ANIME_TAGS if job.kind == "anime" else BASE_TAGS
    for t in base.split() + [str(x) for x in tags]:
        t = "#" + re.sub(r"[^\w]+", "", t.strip().lstrip("#"), flags=re.UNICODE)
        key = t[1:].lower()
        if len(key) < 2 or key in seen or any(b and (b in key or key in b) and len(key) > 3 for b in banned):
            continue
        seen.add(key)
        out.append(t)
    return out[:16]


def insta_caption(job, text=None):
    parts = []
    text = text or job.caption
    if text:
        parts.append(text)
    if job.code:
        cta = CAPTION_CTA.format(kod=job.code)
        parts.append(cta.replace("kinoni", "animeni") if job.kind == "anime" else cta)
    if job.tags:
        parts.append(" ".join(job.tags))
    return "\n\n".join(parts)


def code_label(job):
    """Videoning pastidagi yozuv: «Kino kodi: 125» yoki «Anime kodi: 125» (kod bo'lmasa — bo'sh)."""
    if not job.code:
        return ""
    return CODE_LABEL.get(job.kind, CODE_LABEL["kino"]).format(kod=job.code)


def kind_button(job):
    return (f"🏷 {code_label(job) or 'Kino kodi'}  🔁", "kind")


def remap_script(job, sentences):
    """Foydalanuvchi matnni o'zgartirsa, yangi gaplarga eski sahna oraliqlarini tartib bo'yicha beradi."""
    story = [s for s in job.script if not s.get("cta")] or job.script
    cta = next((s for s in job.script if s.get("cta")), None)
    new_cta = None
    if cta and job.code and len(sentences) >= 2 and job.code in sentences[-1]:
        new_cta = dict(cta, matn=sentences[-1])
        sentences = sentences[:-1]
    m, n = len(sentences), len(story)
    out = []
    for j, t in enumerate(sentences):
        k = 0 if m == 1 or n == 1 else round(j * (n - 1) / (m - 1))
        out.append({"matn": t, "start": story[k]["start"], "end": story[k]["end"]})
    if new_cta:
        out.append(new_cta)
    return out


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


async def remember_job(job):
    """Kino ma'lumotini chatdagi qadalgan faylga yozib qo'yadi — server qayta ishga tushsa, «Davom ettirish» mumkin bo'lsin."""
    try:
        lib = await music.load(bot, job.chat_id, WORK_DIR)
        meta = {"msg_id": job.msg_id, "file_id": job.file_id, "size": job.size, "file_name": job.file_name,
                "name": job.name, "code": job.code, "t": int(time.time())}
        if job.script:  # matn va lahzalar ham saqlanadi — davom ettirishda Gemini qayta ishlamaydi
            meta.update(script=job.script, moments=job.moments, summary=job.summary, characters=job.characters,
                        title=job.title, caption=job.caption, tags=job.tags, target_sec=job.target_sec,
                        kind=job.kind, kind_manual=job.kind_manual, music_id=job.music_id, no_music=job.no_music)
            if job.plain.get("cands"):  # oddiy video sahnalari — qayta ishga tushsa ham o'sha sahna qoladi
                meta["plain"] = {"cands": job.plain["cands"], "idx": job.plain.get("idx", -1),
                                 "used": [list(x) for x in job.plain.get("used", [])]}
        lib["job"] = meta
        music.save_soon(bot, job.chat_id)
    except Exception as e:
        log.warning("ish ma'lumoti saqlanmadi: %r", e)


async def resume_job(uid, chat_id):
    """Saqlangan kino ma'lumotidan ishni qaytadan boshlaydi (kino Telegram'dan qayta yuklanadi)."""
    lib = await music.load(bot, chat_id, WORK_DIR)
    meta = lib.get("job") or {}
    if not meta.get("msg_id"):
        return await bot.send_message(chat_id, "Davom ettiradigan ish topilmadi. Kinoni qaytadan yuboring 🎬")
    old = JOBS.pop(uid, None)
    if old:
        await drop_job(old)
    d = os.path.join(WORK_DIR, f"{uid}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    job = Job(uid=uid, chat_id=chat_id, dir=d, msg_id=meta["msg_id"], file_id=meta.get("file_id", ""),
              size=int(meta.get("size") or 0), file_name=meta.get("file_name") or "kino.mp4",
              name=meta.get("name"), code=meta.get("code"))
    if meta.get("script"):
        job.script, job.moments = meta["script"], meta.get("moments") or []
        job.summary, job.characters = meta.get("summary") or "", meta.get("characters") or []
        job.title, job.caption, job.tags = meta.get("title") or "", meta.get("caption") or "", meta.get("tags") or []
        job.target_sec = int(meta.get("target_sec") or DEFAULT_LEN)
        job.kind, job.kind_manual = meta.get("kind") or "kino", bool(meta.get("kind_manual"))
        job.music_id, job.no_music = meta.get("music_id"), bool(meta.get("no_music"))
        pl = meta.get("plain") or {}
        if pl.get("cands"):
            job.plain.update(cands=pl["cands"], idx=int(pl.get("idx", -1)),
                             used=[tuple(x) for x in pl.get("used") or []])
        if not SPOKEN_CTA:
            job.script = [x for x in job.script if not x.get("cta")] or job.script
    JOBS[uid] = job
    sm = await bot.send_message(chat_id, f"🔁 Davom ettiryapman: kinoni qaytadan yuklab olyapman ({human_mb(job.size)})…")
    job.status = Status(chat_id, sm.message_id)
    job.task = asyncio.create_task(analyse(job))
    await ask_next(job)


async def drop_job(job):
    if job.task and not job.task.done():
        job.task.cancel()
    shutil.rmtree(job.dir, ignore_errors=True)


async def ask_next(job):
    if not job.name:
        job.asking = "name"
        await bot.send_message(job.chat_id, "🎬 Kino nomini yozing.\nMasalan: <i>Pet Sematary 2019</i>")
    elif not job.code:
        job.asking = "code"
        await bot.send_message(job.chat_id, "🔢 Kino kodini yozing (videoning pastida chiqadi).\nMasalan: <i>125</i>")
    else:
        job.asking = None


async def wait_for(cond, seconds):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        await asyncio.sleep(1)
    return cond()


# ---------------- 1-bosqich: kinoni olish va matn yozish ----------------

async def download_movie(job):
    st = job.status
    ext = os.path.splitext(job.file_name)[1].lower() or ".mp4"
    path = os.path.join(job.dir, "movie" + (ext if len(ext) <= 6 else ".mp4"))
    free = shutil.disk_usage(job.dir).free
    if free < job.size + 300 * 1024 * 1024:
        raise RuntimeError(f"Serverda joy yetmayapti (bo‘sh: {human_mb(free)})")
    t0 = time.time()
    if BIG is not None and job.size > 0:
        def progress(done, total):
            job.last = time.time()
            pct = done * 100 // max(1, total)
            speed = done / max(1.0, time.time() - t0)
            st.soon(f"⬇️ Yuklab olinmoqda: {pct}% ({human_mb(done)} / {human_mb(total)}, {speed / 1048576:.1f} MB/s)")
        await BIG.download(job.msg_id, path, progress)
    elif job.size <= BOT_API_LIMIT:
        await bot.download(job.file_id, destination=path, timeout=600)
    else:
        raise RuntimeError("Fayl 20 MB dan katta. Serverga API_ID va API_HASH kiritilmagan.")
    log.info("yuklandi %s %.0fs", human_mb(os.path.getsize(path)), time.time() - t0)
    return path


async def analyse(job):
    """Kinoni yuklash (kerak bo'lsa) → ovozni ajratish → Gemini lahzalar → matn → foydalanuvchiga yuborish."""
    st = job.status
    try:
        if not job.movie:
            await st.set("⬇️ Yuklab olinmoqda…", force=True)
            path = await download_movie(job)
            info = await video.probe(path)
            if not info["video"] or info["duration"] < 20:
                raise RuntimeError("Bu fayl kino emasga o‘xshaydi (video topilmadi yoki juda qisqa)")
            job.movie, job.duration, job.has_audio = path, info["duration"], info["audio"]
            job.out_px = video.out_size(info.get("side", 0))
            if not job.has_audio:
                raise RuntimeError("Kinoda ovoz yo‘lagi yo‘q — Gemini voqealarni eshita olmaydi")
        job.last = time.time()
        if not job.chunks and not job.moments:
            await st.set("🎧 Kinoning ovozi ajratilmoqda… (bepul serverda 5–10 daqiqa)", force=True)
            async with HEAVY:
                job.chunks = await video.extract_audio_chunks(job.movie, job.dir, job.duration)
        if not await wait_for(lambda: job.name, 1800):
            job.name = os.path.splitext(job.file_name)[0]
        if not job.moments:
            moments, summaries, chars = [], [], {}
            for k, (cpath, mime, start, length) in enumerate(job.chunks):
                n = len(job.chunks)
                await st.set(f"🤖 Gemini kinoni eshityapti{f' ({k + 1}/{n})' if n > 1 else ''}… Bu bir necha daqiqa oladi.",
                             force=True)
                part = await gem.scene_log(HTTP, cpath, mime, job.name, k, n, start, length)
                moments += part["moments"]
                if part["summary"]:
                    summaries.append(part["summary"])
                for c in part["characters"]:
                    chars.setdefault(c["ism"].lower(), c)
                job.last = time.time()
            if len(moments) < 3:
                raise RuntimeError("Gemini kinodan yetarli voqea topmadi")
            moments.sort(key=lambda m: m["t"])
            job.moments, job.summary, job.characters = moments, " ".join(summaries), list(chars.values())
            for cpath, *_ in job.chunks or []:
                try:
                    os.remove(cpath)  # diskni bo'shatish: lahzalar topildi, ovoz bo'laklari endi kerak emas
                except OSError:
                    pass
        if not job.script:
            await st.set("✍️ Hikoya matni yozilmoqda…", force=True)
            if not job.code:
                await wait_for(lambda: job.code, 1800)
            job.target_sec = await get_len(job.chat_id)
            res = await write_checked(job)
            job.script = build_script(job, res)
        job.last = time.time()
        asyncio.create_task(remember_job(job))
        await st.set("✅ Matn tayyor! Pastda 👇", force=True)
        await send_script(job)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("analyse xato")
        await st.set(f"❌ Xatolik: {short_err(e)}", force=True)
        await bot.send_message(job.chat_id, "Qayta urinib ko‘rasizmi?", reply_markup=kb([("🔁 Qayta urinish", "retry")]))


async def send_script(job):
    body = "\n\n".join(esc(s["matn"]) for s in job.script)
    title = f"🎬 <b>{esc(job.title)}</b>\n\n" if job.title else ""
    text = (f"{title}🎙 <b>Shu matnni o‘qib, ovozli xabar yuboring:</b>\n\n{body}\n\n"
            "💡 <i>Har gapdan keyin bir lahza to‘xtab o‘qing — sahnalar ovozga aniq mos tushadi. "
            "Matnni o‘zgartirmoqchi bo‘lsangiz, o‘zgartirilgan matnni yozib yuboring.</i>")
    text += "\n\n🎵 <i>Ovozsiz video kerak bo‘lsa — «Oddiy video»: kinoning eng qiziq sahnasi + qo‘shiq.</i>"
    rows = [[("🎵 Oddiy video (ovozsiz)", "plain")], [("🔄 Boshqa matn", "regen")]]
    if job.code:
        rows.append([kind_button(job)])
    if ELEVEN_KEY:
        rows.insert(0, [("🤖 ElevenLabs ovozida o‘qit", "tts")])
    await bot.send_message(job.chat_id, text, reply_markup=kb(*rows))


async def regen(job):
    st = Status(job.chat_id, (await bot.send_message(job.chat_id, "✍️ Yangi matn yozilmoqda…")).message_id)
    try:
        job.variant += 1
        job.target_sec = await get_len(job.chat_id)
        res = await write_checked(job)
        job.script = build_script(job, res)
        asyncio.create_task(remember_job(job))
        await st.delete()
        await send_script(job)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("regen xato")
        await st.set(f"❌ Xatolik: {short_err(e)}", force=True)


# ---------------- 2-bosqich: ovoz kelgach videoni yig'ish ----------------

async def render(job):
    job.busy = "render"
    job.mode = "story"
    vlen = await video.audio_duration(job.voice)
    st = Status(job.chat_id, (await bot.send_message(
        job.chat_id, f"🎙 Ovoz qabul qilindi ({fmt_sec(vlen)}). Video yig‘ilmoqda…")).message_id)
    try:
        wav = os.path.join(job.dir, "voice.wav")
        await video.voice_to_wav(job.voice, wav)
        vs, ve, pauses, level = video.speech_layout(wav)
        weights = [video.text_weight(s["matn"]) for s in job.script]
        segs = None
        if job.voice_anchors:
            segs = video.bounds_from_anchors(vs, ve, job.voice_anchors, weights, pauses)
            if segs:
                log.info("ovoz moslash: ElevenLabs vaqtlari ishlatildi")
        if not segs:
            try:
                await st.set("🎧 Ovozingizdagi har bir gap qayerda boshlanishi aniqlanmoqda…", force=True)
                amp3 = os.path.join(job.dir, "voice_align.mp3")
                await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a",
                                 "48k", amp3], timeout=300)
                anchors = await gemini.align_voice(gem, HTTP, amp3, [s["matn"] for s in job.script])
                segs = video.bounds_from_anchors(vs, ve, anchors, weights, pauses)
                log.info("ovoz moslash: %d/%d gap topildi", len(anchors), len(weights))
            except Exception as e:
                log.warning("ovozni Gemini bilan moslab bo'lmadi: %r", e)
        if not segs:
            segs = video.sentence_bounds(vs, ve, pauses, weights)
        log.info("gaplar (ovoz): %s", [round(a, 1) for a, _ in segs])
        log.info("oraliqlar (kino): %s", [(round(s["start"]), round(s["end"])) for s in job.script])
        seg_durs = [b - a for a, b in segs]
        seg_durs[-1] += TAIL
        total = float(sum(seg_durs))
        windows = [(s["start"], s["end"]) for s in job.script]
        shots = video.plan_shots(seg_durs, windows, job.duration, base_len=2.0 if total < 75 else 2.4)
        maxrate = video.video_maxrate(total)
        has_cta = False  # oxirida «@bot / KOD» yozuvi yo'q — kod butun video davomida pastda yoziladi
        cta = None
        last = len(job.script) - 1
        vfiles, afiles, used = [], [], []
        await st.set("⏳ Navbat kutilmoqda…", force=True)
        async with HEAVY:
            t0 = time.time()
            first_cta = True
            for i, sh in enumerate(shots):
                d = sh["frames"] / video.FPS
                t = await video.choose_start(job.movie, sh["t"], d, job.duration, used)
                used.append((t, t + d))
                is_cta = has_cta and sh["sentence"] == last
                ov = video.text_overlays(job.dir, WATERMARK, cta if is_cta else None, fade=is_cta and first_cta,
                                         size=job.out_px, code=code_label(job))
                first_cta = first_cta and not is_cta
                vo, ao = os.path.join(job.dir, f"shot_{i:03d}.mp4"), os.path.join(job.dir, f"shot_{i:03d}.wav")
                await video.render_shot(job.movie, t, sh["frames"], vo, ao, job.has_audio, ov, maxrate, job.out_px)
                vfiles.append(vo)
                afiles.append(ao)
                job.last = time.time()
                left = (time.time() - t0) / (i + 1) * (len(shots) - i - 1)
                eta = f" (taxminan {max(1, round(left / 60))} daqiqa qoldi)" if i >= 2 else ""
                await st.set(f"✂️ Sahnalar qirqilmoqda: {i + 1}/{len(shots)}{eta}")
            job.concat = os.path.join(job.dir, "video.mp4")
            job.bg = os.path.join(job.dir, "bg.wav")
            await video.concat(vfiles, job.concat)
            await video.concat(afiles, job.bg)
            for f in vfiles + afiles:
                os.remove(f)
            log.info("sahnalar: %d ta, %.0fs", len(shots), time.time() - t0)
        job.timing = {"vs": vs, "ve": ve, "total": total, "level": level}
        job.square = None
        await finish(job, job.layout, st)
    except asyncio.CancelledError:
        try:  # eski holat xabari «qotib» qolmasin
            await asyncio.wait_for(st.set("⏹ Bu yig‘ish to‘xtatildi.", force=True), 10)
        except Exception:
            pass
        raise
    except Exception as e:
        log.exception("render xato")
        await st.set(f"❌ Video yig‘ishda xatolik: {short_err(e)}", force=True)
    finally:
        job.busy = None


async def finish(job, layout, st=None):
    """Ovozlarni aralashtirib kvadrat videoni yasaydi (kerak bo'lsa 9:16 variantini ham) va yuboradi."""
    job.busy = "render"
    try:
        if st is None:
            st = Status(job.chat_id, (await bot.send_message(job.chat_id, "🎞 Video tayyorlanmoqda…")).message_id)
        await st.set("🎞 Yakuniy video tayyorlanmoqda…", force=True)
        mus = None
        if not (job.square and os.path.exists(job.square)):
            mus = await ensure_music(job)
        async with HEAVY:
            t0 = time.time()
            if not (job.square and os.path.exists(job.square)):
                sq = os.path.join(job.dir, "kino_1x1.mp4")
                tm = job.timing
                await video.final_mux(job.concat, job.bg, job.voice, tm["vs"], tm["ve"], tm["total"], sq, tm["level"],
                                      music=mus, size=job.out_px)
                job.square = sq
                job.music_name = mus["name"] if mus else ""
            out, w, h = job.square, job.out_px, job.out_px
            if layout == "vertical":
                out = os.path.join(job.dir, "kino_9x16.mp4")
                if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(job.square):
                    await st.set("📐 9:16 variant tayyorlanmoqda…", force=True)
                    await video.make_vertical(job.square, out, job.timing["total"], job.out_px)
                w, h, _ = video.vertical_dims(job.out_px)
            log.info("yakuniy %s %.0fs", layout, time.time() - t0)
        job.layout = layout
        note = "✅ Tayyor!"
        if job.music_name:
            note += f"\n🎵 Musiqa: {esc(job.music_name)}"
        note += "\n🎙 Boshqa ovoz yuborsangiz — qayta yig‘aman.\n✏️ Matnni o‘zgartirish uchun yangi matnni yozing."
        other = ("📐 9:16 (Reels)", "v916") if layout == "square" else ("⬛ 1:1 (namunadek)", "v11")
        rows = [[other]]
        if job.music_name:
            rows.append([("🎵 Boshqa musiqa", "mnext"), ("🔇 Musiqasiz", "mnone")])
        elif job.no_music:
            rows.append([("🎵 Musiqa qo‘shish", "mback")])
        rows.append([("🔄 Boshqa matn", "regen"), ("🎵 Oddiy video", "plain")])
        if os.path.getsize(out) > 49 * 1024 * 1024 and BIG is not None:
            await BIG.send_video(job.chat_id, out, job.timing["total"], w, h, hint_msg_id=job.msg_id)
            await bot.send_message(job.chat_id, note, reply_markup=kb(*rows))
        else:
            await bot.send_video(job.chat_id, FSInputFile(out, filename=os.path.basename(out)),
                                 duration=int(round(job.timing["total"])), width=w, height=h,
                                 supports_streaming=True, caption=note,
                                 reply_markup=kb(*rows), request_timeout=900)
        await st.delete()
        cap = insta_caption(job)
        if cap:
            await bot.send_message(job.chat_id, "📝 <b>Instagram uchun tavsif</b> (bosib nusxalang):\n\n"
                                   f"<pre>{esc(cap)[:3800]}</pre>")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("finish xato")
        if st:
            await st.set(f"❌ Video tayyorlashda xatolik: {short_err(e)}", force=True)
    finally:
        job.busy = None
        job.last = time.time()


# ---------------- Oddiy video: eng qiziq sahna + qo'shiq, effektsiz ----------------

def story_ready(job):
    return bool(job.concat and os.path.exists(job.concat) and job.bg and job.timing)


def plain_ready(job):
    p = job.plain
    return bool(p.get("concat") and os.path.exists(p["concat"]) and p.get("bg") and p.get("total"))


def clamp_len(s, e, dur):
    """Sahna uzunligini PLAIN_MIN–PLAIN_MAX oralig'iga va kino ichiga keltiradi."""
    s = max(0.0, s)
    e = min(e, dur - 0.5)
    if e - s > PLAIN_MAX:
        e = s + PLAIN_MAX
    if e - s < PLAIN_MIN:
        e = min(dur - 0.5, s + PLAIN_MIN)
        s = max(0.0, e - PLAIN_MIN)
    return s, e


def fallback_scenes(job, excl):
    """Gemini band bo'lsa: kuchli lahzalar eng zich joylashgan oraliqlardan sahna tanlaymiz."""
    dur = job.duration
    lo, hi = min(120.0, dur * 0.05), dur * 0.85
    moms = [m for m in job.moments or [] if lo <= m["t"] <= hi]
    strong = [m["t"] for m in moms if m.get("kuchli")]
    density = {id(m): sum(1 for x in strong if m["t"] - 5 <= x <= m["t"] + 75) for m in moms}
    pool = sorted(moms, key=lambda m: (not m.get("kuchli"), -density[id(m)]))  # avval kuchlilar, zichi birinchi
    res = []
    for m in pool:
        a = max(0.0, m["t"] - 8)
        b = min(dur - 1, a + 75)
        taken = list(excl) + [(r["start"], r["end"]) for r in res]
        if b - a < PLAIN_MIN or any(a < ue and b > us for us, ue in taken):
            continue
        res.append({"start": a, "end": b, "nima": m.get("voqea") or "", "izoh": ""})
        if len(res) == 3:
            break
    return res


async def refine_bounds(job, cand):
    """Gemini taxminiy oralig'ini aniqlaydi: sahna atrofini eshittirib aniq boshi/oxirini so'raydi, keyin
    kesish joylarini kadr almashgan va jim (gap o'rtasi bo'lmagan) nuqtalarga yopishtiradi."""
    a, b = cand["start"], cand["end"]
    A, B = max(0.0, a - 40), min(job.duration, b + 40)
    wav = os.path.join(job.dir, "scene.wav")
    async with HEAVY:
        await video.extract_wav(job.movie, A, B - A, wav)
    s, e = a, b
    try:
        mp3 = os.path.join(job.dir, "scene.mp3")
        await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "48k",
                         mp3], timeout=300)
        r = await gemini.refine_scene(gem, HTTP, mp3, job.name or job.title or "", A, cand.get("nima") or "",
                                      PLAIN_MIN, PLAIN_MAX)
        if r:
            rs, rend = A + r[0], A + r[1]
            if PLAIN_MIN * 0.8 <= rend - rs <= PLAIN_MAX * 1.15 and abs(rs - a) < 90:
                s, e = rs, rend
        log.info("sahna aniqlandi: %.1f–%.1f (taxmin %.1f–%.1f)", s, e, a, b)
    except Exception as ex:
        log.warning("sahna chegarasini Gemini aniqlay olmadi: %r", ex)
    s, e = clamp_len(s, e, job.duration)
    env = video.rms_envelope(wav)
    async with HEAVY:
        cs = await video.scene_cuts(job.movie, s - 3.0, 5.0)
        ce = await video.scene_cuts(job.movie, e - 2.5, 5.5)
    s2 = video.best_cut(s, cs, env, A, lo=-2.5, hi=1.5)
    e2 = video.best_cut(e, ce, env, A, lo=-2.0, hi=2.5)
    if e2 - s2 < PLAIN_MIN:
        e2 = s2 + PLAIN_MIN
    s, e = clamp_len(s2, e2, job.duration)
    for f in (wav, os.path.join(job.dir, "scene.mp3")):
        try:
            os.remove(f)
        except OSError:
            pass
    return round(s + 0.01, 3), round(e - 0.02, 3)


async def plain_job(job, new=False):
    """Oddiy video: Gemini tanlagan eng qiziq sahna effektsiz kesiladi, kino ovozi baland, qo'shiq past."""
    job.busy = "plain"
    job.mode = "plain"
    p = job.plain
    st = Status(job.chat_id, (await bot.send_message(
        job.chat_id, "🎞 Kinoning eng qiziq sahnasi qidirilmoqda…")).message_id)
    try:
        cands = p.get("cands") or []
        idx = p.get("idx", -1) + 1 if new or not cands else max(0, p.get("idx", 0))
        if idx >= len(cands):
            excl = [tuple(x) for x in p.get("used", [])]
            try:
                cands = await gem.pick_scenes(HTTP, job.name or job.title or "", job.duration, job.moments or [],
                                              job.summary, job.characters, PLAIN_MIN, PLAIN_MAX, excl)
            except Exception as e:
                log.warning("Gemini sahna tanlay olmadi: %r — lahzalardan tanlanadi", e)
                cands = []
            if not cands:
                cands = fallback_scenes(job, excl)
            if not cands:
                raise RuntimeError("kinodan mos sahna topilmadi")
            idx = 0
        p["cands"], p["idx"] = cands, idx
        cand = cands[idx]
        await st.set(f"🎞 Sahna: {esc(cand.get('nima') or '')}\n✂️ Aniq boshlanishi va oxiri aniqlanmoqda…", force=True)
        if cand.get("exact"):  # avval aniqlangan (masalan, qayta ishga tushgandan keyin) — o'sha chegaralar
            start, end = float(cand["exact"][0]), float(cand["exact"][1])
        else:
            start, end = await refine_bounds(job, cand)
            cand["exact"] = [start, end]
        p.setdefault("used", []).append((start, end))
        fps = video.FPS
        total_frames = int((end - start) * fps)
        total = total_frames / fps
        maxrate = video.video_maxrate(total)
        ov = video.text_overlays(job.dir, WATERMARK, size=job.out_px, code=code_label(job))
        cv, cb = os.path.join(job.dir, "plain.mp4"), os.path.join(job.dir, "plain_bg.wav")
        await st.set(f"⏳ Navbat kutilmoqda… (sahna {fmt_sec(total)})", force=True)
        async with HEAVY:
            t0 = time.time()
            await st.set(f"✂️ Sahna kesilmoqda ({fmt_sec(total)})…", force=True)

            def progress(sec):
                job.last = time.time()
                if sec < 1:
                    return
                spent = time.time() - t0
                left = spent / sec * (total - sec)
                eta = f", taxminan {max(1, round(left / 60))} daqiqa qoldi" if spent > 20 else ""
                st.soon(f"✂️ Sahna kesilmoqda: {int(sec * 100 / total)}%{eta}")

            await video.render_shot(job.movie, start, total_frames, cv, cb, job.has_audio, ov, maxrate, job.out_px,
                                    grade=False, progress=progress)
            log.info("oddiy video: %.1f–%.1f (%.1fs), %.0fs", start, end, total, time.time() - t0)
        p.update(concat=cv, bg=cb, total=total, scene=(start, end), nima=cand.get("nima") or "",
                 izoh=hide_title(cand.get("izoh") or "", job), square=None)
        asyncio.create_task(remember_job(job))
        await finish_plain(job, job.layout, st)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(st.set("⏹ Bu yig‘ish to‘xtatildi.", force=True), 10)
        except Exception:
            pass
        raise
    except Exception as e:
        log.exception("oddiy video xato")
        await st.set(f"❌ Oddiy videoni yasashda xatolik: {short_err(e)}", force=True)
    finally:
        job.busy = None


async def finish_plain(job, layout, st=None):
    """Oddiy video: qo'shiqni qo'shadi (kerak bo'lsa 9:16 variantini ham yasaydi) va yuboradi."""
    job.busy = "plain"
    job.mode = "plain"
    p = job.plain
    try:
        if st is None:
            st = Status(job.chat_id, (await bot.send_message(job.chat_id, "🎞 Video tayyorlanmoqda…")).message_id)
        await st.set("🎵 Qo‘shiq qo‘shilmoqda…", force=True)
        mus = None
        if not (p.get("square") and os.path.exists(p["square"])):
            mus = await ensure_music(job, f"{p.get('nima') or ''} {job.summary or ''}".strip() or None)
        async with HEAVY:
            t0 = time.time()
            if not (p.get("square") and os.path.exists(p["square"])):
                sq = os.path.join(job.dir, "oddiy_1x1.mp4")
                await video.plain_mux(p["concat"], p["bg"], p["total"], sq, music=mus, size=job.out_px)
                p["square"], p["music_name"] = sq, (mus["name"] if mus else "")
            out, w, h = p["square"], job.out_px, job.out_px
            if layout == "vertical":
                out = os.path.join(job.dir, "oddiy_9x16.mp4")
                if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(p["square"]):
                    await st.set("📐 9:16 variant tayyorlanmoqda…", force=True)
                    await video.make_vertical(p["square"], out, p["total"], job.out_px, plain=True)
                w, h, _ = video.vertical_dims(job.out_px)
            log.info("oddiy yakuniy %s %.0fs", layout, time.time() - t0)
        job.layout = layout
        a, b = p["scene"]
        note = f"✅ Oddiy video tayyor ({fmt_sec(p['total'])})\n🎞 Sahna: {gemini.fmt_ts(a)} – {gemini.fmt_ts(b)}"
        if p.get("music_name"):
            note += f"\n🎵 Qo‘shiq: {esc(p['music_name'])}"
        other = ("📐 9:16 (Reels)", "v916:p") if layout == "square" else ("⬛ 1:1", "v11:p")
        rows = [[other, ("🎞 Boshqa sahna", "pnext")]]
        if p.get("music_name"):
            rows.append([("🎵 Boshqa qo‘shiq", "mnext:p"), ("🔇 Qo‘shiqsiz", "mnone:p")])
        elif job.no_music:
            rows.append([("🎵 Qo‘shiq qo‘shish", "mback:p")])
        if os.path.getsize(out) > 49 * 1024 * 1024 and BIG is not None:
            await BIG.send_video(job.chat_id, out, p["total"], w, h, hint_msg_id=job.msg_id)
            await bot.send_message(job.chat_id, note, reply_markup=kb(*rows))
        else:
            await bot.send_video(job.chat_id, FSInputFile(out, filename=os.path.basename(out)),
                                 duration=int(round(p["total"])), width=w, height=h,
                                 supports_streaming=True, caption=note,
                                 reply_markup=kb(*rows), request_timeout=900)
        await st.delete()
        cap = insta_caption(job, p.get("izoh"))
        if cap:
            await bot.send_message(job.chat_id, "📝 <b>Instagram uchun tavsif</b> (bosib nusxalang):\n\n"
                                   f"<pre>{esc(cap)[:3800]}</pre>")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("oddiy finish xato")
        if st:
            await st.set(f"❌ Video tayyorlashda xatolik: {short_err(e)}", force=True)
    finally:
        job.busy = None
        job.last = time.time()


# ---------------- Sozlamalar ----------------

async def get_len(chat_id):
    try:
        lib = await music.load(bot, chat_id, WORK_DIR)
        v = int(lib.get("uzunlik") or DEFAULT_LEN)
        return v if v in LEN_CHOICES else DEFAULT_LEN
    except Exception:
        return DEFAULT_LEN


def len_label(sec):
    return f"{sec // 60} daqiqa" if sec % 60 == 0 else f"{sec / 60:.1f}".replace(".", ",") + " daqiqa"


# ---------------- Musiqa ----------------

async def ensure_music(job, text=None):
    """Videoga musiqa tanlaydi (kutubxona bo'sh yoki o'chirilgan bo'lsa — musiqasiz). Xato videoni to'xtatmaydi."""
    if job.no_music:
        return None
    try:
        lib = await music.load(bot, job.chat_id, WORK_DIR)
        if lib.get("off") or not lib.get("songs"):
            return None
        song = music.find(lib, job.music_id) if job.music_id else None
        if song is None:
            text = text or " ".join(s["matn"] for s in (job.script or []))
            song = await music.pick(gem, HTTP, lib, job.title or job.name or "", text)
            if not song:
                return None
            job.music_id = song["id"]
            song["used"] = int(song.get("used", 0)) + 1
            lib["last"] = song["id"]
            music.save_soon(bot, job.chat_id)
        path = await music.fetch(bot, BIG, song, MUSIC_DIR)
        if await music.ensure_best(path, song, MUSIC_DIR):
            music.save_soon(bot, job.chat_id)
        return {"path": path, "start": music.music_start(song), "name": song.get("name", "qo‘shiq"),
                "gain_db": music.TARGET_RMS - float(song.get("rms", -20)), "rms": float(song.get("rms", -20))}
    except Exception as e:
        log.warning("musiqa qo'shilmadi: %r", e)
        return None


def audio_info(m: Message):
    a = m.audio or m.document
    name = ""
    if m.audio:
        name = " — ".join(x for x in ((m.audio.performer or "").strip(), (m.audio.title or "").strip()) if x)
    fname = getattr(a, "file_name", None) or ""
    if not name:
        name = os.path.splitext(fname)[0] or "Qo‘shiq"
    ext = os.path.splitext(fname)[1].lower()
    if not ext or len(ext) > 6:
        ext = ".mp3" if "mpeg" in (a.mime_type or "") else ".m4a"
    return {"id": a.file_unique_id, "file_id": a.file_id, "name": name[:80], "size": a.file_size or 0,
            "msg_id": m.message_id, "ext": ext}


async def add_song(chat_id, a):
    lib = await music.load(bot, chat_id, WORK_DIR)
    if music.find(lib, a["id"]):
        return await bot.send_message(chat_id, f"🎵 «{esc(a['name'])}» ro‘yxatda allaqachon bor.")
    st = Status(chat_id, (await bot.send_message(chat_id, f"🎵 «{esc(a['name'])}» qabul qilindi. Navbatda…")).message_id)
    async with MUSIC_SEM:
        try:
            await st.set(f"🎧 «{esc(a['name'])}» tinglanmoqda — kayfiyatini aniqlayapman…", force=True)
            path = await music.fetch(bot, BIG, a, MUSIC_DIR)
            info = await music.analyse(gem, HTTP, path, MUSIC_DIR, a["id"])
            song = {**a, **info, "used": 0}
            lib.setdefault("songs", []).append(song)
            music.save_soon(bot, chat_id)
            mood = music.describe(song) or "aniqlanmadi (Gemini band edi), lekin qo‘shiq saqlandi"
            await st.set(f"✅ Saqlandi: <b>{esc(song['name'])}</b>\n🎭 {esc(mood)}\n"
                         f"Jami: {len(lib['songs'])} ta qo‘shiq. /musiqa — ro‘yxat", force=True)
        except Exception as e:
            log.exception("qo'shiq xato")
            await st.set(f"❌ «{esc(a['name'])}» saqlanmadi: {short_err(e)}", force=True)


def expected_voice_sec(job):
    """Matnni o'qish uchun taxminiy vaqt (soniya): o'zbekcha diktor ~2.2 so'z/soniya."""
    words = sum(len((s.get("matn") or "").split()) for s in (job.script or []))
    return max(10.0, words / 2.2)


def fmt_sec(x):
    x = int(round(x))
    return f"{x // 60}:{x % 60:02d}"


PART_RE = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")


def part_of(text):
    """«1/2», «2 / 3» kabi belgidan (qism, jami) ni qaytaradi, bo'lmasa None."""
    m = PART_RE.search(text or "")
    if m and 1 <= int(m.group(1)) <= int(m.group(2)) <= 10:
        return int(m.group(1)), int(m.group(2))
    return None


def can_restart(job):
    """Video yig'ila boshlaganiga hali ko'p bo'lmadimi (kech kelgan qism bilan qaytadan yig'sa bo'ladimi)."""
    return (job.busy == "render" and job.render_parts is not None
            and time.time() - job.render_started < RESTART_WINDOW)


async def take_voice(job, a, caption=""):
    """Diktor ovozini (yoki uning bir qismini) qabul qiladi. «1/2» kabi izoh bo'lsa — qolgan qismlarni kutadi.
    Video yig'ila boshlaganiga ko'p bo'lmagan bo'lsa va yana qism kelsa — hamma qismlar bilan qaytadan yig'adi."""
    async with job.lock:
        mid = a.get("msg_id") or 0
        if mid and mid in job.part_msg.values():
            return  # shu xabar allaqachon olingan (Telegram qayta yuborgan)
        restarted = False
        if job.busy:
            if not can_restart(job):
                await bot.send_message(job.chat_id, "⏳ Video yig‘ilyapti. Tugagach, yangi ovozni yuborishingiz mumkin.")
                return
            if job.task and not job.task.done():
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)
            job.busy = None
            job.voice_parts = list(job.render_parts)
            job.render_parts = None
            restarted = True
        path = os.path.join(job.dir, f"voice_{int(time.time() * 1000)}{a.get('ext') or '.ogg'}")
        if (a.get("size") or 0) <= BOT_API_LIMIT:
            await bot.download(a["file_id"], destination=path, timeout=300)
        elif BIG is not None:
            await BIG.download(a["msg_id"], path)
        else:
            raise RuntimeError("Ovoz fayli juda katta")
        dur = await video.audio_duration(path)
        part = part_of(caption)
        if part:
            job.expect_parts = max(job.expect_parts, part[1])
        job.part_msg[path] = mid
        job.voice_parts.append((path, dur))
        job.voice_parts.sort(key=lambda x: job.part_msg.get(x[0], 0))  # yuborilgan tartibda
        job.last = time.time()
        if restarted:
            await bot.send_message(job.chat_id, "➕ Ovozning yana bir qismi keldi — videoni hamma qismlar bilan "
                                                "qaytadan yig‘aman.")
        if job.voice_timer and not job.voice_timer.done():
            job.voice_timer.cancel()
        job.voice_timer = asyncio.create_task(decide_voice(job))


async def decide_voice(job, wait=8.0):
    """Ovoz matnga yetarli bo'lsa — videoni yig'adi, juda qisqa bo'lsa — davomini so'raydi."""
    try:
        await asyncio.sleep(wait)
        # «2/3» kabi izoh bo'lsa — qolgan qismlarni 2 daqiqagacha kutamiz
        waited = 0.0
        while job.expect_parts and len(job.voice_parts) < job.expect_parts and waited < PART_WAIT:
            await asyncio.sleep(3)
            waited += 3
    except asyncio.CancelledError:
        return
    async with job.lock:
        if job.busy or not job.voice_parts:
            return
        total = sum(d for _, d in job.voice_parts)
        need = expected_voice_sec(job)
        missing = job.expect_parts and len(job.voice_parts) < job.expect_parts
        if total < 0.6 * need or missing:
            n = len(job.voice_parts)
            extra = f" {job.expect_parts} ta qismdan {n} tasi keldi." if missing else ""
            await bot.send_message(
                job.chat_id,
                f"🎙 Ovoz qabul qilindi{f' ({n} ta qism)' if n > 1 else ''}: jami <b>{fmt_sec(total)}</b>.{extra} "
                f"Matnni o‘qish esa taxminan <b>{fmt_sec(need)}</b> oladi.\n\n"
                "Davomini yuboring (bir nechta ovozli xabarga bo‘lib yuborsangiz ham bo‘ladi) "
                "yoki shu ovoz bilan yig‘ishni tanlang.",
                reply_markup=kb([("✅ Shu bilan yig‘", "vgo"), ("🗑 Qaytadan yozaman", "vclear")]))
            return
        await start_render(job)


async def start_render(job):
    """Qismlarni birlashtirib, videoni yig'ishni boshlaydi (job.lock ichida chaqiriladi)."""
    parts = [p for p, _ in job.voice_parts]
    job.render_parts, job.render_started = list(job.voice_parts), time.time()
    job.voice_parts, job.expect_parts = [], 0
    if len(parts) == 1:
        job.voice = parts[0]
    else:
        job.voice = await video.concat_voice(parts, os.path.join(job.dir, f"voice_all_{int(time.time())}.ogg"))
    job.square = None
    job.voice_anchors = None
    job.busy = "render"  # darhol — ikkinchi ovoz kelsa, ikki marta yig'ilmasin
    job.task = asyncio.create_task(render(job))


async def tts_job(job):
    """ElevenLabs bilan matnni o'qitib, videoni yig'ishni boshlaydi."""
    st = Status(job.chat_id, (await bot.send_message(job.chat_id, "🤖 ElevenLabs matnni o‘qiyapti…")).message_id)
    try:
        lib = await music.load(bot, job.chat_id, WORK_DIR)
        vid = lib.get("ovoz") or eleven.VOICES[0][1]
        sentences = [s["matn"] for s in job.script]
        path = os.path.join(job.dir, f"eleven_{int(time.time())}.mp3")
        alignment = await eleven.tts(HTTP, ELEVEN_KEY, vid, "\n\n".join(sentences), path)
        anchors = eleven.sentence_starts(sentences, alignment)
        dur = await video.audio_duration(path)
        left = await eleven.credits_left(HTTP, ELEVEN_KEY)
        log.info("elevenlabs: %s, %.0fs, gaplar %d/%d, kredit %s", eleven.voice_name(vid), dur, len(anchors),
                 len(sentences), left)
        await st.set(f"✅ Ovoz tayyor: {eleven.voice_name(vid)}, {fmt_sec(dur)}"
                     + (f". ElevenLabs krediti qoldi: {left:,}".replace(",", " ") if left is not None else ""), force=True)
        async with job.lock:
            job.voice, job.voice_parts, job.voice_anchors = path, [], anchors or None
            job.render_parts, job.expect_parts = None, 0
            job.square = None
            job.busy = "render"
            job.task = asyncio.create_task(render(job))
    except eleven.ElevenError as e:
        job.busy = None
        log.warning("elevenlabs xato: %s %s %s", e.status, e.code, e)
        await st.set(f"❌ ElevenLabs: {esc(eleven.friendly(e))}", force=True)
    except Exception as e:
        job.busy = None
        log.exception("tts xato")
        await st.set(f"❌ Ovoz yaratilmadi: {short_err(e)}", force=True)


# ---------------- Xabarlar ----------------

WELCOME = (
    "Salom! 🎬 Men kinodan 1 daqiqalik qisqa hikoya-video yasayman.\n\n"
    "1) Kinoni shu yerga <b>fayl qilib</b> yuboring yoki kanalingizdan forward qiling (2 GB gacha).\n"
    "2) Kino nomini va kodini yozasiz.\n"
    "3) Men hikoya matnini yozib beraman — uni o‘qib, <b>ovozli xabar</b> yuborasiz.\n"
    "4) Sahnalarni qirqib, ovozingiz bilan tayyor videoni yuboraman.\n\n"
    "🎞 Ovozsiz video kerak bo‘lsa — matn tagidagi «Oddiy video» tugmasi: kinoning eng qiziq sahnasini "
    "(45–120 soniya) effektsiz kesib, mos qo‘shiq bilan yasayman.\n"
    "🎵 Qo‘shiq (mp3) yuborsangiz, saqlab qo‘yaman va har videoga mosini o‘zim tanlayman.\n"
    "🤖 Ovozni o‘zingiz yozmasangiz, matn tagidagi «ElevenLabs ovozida o‘qit» tugmasini bosing (/ovoz — diktor).\n\n"
    "Buyruqlar: /uzunlik — video uzunligi, /musiqa — qo‘shiqlar ro‘yxati, /kod 125 — kodni o‘zgartirish, "
    "/bekor — joriy ishni to‘xtatish."
)


@dp.message(CommandStart())
async def cmd_start(m: Message):
    if not allowed(m.from_user.id):
        return await m.answer(f"Bu bot shaxsiy 🙂\nSizning ID: <code>{m.from_user.id}</code>")
    warn = []
    if not GEMINI_STATUS.startswith("ishlaydi"):
        warn.append(f"⚠️ Gemini: {esc(GEMINI_STATUS)}")
    if not BIG:
        warn.append("⚠️ API_ID/API_HASH kiritilmagan — 20 MB dan katta fayllar olinmaydi")
    if ELEVEN_KEY and not ELEVEN_STATUS.startswith("ishlaydi"):
        warn.append(f"⚠️ ElevenLabs: {esc(ELEVEN_STATUS)}")
    await m.answer(WELCOME + f"\n\nSizning ID: <code>{m.from_user.id}</code>" + ("\n\n" + "\n".join(warn) if warn else ""))


@dp.message(Command("bekor"))
async def cmd_cancel(m: Message):
    job = JOBS.pop(m.from_user.id, None)
    if job:
        await drop_job(job)
        return await m.answer("❌ Bekor qilindi. Yangi kino yuborishingiz mumkin.")
    await m.answer("Hozir hech qanday ish yo‘q.")


@dp.message(Command("kod"))
async def cmd_code(m: Message, command: CommandObject):
    job = JOBS.get(m.from_user.id)
    if not job:
        return await m.answer("Avval kinoni yuboring 🎬")
    code = clean_code(command.args or "")
    if not code:
        return await m.answer("Masalan: <code>/kod 125</code>")
    job.code, job.asking = code, (None if job.asking == "code" else job.asking)
    if job.script and SPOKEN_CTA:
        if job.script[-1].get("cta"):
            job.script[-1]["matn"] = CTA_TEXT.format(kod=code)
        else:
            job.script.append(cta_item(job))
        return await m.answer(f"✅ Kod: {esc(code)}\nOxirgi gap: <i>{esc(job.script[-1]['matn'])}</i>\n"
                              "Ovozni shu gap bilan o‘qib yuboring.")
    await m.answer(f"✅ Kod: {esc(code)}. Videoning pastida «{esc(code_label(job))}» deb yoziladi.")


@dp.message(F.video | F.document)
async def on_file(m: Message):
    if not allowed(m.from_user.id):
        return
    media = m.video or m.document
    fname = getattr(media, "file_name", None) or ("kino.mp4" if m.video else "fayl")
    mime = (media.mime_type or "").lower()
    if m.document and not (mime.startswith("video/") or VIDEO_EXT.search(fname)):
        if mime.startswith("audio/") or mime == "application/ogg" or re.search(r"\.(mp3|m4a|wav|ogg|aac|flac|opus)$", fname, re.I):
            return await on_audio(m)
        return await m.answer("Bu video faylga o‘xshamaydi. Kinoni video yoki fayl qilib yuboring 🎬")
    size = media.file_size or 0
    if size > MAX_BYTES:
        return await m.answer(f"Fayl juda katta ({human_mb(size)}). Ko‘pi bilan {human_mb(MAX_BYTES)}.")
    if size > BOT_API_LIMIT and BIG is None:
        return await m.answer("Bu fayl 20 MB dan katta. Katta fayllar uchun serverga API_ID va API_HASH kiritilishi kerak.")
    if not GEMINI_API_KEY:
        return await m.answer("⚠️ Serverga GEMINI_API_KEY kiritilmagan.")
    old = JOBS.pop(m.from_user.id, None)
    if old:
        await drop_job(old)
    d = os.path.join(WORK_DIR, f"{m.from_user.id}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    job = Job(uid=m.from_user.id, chat_id=m.chat.id, dir=d, msg_id=m.message_id, file_id=media.file_id,
              size=size, file_name=fname)
    if m.caption and m.caption.strip():
        job.name, code = split_name_code(m.caption)
        if code:
            job.code = clean_code(code)
    JOBS[job.uid] = job
    asyncio.create_task(remember_job(job))
    sm = await m.answer(f"🎬 Kino qabul qilindi ({human_mb(size)}). Yuklab olyapman…")
    job.status = Status(m.chat.id, sm.message_id)
    job.task = asyncio.create_task(analyse(job))
    await ask_next(job)


@dp.message(F.voice)
async def on_voice(m: Message):
    if not allowed(m.from_user.id):
        return
    job = JOBS.get(m.from_user.id)
    if not job or not job.movie:
        return await m.answer("Avval kinoni yuboring 🎬")
    if not job.script:
        return await m.answer("⏳ Matn hali tayyor emas. Tayyor bo‘lgach, o‘shani o‘qib yuborasiz.")
    a = {"file_id": m.voice.file_id, "size": m.voice.file_size or 0, "msg_id": m.message_id, "ext": ".ogg"}
    try:
        await take_voice(job, a, m.caption or "")
    except Exception as e:
        await m.answer(f"❌ Ovozni yuklab bo‘lmadi: {short_err(e)}")


@dp.message(F.audio)
async def on_audio(m: Message):
    """Audio fayl: matn kutilayotgan bo'lsa — so'raymiz (diktor ovozimi yoki musiqami), aks holda musiqa."""
    if not allowed(m.from_user.id):
        return
    a = audio_info(m)
    media = m.audio or m.document
    a["caption"] = " ".join(x for x in (m.caption, getattr(media, "title", None), getattr(media, "file_name", None)) if x)
    job = JOBS.get(m.from_user.id)
    if job and job.script and job.movie and (not job.busy or can_restart(job)):
        if part_of(a["caption"]):
            # «1/2» kabi belgisi bor — bu diktor ovozining bir qismi, so'ramasdan olamiz
            try:
                await take_voice(job, a, a["caption"])
            except Exception as e:
                await m.answer(f"❌ Ovozni yuklab bo‘lmadi: {short_err(e)}")
            return
        pend = PENDING_AUDIO.setdefault(m.from_user.id, {})
        pend[m.message_id] = a
        while len(pend) > 10:
            pend.pop(next(iter(pend)))
        return await m.answer(f"«{esc(a['name'])}» — bu nima?",
                              reply_markup=kb([("🎙 Diktor ovozi", f"as_voice:{m.message_id}"),
                                               ("🎵 Musiqa (saqlash)", f"as_music:{m.message_id}")]))
    asyncio.create_task(add_song(m.chat.id, a))


@dp.message(Command("uzunlik"))
async def cmd_len(m: Message):
    if not allowed(m.from_user.id):
        return
    cur = await get_len(m.chat.id)
    await m.answer(f"⏱ Video uzunligi hozir: <b>{len_label(cur)}</b>\nKeyingi videolar uchun tanlang:",
                   reply_markup=kb([((("✅ " if s == cur else "") + len_label(s)), f"len:{s}") for s in LEN_CHOICES]))


@dp.callback_query(F.data.startswith("len:"))
async def on_len(c: CallbackQuery):
    await c.answer()
    if not allowed(c.from_user.id):
        return
    sec = int(c.data.split(":", 1)[1])
    chat_id = c.message.chat.id if c.message else c.from_user.id
    lib = await music.load(bot, chat_id, WORK_DIR)
    lib["uzunlik"] = sec
    music.save_soon(bot, chat_id, delay=0.5)
    await bot.send_message(chat_id, f"✅ Endi videolar taxminan <b>{len_label(sec)}</b> bo‘ladi. "
                                    "Hozirgi kino uchun ham kerak bo‘lsa, «🔄 Boshqa matn» ni bosing.")


@dp.message(Command("musiqa"))
async def cmd_music(m: Message):
    if not allowed(m.from_user.id):
        return
    await show_music(m.chat.id)


async def show_music(chat_id):
    lib = await music.load(bot, chat_id, WORK_DIR)
    songs = lib.get("songs") or []
    if not songs:
        return await bot.send_message(chat_id, "🎵 Hali qo‘shiq yo‘q.\nQo‘shiq (mp3) faylini shu yerga yuboring — "
                                               "saqlab qo‘yaman va har videoga mosini o‘zim tanlayman.")
    lines = [f"{i + 1}. <b>{esc(x['name'])}</b> — {esc(music.describe(x) or 'tavsif yo‘q')} "
             f"({x.get('used', 0)} marta)" for i, x in enumerate(songs[:60])]
    state = "🔇 o‘chirilgan" if lib.get("off") else "🔊 yoqilgan"
    rows = [[(f"🗑 {i + 1}", f"mdel:{x['id']}") for i, x in enumerate(songs[j:j + 5], start=j)]
            for j in range(0, min(len(songs), 60), 5)]
    rows.append([("🔊 Musiqani yoqish" if lib.get("off") else "🔇 Musiqani o‘chirish", "mtoggle")])
    text = (f"🎵 <b>Musiqalar</b> ({len(songs)} ta), musiqa {state}\n\n" + "\n".join(lines)
            + "\n\n🗑 — o‘chirish. Yangi qo‘shiq qo‘shish uchun faylni yuboring.")
    await bot.send_message(chat_id, text[:4000], reply_markup=kb(*rows))


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if not allowed(m.from_user.id):
        return await m.answer(f"Bu bot shaxsiy 🙂\nSizning ID: <code>{m.from_user.id}</code>")
    job = JOBS.get(m.from_user.id)
    text = (m.text or "").strip()
    if not job:
        try:
            meta = (await music.load(bot, m.chat.id, WORK_DIR)).get("job") or {}
        except Exception:
            meta = {}
        if meta.get("msg_id") and time.time() - meta.get("t", 0) < 48 * 3600:
            return await m.answer("Server qayta ishga tushgani uchun avvalgi ish to‘xtab qolgan bo‘lishi mumkin. "
                                  f"«{esc(meta.get('name') or meta.get('file_name') or 'kino')}» bilan davom ettiraymi?",
                                  reply_markup=kb([("🔁 Davom ettirish", "resume")]))
        return await m.answer("Kinoni fayl qilib yuboring 🎬 (/start — yo‘riqnoma)")
    job.last = time.time()
    if job.asking == "name":
        job.name, code = split_name_code(text)
        if code and not job.code:
            job.code = clean_code(code)
        await ask_next(job)
        asyncio.create_task(remember_job(job))
        if not job.asking:
            await m.answer("✅ Rahmat! Matn tayyor bo‘lgach yuboraman.")
        return
    if job.asking == "code":
        job.code, job.asking = clean_code(text), None
        asyncio.create_task(remember_job(job))
        return await m.answer("✅ Rahmat! Matn tayyor bo‘lgach yuboraman.")
    if job.script and not job.busy:
        sentences = split_sentences(text)
        if len(sentences) < 2:
            return await m.answer("Matnni kamida 2 ta gap qilib yuboring.")
        job.script = remap_script(job, sentences)
        asyncio.create_task(remember_job(job))
        return await m.answer("✅ Matn yangilandi. Endi shu matnni o‘qib, ovozli xabar yuboring 🎙")
    await m.answer("⏳ Ishlayapman, biroz kuting…")


@dp.callback_query(F.data.startswith("mdel:") | F.data.startswith("as_music") | F.data.startswith("as_voice")
                   | (F.data == "mtoggle"))
async def on_music_button(c: CallbackQuery):
    await c.answer()
    uid, chat_id = c.from_user.id, c.message.chat.id if c.message else c.from_user.id
    if not allowed(uid):
        return
    if c.data.startswith(("as_music", "as_voice")):
        kind, _, mid = c.data.partition(":")
        pend = PENDING_AUDIO.get(uid) or {}
        if mid.isdigit():
            a = pend.pop(int(mid), None)
        else:  # eski tugma
            a = pend.pop(next(reversed(pend)), None) if pend else None
        if not a:
            return await bot.send_message(chat_id, "Faylni qaytadan yuboring.")
        if kind == "as_music":
            asyncio.create_task(add_song(chat_id, a))
            return
        job = JOBS.get(uid)
        if not job or not job.script:
            return await bot.send_message(chat_id, "⏳ Hozir ovoz qabul qila olmayman, biroz kuting.")
        try:
            await take_voice(job, a, a.get("caption", ""))
        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ovozni yuklab bo‘lmadi: {short_err(e)}")
        return
    lib = await music.load(bot, chat_id, WORK_DIR)
    if c.data == "mtoggle":
        lib["off"] = not lib.get("off")
    else:
        sid = c.data.split(":", 1)[1]
        song = music.find(lib, sid)
        if song:
            lib["songs"].remove(song)
            await bot.send_message(chat_id, f"🗑 O‘chirildi: {esc(song['name'])}")
    music.save_soon(bot, chat_id, delay=0.5)
    await show_music(chat_id)


@dp.callback_query(F.data == "tts")
async def on_tts(c: CallbackQuery):
    await c.answer()
    job = JOBS.get(c.from_user.id)
    if not job or not allowed(c.from_user.id) or not job.script or not job.movie:
        return await bot.send_message(c.from_user.id, "Bu ish eskirgan. Kinoni qaytadan yuboring 🎬")
    if not ELEVEN_KEY:
        return await bot.send_message(job.chat_id, "ElevenLabs kaliti kiritilmagan.")
    async with job.lock:
        if job.busy:
            return await bot.send_message(job.chat_id, "⏳ Hozir ishlayapman, biroz kuting…")
        job.busy = "tts"
    asyncio.create_task(tts_job(job))


@dp.message(Command("ovoz"))
async def cmd_voice(m: Message):
    if not allowed(m.from_user.id):
        return
    if not ELEVEN_KEY:
        return await m.answer("ElevenLabs hali ulanmagan.")
    lib = await music.load(bot, m.chat.id, WORK_DIR)
    cur = lib.get("ovoz") or eleven.VOICES[0][1]
    lines = [f"{'✅' if v == cur else '▫️'} <b>{n}</b> — {d}" for n, v, d in eleven.VOICES]
    await m.answer("🎙 <b>ElevenLabs diktori</b>\n\n" + "\n".join(lines),
                   reply_markup=kb(*[[((("✅ " if v == cur else "") + n), f"voice:{v}")] for n, v, _ in eleven.VOICES]))


@dp.callback_query(F.data.startswith("voice:"))
async def on_voice_pick(c: CallbackQuery):
    await c.answer()
    if not allowed(c.from_user.id):
        return
    vid = c.data.split(":", 1)[1]
    chat_id = c.message.chat.id if c.message else c.from_user.id
    lib = await music.load(bot, chat_id, WORK_DIR)
    lib["ovoz"] = vid
    music.save_soon(bot, chat_id, delay=0.5)
    await bot.send_message(chat_id, f"✅ Diktor: <b>{eleven.voice_name(vid)}</b>")


@dp.callback_query(F.data == "kind")
async def on_kind(c: CallbackQuery):
    job = JOBS.get(c.from_user.id)
    if not job or not allowed(c.from_user.id):
        await c.answer()
        return await bot.send_message(c.from_user.id, "Bu ish eskirgan. Kinoni qaytadan yuboring 🎬")
    job.kind = "anime" if job.kind == "kino" else "kino"
    job.kind_manual = True
    other = {t.lower() for t in (BASE_TAGS if job.kind == "anime" else ANIME_TAGS).split()}
    job.tags = clean_tags([t for t in job.tags if t.lower() not in other], job)
    asyncio.create_task(remember_job(job))
    await c.answer(f"Endi videoda «{code_label(job)}» deb yoziladi", show_alert=False)
    if c.message and c.message.reply_markup:
        rows = [[(b.text, b.callback_data) for b in row] for row in c.message.reply_markup.inline_keyboard]
        rows = [[kind_button(job) if d == "kind" else (t, d) for t, d in row] for row in rows]
        try:
            await bot.edit_message_reply_markup(chat_id=c.message.chat.id, message_id=c.message.message_id,
                                                reply_markup=kb(*rows))
        except Exception as e:
            log.warning("tugmani yangilab bo'lmadi: %r", e)


@dp.callback_query(F.data == "resume")
async def on_resume(c: CallbackQuery):
    await c.answer()
    if not allowed(c.from_user.id):
        return
    chat_id = c.message.chat.id if c.message else c.from_user.id
    job = JOBS.get(c.from_user.id)
    if job and job.task and not job.task.done():
        return await bot.send_message(chat_id, "⏳ Ish allaqachon davom etyapti.")
    await resume_job(c.from_user.id, chat_id)


@dp.callback_query(F.data.in_({"vgo", "vclear"}))
async def on_voice_choice(c: CallbackQuery):
    await c.answer()
    job = JOBS.get(c.from_user.id)
    if not job or not allowed(c.from_user.id):
        return await bot.send_message(c.from_user.id, "Bu ish eskirgan. Kinoni qaytadan yuboring 🎬")
    async with job.lock:
        if job.busy:
            return await bot.send_message(job.chat_id, "⏳ Video yig‘ilyapti…")
        if c.data == "vclear":
            for p, _ in job.voice_parts:
                try:
                    os.remove(p)
                except OSError:
                    pass
            job.voice_parts, job.expect_parts = [], 0
            if job.voice_timer and not job.voice_timer.done():
                job.voice_timer.cancel()
            return await bot.send_message(job.chat_id, "🗑 Mayli, ovoz o‘chirildi. Matnni qaytadan o‘qib yuboring 🎙")
        if not job.voice_parts:
            return await bot.send_message(job.chat_id, "Avval ovozli xabar yuboring 🎙")
        await start_render(job)


@dp.callback_query(F.data.in_({"mnext", "mnone", "mback", "mnext:p", "mnone:p", "mback:p"}))
async def on_music_choice(c: CallbackQuery):
    await c.answer()
    action, _, kind = c.data.partition(":")
    plain = kind == "p"
    job = JOBS.get(c.from_user.id)
    if not job or not allowed(c.from_user.id) or not (plain_ready(job) if plain else story_ready(job)):
        return await bot.send_message(c.from_user.id, "Bu ish eskirgan. Kinoni qaytadan yuboring 🎬")
    if job.busy or (job.task and not job.task.done()):
        return await bot.send_message(job.chat_id, "⏳ Hozir ishlayapman, biroz kuting…")
    if action == "mnone":
        job.no_music = True
    elif action == "mback":
        job.no_music, job.music_id = False, None
    else:
        lib = await music.load(bot, job.chat_id, WORK_DIR)
        song = music.next_other(lib, job.music_id)
        if not song:
            return await bot.send_message(job.chat_id, "Ro‘yxatda boshqa qo‘shiq yo‘q. Yangi qo‘shiq yuboring 🎵")
        song["used"] = int(song.get("used", 0)) + 1
        lib["last"] = song["id"]
        music.save_soon(bot, job.chat_id)
        job.no_music, job.music_id = False, song["id"]
    if plain:
        job.plain["square"] = None
        job.task = asyncio.create_task(finish_plain(job, job.layout))
    else:
        job.square = None
        job.task = asyncio.create_task(finish(job, job.layout))


@dp.callback_query(F.data.in_({"regen", "retry", "v916", "v11", "v916:p", "v11:p", "plain", "pnext"}))
async def on_button(c: CallbackQuery):
    await c.answer()
    job = JOBS.get(c.from_user.id)
    if not job or not allowed(c.from_user.id):
        return await bot.send_message(c.from_user.id, "Bu ish eskirgan. Kinoni qaytadan yuboring 🎬")
    if job.busy or (job.task and not job.task.done()):
        return await bot.send_message(job.chat_id, "⏳ Hozir ishlayapman, biroz kuting…")
    job.last = time.time()
    if c.data == "retry":
        if not job.status:
            job.status = Status(job.chat_id, (await bot.send_message(job.chat_id, "🔁 Qayta urinilmoqda…")).message_id)
        job.task = asyncio.create_task(analyse(job))
    elif c.data == "regen":
        if not job.moments:
            return await bot.send_message(job.chat_id, "⏳ Matn hali yozilmoqda…")
        job.task = asyncio.create_task(regen(job))
    elif c.data in ("v916:p", "v11:p"):
        if not plain_ready(job):
            return await bot.send_message(job.chat_id, "Avval «🎵 Oddiy video» ni bosing.")
        job.task = asyncio.create_task(finish_plain(job, "vertical" if c.data == "v916:p" else "square"))
    elif c.data in ("v916", "v11"):
        if not story_ready(job):
            return await bot.send_message(job.chat_id, "Avval ovozli xabar yuboring 🎙")
        job.task = asyncio.create_task(finish(job, "vertical" if c.data == "v916" else "square"))
    elif c.data in ("plain", "pnext"):
        if not (job.movie and job.moments):
            return await bot.send_message(job.chat_id, "⏳ Kino hali tahlil qilinyapti, biroz kuting…")
        if c.data == "plain" and plain_ready(job):
            job.task = asyncio.create_task(finish_plain(job, job.layout))  # tayyori bor — qaytadan yuboramiz
        else:
            job.task = asyncio.create_task(plain_job(job, new=c.data == "pnext"))


# ---------------- Server ----------------

async def keepalive():
    """Bepul Render 15 daqiqa so'rov bo'lmasa uxlab qoladi va yuklangan kino o'chadi.
    Ish ketayotganda (yoki ovoz kutilayotganda) server o'zini uyg'oq ushlab turadi."""
    while True:
        await asyncio.sleep(240)
        now = time.time()
        for uid, job in list(JOBS.items()):
            if now - job.last > 6 * 3600:
                JOBS.pop(uid, None)
                await drop_job(job)
        if PUBLIC_URL and any(now - j.last < 3 * 3600 for j in JOBS.values()):
            try:
                async with HTTP.get(PUBLIC_URL + "/health", timeout=aiohttp.ClientTimeout(total=30)) as r:
                    await r.read()
            except Exception as e:
                log.warning("keepalive: %r", e)


async def check_gemini():
    """Kalit ishlashini va qaysi modellar ochiqligini tekshiradi (ishga tushganda bir marta)."""
    global GEMINI_STATUS
    if not GEMINI_API_KEY:
        GEMINI_STATUS = "kalit kiritilmagan"
        return
    try:
        async with HTTP.get(f"{gemini.BASE}/v1beta/models?pageSize=1000", headers={"x-goog-api-key": GEMINI_API_KEY},
                            timeout=aiohttp.ClientTimeout(total=30)) as r:
            txt = await r.text()
            if r.status != 200:
                GEMINI_STATUS = f"xato {r.status}: {re.sub(chr(10), ' ', txt)[:160]}"
                return
            data = await r.json(content_type=None)
        names = {m.get("name", "").split("/")[-1] for m in data.get("models", [])}
        avail = [m for m in gem.models if m in names]
        if avail:
            gem.models = avail
        GEMINI_STATUS = "ishlaydi ✅ (" + ", ".join(gem.models[:4]) + ")"
    except Exception as e:
        GEMINI_STATUS = f"tekshirib bo‘lmadi: {e!r}"[:200]
    finally:
        log.info("Gemini: %s", GEMINI_STATUS)


async def on_startup():
    global HTTP, ELEVEN_STATUS
    os.makedirs(WORK_DIR, exist_ok=True)
    HTTP = aiohttp.ClientSession()
    asyncio.create_task(keepalive())
    await check_gemini()
    if ELEVEN_KEY:
        ok, info = await eleven.check(HTTP, ELEVEN_KEY)
        ELEVEN_STATUS = ("ishlaydi ✅ " if ok else "xato: ") + info
        log.info("ElevenLabs: %s", ELEVEN_STATUS)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Yo‘riqnoma"),
            BotCommand(command="bekor", description="Joriy ishni to‘xtatish"),
            BotCommand(command="kod", description="Kino kodini o‘zgartirish"),
            BotCommand(command="musiqa", description="Qo‘shiqlar ro‘yxati"),
            BotCommand(command="uzunlik", description="Video uzunligi (1–3 daqiqa)"),
            BotCommand(command="ovoz", description="ElevenLabs diktorini tanlash"),
        ])
    except Exception as e:
        log.warning("set_my_commands: %r", e)
    try:
        du = shutil.disk_usage(WORK_DIR)
        log.info("Ish papkasi: %s (bo'sh: %s)", WORK_DIR, human_mb(du.free))
    except Exception:
        pass
    me = await bot.get_me()
    log.info("Bot ulandi: @%s (katta fayllar: %s, Gemini: %s, ElevenLabs: %s)", me.username, "ha" if BIG else "yo‘q",
             "ha" if GEMINI_API_KEY else "yo‘q", "ha" if ELEVEN_KEY else "yo‘q")


async def on_shutdown():
    """Server to'xtatilayotganda (yangilanish yoki qayta ishga tushish) ishi bor foydalanuvchilarni ogohlantiradi."""
    now = time.time()
    for job in list(JOBS.values()):
        working = job.busy or (job.task and not job.task.done()) or job.script
        if working and now - job.last < 3 * 3600:
            try:
                await bot.send_message(job.chat_id, "⚠️ Server qayta ishga tushyapti, kino bilan ish to‘xtab qoldi. "
                                                    "1–2 daqiqadan keyin «🔁 Davom ettirish» ni bosing.",
                                       reply_markup=kb([("🔁 Davom ettirish", "resume")]))
            except Exception as e:
                log.warning("ogohlantirish yuborilmadi: %r", e)
    if BIG:
        await BIG.close()
    if HTTP:
        await HTTP.close()


async def health(_request):
    return web.Response(text="ok")


def main():
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN kiritilmagan — faqat sog'liq sahifasi ishlaydi")
        app = web.Application()
        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        web.run_app(app, host="0.0.0.0", port=PORT)
        return
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    if PUBLIC_URL:
        secret = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]
        path = f"/tg/{secret[:16]}"

        async def set_hook():
            await bot.set_webhook(PUBLIC_URL + path, secret_token=secret,
                                  allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False)
            log.info("Webhook: %s%s", PUBLIC_URL, "/tg/…")

        dp.startup.register(set_hook)
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=path)
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        async def polling():
            app = web.Application()
            app.router.add_get("/", health)
            app.router.add_get("/health", health)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", PORT).start()
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(bot)

        asyncio.run(polling())


if __name__ == "__main__":
    main()
