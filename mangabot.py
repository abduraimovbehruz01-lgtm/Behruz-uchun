"""Manga-video: botdagi /manga buyrug'i.

Oqim: /manga → manga sahifalari (rasm yoki ZIP/CBZ) → «✅ Tugadi» → bot panellarni kesib raqamlangan rasm yuboradi →
siz har panel uchun bitta qator matn yuborasiz → ovozsiz, ElevenLabs ovozi bilan yoki o'zingiz yuborgan audio
(Blox lab, o'z ovozingiz) bilan subtitrli vertikal (9:16) video yasaydi, qo'shiq qo'shadi.

Bu modul bot.py dagi umumiy narsalardan (bot, dp, music, gem, HEAVY ...) foydalanadi: setup() ularni oladi va
handlerlarni dp ga BIRINCHI bo'lib ro'yxatdan o'tkazadi (kino oqimiga xalaqit bermaslik uchun faqat /manga seansi
ochiq bo'lgandagina ishlaydi)."""
import asyncio
import html
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
                           Message)

import manga
import video

log = logging.getLogger("mangabot")

D = None  # setup() to'ldiradi: umumiy narsalar (bot, music, gem ...)
SESS: dict = {}
IMG_MIME = ("image/jpeg", "image/png", "image/webp", "image/bmp", "image/gif", "image/tiff")
ZIP_EXT = re.compile(r"\.(zip|cbz)$", re.I)
NUM_LINE = re.compile(r"^\s*(\d{1,3})\s*[.):]\s+(.*)$")
AUDIO_EXT = re.compile(r"\.(mp3|m4a|aac|wav|ogg|oga|opus|flac|amr|wma|mp4a)$", re.I)
TEXT_WAIT = 2.0   # uzun matnni Telegram bir necha xabarga bo'ladi — shuncha kutib, birlashtiramiz
MAX_FILE = 200 * 1024 * 1024


@dataclass
class Sess:
    uid: int
    chat_id: int
    dir: str
    entries: list = field(default_factory=list)   # {"mid": xabar id, "paths": [fayllar]}
    pending: int = 0
    state: str = "pages"        # pages → busy → texts → busy → done
    msg_id: int | None = None
    rtl: bool = False
    panels: list = field(default_factory=list)
    texts: list | None = None
    orig: list | None = None     # foydalanuvchi yuborgan asl qatorlar (tarjimadan oldin)
    silent: str | None = None
    total: float = 0.0
    out: str | None = None
    music_id: str | None = None
    no_music: bool = False
    last: float = field(default_factory=time.time)
    refresh_task: asyncio.Task | None = None
    last_user_mid: int | None = None
    seq: int = 0
    infos: list | None = None      # panel kadrlari (prepare_all), qayta hisoblamaslik uchun
    voice_wav: str | None = None   # ovozli videoning ovoz yo'lagi
    voice_level: float = -20.0
    voice_name: str = ""
    buf: list = field(default_factory=list)   # kelayotgan matn bo'laklari
    buf_t: float = 0.0
    text_task: asyncio.Task | None = None


def esc(s):
    return html.escape(str(s), quote=False)


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


def fmt_sec(x):
    x = int(round(x))
    return f"{x // 60}:{x % 60:02d}"


def pages_of(s):
    out = []
    for e in sorted(s.entries, key=lambda e: e["mid"]):
        out.extend(e["paths"])
    return out


def mark(s, state=None):
    """Seans belgisi diskda: server qayta ishga tushsa (masalan, xotira yetmay), foydalanuvchiga xabar beramiz."""
    try:
        import json
        with open(os.path.join(s.dir, "session.json"), "w") as f:
            json.dump({"uid": s.uid, "chat_id": s.chat_id, "state": state or s.state}, f)
    except Exception:
        pass


async def notify_restart():
    """Bot qayta ishga tushganda: tugallanmay qolgan manga-video seanslari egalariga xabar."""
    import json
    try:
        names = os.listdir(D.work_dir)
    except OSError:
        return
    for n in names:
        p = os.path.join(D.work_dir, n)
        f = os.path.join(p, "session.json")
        if not (n.startswith("manga_") and os.path.isfile(f)):
            continue
        try:
            with open(f) as fh:
                info = json.load(fh)
            if info.get("state") != "done" and time.time() - os.path.getmtime(f) < 6 * 3600:
                await D.bot.send_message(
                    info["chat_id"],
                    "⚠️ Bot qayta ishga tushdi, shuning uchun manga-video to‘xtab qoldi.\n"
                    "Iltimos, /manga bilan qaytadan boshlang va sahifalarni yana yuboring.")
        except Exception as e:
            log.warning("manga: qayta ishga tushish xabari yuborilmadi: %r", e)
        shutil.rmtree(p, ignore_errors=True)


def drop(uid):
    s = SESS.pop(uid, None)
    if s:
        if s.refresh_task and not s.refresh_task.done():
            s.refresh_task.cancel()
        shutil.rmtree(s.dir, ignore_errors=True)


def sweep_old(work_dir, hours=6):
    try:
        for n in os.listdir(work_dir):
            p = os.path.join(work_dir, n)
            if n.startswith("manga_") and os.path.isdir(p) and time.time() - os.path.getmtime(p) > hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------- sahifalar yig'ish

