"""Torii Image Translator — sahifani to'liq o'zi tarjima qiladigan xizmat (toriitranslate.com/api).

Torii matnni topadi, asl yozuvni o'chiradi va o'zbekcha tarjimani pufaklarga joylaydi. Kalit: TORII_API_KEY
(Render sozlamalari). Har sahifa kamida 1 kredit. Kredit tugasa, kalit ishlamasa yoki xizmat javob bermasa —
bot o'zi hozirgi Gemini usuliga o'tadi (tarjima to'xtamaydi) va bosh adminga xabar beradi; kredit qo'shilgach
yana Torii'ga qaytadi. Manga glossariysi va sahifalararo kontekst Torii'ga uzatiladi."""
import asyncio
import base64
import io
import json
import os
import time

import typeset
from core import CAT, bot, http, log

API = "https://api.toriitranslate.com"
KEY = os.environ.get("TORII_API_KEY", "").strip()
MODEL = os.environ.get("TORII_MODEL", "gemini-3.1-flash-lite").strip()
FONT = os.environ.get("TORII_FONT", "wildwords").strip()
MAX_WIDTH = 1600
LOW_CREDITS = 30
PROMPT = ("Translate into Uzbek using the Latin script. Write the letters oʻ and gʻ with the character ‘ (U+2018), "
          "for example: o‘g‘il, bo‘ldi. Use natural, lively, conversational Uzbek that fits inside speech bubbles; "
          "keep it short. Keep character names and terms exactly as in the glossary.")
STATE = {"down_until": 0.0, "why": "", "credits": None, "credits_at": 0.0, "low_warned": False,
         "fails": 0, "texts": False, "last_call": 0.0, "checked": 0.0, "used": 0.0, "pages": 0}
CTX: dict[int, tuple] = {}            # uid → (vaqt, Torii konteksti)
_LOCK = asyncio.Lock()


class ToriiError(Exception):
    def __init__(self, msg, kind="error"):
        super().__init__(msg)
        self.kind = kind              # credits | key | busy | bad | error


def configured() -> bool:
    return bool(KEY)


def chosen() -> bool:
    """Admin tanlovi (standart — Torii, kalit bo'lsa)."""
    return configured() and (CAT.cfg.get("translator") or "torii") == "torii"


def active() -> bool:
    return chosen() and time.time() >= STATE["down_until"]


def status_line() -> str:
    if not configured():
        return "🌐 Tarjimon: Gemini"
    if not chosen():
        return "🌐 Tarjimon: Gemini (Torii o‘chirilgan)"
    cr = STATE["credits"]
    credits = f" · 🪙 {cr:g} kredit" if isinstance(cr, (int, float)) else ""
    if not active():
        return f"🌐 Tarjimon: Torii vaqtincha ishlamayapti ({STATE['why'] or 'xato'}) — hozir Gemini{credits}"
    return f"🌐 Tarjimon: Torii{credits}"


def multipart(fields: dict, file_field, filename, ctype, data: bytes):
    b = "----torii" + os.urandom(8).hex()
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + str(v).encode() + b"\r\n")
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def _timeout(sec):
    import aiohttp
    return aiohttp.ClientTimeout(total=sec)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _post(fields, jpeg):
    """Bitta so'rov (1 so'rov/soniya chegarasiga rioya qilib). Qaytaradi: (status, json, matn, sarlavhalar)."""
    import traffic
    body, ctype = multipart(fields, "file", "page.jpg", "image/jpeg", jpeg)
    async with _LOCK:
        wait = 1.05 - (time.time() - STATE["last_call"])
        if wait > 0:
            await asyncio.sleep(wait)
        STATE["last_call"] = time.time()
    traffic.add(len(body) + 600)
    async with http().post(f"{API}/api/v2/upload", data=body, timeout=_timeout(240),
                           headers={"Authorization": f"Bearer {KEY}", "Content-Type": ctype}) as r:
        status, headers = r.status, r.headers
        text = await r.text()
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    return status, (data if isinstance(data, dict) else {}), text, headers


