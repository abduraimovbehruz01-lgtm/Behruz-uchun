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

import gemini
import video

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

# ---------------- Sozlamalar (Render → Environment) ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or None
OWNER_IDS = {int(x) for x in re.findall(r"\d+", os.environ.get("OWNER_IDS", ""))}
WATERMARK = os.environ.get("WATERMARK", "@behruzfilmbot").strip()
CTA_TEXT = os.environ.get("CTA_TEXT", "Kinoni ko‘rmoqchi bo‘lsangiz, profilimdagi botga {kod} kodini yuboring.")
CTA_LINE1 = os.environ.get("CTA_LINE1", WATERMARK).strip()
CAPTION_CTA = os.environ.get("CAPTION_CTA", "🔢 Kod: {kod} — kinoni ko‘rish uchun profilimdagi botga yuboring 🍿")
BASE_TAGS = os.environ.get("BASE_HASHTAGS", "#kino #kinolar #film #filmlar")
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
WORK_DIR = os.environ.get("WORK_DIR", "/tmp/kino")
MAX_BYTES = int(float(os.environ.get("MAX_FILE_GB", "2")) * 1024 ** 3)
DL_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "6"))
BOT_API_LIMIT = 20 * 1024 * 1024
TAIL = 0.8  # oxirgi gapdan keyin video yana shuncha soniya davom etadi
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
    chunks: list | None = None
    moments: list | None = None
    script: list | None = None
    title: str = ""
    caption: str = ""
    tags: list = field(default_factory=list)
    voice: str | None = None
    concat: str | None = None
    bg: str | None = None
    square: str | None = None
    timing: dict | None = None
    layout: str = "square"
    busy: str | None = None
    task: asyncio.Task | None = None
    last: float = field(default_factory=time.time)
    variant: int = 0


JOBS: dict[int, Job] = {}
GEMINI_STATUS = "tekshirilmagan"


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
    job.caption = hide_title((res.get("izoh") or "").strip(), job)
    job.tags = clean_tags(res.get("heshteglar") or [], job)
    if job.code:
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
    for t in BASE_TAGS.split() + [str(x) for x in tags]:
        t = "#" + re.sub(r"[^\w]+", "", t.strip().lstrip("#"), flags=re.UNICODE)
        key = t[1:].lower()
        if len(key) < 2 or key in seen or any(b and (b in key or key in b) and len(key) > 3 for b in banned):
            continue
        seen.add(key)
        out.append(t)
    return out[:16]


def insta_caption(job):
    parts = []
    if job.caption:
        parts.append(job.caption)
    if job.code:
        parts.append(CAPTION_CTA.format(kod=job.code))
    if job.tags:
        parts.append(" ".join(job.tags))
    return "\n\n".join(parts)


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
        await bot.send_message(job.chat_id, f"🔢 Kino kodini yozing ({esc(WATERMARK)} uchun).\nMasalan: <i>125</i>")
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
            if not job.has_audio:
                raise RuntimeError("Kinoda ovoz yo‘lagi yo‘q — Gemini voqealarni eshita olmaydi")
        job.last = time.time()
        if not job.chunks:
            await st.set("🎧 Kinoning ovozi ajratilmoqda…", force=True)
            async with HEAVY:
                job.chunks = await video.extract_audio_chunks(job.movie, job.dir, job.duration)
        if not await wait_for(lambda: job.name, 1800):
            job.name = os.path.splitext(job.file_name)[0]
        if not job.moments:
            moments = []
            for k, (cpath, mime, start, length) in enumerate(job.chunks):
                n = len(job.chunks)
                await st.set(f"🤖 Gemini kinoni eshityapti{f' ({k + 1}/{n})' if n > 1 else ''}… Bu bir necha daqiqa oladi.",
                             force=True)
                moments += await gem.scene_log(HTTP, cpath, mime, job.name, k, n, start, length)
                job.last = time.time()
            if len(moments) < 3:
                raise RuntimeError("Gemini kinodan yetarli voqea topmadi")
            moments.sort(key=lambda m: m["t"])
            job.moments = moments
        await st.set("✍️ Hikoya matni yozilmoqda…", force=True)
        if not job.code:
            await wait_for(lambda: job.code, 1800)
        res = await gem.write_script(HTTP, job.name, job.duration, job.moments, job.variant)
        job.script = build_script(job, res)
        job.last = time.time()
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
    await bot.send_message(job.chat_id, text, reply_markup=kb([("🔄 Boshqa matn", "regen")]))