def _pages_text(s):
    n = sum(len(e["paths"]) for e in s.entries)
    t = f"📥 Qabul qilindi: <b>{n}</b> ta sahifa"
    if s.pending:
        t += f" (yana {s.pending} ta yuklanmoqda…)"
    return (t + "\nYana yuborishingiz mumkin. Hammasi tugagach «✅ Tugadi» tugmasini bosing.")


async def _refresh_now(s):
    try:
        await D.bot.edit_message_text(_pages_text(s), chat_id=s.chat_id, message_id=s.msg_id,
                                      reply_markup=kb([("✅ Tugadi", "mg_done")], [("❌ Bekor", "mg_cancel")]))
    except TelegramBadRequest:
        pass
    except Exception as e:
        log.warning("manga status: %r", e)


def schedule_refresh(s):
    if s.refresh_task and not s.refresh_task.done():
        s.refresh_task.cancel()

    async def go():
        await asyncio.sleep(1.5)
        await _refresh_now(s)

    s.refresh_task = asyncio.create_task(go())


async def cmd_manga(m: Message):
    if not D.allowed(m.from_user.id):
        return await m.answer(f"Bu bot shaxsiy 🙂\nSizning ID: <code>{m.from_user.id}</code>")
    drop(m.from_user.id)
    d = os.path.join(D.work_dir, f"manga_{m.from_user.id}_{int(time.time())}")
    os.makedirs(os.path.join(d, "pages"), exist_ok=True)
    s = Sess(uid=m.from_user.id, chat_id=m.chat.id, dir=d)
    SESS[s.uid] = s
    mark(s)
    sm = await m.answer(
        "🎞 <b>Manga-video</b>\n\n"
        "Manga sahifalarini yuboring: <b>rasm</b> (sifat uchun «fayl» qilib yuborgan yaxshi), <b>PDF</b> yoki <b>ZIP / CBZ</b>. "
        "Webtoon bo‘laklarini ham shunday yuborasiz.\n\n"
        "Hammasini yuborib bo‘lgach «✅ Tugadi» ni bosing — men panellarni qirqib, raqamlab ko‘rsataman.",
        reply_markup=kb([("❌ Bekor", "mg_cancel")]))
    s.msg_id = sm.message_id
    sweep_old(D.work_dir)


def _is_state(state):
    async def f(m: Message):
        s = SESS.get(m.from_user.id)
        return bool(s and s.state == state)
    return f


async def on_page(m: Message):
    """Sahifa (rasm, rasm-fayl yoki ZIP) qabul qilish."""
    s = SESS.get(m.from_user.id)
    if not s or s.state != "pages":
        raise SkipHandler()
    s.last = time.time()
    if m.photo:
        ph = m.photo[-1]
        kind, fid, size, name = "img", ph.file_id, ph.file_size or 0, f"{m.message_id}.jpg"
    else:
        doc = m.document
        name = doc.file_name or f"{m.message_id}"
        mime = (doc.mime_type or "").lower()
        if mime in IMG_MIME or manga.IMG_EXT.search(name):
            kind = "img"
        elif ZIP_EXT.search(name) or mime in ("application/zip", "application/x-zip-compressed",
                                              "application/vnd.comicbook+zip"):
            kind = "zip"
        elif mime == "application/pdf" or name.lower().endswith(".pdf"):
            kind = "pdf"
        else:
            raise SkipHandler()    # qo'shiq/video bo'lsa — oddiy oqim ko'rsin
        fid, size = doc.file_id, doc.file_size or 0
    if size > MAX_FILE:
        return await m.answer("Fayl juda katta (200 MB gacha).")
    if size > 20 * 1024 * 1024 and D.big is None:
        return await m.answer("Bu fayl 20 MB dan katta. Kichikroq qismlarga bo‘lib yuboring.")
    ext = os.path.splitext(name)[1].lower() or (".zip" if kind == "zip" else ".jpg")
    path = os.path.join(s.dir, "pages", f"{m.message_id:09d}{ext}")
    s.pending += 1
    schedule_refresh(s)
    try:
        if size > 20 * 1024 * 1024:
            await D.big.download(m.message_id, path)
        else:
            await D.bot.download(fid, destination=path, timeout=300)
        if kind == "zip":
            paths = await asyncio.to_thread(manga.extract_zip, path, os.path.join(s.dir, "pages", f"z{m.message_id}"))
            os.remove(path)
            if not paths:
                await m.answer("ZIP ichida rasm topilmadi.")
        elif kind == "pdf":
            paths, note = await asyncio.to_thread(manga.pdf_to_images, path,
                                                  os.path.join(s.dir, "pages", f"z{m.message_id}"))
            os.remove(path)
            if note:
                await m.answer(note)
            if not paths:
                await m.answer("PDF ichidan sahifa chiqmadi.")
        else:
            paths = [path]
        if paths and kind != "pdf":     # juda uzun rasmlar (webtoon tasmasi) bo'laklarga bo'linadi
            paths = await asyncio.to_thread(lambda ps: [q for p in ps for q in manga.slice_tall(p)], paths)
        if paths:
            s.entries.append({"mid": m.message_id, "paths": paths})
    except Exception as e:
        log.exception("manga sahifa xato")
        await m.answer(f"❌ Fayl olinmadi: {esc(D.short_err(e))}")
    finally:
        s.pending -= 1
        schedule_refresh(s)


