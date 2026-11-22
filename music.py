"""Musiqa kutubxonasi.
Qo'shiqlarning o'zi Telegram'da turadi (file_id), ro'yxat esa bot chatidagi qadalgan (pin) «musiqalar.json»
faylida saqlanadi — shuning uchun server qayta ishga tushsa ham hech narsa yo'qolmaydi."""
import asyncio
import json
import logging
import os
import random
import wave

import numpy as np
from aiogram.types import BufferedInputFile, InputMediaDocument

import gemini
import video

log = logging.getLogger("music")

LIB_FILE = "musiqalar.json"
LIB_CAPTION = ("🎵 Musiqalar ro‘yxati — bot har videoga shu yerdan mos qo‘shiq tanlaydi.\n"
               "Bu xabarni o‘chirmang va pindan olmang. /musiqa — ro‘yxatni ko‘rish.")
TARGET_RMS = float(os.environ.get("MUSIC_RMS_DB", "-28"))  # ovoz ostidagi musiqa balandligi (dBFS)

_cache: dict[int, dict] = {}      # chat_id -> {"lib": {...}, "msg_id": int | None}
_locks: dict[int, asyncio.Lock] = {}
_save_tasks: dict[int, asyncio.Task] = {}

MOOD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "kayfiyat": {"type": "STRING"},
        "janr": {"type": "STRING"},
        "temp": {"type": "STRING"},
        "mos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "eng_qiziq": {"type": "STRING"},
    },
    "required": ["kayfiyat", "janr", "temp", "mos", "eng_qiziq"],
}
PICK_SCHEMA = {
    "type": "OBJECT",
    "properties": {"tanlov": {"type": "INTEGER"}, "sabab": {"type": "STRING"}},
    "required": ["tanlov"],
}

MOOD_PROMPT = (
    "This music track will be used as quiet background music under the narration of Uzbek Instagram movie-recap "
    "videos. Describe it briefly in Uzbek (Latin script):\n"
    "- \"kayfiyat\": 2–4 words for the mood (e.g. qorqinchli, sirli, g‘amgin, dramatik, epik, tarang, quvnoq, "
    "romantik, sokin);\n"
    "- \"janr\": music genre in 1–3 words;\n"
    "- \"temp\": sekin, o‘rta or tez;\n"
    "- \"mos\": 2–5 movie genres it fits (ujas, triller, drama, jangari, fantastika, komediya, romantik, detektiv, "
    "sarguzasht, multfilm, tarixiy);\n"
    "- \"eng_qiziq\": the timestamp (M:SS from the start of this file) where the most memorable, catchy or powerful "
    "part begins — the chorus, the drop or the climax."
)


def _lock(chat_id):
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


async def load(bot, chat_id, workdir):
    """Ro'yxatni (bir marta) chatdagi qadalgan fayldan o'qiydi."""
    async with _lock(chat_id):
        if chat_id in _cache:
            return _cache[chat_id]["lib"]
        lib, mid = {"v": 1, "songs": [], "off": False, "last": None}, None
        try:
            chat = await bot.get_chat(chat_id)
            pm = getattr(chat, "pinned_message", None)
            if pm and pm.document and pm.document.file_name == LIB_FILE:
                os.makedirs(workdir, exist_ok=True)
                path = os.path.join(workdir, f"lib_{chat_id}.json")
                await bot.download(pm.document.file_id, destination=path, timeout=60)
                with open(path, encoding="utf-8") as f:
                    lib.update(json.load(f))
                mid = pm.message_id
        except Exception as e:
            log.warning("ro'yxatni o'qib bo'lmadi: %r", e)
        _cache[chat_id] = {"lib": lib, "msg_id": mid}
        return lib