def _classify(status, text):
    low = (text or "").lower()
    if "credit" in low and status != 200:
        return "credits"
    if status in (401, 403):
        return "key"
    if status in (429, 503, 502, 504):
        return "busy"
    if status in (400, 413, 415, 422):
        return "bad"
    return "error"


async def translate_jpeg(jpeg: bytes, glossary=None, context=None, notify=None):
    """Torii'ga sahifa yuboradi. Qaytaradi: dict(image=bytes, context=str, texts=list, used=float)."""
    fields = {"target_lang": "uz", "translator": MODEL, "font": FONT, "custom_prompt": PROMPT}
    if glossary:
        g = [{"key": str(o), "value": str(t)} for o, t in glossary if o and t]
        while g and len(json.dumps(g, ensure_ascii=False)) > 11500:
            g.pop()
        if g:
            fields["glossary"] = json.dumps(g, ensure_ascii=False)
    if context:
        fields["context"] = str(context)[:10000]
    if STATE["texts"]:
        fields["texts"] = "[]"
    delays = [2, 6, 15]
    for attempt in range(len(delays) + 1):
        try:
            status, data, text, headers = await _post(fields, jpeg)
        except asyncio.TimeoutError:
            status, data, text, headers = 504, {}, "timeout", {}
        except Exception as e:
            status, data, text, headers = 0, {}, repr(e), {}
        cr = _num((headers or {}).get("credits"))
        if cr is not None:
            STATE.update(credits=cr, credits_at=time.time())
        if status == 200 and str(data.get("image") or "").startswith("data:"):
            raw = base64.b64decode(data["image"].split(",", 1)[1])
            used = _num((data.get("metadata") or {}).get("credits_used")) or 0.0
            return {"image": raw, "context": data.get("context"), "texts": data.get("text") or [], "used": used}
        kind = _classify(status, text) if status != 200 else "bad"
        if (kind == "bad" and not STATE["texts"] and "texts" in (text or "").lower()):
            STATE["texts"] = True                     # ba'zi versiyalarda «texts» maydoni majburiy
            fields["texts"] = "[]"
            continue
        if kind in ("busy", "error") and attempt < len(delays):
            if notify is not None:
                try:
                    await notify(f"⏳ Torii band, {delays[attempt]} soniyadan keyin qayta urinaman…")
                except Exception:
                    pass
            await asyncio.sleep(delays[attempt])
            continue
        raise ToriiError(f"{status}: {(text or '')[:300]}", kind)
    raise ToriiError("javob bo‘lmadi", "busy")


def _prepare(img) -> bytes:
    w, h = img.size
    if w > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, max(1, round(h * MAX_WIDTH / w))), typeset._RS.LANCZOS)
    return typeset.to_jpeg(img.convert("RGB"), 92)


async def translate_image(img, uid, glossary=None, notify=None):
    """core.translate_image bilan bir xil natija: (yangi rasm, bloklar, asl til)."""
    jpeg = await asyncio.to_thread(_prepare, img)
    t, ctx = CTX.get(uid, (0.0, None))
    ctx = ctx if time.time() - t < 1800 else None
    res = await translate_jpeg(jpeg, glossary=glossary, context=ctx, notify=notify)
    out = await asyncio.to_thread(lambda: typeset.load_image(res["image"]).convert("RGB"))
    if res.get("context"):
        CTX[uid] = (time.time(), res["context"])
        if len(CTX) > 500:
            CTX.pop(next(iter(CTX)))
    blocks = []
    for it in res["texts"]:
        if not isinstance(it, dict) or not str(it.get("text") or "").strip():
            continue
        try:
            x, y = int(float(it.get("x") or 0)), int(float(it.get("y") or 0))
            w, h = int(float(it.get("width") or 0)), int(float(it.get("height") or 0))
        except (TypeError, ValueError):
            x = y = w = h = 0
        blocks.append(typeset.Block(box=(x, y, x + w, y + h), text=str(it["text"]).strip(), kind="speech",
                                    original=str(it.get("originalText") or "").strip()))
    STATE["fails"] = 0
    STATE["used"] += res["used"]
    STATE["pages"] += 1
    await _low_credits_check()
    return out, blocks, ""