# ---------------------------------------------------------------- panellarni topish

def _texts_prompt(s):
    n = len(s.panels)
    return (f"✅ <b>{n}</b> ta panel topildi (raqamlari rasmda).\n\n"
            "Endi matnni yuboring: <b>har panel uchun bitta qator</b> (1-qator → 1-panel, 2-qator → 2-panel …). "
            "Panelga matn kerak bo‘lmasa shu qatorga «-» yozing.\n"
            "Boshqacha usul: qatorlarni raqam bilan yozsangiz (<code>3. matn</code>), faqat kerakli panellarga qo‘yiladi.\n"
            "🌐 Inglizcha yoki boshqa tilda yozsangiz ham bo‘ladi — o‘zbekchaga o‘giraman va avval sizga ko‘rsataman.\n"
            "🎙 Matndan keyin ovoz qo‘shish mumkin: ElevenLabs yoki o‘zingiz yuborgan audio (Blox lab, o‘z ovozingiz).\n\n"
            "Panellar noto‘g‘ri kesilgan yoki tartibi teskari bo‘lsa — pastdagi tugma bilan o‘qish yo‘nalishini almashtiring.")


def _texts_kb(s):
    way = "o‘ngdan chapga (yapon manga)" if s.rtl else "chapdan o‘ngga (webtoon, manhva)"
    return kb([(f"↔ Hozir: {way}", "mg_rtl")], [("🎬 Matnsiz yasash", "mg_go")], [("❌ Bekor", "mg_cancel")])


async def analyse(s):
    s.state = "busy"
    st = await D.bot.send_message(s.chat_id, "✂️ Panellar qirqilmoqda…")
    try:
        async with D.heavy:
            panels = await asyncio.to_thread(manga.detect_panels, pages_of(s), os.path.join(s.dir, "panels"), s.rtl)
            sheets = await asyncio.to_thread(manga.contact_sheets, panels, s.dir) if panels else []
    except Exception as e:
        log.exception("manga panel xato")
        s.state = "pages"
        return await st.edit_text(f"❌ Panellarni qirqib bo‘lmadi: {esc(D.short_err(e))}\n"
                                  "Sahifalarni qayta yuboring yoki /manga bilan boshidan boshlang.")
    if not panels:
        s.state = "pages"
        return await st.edit_text("Panel topilmadi. Rasmlar to‘g‘ri ekanini tekshiring.")
    s.panels, s.texts, s.infos, s.voice_wav = panels, None, None, None
    try:
        await st.delete()
    except Exception:
        pass
    if len(sheets) == 1:
        await D.bot.send_photo(s.chat_id, FSInputFile(sheets[0]))
    else:
        for i in range(0, len(sheets), 10):
            await D.bot.send_media_group(s.chat_id, [InputMediaPhoto(media=FSInputFile(p)) for p in sheets[i:i + 10]])
    await D.bot.send_message(s.chat_id, _texts_prompt(s), reply_markup=_texts_kb(s))
    s.state = "texts"
    mark(s)


# ---------------------------------------------------------------- matn

def parse_lines(text, n):
    """Matnni panellarga bo'ladi. Qaytaradi (texts, ogohlantirish|None, xato|None)."""
    raw = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not raw:
        return None, None, "Matn bo‘sh."
    m = [NUM_LINE.match(ln) for ln in raw]
    if len(raw) >= 2 and all(m):
        nums = [int(x.group(1)) for x in m]
        if nums == sorted(nums) and len(set(nums)) == len(nums) and nums[-1] <= n and nums[0] >= 1:
            out = [""] * n
            for x in m:
                out[int(x.group(1)) - 1] = x.group(2).strip()
            return [("" if t == "-" else t) for t in out], None, None
    if len(raw) > n:
        return None, None, (f"Qator ko‘p: {len(raw)} ta qator, lekin panel {n} ta. "
                            "Ortiqcha qatorlarni olib tashlab, qayta yuboring.")
    out = [("" if t in ("-", "—", "–") else t) for t in raw]
    warn = None
    if len(out) < n:
        warn = (f"⚠️ {len(out)} ta qator, panel {n} ta. Oxirgi {n - len(out)} ta panel matnsiz chiqadi. "
                "Shunday yasaymi? Yoki to‘liq matnni qayta yuboring.")
        out += [""] * (n - len(out))
    return out, warn, None


TR_SCHEMA = {"type": "OBJECT", "properties": {"qatorlar": {"type": "ARRAY", "items": {"type": "STRING"}}},
             "required": ["qatorlar"]}


def _norm(t):
    return " ".join((t or "").lower().replace("ʻ", "‘").replace("’", "‘").replace("'", "‘").split())


