"""Gemini: manga sahifasidagi matnlarni topish (box_2d) va o'zbekchaga tarjima qilish.

Bitta so'rov = bitta sahifa: model rasmni ko'radi, har bir pufak/yozuvning joyini (0–1000 nisbiy koordinata),
asl matnini va o'zbekcha tarjimasini JSON qilib qaytaradi. Bir nechta model navbat bilan sinaladi: biri band
yoki kunlik bepul limiti tugagan bo'lsa, keyingisiga o'tiladi.
"""
import asyncio
import base64
import json
import logging
import os
import re
import time

import aiohttp

log = logging.getLogger("tarjima")

BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
DEFAULT_MODELS = ("gemini-3.8-flash,gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,"
                  "gemini-3.5-flash-lite,gemini-flash-lite-latest")
# Komikslarda janjal, qurol, qon ko'p — oddiy sahnalar bloklanib qolmasligi uchun faqat «yuqori» xavf bloklanadi
SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source_language": {"type": "STRING"},
        "blocks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "kind": {"type": "STRING", "enum": ["speech", "thought", "narration", "sign", "sfx"]},
                    "original": {"type": "STRING"},
                    "translation": {"type": "STRING"},
                },
                "required": ["box_2d", "kind", "original", "translation"],
            },
        },
        "names": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"original": {"type": "STRING"}, "translation": {"type": "STRING"}},
                "required": ["original", "translation"],
            },
        },
    },
    "required": ["source_language", "blocks"],
}

PROMPT = """You are an expert manga / manhwa / manhua / comic translator working for an Uzbek scanlation team.

TASK: find every text block on this comic page and translate it into natural Uzbek (Latin script).

Include: speech bubbles, thought bubbles, shouting and whisper bubbles, narration and caption boxes, and written text
that matters for the story (signs, letters, phone screens, notes, chapter titles).
Skip: sound effects that are drawn as part of the artwork (onomatopoeia like ドン, ゴゴゴ, 쾅, BOOM) unless they are
inside a bubble; page numbers; watermarks; website, magazine and scanlator credits; tiny unreadable background text.

For every block return:
- "box_2d": [ymin, xmin, ymax, xmax] — a TIGHT box around the text characters only (not the whole bubble),
  normalized to 0-1000 (y relative to image height, x relative to image width). Cover all lines/columns of the
  block, including furigana and punctuation.
- "kind": "speech", "thought", "narration", "sign" or "sfx".
- "original": the original text exactly as written (lines joined; no spaces between Japanese/Chinese characters).
- "translation": the Uzbek translation.
One bubble = one block. Separate or touching bubbles are separate blocks.
Order: natural reading order (Japanese manga: right-to-left, top-to-bottom; Korean, Chinese and Western comics:
left-to-right, top-to-bottom).

Translation style:
- Fluent, natural, modern Uzbek in Latin script, the way young Uzbek fans talk — never word-for-word. Keep the
  tone and emotion (anger, jokes, shyness, slang, politeness: siz/sen as fits the relationship).
- Concise, so it fits in the bubble.
- Keep character names, place names and honorifics, written in Latin letters (-san, -kun, -chan, -sama, senpai,
  sensei, oppa, hyung, unnie, shixiong, etc.).
- Use correct Uzbek letters: o‘, g‘ (with ‘) and the tutuq belgisi ’ (ma’no, san’at). Never use Cyrillic.
- Keep "...", "!", "?" like the original. Pure sounds or punctuation (e.g. "...", "?!", "Hmm", "Eh?") stay short.
- If the text is already in Uzbek, copy it unchanged.
"source_language": the main language of the page in Uzbek (e.g. "yaponcha", "koreyscha", "xitoycha", "inglizcha").
"names": proper nouns that appear in the text of this page — character names, places, organizations, skills,
titles of techniques — each with "original" (as written on the page) and "translation" (how you wrote it in Uzbek).
At most 10; do not include ordinary words. Empty list if none.
If the page has no text, return an empty "blocks" list."""


class GeminiError(Exception):
    def __init__(self, status, msg, retry_after=None, per_day=False, retryable=None, kind=""):
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after    # Google aytgan kutish vaqti (soniya)
        self.per_day = per_day            # kunlik bepul limit tugagan
        self.kind = kind                  # "key", "quota", "busy", "blocked", "bad"
        self.retryable = status in (408, 429, 500, 502, 503, 504) if retryable is None else retryable


def _err_details(txt):
    """Google xato javobidan: necha soniya kutish kerak, kunlik limit tugaganmi, qisqa xabar."""
    retry, per_day, msg = None, False, ""
    try:
        err = (json.loads(txt) or {}).get("error") or {}
        msg = str(err.get("message") or "")
        for d in err.get("details") or []:
            rd = d.get("retryDelay")
            if rd:
                try:
                    retry = float(str(rd).rstrip("s"))
                except ValueError:
                    pass
            for v in d.get("violations") or []:
                if "PerDay" in str(v.get("quotaId") or "") or "per_day" in str(v.get("quotaId") or "").lower():
                    per_day = True
    except (ValueError, AttributeError, TypeError):
        msg = txt[:300]
    if not per_day and re.search(r"per ?day|daily", msg, re.I):
        per_day = True
    return retry, per_day, msg