async def _tell(text):
    for oid in CAT.owners:
        try:
            await bot.send_message(oid, text)
        except Exception:
            pass


async def note_failure(e: ToriiError):
    """Xatoga qarab Torii'ni vaqtincha chetga qo'yadi va kerak bo'lsa bosh adminga yozadi."""
    now = time.time()
    log.warning("Torii xatosi (%s): %s", e.kind, e)
    if e.kind == "credits":
        first = now >= STATE["down_until"]
        STATE.update(down_until=now + 3600, why="kredit tugagan")
        if first:
            await _tell("🪙 <b>Torii kreditlari tugadi.</b> Tarjima vaqtincha Gemini orqali davom etyapti.\n"
                        "toriitranslate.com da kredit qo‘shsangiz, bot o‘zi Torii’ga qaytadi.")
    elif e.kind == "key":
        first = now >= STATE["down_until"]
        STATE.update(down_until=now + 6 * 3600, why="kalit ishlamayapti")
        if first:
            await _tell("⚠️ <b>Torii kaliti ishlamayapti</b> (TORII_API_KEY). Tarjima vaqtincha Gemini orqali.")
    elif e.kind == "busy":
        STATE.update(down_until=now + 120, why="band")
    else:
        STATE["fails"] += 1
        if STATE["fails"] >= 3:
            STATE.update(down_until=now + 600, why="javob bermayapti", fails=0)


async def credits(force=False):
    """Qolgan kreditlar (5 daqiqa keshlanadi). Tekshirish bepul."""
    if not configured():
        return None
    if not force and STATE["credits"] is not None and time.time() - STATE["credits_at"] < 300:
        return STATE["credits"]
    try:
        async with http().get(f"{API}/api/credits", headers={"Authorization": f"Bearer {KEY}"},
                              timeout=_timeout(20)) as r:
            status = r.status
            text = await r.text()
        if status == 200:
            cr = _num((json.loads(text) or {}).get("credits"))
            if cr is not None:
                STATE.update(credits=cr, credits_at=time.time())
                return cr
        elif status in (401, 403):
            STATE["why"] = "kalit ishlamayapti"
    except Exception as e:
        log.warning("Torii kreditlarini bilib bo‘lmadi: %s", e)
    return STATE["credits"]


async def _low_credits_check():
    cr = STATE["credits"]
    if not isinstance(cr, (int, float)):
        return
    if cr < LOW_CREDITS and not STATE["low_warned"]:
        STATE["low_warned"] = True
        await _tell(f"🪙 Torii kreditlari kam qoldi: <b>{cr:g}</b>. Har sahifa kamida 1 kredit — "
                    "toriitranslate.com da to‘ldirib qo‘ying (tugasa bot Gemini’ga o‘tadi).")
    elif cr >= LOW_CREDITS * 2:
        STATE["low_warned"] = False


async def tick(now=None):
    """Rejalashtiruvchidan, 5 daqiqada bir: kredit qo'shilgan bo'lsa Torii'ni qayta yoqadi."""
    now = now or time.time()
    if not chosen() or now - STATE["checked"] < 300:
        return
    STATE["checked"] = now
    cr = await credits(force=True)
    if now < STATE["down_until"] and STATE["why"] == "kredit tugagan" and isinstance(cr, (int, float)) and cr >= 1:
        STATE.update(down_until=0.0, why="")
        await _tell(f"✅ Torii’da kredit bor ({cr:g}) — tarjima yana Torii orqali.")
    await _low_credits_check()