async def translate_lines(lines):
    """Qatorlarni o'zbekchaga (lotin) o'giradi. O'zbekcha qatorlar o'zgarishsiz qoladi.
    Qaytaradi: yangi qatorlar ro'yxati yoki None (Gemini javob bermasa)."""
    idx = [i for i, t in enumerate(lines) if t.strip()]
    if not idx:
        return list(lines)
    numbered = "\n".join(f"{k + 1}. {lines[i]}" for k, i in enumerate(idx))
    prompt = (
        "You prepare subtitles for a short vertical video made from manga / webtoon panels. "
        f"Below are {len(idx)} numbered subtitle lines, one per panel. For EACH line:\n"
        "- if it is already Uzbek in Latin script, return it EXACTLY unchanged;\n"
        "- if it is Uzbek in Cyrillic, convert it to Latin script;\n"
        "- otherwise translate it into natural, vivid spoken Uzbek (Latin script, apostrophes as in o‘, g‘), "
        "short and punchy like a subtitle, keeping the emotion, punctuation and character names.\n"
        f"Return JSON {{\"qatorlar\": [...]}} with exactly {len(idx)} strings in the same order, no numbering.\n\n"
        + numbered)
    for attempt in range(2):
        try:
            data = await D.gem.generate_json(D.http(), [{"text": prompt}], TR_SCHEMA, temperature=0.3,
                                             timeout=120, max_wait=60)
            out = [str(x).strip() for x in (data.get("qatorlar") or [])]
            if len(out) == len(idx):
                res = list(lines)
                for k, i in enumerate(idx):
                    res[i] = out[k] or lines[i]
                return res
            log.warning("tarjima: qator soni mos emas (%d != %d)", len(out), len(idx))
        except Exception as e:
            log.warning("tarjima xato (%d): %r", attempt + 1, e)
            break
    return None


def _preview(texts, limit=3300):
    rows = [f"{i + 1}) {t if t else '—'}" for i, t in enumerate(texts)]
    out, size = [], 0
    for r in rows:
        if size + len(r) > limit:
            out.append("…")
            break
        out.append(r)
        size += len(r) + 1
    return esc("\n".join(out))


def _choice_text(s):
    n_txt = sum(1 for t in (s.texts or []) if t)
    t = (f"📝 Matn tayyor: <b>{n_txt}</b> ta qator, <b>{len(s.panels)}</b> ta panel.\n\n"
         "Endi tanlang:\n"
         "🎬 <b>Ovozsiz</b> — subtitr va qo‘shiq.\n")
    if D.eleven_key:
        t += "🎙 <b>ElevenLabs</b> — bot matnni o‘zi o‘qitadi.\n"
    t += ("🎤 <b>O‘z audiongiz</b> — Blox lab yoki o‘zingiz yozgan ovozni shu yerga yuboring (forward ham bo‘ladi): "
          "bot har bir qatorni ovozga moslaydi. Ovoz uchun toza matnni «📋 Ovoz uchun matn» beradi.")
    return t


def _choice_kb(s, with_orig=False):
    rows = [[("🎬 Ovozsiz yasash", "mg_go")]]
    if D.eleven_key:
        rows.append([("🎙 ElevenLabs bilan o‘qit", "mg_eleven")])
    rows.append([("📋 Ovoz uchun matn", "mg_copy")])
    if with_orig:
        rows.append([("↩️ Asl matnni qoldirish", "mg_orig")])
    return kb(*rows)


async def _st(msg, text):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


async def on_text(m: Message):
    """Matn qatorlari. Telegram uzun matnni bir necha xabarga bo'ladi — 2 soniya jimlikdan keyin birlashtiramiz."""
    s = SESS.get(m.from_user.id)
    if not s or s.state != "texts":
        raise SkipHandler()
    s.last = time.time()
    s.last_user_mid = m.message_id
    s.buf.append((m.message_id, m.text or ""))
    s.buf_t = time.time()
    if not s.text_task or s.text_task.done():
        s.text_task = asyncio.create_task(_text_worker(s))


async def _text_worker(s):
    try:
        while s.buf:
            while time.time() - s.buf_t < TEXT_WAIT:
                await asyncio.sleep(max(0.2, TEXT_WAIT - (time.time() - s.buf_t)))
            parts = [t for _, t in sorted(s.buf)]
            s.buf = []
            if s.state != "texts" or SESS.get(s.uid) is not s:
                return
            await process_text(s, "\n".join(parts))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("manga matn xato")
        try:
            await D.bot.send_message(s.chat_id, f"❌ Matnni o‘qishda xatolik: {esc(D.short_err(e))}")
        except Exception:
            pass