async def regen(job):
    st = Status(job.chat_id, (await bot.send_message(job.chat_id, "✍️ Yangi matn yozilmoqda…")).message_id)
    try:
        job.variant += 1
        res = await gem.write_script(HTTP, job.name, job.duration, job.moments, job.variant)
        job.script = build_script(job, res)
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
    st = Status(job.chat_id, (await bot.send_message(job.chat_id, "🎙 Ovoz qabul qilindi. Video yig‘ilmoqda…")).message_id)
    try:
        wav = os.path.join(job.dir, "voice.wav")
        await video.voice_to_wav(job.voice, wav)
        vs, ve, pauses, level = video.speech_layout(wav)
        weights = [video.text_weight(s["matn"]) for s in job.script]
        segs = video.sentence_bounds(vs, ve, pauses, weights)
        seg_durs = [b - a for a, b in segs]
        seg_durs[-1] += TAIL
        total = float(sum(seg_durs))
        windows = [(s["start"], s["end"]) for s in job.script]
        shots = video.plan_shots(seg_durs, windows, job.duration)
        has_cta = bool(job.script[-1].get("cta") and job.code)
        cta = {"line1": CTA_LINE1, "line2": f"KOD: {job.code}"} if has_cta else None
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
                ov = video.text_overlays(job.dir, WATERMARK, cta if is_cta else None, fade=is_cta and first_cta)
                first_cta = first_cta and not is_cta
                vo, ao = os.path.join(job.dir, f"shot_{i:03d}.mp4"), os.path.join(job.dir, f"shot_{i:03d}.wav")
                await video.render_shot(job.movie, t, sh["frames"], vo, ao, job.has_audio, ov)
                vfiles.append(vo)
                afiles.append(ao)
                job.last = time.time()
                await st.set(f"✂️ Sahnalar qirqilmoqda: {i + 1}/{len(shots)}")
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
        async with HEAVY:
            t0 = time.time()
            if not (job.square and os.path.exists(job.square)):
                sq = os.path.join(job.dir, "kino_1x1.mp4")
                tm = job.timing
                await video.final_mux(job.concat, job.bg, job.voice, tm["vs"], tm["ve"], tm["total"], sq, tm["level"])
                job.square = sq
            out, w, h = job.square, video.SIZE, video.SIZE
            if layout == "vertical":
                out = os.path.join(job.dir, "kino_9x16.mp4")
                if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(job.square):
                    await st.set("📐 9:16 variant tayyorlanmoqda…", force=True)
                    await video.make_vertical(job.square, out)
                w, h = video.V_W, video.V_H
            log.info("yakuniy %s %.0fs", layout, time.time() - t0)
        job.layout = layout
        note = "✅ Tayyor!\n🎙 Boshqa ovoz yuborsangiz — qayta yig‘aman.\n✏️ Matnni o‘zgartirish uchun yangi matnni yozing."
        other = ("📐 9:16 (Reels)", "v916") if layout == "square" else ("⬛ 1:1 (namunadek)", "v11")
        await bot.send_video(job.chat_id, FSInputFile(out, filename=os.path.basename(out)),
                             duration=int(round(job.timing["total"])), width=w, height=h,
                             supports_streaming=True, caption=note,
                             reply_markup=kb([other], [("🔄 Boshqa matn", "regen")]), request_timeout=900)
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


# ---------------- Xabarlar ----------------

