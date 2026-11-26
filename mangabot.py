"""Manga-video: botdagi /manga buyrug'i.

Oqim: /manga → manga sahifalari (rasm yoki ZIP/CBZ) → «✅ Tugadi» → bot panellarni kesib raqamlangan rasm yuboradi →
siz har panel uchun bitta qator matn yuborasiz → bot subtitrli vertikal (9:16) video yasaydi, qo'shiq qo'shadi.

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

log = logging.getLogger("mangabot")

D = None  # setup() to'ldiradi: umumiy narsalar (bot, music, gem ...)
SESS: dict = {}
IMG_MIME = ("image/jpeg", "image/png", "image/webp", "image/bmp", "image/gif", "image/tiff")
ZIP_EXT = re.compile(r"\.(zip|cbz)$", re.I)
NUM_LINE = re.compile(r"^\s*(\d{1,3})\s*[.):]\s+(.*)$")
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
    sm = await m.answer(
        "🎞 <b>Manga-video</b>\n\n"
        "Manga sahifalarini yuboring: <b>rasm</b> (sifat uchun «fayl» qilib yuborgan yaxshi) yoki <b>ZIP / CBZ</b>. "
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
            return await m.answer("PDF hozircha qabul qilinmaydi. Sahifalarni rasm yoki ZIP qilib yuboring 🙏")
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
        else:
            paths = [path]
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
            "🌐 Inglizcha yoki boshqa tilda yozsangiz ham bo‘ladi — o‘zbekchaga o‘giraman va avval sizga ko‘rsataman.\n\n"
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
    s.panels, s.texts = panels, None
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


async def on_text(m: Message):
    s = SESS.get(m.from_user.id)
    if not s or s.state != "texts":
        raise SkipHandler()
    s.last = time.time()
    s.last_user_mid = m.message_id
    texts, warn, err = parse_lines(m.text, len(s.panels))
    if err:
        return await m.answer("❌ " + err)
    s.orig = texts
    s.texts = texts
    st = await m.answer("🌐 Matn tekshirilmoqda (o‘zbekchaga o‘giraman)…") if any(texts) else None
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
        return await m.answer(note + "\nShunday yasaymi?", reply_markup=kb([("🎬 Shunday yasash", "mg_go")]))
    changed = any(_norm(a) != _norm(b) for a, b in zip(texts, tr))
    s.texts = tr
    if changed:
        head = "🌐 <b>O‘zbekchaga o‘girdim:</b>\n\n"
        tail = ("\n\nMaqul bo‘lsa «🎬 Yasash» ni bosing. Tuzatmoqchi bo‘lsangiz — tuzatilgan matnni qayta yuboring."
                + (f"\n\n{warn}" if warn else ""))
        return await m.answer(head + _preview(tr) + tail,
                              reply_markup=kb([("🎬 Yasash", "mg_go")], [("↩️ Asl matn bilan yasash", "mg_orig")]))
    if warn:
        return await m.answer(warn, reply_markup=kb([("🎬 Shunday yasash", "mg_go")]))
    await render(s)


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


async def render(s):
    s.state = "busy"
    st = await D.bot.send_message(s.chat_id, f"🎞 Video yasalmoqda… (0/{len(s.panels)})")
    try:
        async def prog(i, n):
            try:
                await st.edit_text(f"🎞 Video yasalmoqda… ({i}/{n})")
            except Exception:
                pass

        async with D.heavy:
            s.silent, s.total = await manga.render_video(s.panels, s.texts or [], s.dir, prog)
        await finish(s, st)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("manga render xato")
        s.state = "texts"
        await st.edit_text(f"❌ Video yasashda xatolik: {esc(D.short_err(e))}\nMatnni qayta yuborib ko‘ring.")


async def finish(s, st=None, other=False):
    """Qo'shiq qo'shib, tayyor videoni yuboradi."""
    s.state = "busy"
    try:
        if st is None:
            st = await D.bot.send_message(s.chat_id, "🎵 Qo‘shiq almashtirilmoqda…")
        else:
            try:
                await st.edit_text("🎵 Qo‘shiq qo‘shilmoqda…")
            except Exception:
                pass
        mus = await pick_music(s, other=other)
        s.seq += 1
        out = os.path.join(s.dir, f"manga_{s.seq}.mp4")
        async with D.heavy:
            await manga.mux_music(s.silent, out, s.total, mus)
        if s.out and s.out != out and os.path.exists(s.out):
            try:
                os.remove(s.out)
            except OSError:
                pass
        s.out = out
        note = f"✅ Manga-video tayyor ({fmt_sec(s.total)})\n🎞 Panellar: {len(s.panels)}"
        if mus:
            note += f"\n🎵 Qo‘shiq: {esc(mus['name'])}"
        rows = []
        if mus:
            rows.append([("🎵 Boshqa qo‘shiq", "mg_mnext"), ("🔇 Qo‘shiqsiz", "mg_mnone")])
        elif s.no_music:
            rows.append([("🎵 Qo‘shiq qo‘shish", "mg_mback")])
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
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("manga finish xato")
        s.state = "done" if s.out else "texts"
        if st:
            try:
                await st.edit_text(f"❌ Video tayyorlashda xatolik: {esc(D.short_err(e))}")
            except Exception:
                pass


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
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
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
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        if s.texts is None:
            s.texts = [""] * len(s.panels)
        return await render(s)
    if act == "mg_orig":
        if s.state != "texts":
            return await c.answer()
        await c.answer()
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        s.texts = s.orig or s.texts
        return await render(s)
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


async def cmd_cancel(m: Message):
    drop(m.from_user.id)
    await m.answer("❌ Manga-video bekor qilindi. Yangisi uchun /manga")


def setup(**deps):
    """bot.py dan chaqiriladi — handlerlarni dp ga birinchi bo'lib qo'shadi."""
    global D

    class _D:
        pass

    D = _D()
    for k, v in deps.items():
        setattr(D, k, v)
    dp = D.dp
    dp.message.register(cmd_manga, Command("manga"))

    async def _bekor_filter(m: Message):
        return m.from_user.id in SESS

    dp.message.register(cmd_cancel, Command("bekor"), _bekor_filter)
    dp.message.register(on_page, F.photo | F.document, _is_state("pages"))
    dp.message.register(on_text, F.text, ~F.text.startswith("/"), _is_state("texts"))
    dp.message.register(on_wait_text, F.text, ~F.text.startswith("/"))
    dp.callback_query.register(on_cb, F.data.startswith("mg_"))