async def process_text(s, text):
    texts, warn, err = parse_lines(text, len(s.panels))
    if err:
        return await D.bot.send_message(s.chat_id, "❌ " + err)
    s.orig = texts
    s.texts = texts
    s.voice_wav = None          # yangi matn — eski ovoz endi mos kelmaydi
    st = await D.bot.send_message(s.chat_id, "🌐 Matn tekshirilmoqda (o‘zbekchaga o‘giraman)…") if any(texts) else None
    tr = await translate_lines(texts) if any(texts) else texts
    if st:
        try:
            await st.delete()
        except Exception:
            pass
    if tr is None:
        note = "⚠️ Tarjima qilib bo‘lmadi (Gemini band), matn o‘zgartirilmadi."
        if warn:
            note += "\n" + warn
        return await D.bot.send_message(s.chat_id, note + "\n\n" + _choice_text(s), reply_markup=_choice_kb(s))
    changed = any(_norm(a) != _norm(b) for a, b in zip(texts, tr))
    s.texts = tr
    if changed:
        tail = "\n\nTuzatmoqchi bo‘lsangiz — tuzatilgan matnni qayta yuboring." + (f"\n\n{warn}" if warn else "")
        await D.bot.send_message(s.chat_id, "🌐 <b>O‘zbekchaga o‘girdim:</b>\n\n" + _preview(tr) + tail)
        return await D.bot.send_message(s.chat_id, _choice_text(s), reply_markup=_choice_kb(s, with_orig=True))
    await D.bot.send_message(s.chat_id, (warn + "\n\n" if warn else "") + _choice_text(s), reply_markup=_choice_kb(s))


def _voice_lines(texts):
    return [t for t in (texts or []) if t]


def _tts_line(t):
    """Ovoz uchun qator: oxirida tinish belgisi bo'lmasa nuqta qo'yiladi — qatorlar orasida pauza aniq bo'lsin."""
    t = t.strip()
    return t if re.search(r"[.!?…:;,»\"')\]]$", t) else t + "."


async def send_voice_text(s):
    lines = [_tts_line(t) for t in _voice_lines(s.texts)]
    if not lines:
        return await D.bot.send_message(s.chat_id, "Matnda ovoz uchun qator yo‘q.")
    chunks, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > 3500:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        chunks.append(cur)
    await D.bot.send_message(
        s.chat_id, f"📋 <b>Ovoz uchun matn</b> ({len(lines)} qator, tartib bilan). Bosib nusxa oling, Blox lab'ga "
                   "tashlab ovozga aylantiring va tayyor audioni shu yerga yuboring.")
    for ch in chunks:
        await D.bot.send_message(s.chat_id, f"<pre>{esc(ch.strip())}</pre>")


# ---------------------------------------------------------------- yasash

async def pick_music(s, other=False):
    """Qo'shiq tanlaydi (kutubxona bo'sh/o'chirilgan bo'lsa — None). Xato videoni to'xtatmaydi."""
    if s.no_music:
        return None
    try:
        lib = await D.music.load(D.bot, s.chat_id, D.work_dir)
        if lib.get("off") or not lib.get("songs"):
            return None
        song = D.music.find(lib, s.music_id) if (s.music_id and not other) else None
        if song is None:
            if other and s.music_id:
                song = D.music.next_other(lib, s.music_id)
            if song is None:
                text = " ".join(t for t in (s.texts or []) if t)
                song = await D.music.pick(D.gem, D.http(), lib, "manga", text)
            if not song:
                return None
            s.music_id = song["id"]
            song["used"] = int(song.get("used", 0)) + 1
            lib["last"] = song["id"]
            D.music.save_soon(D.bot, s.chat_id)
        path = await D.music.fetch(D.bot, D.big, song, D.music_dir)
        if await D.music.ensure_best(path, song, D.music_dir):
            D.music.save_soon(D.bot, s.chat_id)
        return {"path": path, "start": D.music.music_start(song), "name": song.get("name", "qo‘shiq"),
                "rms": float(song.get("rms", -20))}
    except Exception as e:
        log.warning("manga musiqa qo'shilmadi: %r", e)
        return None


async def render(s, durs=None, st=None, prev=None):
    """Panellardan video yasaydi. durs — ovozli videoda har panel davomiyligi (bo'lmasa — ovozsiz video)."""
    prev = prev or ("done" if s.out else "texts")
    s.state = "busy"
    mark(s)
    if durs is None:
        s.voice_wav = None
    head = "🎞 Video yasalmoqda…"
    if st is None:
        st = await D.bot.send_message(s.chat_id, f"{head} (0/{len(s.panels)})")
    else:
        await _st(st, f"{head} (0/{len(s.panels)})")
    try:
        async def prog(i, n):
            await _st(st, f"{head} ({i}/{n})")

        async with D.heavy:
            if s.infos is None or len(s.infos) != len(s.panels):
                s.infos = await manga.prepare_all(s.panels, s.dir)
            s.silent, s.total = await manga.render_video(s.panels, s.texts or [], s.dir, prog, durs=durs,
                                                         infos=s.infos)
        await finish(s, st)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("manga render xato")
        s.state = prev
        await _st(st, f"❌ Video yasashda xatolik: {esc(D.short_err(e))}\nQayta urinib ko‘ring.")