async def _save_now(bot, chat_id):
    c = _cache.get(chat_id)
    if not c:
        return
    data = json.dumps(c["lib"], ensure_ascii=False, indent=1).encode("utf-8")
    doc = BufferedInputFile(data, filename=LIB_FILE)
    if c["msg_id"]:
        try:
            await bot.edit_message_media(media=InputMediaDocument(media=doc, caption=LIB_CAPTION),
                                         chat_id=chat_id, message_id=c["msg_id"])
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            log.warning("ro'yxatni yangilab bo'lmadi, yangisini yuboraman: %r", e)
    m = await bot.send_document(chat_id, BufferedInputFile(data, filename=LIB_FILE), caption=LIB_CAPTION,
                                disable_notification=True)
    c["msg_id"] = m.message_id
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=m.message_id, disable_notification=True)
    except Exception as e:
        log.warning("pin bo'lmadi: %r", e)


def save_soon(bot, chat_id, delay=2.0):
    """Ketma-ket o'zgarishlarni bitta saqlashga yig'adi (masalan, 10 ta qo'shiq birdan yuborilsa)."""
    old = _save_tasks.get(chat_id)
    if old and not old.done():
        old.cancel()

    async def go():
        try:
            await asyncio.sleep(delay)
            async with _lock(chat_id):
                await _save_now(bot, chat_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("saqlash xato: %r", e)
    _save_tasks[chat_id] = asyncio.create_task(go())


def find(lib, song_id):
    return next((s for s in lib.get("songs", []) if s.get("id") == song_id), None)


def describe(s):
    bits = [s.get("kayfiyat") or "", s.get("janr") or "", s.get("temp") or ""]
    mos = ", ".join(s.get("mos") or [])
    t = "; ".join(b for b in bits if b)
    return t + (f"; mos: {mos}" if mos else "")


# ---------------- Qo'shiqni tahlil qilish ----------------

def _wav_stats(wav_path):
    """Qo'shiq davomiyligi, o'rtacha balandligi va «asosiy qismi» boshlanadigan joy (jim kirishni o'tkazib)."""
    with wave.open(wav_path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    dur = len(x) / sr
    hop = sr // 2
    n = len(x) // hop
    if n < 8:
        return dur, -20.0, 0.0, 0.0
    r = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    rms_db = float(10 * np.log10(np.mean(r ** 2) + 1e-12))
    med = float(np.median(r))
    smooth = np.convolve(r, np.ones(4) / 4, mode="same")
    above = np.flatnonzero(smooth >= 0.6 * med)
    start = float(above[0] * 0.5) if len(above) else 0.0
    start = min(start, 30.0, dur * 0.3)
    # «eng qiziq joy»: 10 soniyalik eng baland (jo'shqin) bo'lak — odatda naqarot yoki drop —
    # va uning boshlanish nuqtasi (ovoz keskin ko'tariladigan joy)
    win = min(20, n)
    e = r ** 2
    csum = np.concatenate([[0.0], np.cumsum(e)])
    means = (csum[win:] - csum[:-win]) / win
    if len(means):
        k = int(np.argmax(means))
        seg = smooth[max(0, k - 6): k + win]
        rise = np.flatnonzero(seg >= 0.8 * float(seg.max()))
        best = (max(0, k - 6) + (int(rise[0]) if len(rise) else 6)) * 0.5
    else:
        best = start
    return dur, rms_db, round(start, 1), round(float(best), 1)


async def analyse(gem, http, path, workdir, key):
    """Qo'shiqning balandligini o'lchaydi va Gemini'dan kayfiyatini so'raydi (Gemini band bo'lsa ham qo'shiq saqlanadi)."""
    wav = os.path.join(workdir, f"m_{key}.wav")
    await video.voice_to_wav(path, wav)
    dur, rms_db, start, best = _wav_stats(wav)
    info = {"dur": round(dur, 1), "rms": round(rms_db, 1), "start": start, "best": best}
    clip = os.path.join(workdir, f"m_{key}.mp3")
    try:
        await video.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-t", "360", "-i", wav,
                         "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", clip], timeout=300)
        up = await gem.upload(http, clip, "audio/mpeg")
        try:
            parts = [{"file_data": {"mime_type": up.get("mimeType") or "audio/mpeg", "file_uri": up["uri"]}},
                     {"text": MOOD_PROMPT}]
            data = await gem.generate_json(http, parts, MOOD_SCHEMA, temperature=0.2, timeout=180)
        finally:
            await gem.delete(http, up.get("name"))
        info.update({
            "kayfiyat": str(data.get("kayfiyat") or "")[:60],
            "janr": str(data.get("janr") or "")[:40],
            "temp": str(data.get("temp") or "")[:12],
            "mos": [str(x)[:20] for x in (data.get("mos") or [])][:5],
        })
        t = gemini.parse_ts(data.get("eng_qiziq"))
        if t is not None and 3 <= t <= max(3.0, dur - 15):
            info["best"] = round(float(t), 1)
    except Exception as e:
        log.warning("musiqa kayfiyati aniqlanmadi: %r", e)
    finally:
        for p in (wav, clip):
            try:
                os.remove(p)
            except OSError:
                pass
    return info