def parse_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        for a, b in (("{", "}"), ("[", "]")):
            i, j = t.find(a), t.rfind(b)
            if i >= 0 and j > i:
                try:
                    return json.loads(t[i:j + 1])
                except ValueError:
                    continue
        raise


def normalize_result(data) -> dict:
    """Model javobini {'blocks': [...], 'source_language': str} ko'rinishiga keltiradi."""
    if isinstance(data, list):
        data = {"blocks": data}
    if not isinstance(data, dict):
        raise GeminiError(0, "javob JSON obyekt emas", retryable=True, kind="bad")
    blocks = data.get("blocks")
    if blocks is None:
        for k in ("items", "texts", "bloklar", "results"):
            if isinstance(data.get(k), list):
                blocks = data[k]
                break
    out = []
    for b in blocks or []:
        if isinstance(b, dict) and isinstance(b.get("box_2d"), list):
            out.append(b)
    names = []
    for n in data.get("names") or []:
        if isinstance(n, dict):
            o, t = str(n.get("original") or "").strip(), str(n.get("translation") or "").strip()
            if o and t and len(o) <= 60 and len(t) <= 60:
                names.append({"original": o, "translation": t})
    return {"blocks": out, "source_language": str(data.get("source_language") or "").strip(), "names": names[:15]}


def build_prompt(context=None, part=None, glossary=None) -> str:
    p = PROMPT
    if part and part[1] > 1:
        p += (f"\n\nThis image is segment {part[0] + 1} of {part[1]} of one long vertical webtoon strip "
              "(continuing from the previous segment).")
    if glossary:
        p += "\n\nAlways use these spellings / translations:\n" + "\n".join(f"- {k} → {v}" for k, v in glossary)
    if context:
        lines = [f"- {o} → {t}" for o, t in context if t][-14:]
        if lines:
            p += ("\n\nFor consistency (names, tone, terms), here is the dialogue from the previous page of the same "
                  "chapter. Do NOT include it in your answer:\n" + "\n".join(lines))
    return p