async def voice_segments(s, audio_path, lines, anchors=None, st=None):
    """Aytilgan qatorlarning ovozdagi (boshlanish, tugash) vaqtlari. Qaytaradi (segs, balandlik_dB)."""
    wav = os.path.join(s.dir, "voice16.wav")
    await video.voice_to_wav(audio_path, wav)
    vs, ve, pauses, level = await asyncio.to_thread(video.speech_layout, wav)
    weights = [video.text_weight(t) for t in lines]
    segs = None
    if anchors:
        segs = video.bounds_from_anchors(vs, ve, anchors, weights, pauses)
        if segs:
            log.info("manga ovoz: ElevenLabs vaqtlari ishlatildi")
    if not segs and len(lines) > 1 and D.gem is not None and D.gemini is not None:
        if st:
            await _st(st, "🎧 Har bir qator ovozda qayerda boshlanishi aniqlanmoqda…")
        try:
            amp3 = os.path.join(s.dir, "voice_align.mp3")
            await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a",
                             "48k", amp3], timeout=300)
            anc = await D.gemini.align_voice(D.gem, D.http(), amp3, lines)
            segs = video.bounds_from_anchors(vs, ve, anc, weights, pauses)
            log.info("manga ovoz moslash: %d/%d qator topildi", len(anc), len(lines))
        except Exception as e:
            log.warning("manga: ovozni Gemini bilan moslab bo'lmadi: %r", e)
    if not segs:
        segs = video.sentence_bounds(vs, ve, pauses, weights)
    return manga.snap_into_pauses(segs, pauses), level


async def voice_render(s, audio_path, st, source, anchors=None):
    """Ovoz bilan video: qatorlarni ovozga moslab, har panel o'z gapi davomida turadi."""
    prev = "done" if s.out else "texts"
    s.state = "busy"
    mark(s)
    try:
        lines = _voice_lines(s.texts)
        if not lines:
            s.state = prev
            return await _st(st, "Matnda ovoz uchun qator yo‘q — avval matn yuboring.")
        await _st(st, "🎧 Ovoz tahlil qilinmoqda…")
        segs, level = await voice_segments(s, audio_path, lines, anchors, st)
        if s.infos is None or len(s.infos) != len(s.panels):
            await _st(st, "🖼 Kadrlar tayyorlanmoqda…")
            async with D.heavy:
                s.infos = await manga.prepare_all(s.panels, s.dir)
        durs, pieces = manga.plan_voice(s.texts, segs, [manga.min_duration(i) for i in s.infos])
        vw = os.path.join(s.dir, "voice_track.wav")
        await manga.build_voice_track(audio_path, durs, pieces, vw, s.dir)
        s.voice_wav, s.voice_level, s.voice_name = vw, level, source
        await render(s, durs=durs, st=st, prev=prev)
    except asyncio.CancelledError:
        raise
    except ValueError as e:          # ovozda nutq topilmadi / juda qisqa
        s.state = prev
        mark(s)
        await _st(st, f"❌ {esc(str(e))}. Boshqa audio yuborib ko‘ring.")
    except Exception as e:
        log.exception("manga ovozli video xato")
        s.state = prev
        mark(s)
        await _st(st, f"❌ Ovozli video yasalmadi: {esc(D.short_err(e))}")


async def eleven_render(s):
    """ElevenLabs bilan matnni o'qitib, ovozli video yasaydi."""
    prev = "done" if s.out else "texts"
    s.state = "busy"
    st = await D.bot.send_message(s.chat_id, "🤖 ElevenLabs matnni o‘qiyapti…")
    try:
        lines = [_tts_line(t) for t in _voice_lines(s.texts)]
        lib = await D.music.load(D.bot, s.chat_id, D.work_dir)
        vid = lib.get("ovoz") or D.eleven.VOICES[0][1]
        name = D.eleven.voice_name(vid)
        chunks, cur, size = [], [], 0
        for ln in lines:                  # ElevenLabs bir so'rovda ~4800 belgigacha oladi
            if cur and size + len(ln) + 2 > 4500:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(ln)
            size += len(ln) + 2
        if cur:
            chunks.append(cur)
        gap = 0.4
        parts, anchors, offset, k = [], {}, 0.0, 0
        for ci, ch in enumerate(chunks):
            p = os.path.join(s.dir, f"eleven_{s.seq}_{ci}.mp3")
            al = await D.eleven.tts(D.http(), D.eleven_key, vid, "\n\n".join(ch), p)
            for j, t0 in D.eleven.sentence_starts(ch, al).items():
                anchors[k + j] = offset + t0
            offset += await video.audio_duration(p) + (gap if len(chunks) > 1 else 0.0)
            k += len(ch)
            parts.append(p)
        audio = parts[0] if len(parts) == 1 else await video.concat_voice(
            parts, os.path.join(s.dir, f"eleven_{s.seq}_all.ogg"), gap=gap)
        left = await D.eleven.credits_left(D.http(), D.eleven_key)
        await _st(st, f"✅ Ovoz tayyor: {esc(name)}" + (f". ElevenLabs krediti qoldi: {left:,}".replace(",", " ")
                                                        if left is not None else ""))
        s.state = prev
        await voice_render(s, audio, st, f"ElevenLabs — {name}", anchors=anchors or None)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        s.state = prev
        mark(s)
        if isinstance(e, D.eleven.ElevenError):
            log.warning("manga elevenlabs xato: %s %s %s", e.status, e.code, e)
            txt = f"❌ ElevenLabs: {esc(D.eleven.friendly(e))}"
        else:
            log.exception("manga elevenlabs xato")
            txt = f"❌ Ovoz yaratilmadi: {esc(D.short_err(e))}"
        await _st(st, txt + "\n\n🎤 Blox lab yoki o‘z ovozingizni audio qilib yuborsangiz ham bo‘ladi.")


