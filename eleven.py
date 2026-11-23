"""ElevenLabs: hikoya matnini tanlangan diktor ovozida o'qitish (har bir harf qaysi soniyada aytilgani bilan)."""
import base64
import json
import logging
import os
import re

import aiohttp

log = logging.getLogger("eleven")

BASE = "https://api.elevenlabs.io"
MODEL = os.environ.get("ELEVEN_MODEL", "eleven_v4")
# O'zbekcha diktorlar (ElevenLabs Voice Library) — /ovoz buyrug'ida tanlanadi
VOICES = [
    ("SARHAD-SIZ", "rwtcHtZgYU6HN2zLNBlf", "sirli va jiddiy, triller va qo‘rqinchli kinolarga"),
    ("Adizzbek", "v4cqTf6wc2CXwA2tR7TF", "hikoya va Instagram videolari uchun"),
    ("Samariddin", "tQBN3grEhnGrrqn0n2Rw", "hikoyachi-tergovchi, detektivlarga"),
    ("Farruh", "9TwIsobDB8LwfbmaVTlt", "past, iliq ovoz, drama va hujjatli kinolarga"),
]
MAX_CHARS = 4800


class ElevenError(Exception):
    def __init__(self, status, msg, code=""):
        super().__init__(msg)
        self.status = status
        self.code = code


def voice_name(voice_id):
    return next((n for n, v, _ in VOICES if v == voice_id), "tanlangan ovoz")


def friendly(e):
    """Xatoni oddiy tilda tushuntiradi (oxirida ElevenLabs aytgan aniq sabab)."""
    s = f"{e.code} {e}".lower()
    if "unusual_activity" in s or "free tier usage disabled" in s:
        txt = ("ElevenLabs bepul (Free) hisobni serverdan ishlatishni blokladi. Pullik tarif (Starter) kerak yoki "
               "ovozni ElevenLabs saytida yasab, mp3 ni botga yuboring.")
    elif "free_users_not_allowed" in s or "library voices" in s:
        txt = "bepul tarifda Voice Library ovozlarini API orqali ishlatib bo‘lmaydi — pullik tarif kerak."
    elif "missing_permissions" in s or "permission" in s:
        txt = "kalitga kerakli ruxsat berilmagan: API Keys → kalit → Text to Speech: Access ni yoqing."
    elif "quota" in s or "credit" in s or "insufficient" in s:
        txt = "ElevenLabs kreditlari tugagan yoki yetmayapti."
    elif "voice_not_found" in s or "voice not found" in s or e.status == 404:
        txt = ("bu ovoz hisobingizda yo‘q: ElevenLabs’da Voice Library’dan shu ovozni topib «Add to My Voices» ni "
               "bosing yoki /ovoz bilan boshqasini tanlang.")
    elif "invalid_api_key" in s or "invalid api key" in s:
        txt = "API kalit noto‘g‘ri."
    elif e.status == 429 or "too_many" in s or "busy" in s:
        txt = "ElevenLabs hozir band. Birozdan keyin qayta urinib ko‘ring."
    elif e.status == 401:
        txt = "ElevenLabs kirishga ruxsat bermadi."
    else:
        txt = "kutilmagan xato."
    raw = (e.code or str(e) or "").strip()
    return f"{txt}\n(ElevenLabs javobi: {e.status} {raw[:160]})"


async def check(session, key):
    """Kalit ishlashini tekshiradi. Qaytaradi: (True, "tarif, qolgan kredit") yoki (False, sabab)."""
    try:
        async with session.get(f"{BASE}/v1/user/subscription", headers={"xi-api-key": key},
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            txt = await r.text()
            if r.status == 200:
                j = json.loads(txt)
                left = int(j.get("character_limit", 0)) - int(j.get("character_count", 0))
                return True, f"tarif: {j.get('tier', '?')}, kredit: {left}"
            msg, code = _detail(txt)
            return False, f"{r.status} {code} {msg}"[:200]
    except Exception as ex:
        return False, repr(ex)[:200]


def _detail(txt):
    try:
        d = json.loads(txt).get("detail")
        if isinstance(d, dict):
            code = d.get("status") or d.get("code") or ""
            return d.get("message") or code or str(d), f"{code}: {d.get('message', '')}" if code else ""
        return str(d), ""
    except (ValueError, AttributeError):
        return re.sub(r"\s+", " ", txt)[:200], ""


async def tts(session, key, voice_id, text, out_path):
    """Ovozni out_path ga yozadi. Qaytaradi: alignment (harflar va ularning boshlanish vaqtlari) yoki {}."""
    if len(text) > MAX_CHARS:
        raise ElevenError(400, f"matn juda uzun ({len(text)} belgi)")
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    body = {"text": text, "model_id": MODEL}
    url = f"{BASE}/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128"
    async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=600)) as r:
        txt = await r.text()
        status = r.status
    if status == 200:
        j = json.loads(txt)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(j.get("audio_base64") or ""))
        return j.get("alignment") or j.get("normalized_alignment") or {}
    msg, code = _detail(txt)
    if status in (400, 422) and "timestamp" in msg.lower():
        # bu model vaqtlarni bermasa — oddiy ovoz (kadrlar Gemini bilan moslanadi)
        url2 = f"{BASE}/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
        async with session.post(url2, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=600)) as r:
            data = await r.read()
            if r.status == 200:
                with open(out_path, "wb") as f:
                    f.write(data)
                return {}
            msg, code = _detail(data.decode("utf-8", "ignore"))
            raise ElevenError(r.status, msg, code)
    raise ElevenError(status, msg, code)


def sentence_starts(sentences, alignment):
    """Har bir gap ovozda nechanchi soniyada boshlanishi: {gap_raqami: soniya}."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    if not chars or len(chars) != len(starts):
        return {}
    full = "".join(chars)
    res, pos = {}, 0
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        j = full.find(sent[:24], pos)
        if j < 0:
            words = sent.split()
            j = full.find(words[0], pos) if words else -1
        if j >= 0:
            res[i] = float(starts[j])
            pos = j + max(1, len(sent) // 2)
    return res


async def credits_left(session, key):
    try:
        async with session.get(f"{BASE}/v1/user/subscription", headers={"xi-api-key": key},
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                return None
            j = await r.json(content_type=None)
        return int(j.get("character_limit", 0)) - int(j.get("character_count", 0))
    except Exception:
        return None