async def pick(gem, http, lib, title, script_text, exclude=None):
    """Kino va matnga eng mos qo'shiqni tanlaydi. Gemini javob bermasa — kam ishlatilganlaridan biri."""
    songs = [s for s in lib.get("songs", []) if s.get("id") != exclude]
    if not songs:
        return None
    if len(songs) == 1:
        return songs[0]
    last = lib.get("last")
    try:
        opts = "\n".join(
            f"{i + 1}) {s['name']} — {describe(s) or 'tavsif yo‘q'}; ishlatilgan: {s.get('used', 0)} marta"
            + (" (oxirgi videoda ishlatilgan)" if s.get("id") == last else "")
            for i, s in enumerate(songs))
        prompt = (f"Movie: «{title}».\nNarration of the Uzbek recap video:\n{script_text}\n\n"
                  f"Background music options:\n{opts}\n\n"
                  "Choose the option whose mood best fits this movie and narration. If several fit equally well, "
                  "prefer one used fewer times and not the one used in the last video. "
                  "Answer with \"tanlov\" (the option number) and a short Uzbek \"sabab\".")
        data = await gem.generate_json(http, [{"text": prompt}], PICK_SCHEMA, temperature=0.3, timeout=90)
        k = int(data.get("tanlov") or 0) - 1
        if 0 <= k < len(songs):
            return songs[k]
    except Exception as e:
        log.warning("musiqa tanlanmadi, tasodifiy olinadi: %r", e)
    least = min(s.get("used", 0) for s in songs)
    pool = [s for s in songs if s.get("used", 0) == least and s.get("id") != last] or songs
    return random.choice(pool)


def next_other(lib, current_id):
    """«Boshqa musiqa»: hozirgisidan boshqa, eng kam ishlatilganlaridan biri."""
    songs = [s for s in lib.get("songs", []) if s.get("id") != current_id]
    if not songs:
        return None
    least = min(s.get("used", 0) for s in songs)
    return random.choice([s for s in songs if s.get("used", 0) == least])


async def ensure_best(path, song, workdir):
    """Oldin qo'shilgan qo'shiqlarda «eng qiziq joy» yo'q bo'lsa — ovoz balandligidan topadi."""
    if song.get("best") is not None:
        return False
    wav = os.path.join(workdir, f"b_{song['id']}.wav")
    try:
        await video.voice_to_wav(path, wav)
        _, _, _, best = _wav_stats(wav)
        song["best"] = best
        return True
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def music_start(song):
    """Musiqa qayerdan boshlansin: eng qiziq joydan 1 soniya oldin (bo'lmasa — jim kirishdan keyin)."""
    best = song.get("best")
    if best is not None:
        return max(0.0, float(best) - 1.0)
    return float(song.get("start", 0))


async def fetch(bot, big, song, cache_dir):
    """Qo'shiq faylini serverga oladi (bir marta, keyin keshdan)."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{song['id']}{song.get('ext') or '.mp3'}")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    if (song.get("size") or 0) <= 20 * 1024 * 1024:
        await bot.download(song["file_id"], destination=path, timeout=300)
    elif big is not None and song.get("msg_id"):
        await big.download(song["msg_id"], path)
    else:
        raise RuntimeError("qo‘shiq fayli juda katta")
    return path