async def finish(s, st=None, other=False):
    """Qo'shiq (va ovoz) qo'shib, tayyor videoni yuboradi."""
    s.state = "busy"
    voiced = bool(s.voice_wav and os.path.exists(s.voice_wav))
    try:
        if st is None:
            st = await D.bot.send_message(s.chat_id, "🎵 Qo‘shiq almashtirilmoqda…")
        else:
            await _st(st, "🎵 Qo‘shiq qo‘shilmoqda…")
        mus = await pick_music(s, other=other)
        s.seq += 1
        out = os.path.join(s.dir, f"manga_{s.seq}.mp4")
        async with D.heavy:
            if voiced:
                await manga.mux_voice_music(s.silent, s.voice_wav, out, s.total, s.voice_level, mus)
            else:
                await manga.mux_music(s.silent, out, s.total, mus)
        if s.out and s.out != out and os.path.exists(s.out):
            try:
                os.remove(s.out)
            except OSError:
                pass
        s.out = out
        note = f"✅ Manga-video tayyor ({fmt_sec(s.total)})\n🎞 Panellar: {len(s.panels)}"
        if voiced:
            note += f"\n🎙 Ovoz: {esc(s.voice_name)}"
        if mus:
            note += f"\n🎵 Qo‘shiq: {esc(mus['name'])}"
        if not voiced:
            note += "\n\n🎤 Ovozli qilish uchun audioni (Blox lab yoki o‘z ovozingiz) shu yerga yuboring."
        rows = []
        if mus:
            rows.append([("🎵 Boshqa qo‘shiq", "mg_mnext"), ("🔇 Qo‘shiqsiz", "mg_mnone")])
        elif s.no_music:
            rows.append([("🎵 Qo‘shiq qo‘shish", "mg_mback")])
        if not voiced and D.eleven_key:
            rows.append([("🎙 ElevenLabs bilan o‘qit", "mg_eleven")])
        if not voiced:
            rows.append([("📋 Ovoz uchun matn", "mg_copy")])
        rows.append([("🔄 Matnni o‘zgartirish", "mg_again")])
        if os.path.getsize(out) > 49 * 1024 * 1024 and D.big is not None:
            await D.big.send_video(s.chat_id, out, s.total, manga.W, manga.H, hint_msg_id=s.last_user_mid)
            await D.bot.send_message(s.chat_id, note, reply_markup=kb(*rows))
        else:
            await D.bot.send_video(s.chat_id, FSInputFile(out, filename="manga_video.mp4"),
                                   duration=int(round(s.total)), width=manga.W, height=manga.H,
                                   supports_streaming=True, caption=note, reply_markup=kb(*rows),
                                   request_timeout=900)
        try:
            await st.delete()
        except Exception:
            pass
        s.state = "done"
        mark(s)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("manga finish xato")
        s.state = "done" if s.out else "texts"
        if st:
            await _st(st, f"❌ Video tayyorlashda xatolik: {esc(D.short_err(e))}")


# ---------------------------------------------------------------- tugmalar

async def on_cb(c: CallbackQuery):
    s = SESS.get(c.from_user.id)
    act = c.data
    if act == "mg_cancel":
        drop(c.from_user.id)
        await c.answer("Bekor qilindi")
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await D.bot.send_message(c.message.chat.id, "❌ Manga-video bekor qilindi. Yangisi uchun /manga")
    if not s:
        return await c.answer("Seans tugagan. /manga bilan boshlang.", show_alert=True)
    s.last = time.time()

    async def unbutton():
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    if act == "mg_done":
        if s.state != "pages":
            return await c.answer()
        if s.pending:
            return await c.answer("⏳ Fayllar hali yuklanmoqda, bir oz kuting.", show_alert=True)
        if not s.entries:
            return await c.answer("Avval sahifa yuboring.", show_alert=True)
        await c.answer()
        if s.refresh_task and not s.refresh_task.done():
            s.refresh_task.cancel()
        await unbutton()
        return await analyse(s)
    if act == "mg_rtl":
        if s.state != "texts":
            return await c.answer()
        await c.answer("O‘qish yo‘nalishi almashtirildi")
        s.rtl = not s.rtl
        return await analyse(s)
    if act == "mg_go":
        if s.state != "texts":
            return await c.answer()
        await c.answer()
        await unbutton()
        if s.texts is None:
            s.texts = [""] * len(s.panels)
        return await render(s)
    if act == "mg_orig":
        if s.state != "texts" or not s.orig:
            return await c.answer()
        await c.answer("Asl matn qoldirildi")
        await unbutton()
        s.texts = s.orig
        return await D.bot.send_message(s.chat_id, _choice_text(s), reply_markup=_choice_kb(s))
    if act == "mg_copy":
        if s.state not in ("texts", "done") or not _voice_lines(s.texts):
            return await c.answer("Avval matnni yuboring.", show_alert=True)
        await c.answer()
        return await send_voice_text(s)
    if act == "mg_eleven":
        if s.state not in ("texts", "done") or not _voice_lines(s.texts):
            return await c.answer("Avval matnni yuboring.", show_alert=True)
        if not D.eleven_key:
            return await c.answer("ElevenLabs ulanmagan.", show_alert=True)
        await c.answer()
        await unbutton()
        return await eleven_render(s)
    if act in ("mg_mnext", "mg_mnone", "mg_mback"):
        if s.state != "done" or not s.silent or not os.path.exists(s.silent):
            return await c.answer("Hozir bu mumkin emas.", show_alert=True)
        await c.answer()
        if act == "mg_mnone":
            s.no_music = True
        elif act == "mg_mback":
            s.no_music = False
        return await finish(s, other=(act == "mg_mnext"))
    if act == "mg_again":
        if s.state != "done":
            return await c.answer()
        await c.answer()
        s.state = "texts"
        s.texts = None
        s.voice_wav = None
        mark(s)
        return await D.bot.send_message(
            s.chat_id, "Yangi matnni yuboring (har panel uchun bitta qator). "
                       f"Panellar soni: <b>{len(s.panels)}</b>.", reply_markup=_texts_kb(s))
    await c.answer()