WELCOME = (
    "Salom! 🎬 Men kinodan 1 daqiqalik qisqa hikoya-video yasayman.\n\n"
    "1) Kinoni shu yerga <b>fayl qilib</b> yuboring yoki kanalingizdan forward qiling (2 GB gacha).\n"
    "2) Kino nomini va kodini yozasiz.\n"
    "3) Men hikoya matnini yozib beraman — uni o‘qib, <b>ovozli xabar</b> yuborasiz.\n"
    "4) Sahnalarni qirqib, ovozingiz bilan tayyor videoni yuboraman.\n\n"
    "Buyruqlar: /bekor — joriy ishni to‘xtatish, /kod 125 — kodni o‘zgartirish."
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
    if job.script:
        if job.script[-1].get("cta"):
            job.script[-1]["matn"] = CTA_TEXT.format(kod=code)
        else:
            job.script.append(cta_item(job))
        return await m.answer(f"✅ Kod: {esc(code)}\nOxirgi gap: <i>{esc(job.script[-1]['matn'])}</i>\n"
                              "Ovozni shu gap bilan o‘qib yuboring.")
    await m.answer(f"✅ Kod: {esc(code)}")


@dp.message(F.video | F.document)
async def on_file(m: Message):
    if not allowed(m.from_user.id):
        return
    media = m.video or m.document
    fname = getattr(media, "file_name", None) or ("kino.mp4" if m.video else "fayl")
    mime = (media.mime_type or "").lower()
    if m.document and not (mime.startswith("video/") or VIDEO_EXT.search(fname)):
        if mime.startswith("audio/") or mime == "application/ogg":
            return await on_voice(m)
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
    sm = await m.answer(f"🎬 Kino qabul qilindi ({human_mb(size)}). Yuklab olyapman…")
    job.status = Status(m.chat.id, sm.message_id)
    job.task = asyncio.create_task(analyse(job))
    await ask_next(job)


@dp.message(F.voice | F.audio)
async def on_voice(m: Message):
    if not allowed(m.from_user.id):
        return
    job = JOBS.get(m.from_user.id)
    if not job or not job.movie:
        return await m.answer("Avval kinoni yuboring 🎬")
    if not job.script:
        return await m.answer("⏳ Matn hali tayyor emas. Tayyor bo‘lgach, o‘shani o‘qib yuborasiz.")
    if job.busy:
        return await m.answer("⏳ Video yig‘ilyapti. Tugagach, yangi ovozni yuborishingiz mumkin.")
    media = m.voice or m.audio or m.document
    ext = ".ogg" if m.voice else (os.path.splitext(getattr(media, "file_name", "") or "")[1] or ".m4a")
    path = os.path.join(job.dir, f"voice_{int(time.time())}{ext}")
    try:
        if (media.file_size or 0) <= BOT_API_LIMIT:
            await bot.download(media.file_id, destination=path, timeout=300)
        elif BIG is not None:
            await BIG.download(m.message_id, path)
        else:
            return await m.answer("Ovoz fayli juda katta.")
    except Exception as e:
        return await m.answer(f"❌ Ovozni yuklab bo‘lmadi: {short_err(e)}")
    job.voice = path
    job.last = time.time()
    job.task = asyncio.create_task(render(job))


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if not allowed(m.from_user.id):
        return await m.answer(f"Bu bot shaxsiy 🙂\nSizning ID: <code>{m.from_user.id}</code>")
    job = JOBS.get(m.from_user.id)
    text = (m.text or "").strip()
    if not job:
        return await m.answer("Kinoni fayl qilib yuboring 🎬 (/start — yo‘riqnoma)")
    job.last = time.time()
    if job.asking == "name":
        job.name, code = split_name_code(text)
        if code and not job.code:
            job.code = clean_code(code)
        await ask_next(job)
        if not job.asking:
            await m.answer("✅ Rahmat! Matn tayyor bo‘lgach yuboraman.")
        return
    if job.asking == "code":
        job.code, job.asking = clean_code(text), None
        return await m.answer("✅ Rahmat! Matn tayyor bo‘lgach yuboraman.")
    if job.script and not job.busy:
        sentences = split_sentences(text)
        if len(sentences) < 2:
            return await m.answer("Matnni kamida 2 ta gap qilib yuboring.")
        job.script = remap_script(job, sentences)
        return await m.answer("✅ Matn yangilandi. Endi shu matnni o‘qib, ovozli xabar yuboring 🎙")
    await m.answer("⏳ Ishlayapman, biroz kuting…")


@dp.callback_query(F.data.in_({"regen", "retry", "v916", "v11"}))
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
    elif c.data in ("v916", "v11"):
        if not (job.concat and os.path.exists(job.concat) and job.bg and job.timing):
            return await bot.send_message(job.chat_id, "Avval ovozli xabar yuboring 🎙")
        job.task = asyncio.create_task(finish(job, "vertical" if c.data == "v916" else "square"))


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
    global HTTP
    os.makedirs(WORK_DIR, exist_ok=True)
    HTTP = aiohttp.ClientSession()
    asyncio.create_task(keepalive())
    await check_gemini()
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Yo‘riqnoma"),
            BotCommand(command="bekor", description="Joriy ishni to‘xtatish"),
            BotCommand(command="kod", description="Kino kodini o‘zgartirish"),
        ])
    except Exception as e:
        log.warning("set_my_commands: %r", e)
    me = await bot.get_me()
    log.info("Bot ulandi: @%s (katta fayllar: %s, Gemini: %s)", me.username, "ha" if BIG else "yo‘q",
             "ha" if GEMINI_API_KEY else "yo‘q")


async def on_shutdown():
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
