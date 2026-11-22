"""Gemini: kinoning ovozini «eshitib» muhim lahzalarni topish va o'zbekcha hikoya matnini yozish."""
import asyncio
import json
import logging
import os
import re
import time

import aiohttp

log = logging.getLogger("gemini")

BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
DEFAULT_MODELS = "gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest"
SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


class GeminiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def fmt_ts(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_ts(v, default=None):
    """'1:02:03', '62:03', '00:05:10.5', 75 → soniya."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    nums = re.findall(r"\d+(?:[.,]\d+)?", str(v))
    if not nums:
        return default
    p = [float(x.replace(",", ".")) for x in nums[:3]]
    if len(p) == 3:
        return p[0] * 3600 + p[1] * 60 + p[2]
    if len(p) == 2:
        return p[0] * 60 + p[1]
    return p[0]


def _parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


SCENE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lahzalar": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "vaqt": {"type": "STRING"},
                    "voqea": {"type": "STRING"},
                    "kuchli": {"type": "BOOLEAN"},
                },
                "required": ["vaqt", "voqea"],
            },
        }
    },
    "required": ["lahzalar"],
}

SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "nomi": {"type": "STRING"},
        "jumlalar": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "matn": {"type": "STRING"},
                    "boshlanish": {"type": "STRING"},
                    "tugash": {"type": "STRING"},
                },
                "required": ["matn", "boshlanish", "tugash"],
            },
        },
        "izoh": {"type": "STRING"},
        "heshteglar": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["nomi", "jumlalar", "izoh", "heshteglar"],
}


def scene_prompt(name, k, n, start, length):
    part = f"part {k + 1} of {n} of the" if n > 1 else "the full"
    return (
        f"This audio is {part} soundtrack of the movie «{name}». "
        f"It covers movie time {fmt_ts(start)} – {fmt_ts(start + length)}.\n"
        "Listen carefully to the dialogue, sound effects and music, and list the key plot moments of this audio "
        "in chronological order. For each moment give:\n"
        "- \"vaqt\": timestamp FROM THE START OF THIS AUDIO FILE, format H:MM:SS;\n"
        "- \"voqea\": 1–2 sentences in Uzbek (Latin script) — who does what, what happens, the emotion; "
        "use character names when they are said;\n"
        "- \"kuchli\": true if the moment is especially dramatic, scary, emotional or action-packed "
        "(good for a trailer-like edit).\n"
        "Give roughly one moment every 2–3 minutes, more where the action is dense. Skip the opening logos and "
        "the end credits. Do not invent anything that is not in the audio; if speech is unclear, describe by sounds."
    )


def script_prompt(name, duration, moments, variant=0):
    lines = "\n".join(
        f"{fmt_ts(m['t'])} — {m['voqea']}" + (" [KUCHLI]" if m.get("kuchli") else "") for m in moments
    )
    extra = "\nWrite a NEW version that differs from earlier ones (another hook and wording)." if variant else ""
    return (
        "You write viral Uzbek Instagram Reels movie recaps («kino qisqacha mazmuni»).\n"
        f"Movie: «{name}», length {fmt_ts(duration)}.\n"
        "Key moments of the movie with timestamps (movie time):\n"
        f"{lines}\n\n"
        "Write a narration script in Uzbek (Latin script; lively spoken but correct literary Uzbek; "
        "write o‘ and g‘ with the ‘ sign) that a narrator will read aloud in about 55 seconds "
        "(110–130 words in total).\n"
        "Rules:\n"
        "- 8–11 short sentences, each 6–16 words, easy to read aloud.\n"
        "- Sentence 1 is a strong hook that makes the viewer keep watching (an intriguing question, a shocking "
        "fact, or the most gripping moment).\n"
        "- Then retell the story in chronological order, in the present tense, simply and vividly; use the "
        "characters' names if known.\n"
        "- Do NOT reveal the ending: stop at the most intense moment and leave intrigue (cliffhanger).\n"
        "- NEVER say the movie's title (in any language) in the narration — viewers must not learn which movie it is; "
        "character names are fine.\n"
        "- No greetings, no call to action, no hashtags, no emojis — an ending phrase will be added automatically.\n"
        "- For every sentence give \"boshlanish\" and \"tugash\" (H:MM:SS, movie time): the part of the movie "
        "whose scenes best illustrate that sentence, 8–40 seconds long, taken from the moments above. "
        "Sentence 1 may use the most dramatic moment of the film.\n"
        "- \"nomi\": the movie title as Uzbek viewers know it (for internal use only).\n"
        "- \"izoh\": an intriguing Instagram caption in Uzbek, 2–3 short sentences, 1–2 fitting emojis allowed, that makes "
        "people want to watch. It must NOT contain the movie title, the year, actor names or anything that reveals which "
        "movie it is; no hashtags here.\n"
        "- \"heshteglar\": 10–14 popular hashtags for this video (genre and mood, in Uzbek and English, e.g. #qorqinchli "
        "#horror #triller #drama). No hashtags with the movie title, actors or year."
        f"{extra}"
    )


class Gemini:
    def __init__(self, key, models=None):
        self.key = key
        self.models = [m.strip() for m in (models or DEFAULT_MODELS).split(",") if m.strip()]
        self.last_good = None
        self.dead = {}

    def _headers(self):
        return {"x-goog-api-key": self.key}

    def _order(self):
        now = time.time()
        order = [m for m in self.models if self.dead.get(m, 0) <= now] or list(self.models)
        if self.last_good in order:
            order.remove(self.last_good)
            order.insert(0, self.last_good)
        return order

    async def upload(self, session, path, mime="audio/mpeg"):
        size = os.path.getsize(path)
        h = dict(self._headers())
        h.update({
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        })
        async with session.post(f"{BASE}/upload/v1beta/files", headers=h,
                                json={"file": {"display_name": os.path.basename(path)}},
                                timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                raise GeminiError(r.status, "yuklash boshlanmadi: " + (await r.text())[:300])
            url = r.headers.get("X-Goog-Upload-URL")
        if not url:
            raise GeminiError(0, "yuklash manzili kelmadi")
        with open(path, "rb") as f:
            data = f.read()
        async with session.post(url, data=data, headers={
            "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
        }, timeout=aiohttp.ClientTimeout(total=900)) as r:
            if r.status != 200:
                raise GeminiError(r.status, "fayl yuklanmadi: " + (await r.text())[:300])
            info = (await r.json(content_type=None)).get("file") or {}
        name = info.get("name")
        for _ in range(150):
            st = info.get("state")
            if st == "ACTIVE" or (st is None and info.get("uri")):
                return info
            if st == "FAILED":
                raise GeminiError(0, "Gemini faylni o‘qiy olmadi")
            await asyncio.sleep(2)
            async with session.get(f"{BASE}/v1beta/{name}", headers=self._headers(),
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status == 200:
                    info = await r.json(content_type=None)
        raise GeminiError(0, "Gemini faylni tayyorlashga ulgurmadi")

    async def delete(self, session, name):
        try:
            async with session.delete(f"{BASE}/v1beta/{name}", headers=self._headers(),
                                      timeout=aiohttp.ClientTimeout(total=30)):
                pass
        except Exception:
            pass

    async def _call(self, session, model, parts, schema, temperature, timeout):
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
            "safetySettings": SAFETY,
        }
        if schema:
            body["generationConfig"]["responseSchema"] = schema
        async with session.post(f"{BASE}/v1beta/models/{model}:generateContent", headers=self._headers(),
                                json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            txt = await r.text()
            if r.status != 200:
                raise GeminiError(r.status, re.sub(r"\s+", " ", txt)[:300])
        j = json.loads(txt)
        cands = j.get("candidates") or []
        if not cands:
            fb = (j.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(0, f"javob yo‘q ({fb or 'noma’lum'})")
        cand = cands[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        if not text.strip():
            raise GeminiError(0, f"javob bo‘sh ({cand.get('finishReason')})")
        return text

    async def generate_json(self, session, parts, schema, temperature=0.7, timeout=480):
        errors = []
        for model in self._order():
            t0 = time.time()
            try:
                data = _parse_json(await self._call(session, model, parts, schema, temperature, timeout))
                self.last_good = model
                log.info("gemini ok: %s %.0fs", model, time.time() - t0)
                return data
            except GeminiError as e:
                log.warning("gemini %s xato: %s %s", model, e.status, e)
                errors.append(f"{model}: {e.status} {str(e)[:120]}")
                if e.status in (401, 403) and "PERMISSION" not in str(e).upper():
                    break  # kalit noto'g'ri — boshqa modelni sinash befoyda
                if e.status == 404:
                    self.dead[model] = time.time() + 6 * 3600
            except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, KeyError) as e:
                log.warning("gemini %s: %r", model, e)
                errors.append(f"{model}: {type(e).__name__}")
        raise GeminiError(0, "; ".join(errors)[-700:] or "modellar javob bermadi")

    async def scene_log(self, session, audio_path, mime, name, k, n, start, length):
        info = await self.upload(session, audio_path, mime)
        try:
            parts = [
                {"file_data": {"mime_type": info.get("mimeType") or mime, "file_uri": info["uri"]}},
                {"text": scene_prompt(name, k, n, start, length)},
            ]
            data = await self.generate_json(session, parts, SCENE_SCHEMA, temperature=0.3)
        finally:
            await self.delete(session, info.get("name"))
        raw = []
        for m in data.get("lahzalar") or []:
            t = parse_ts(m.get("vaqt"))
            text = (m.get("voqea") or "").strip()
            if t is not None and text:
                raw.append((max(0.0, t), text, bool(m.get("kuchli"))))
        # Ba'zan model 2-bo'lakda ham kino boshidan hisoblangan vaqtni yozadi — buni aniqlab to'g'rilaymiz
        absolute = start > 0 and raw and sum(1 for t, _, _ in raw if t > length + 30) > len(raw) / 2
        res = []
        for t, text, strong in raw:
            mt = t if absolute else start + t
            if start - 30 <= mt <= start + length + 30:
                res.append({"t": mt, "voqea": text, "kuchli": strong})
        return res

    async def write_script(self, session, name, duration, moments, variant=0):
        parts = [{"text": script_prompt(name, duration, moments, variant)}]
        return await self.generate_json(session, parts, SCRIPT_SCHEMA, temperature=0.9 if variant else 0.75,
                                        timeout=240)