class Translator:
    def __init__(self, key: str, models: str | None = None):
        self.key = (key or "").strip()
        self.models = [m.strip() for m in (models or DEFAULT_MODELS).split(",") if m.strip()]
        self.dead: dict[str, float] = {}     # model → shu vaqtgacha ishlatmaymiz
        self.plain: set[str] = set()         # qo'shimcha sozlamalarni qabul qilmaydigan modellar
        self.last_good: str | None = None
        self.status = "tekshirilmagan"
        self.meter = None                    # trafik hisoblagichi: meter(baytlar)

    def _headers(self):
        return {"x-goog-api-key": self.key, "Content-Type": "application/json"}

    def _order(self):
        now = time.time()
        live = [m for m in self.models if self.dead.get(m, 0) <= now]
        if self.last_good in live:  # oxirgi ishlagan model birinchi
            live.remove(self.last_good)
            live.insert(0, self.last_good)
        return live

    async def check(self, session: aiohttp.ClientSession) -> str:
        """Kalit ishlashini va ro'yxatdagi qaysi modellar ochiqligini tekshiradi."""
        if not self.key:
            self.status = "kalit kiritilmagan"
            return self.status
        try:
            async with session.get(f"{BASE}/v1beta/models?pageSize=1000", headers=self._headers(),
                                   timeout=aiohttp.ClientTimeout(total=30)) as r:
                txt = await r.text()
                if r.status != 200:
                    _, _, msg = _err_details(txt)
                    self.status = f"xato {r.status}: {msg[:160]}"
                    return self.status
                data = json.loads(txt)
            names = {m.get("name", "").split("/")[-1] for m in data.get("models", [])}
            avail = [m for m in self.models if m in names]
            if avail:
                self.models = avail
            self.status = "ishlaydi ✅ (" + ", ".join(self.models[:4]) + ")"
        except Exception as e:  # tarmoq xatosi — modellarni o'zgartirmaymiz
            self.status = f"tekshirib bo‘lmadi: {e!r}"[:200]
        return self.status

    async def _call(self, session, model, jpeg: bytes, prompt: str, timeout: float):
        gen = {"responseMimeType": "application/json", "responseSchema": SCHEMA}
        extra = model not in self.plain
        if extra:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}
            gen["mediaResolution"] = "MEDIA_RESOLUTION_HIGH"
        b64 = base64.b64encode(jpeg).decode()
        if self.meter is not None:          # trafik hisobi (rasm + ko'rsatma + sozlamalar)
            try:
                self.meter(len(b64) + len(prompt.encode("utf-8", "ignore")) + 3000)
            except Exception:
                pass
        body = {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": prompt},
            ]}],
            "generationConfig": gen,
            "safetySettings": SAFETY,
        }
        async with session.post(f"{BASE}/v1beta/models/{model}:generateContent", headers=self._headers(),
                                json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            txt = await r.text()
            status = r.status
        if status == 400 and extra and re.search(
                r"thinking|media.?resolution|unknown name|not supported|unsupported|invalid value|invalid json",
                _err_details(txt)[2], re.I):
            # thinkingConfig / mediaResolution ni bilmaydigan model — oddiy so'rov bilan qayta urinamiz
            self.plain.add(model)
            log.info("gemini %s: qo'shimcha sozlamalarsiz qayta urinamiz (%s)", model, _err_details(txt)[2][:120])
            return await self._call(session, model, jpeg, prompt, timeout)
        if status != 200:
            retry, per_day, msg = _err_details(txt)
            kind = "key" if status in (401, 403) else "quota" if status == 429 and per_day else (
                "busy" if status in (408, 429, 500, 502, 503, 504) else "bad")
            if status == 400 and re.search(r"API key|API_KEY", msg):
                kind = "key"
            raise GeminiError(status, f"{status}: {msg[:300]}", retry_after=retry, per_day=per_day, kind=kind)
        j = json.loads(txt)
        cands = j.get("candidates") or []
        if not cands:
            fb = (j.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(0, f"bloklandi: {fb}", retryable=False, kind="blocked")
        cand = cands[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        if not text.strip():
            reason = cand.get("finishReason")
            blocked = reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY")
            raise GeminiError(0, f"javob bo‘sh ({reason})", retryable=not blocked, kind="blocked" if blocked else "bad")
        try:
            return normalize_result(parse_json(text))
        except ValueError:
            raise GeminiError(0, "JSON buzuq: " + text[:120], retryable=True, kind="bad")

    async def read_page(self, session, jpeg: bytes, context=None, part=None, glossary=None,
                        notify=None, max_wait: float = 150, timeout: float = 150) -> dict:
        """Sahifani o'qib tarjima qiladi. Qaytaradi: {'blocks': [...], 'source_language': str, 'model': str}.
        Hamma modellar band bo'lsa — max_wait soniyagacha kutib qayta urinadi."""
        if not self.key:
            raise GeminiError(401, "GEMINI_API_KEY kiritilmagan", retryable=False, kind="key")
        prompt = build_prompt(context, part, glossary)
        waited, rnd = 0.0, 0
        while True:
            rnd += 1
            order = self._order()
            if not order:
                soon = min(self.dead.values()) - time.time() if self.dead else 0
                raise GeminiError(429, "hamma modellarning kunlik limiti tugagan", retry_after=max(0, soon),
                                  per_day=True, retryable=False, kind="quota")
            errors, busy, retry_after = [], 0, None
            for model in order:
                t0 = time.time()
                try:
                    res = await self._call(session, model, jpeg, prompt, timeout)
                    self.last_good = model
                    res["model"] = model
                    log.info("gemini ok: %s %.1fs, %d blok", model, time.time() - t0, len(res["blocks"]))
                    return res
                except GeminiError as e:
                    log.warning("gemini %s: %s", model, str(e)[:220])
                    errors.append(e)
                    if e.kind == "key":
                        raise
                    if e.kind == "blocked":
                        raise
                    if e.status == 404:
                        self.dead[model] = time.time() + 6 * 3600
                    elif e.kind == "quota":
                        self.dead[model] = time.time() + 3600  # kunlik limit — bir soat tegmaymiz
                    elif e.retryable:
                        busy += 1
                        if e.retry_after:
                            retry_after = max(retry_after or 0.0, e.retry_after)
                except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                    log.warning("gemini %s: %r", model, e)
                    errors.append(GeminiError(0, repr(e), kind="busy"))
                    busy += 1
            if not busy:
                if all(e.kind == "quota" or e.status == 404 for e in errors):
                    raise GeminiError(429, "hamma modellarning kunlik limiti tugagan", per_day=True,
                                      retryable=False, kind="quota")
                last = errors[-1] if errors else GeminiError(0, "modellar javob bermadi")
                raise GeminiError(last.status, "; ".join(str(e)[:120] for e in errors)[-600:],
                                  retryable=False, kind=last.kind or "bad")
            delay = min(max(retry_after or 0.0, 12.0 * rnd), 45.0, max_wait - waited)
            if delay <= 0:
                raise GeminiError(503, "Gemini band", retryable=True, kind="busy")
            if notify:
                try:
                    await notify(f"⏳ Google serverlari band — {round(delay)} soniyadan keyin yana urinaman…")
                except Exception:
                    pass
            await asyncio.sleep(delay)
            waited += delay