async def on_wait_text(m: Message):
    s = SESS.get(m.from_user.id)
    if not s:
        raise SkipHandler()
    if s.state == "busy":
        return await m.answer("⏳ Hozir ishlayapman, biroz kuting…")
    if s.state == "pages":
        return await m.answer("Avval manga sahifalarini yuboring, hammasi tugagach «✅ Tugadi» tugmasini bosing.")
    raise SkipHandler()


def _audio_of(m: Message):
    """(file_id, hajm, kengaytma) — xabardagi audio; audio bo'lmasa None."""
    if m.voice:
        return m.voice.file_id, m.voice.file_size or 0, ".ogg"
    if m.audio:
        ext = os.path.splitext(m.audio.file_name or "")[1].lower() or ".mp3"
        return m.audio.file_id, m.audio.file_size or 0, ext
    doc = m.document
    if doc and ((doc.mime_type or "").lower().startswith("audio/") or AUDIO_EXT.search(doc.file_name or "")):
        ext = os.path.splitext(doc.file_name or "")[1].lower() or ".mp3"
        return doc.file_id, doc.file_size or 0, ext
    return None


async def on_audio(m: Message):
    """Ovoz (Blox lab, o'z ovozi va h.k.) — qatorlarni moslab, ovozli video yasaladi."""
    s = SESS.get(m.from_user.id)
    a = _audio_of(m) if s else None
    if not a:
        raise SkipHandler()
    s.last = time.time()
    if s.state == "pages":
        return await m.answer("Avval sahifalarni yuborib «✅ Tugadi» ni bosing. Ovoz matndan keyin yuboriladi.")
    if s.state == "busy":
        return await m.answer("⏳ Hozir ishlayapman. Tugagach audioni qayta yuboring.")
    if not _voice_lines(s.texts):
        return await m.answer("Avval matnni yuboring (har panel uchun bitta qator), keyin ovozni.")
    fid, size, ext = a
    if size > MAX_FILE:
        return await m.answer("Audio juda katta (200 MB gacha).")
    if size > 20 * 1024 * 1024 and D.big is None:
        return await m.answer("Bu audio 20 MB dan katta. Kichikroq (masalan, mp3) qilib yuboring.")
    s.last_user_mid = m.message_id
    path = os.path.join(s.dir, f"voice_in_{m.message_id}{ext}")
    st = await m.answer("🎙 Ovoz qabul qilindi, yuklab olinmoqda…")
    try:
        if size > 20 * 1024 * 1024:
            await D.big.download(m.message_id, path)
        else:
            await D.bot.download(fid, destination=path, timeout=300)
    except Exception as e:
        log.exception("manga audio yuklanmadi")
        return await _st(st, f"❌ Audio olinmadi: {esc(D.short_err(e))}")
    await voice_render(s, path, st, "sizning audiongiz")


async def cmd_cancel(m: Message):
    drop(m.from_user.id)
    await m.answer("❌ Manga-video bekor qilindi. Yangisi uchun /manga")


def setup(**deps):
    """bot.py dan chaqiriladi — handlerlarni dp ga birinchi bo'lib qo'shadi."""
    global D

    class _D:
        eleven = None
        gemini = None
        eleven_key = ""

    D = _D()
    for k, v in deps.items():
        setattr(D, k, v)
    dp = D.dp
    dp.startup.register(notify_restart)
    dp.message.register(cmd_manga, Command("manga"))

    async def _bekor_filter(m: Message):
        return m.from_user.id in SESS

    dp.message.register(cmd_cancel, Command("bekor"), _bekor_filter)
    dp.message.register(on_page, F.photo | F.document, _is_state("pages"))
    dp.message.register(on_audio, F.audio | F.voice | F.document, _bekor_filter)
    dp.message.register(on_text, F.text, ~F.text.startswith("/"), _is_state("texts"))
    dp.message.register(on_wait_text, F.text, ~F.text.startswith("/"))
    dp.callback_query.register(on_cb, F.data.startswith("mg_"))
